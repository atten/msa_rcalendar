import datetime

from rest_framework import viewsets, mixins
from rest_framework.decorators import list_route, detail_route
from rest_framework.exceptions import ParseError, ValidationError
from rest_framework import status
from rest_framework.response import Response

from django.db.models import Q
from django.utils.dateparse import parse_datetime
from django.utils.timezone import get_default_timezone
from django.utils.translation import ugettext_lazy as _
from django.shortcuts import get_object_or_404

from . import serializers, permissions, exceptions
from .models import Organization, Manager, Resource, Interval, ResourceScheduleInterval
from .utils import parse_args


class CreateWithIdMixIn(mixins.CreateModelMixin):
    def create(self, request, *args, **kwargs):
        id = request.data.get('id')
        if id is not None:
            obj, created = self.get_queryset().get_or_create(id=id)
            return Response(self.get_serializer(obj).data, status=status.HTTP_201_CREATED)
        else:
            super().create(*args, **kwargs)


class OrganizationViewSet(CreateWithIdMixIn,
                          mixins.RetrieveModelMixin,
                          mixins.DestroyModelMixin,
                          viewsets.GenericViewSet):
    queryset = Organization.objects.all()
    serializer_class = serializers.OrganizationSerializer

    @detail_route()
    def intervals(self, request, pk):
        start, end = parse_args(parse_datetime, request.GET, False, 'start', 'end')
        resource_id = request.GET.get('resource')
        org = self.get_object()

        intervals = Interval.objects.filter(Q(organization=org) |                           # интервалы, относящиеся к текущей организации
                                            Q(kind=Interval.Kind_OrganizationReserved) |    # или к организациям вообще
                                            Q(kind=Interval.Kind_Unavailable) |
                                            Q(kind=Interval.Kind_ScheduledUnavailable))
        if resource_id:
            intervals = intervals.filter(resource_id=resource_id)
            resources = Resource.objects.filter(id=resource_id)
        else:
            ids = org.get_resource_ids()
            intervals = intervals.filter(resource_id__in=ids)
            resources = Resource.objects.filter(id__in=ids)

        resources.extend_schedules(end)             # продлеваем расписание до конечной просматриваемой даты
        intervals.update_extendables(end)           # обновляем продлеваемые интервалы до конечной просматриваемой даты
        intervals = intervals.between(start, end)   # и выбираем границы просмотра

        # фильтруем интервалы, попадающие в интервалы других организаций
        other_org_interval_ranges = {}     # ключ: resource_id, значения: [(start, end), ...]
        filtered_intervals = []
        for i in intervals:
            add = True
            if i.kind == Interval.Kind_OrganizationReserved and i.organization_id != org.id:    # найден интервал другой орг-ии
                i.comment = None      # скрываем комментарий
                i.manager = None      # скрываем менеджера
                other_org_interval_ranges.setdefault(i.resource_id, [])
                other_org_interval_ranges[i.resource_id].append((i.start, i.end))

            if i.resource_id in other_org_interval_ranges and i.kind in (i.Kind_Unavailable, i.Kind_ScheduledUnavailable):  # найден интервал ресурса
                for r in other_org_interval_ranges[i.resource_id]:
                    if r[0] <= i.start and r[1] >= i.end:
                        add = False
                        break
            if add:
                filtered_intervals.append(i)

        data = serializers.IntervalSerializer(filtered_intervals, many=True).data

        return Response(data)


class ManagerViewSet(CreateWithIdMixIn,
                     mixins.DestroyModelMixin,
                     viewsets.GenericViewSet):
    queryset = Manager.objects.all()
    serializer_class = serializers.ManagerSerializer

    def destroy(self, request, *args, **kwargs):
        if 'organization' in request.GET:           # удалить только из указанной организации
            instance = self.get_object()
            organization_id = request.GET.get('organization')
            organization = Organization.objects.get(id=organization_id)
            instance.organizations.remove(organization)
            return Response()

        return super().destroy(request, *args, **kwargs)

    @list_route(['POST'])
    def add_many(self, request):
        ids = request.data.get('ids')
        organization_id = request.data.get('organization')

        if not ids:
            raise ParseError({'ids': 'This fields is required.'})
        if not organization_id:
            raise ParseError({'organization': 'This fields is required.'})

        organization = Organization.objects.get(id=organization_id)
        count = 0
        for id in ids:
            if not organization.managers.filter(id=id).exists():
                obj, created = Manager.objects.get_or_create(id=id)
                organization.managers.add(obj)
                count += 1
        return Response({'count': count}, status=status.HTTP_201_CREATED)


