import datetime

from rest_framework import viewsets, mixins
from rest_framework.decorators import list_route, detail_route
from rest_framework.exceptions import ParseError, ValidationError
from rest_framework.serializers import ModelSerializer
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


class SafeModelSerializerMixIn(object):
    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            orig_model = self.serializer_class.Meta.model

            class CreateInstanceSerializer(ModelSerializer):
                class Meta:
                    model = orig_model

            return CreateInstanceSerializer

        return self.serializer_class


class FilterByAppViewSet:
    def create(self, request, *args, **kwargs):
        id = request.data.pop('id')
        request.data['msa_id'] = id
        request.data['app'] = request.app
        return super().create(request, *args, **kwargs)

    def get_queryset(self):
        queryset = self.queryset
        if hasattr(self.request, 'app'):
            queryset = queryset.filter(app=self.request.app)
        return queryset


class OrganizationViewSet(FilterByAppViewSet,
                          mixins.CreateModelMixin,
                          mixins.RetrieveModelMixin,
                          mixins.DestroyModelMixin,
                          SafeModelSerializerMixIn,
                          viewsets.GenericViewSet):
    queryset = Organization.objects.all()
    serializer_class = serializers.OrganizationSerializer
    lookup_field = 'msa_id'

    @detail_route()
    def intervals(self, request, msa_id):
        start, end = parse_args(parse_datetime, request.GET, False, 'start', 'end')
        resource_msa_id = request.GET.get('resource')
        org = self.get_object()

        intervals = Interval.objects.filter(Q(organization=org) |                           # интервалы, относящиеся к текущей организации
                                            Q(kind=Interval.Kind_OrganizationReserved) |    # или к организациям вообще
                                            Q(kind=Interval.Kind_Unavailable) |
                                            Q(kind=Interval.Kind_ScheduledUnavailable))
        if resource_msa_id:
            intervals = intervals.filter(resource__msa_id=resource_msa_id)
            resources = Resource.objects.filter(msa_id=resource_msa_id)
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


class ManagerViewSet(FilterByAppViewSet,
                     mixins.CreateModelMixin,
                     mixins.DestroyModelMixin,
                     viewsets.GenericViewSet):
    queryset = Manager.objects.all()
    serializer_class = serializers.ManagerSerializer
    lookup_field = 'msa_id'

    def destroy(self, request, *args, **kwargs):
        if 'organization' in request.GET:           # удалить только из указанной организации
            instance = self.get_object()
            organization_msa_id = request.GET.get('organization')
            organization = Organization.objects.get(msa_id=organization_msa_id)
            instance.organizations.remove(organization)
            return Response()

        return super().destroy(request, *args, **kwargs)

    @list_route(['POST'])
    def add_many(self, request):
        msa_ids = request.data.get('ids')
        organization_msa_id = request.data.get('organization')

        if not msa_ids:
            raise ParseError({'ids': _('This field is required.')})
        if not organization_msa_id:
            raise ParseError({'organization': _('This field is required.')})

        organization = Organization.objects.get(msa_id=organization_msa_id)
        count = 0
        for msa_id in msa_ids:
            if not organization.managers.filter(msa_id=msa_id).exists():
                obj, created = Manager.objects.get_or_create(app=request.app, msa_id=msa_id)
                organization.managers.add(obj)
                count += 1
        return Response({'count': count}, status=status.HTTP_201_CREATED)


class ResourceViewSet(FilterByAppViewSet,
                      mixins.CreateModelMixin,
                      mixins.DestroyModelMixin,
                      viewsets.GenericViewSet):
    queryset = Resource.objects.all()
    serializer_class = serializers.ResourceSerializer
    lookup_field = 'msa_id'

    @list_route(['POST'])
    def add_many(self, request):
        msa_ids = request.data.get('ids')
        msa_organization_id = request.data.get('organization')
        fulltime = request.data.get('fulltime')
        if not msa_ids:
            raise ParseError({'ids': _('This field is required.')})

        count = 0
        if msa_organization_id:
            organization = get_object_or_404(Organization, msa_id=msa_organization_id)

        for msa_id in msa_ids:
            obj, created = Resource.objects.get_or_create(app=request.app, msa_id=msa_id)
            if msa_organization_id:
                obj.set_participation(organization, fulltime)
            if created:
                count += 1
        return Response({'created': count}, status=status.HTTP_201_CREATED)

    @detail_route(['PATCH', 'DELETE'])
    def participation(self, request, msa_id):
        obj = self.get_object()

        if request.method == 'PATCH':
            msa_organization_id = request.data.get('organization')
            fulltime = request.data.get('fulltime')

            organization = get_object_or_404(Organization, msa_id=msa_organization_id)
            try:
                obj.set_participation(organization, fulltime)
            except (ValueError, exceptions.FormError) as e:
                raise ValidationError({'detail': str(e)})
            return Response()

        elif request.method == 'DELETE':
            msa_organization_id = request.GET.get('organization')

            organization = get_object_or_404(Organization, msa_id=msa_organization_id)
            obj.dismiss_from_organization(organization)
            return Response()

    @detail_route()
    def schedule(self, request, msa_id):
        instance = self.get_object()
        serializer = serializers.ResourceScheduleSerializer(instance)
        return Response(serializer.data)

    @detail_route(['POST'])
    def apply_schedule(self, request, msa_id):
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
    def intervals(self, request, msa_id):
        start, end = parse_args(parse_datetime, request.GET, False, 'start', 'end')
        resource = self.get_object()
        intervals = Interval.objects.filter(resource=resource)

        resource.extend_schedule(end)      # продлеваем расписание до конечной просматриваемой даты
        intervals.update_extendables(end)  # обновляем продлеваемые интервалы до конечной просматриваемой даты

        intervals = intervals.between(start, end)
        data = serializers.IntervalSerializer(intervals, many=True).data
        return Response(data)

    @detail_route(['POST'])
    def clear_unavailable_interval(self, request, msa_id):
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
    queryset = Interval.objects.select_related('resource', 'organization', 'manager')
    serializer_class = serializers.IntervalSerializer
    permission_classes = (permissions.HasValidApiKey, permissions.IntervalPermission,)

    def create(self, request, *args, **kwargs):
        kind = request.data.get('kind')
        if isinstance(kind, str):
            kind = Interval.kind_from_str(kind)
            request.data['kind'] = kind
        return super().create(request, *args, **kwargs)

    @list_route(['DELETE'])
    def delete_many(self, request):
        ids = request.data.get('ids')
        for id in ids:
            self.kwargs = {'pk': id}
            self.destroy(request)
        return Response(status=status.HTTP_204_NO_CONTENT)