class ResourceViewSet(CreateWithIdMixIn,
                      mixins.DestroyModelMixin,
                      viewsets.GenericViewSet):
    queryset = Resource.objects.all()
    serializer_class = serializers.ResourceSerializer

    @list_route(['POST'])
    def add_many(self, request):
        ids = request.data.get('ids')
        organization_id = request.data.get('organization')
        fulltime = request.data.get('fulltime')
        if not ids:
            raise ParseError({'ids': 'This fields is required.'})

        count = 0
        if organization_id:
            organization = get_object_or_404(Organization, id=organization_id)

        for id in ids:
            obj, created = Resource.objects.get_or_create(id=id)
            if organization_id:
                obj.set_participation(organization, fulltime)
            if created:
                count += 1
        return Response({'created': count}, status=status.HTTP_201_CREATED)

    @detail_route(['PATCH', 'DELETE'])
    def participation(self, request, pk):
        obj = self.get_object()

        if request.method == 'PATCH':
            organization_id = request.data.get('organization')
            fulltime = request.data.get('fulltime')

            organization = get_object_or_404(Organization, id=organization_id)
            try:
                obj.set_participation(organization, fulltime)
            except (ValueError, exceptions.FormError) as e:
                raise ValidationError({'detail': str(e)})
            return Response()

        elif request.method == 'DELETE':
            organization_id = request.GET.get('organization')

            organization = get_object_or_404(Organization, id=organization_id)
            obj.dismiss_from_organization(organization)
            return Response()

    @detail_route()
    def schedule(self, request, pk):
        instance = self.get_object()
        serializer = serializers.ResourceScheduleSerializer(instance)
        return Response(serializer.data)

    @detail_route(['POST'])
    def apply_schedule(self, request, pk):
        start, end = parse_args(parse_datetime, request.data, True, 'start', 'end')
        intervals_raw = request.data.get('schedule_intervals')
        intervals = []

        permanent = not start and not end
        do_clear = not intervals_raw
        if not do_clear:
            intervals_serializer = serializers.ResourceScheduleIntervalSerializer(
                                        data=intervals_raw,
                                        many=True
                                    )
            intervals_serializer.is_valid(raise_exception=True)
            intervals = [ResourceScheduleInterval(**kwargs) for kwargs in intervals_serializer.validated_data]

        instance = self.get_object()
        detail_str = _('Resource schedule has been %s.')

        if do_clear:
            detail_str %= _('cleared %s')
        elif instance.schedule_intervals.count():
            detail_str %= _('updated %s')
        else:
            detail_str %= _('created %s')

        if permanent:
            detail_str %= _('from now on')
        elif start and end:
            detail_str %= _('from %s to %s') % (start.strftime('%x'), end.strftime('%x'))
        elif start:
            detail_str %= _('starting %s') % start.strftime('%x')
        else:
            raise ValidationError({'detail': _('Missing argument "start".')})

        if not start:
            start = datetime.datetime.now(get_default_timezone())

        if not end:
            end = start + Interval.EXTENDABLE_INTERVALS_MIN_DURATION

        instance.apply_schedule(start, end, intervals, save_as_default=permanent)

        if permanent or not instance.schedule_extended_date or instance.schedule_extended_date < end:
            instance.schedule_extended_date = end
            instance.save()

        return Response({'detail': detail_str})

    @detail_route()
    def intervals(self, request, pk):
        start, end = parse_args(parse_datetime, request.GET, False, 'start', 'end')
        resource = self.get_object()
        intervals = Interval.objects.filter(resource=resource)

        resource.extend_schedule(end)      # продлеваем расписание до конечной просматриваемой даты
        intervals.update_extendables(end)  # обновляем продлеваемые интервалы до конечной просматриваемой даты

        intervals = intervals.between(start, end)
        data = serializers.IntervalSerializer(intervals, many=True).data
        return Response(data)

    @detail_route(['POST'])
    def clear_unavailable_interval(self, request, pk):
        start, end = parse_args(parse_datetime, request.data, False, 'start', 'end')
        resource = self.get_object()
        result = resource.clear_unvailable_interval(start, end)
        if result:
            return Response({'detail': _('Specified interval has been marked as available for working.')})
        return Response({'detail': _('Specified interval already marked as available for working.')})


class IntervalViewSet(mixins.CreateModelMixin,
                      mixins.UpdateModelMixin,
                      mixins.DestroyModelMixin,
                      viewsets.GenericViewSet):
    queryset = Interval.objects.select_related('resource', 'organization')
    serializer_class = serializers.IntervalSerializer
    permission_classes = (permissions.IntervalPermission,)

    def create(self, request, *args, **kwargs):
        kind = request.data.get('kind')
        if isinstance(kind, str):
            kind = Interval.kind_from_str(kind)
            request.data['kind'] = kind
        return super().create(request, *args, **kwargs)     # права по созданию проверяются в методе Interval.save()

    @list_route(['DELETE'])
    def delete_many(self, request):
        ids = request.data.get('ids')
        for id in ids:
            self.kwargs = {'pk': id}
            self.destroy(request)
        return Response(status=status.HTTP_204_NO_CONTENT)
