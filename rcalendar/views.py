import datetime

from rest_framework import viewsets, mixins
from rest_framework.decorators import list_route, detail_route
from rest_framework.exceptions import ParseError, ValidationError, NotFound
from rest_framework.serializers import ModelSerializer
from rest_framework import status
from rest_framework.response import Response

from django.db.models import Q, ObjectDoesNotExist
from django.utils.dateparse import parse_datetime
from django.utils.timezone import get_default_timezone
from django.utils.translation import ugettext_lazy as _
from django.utils.formats import localize
from django.shortcuts import get_object_or_404

from . import serializers, permissions, exceptions
from .models import Organization, Manager, Resource, Interval, ScheduleInterval, ResourceMembership
from .utils import parse_args
from .decorators import append_events_data
from .middleware import EventDispatchMiddleware as EventDispatcher


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
    permission_classes = (permissions.HasValidApiKey,)
    lookup_field = 'msa_id'

    @detail_route()
    def intervals(self, request, msa_id):
        start, end = parse_args(parse_datetime, request.GET, False, 'start', 'end')
        resource_msa_id = request.GET.get('resource')
        org = self.get_object()

        intervals = Interval.objects.between(start, end).filter(Q(organization=org) |                           # интервалы, относящиеся к текущей организации
                                                                Q(kind=Interval.Kind_OrganizationReserved) |    # или к организациям вообще
                                                                Q(kind=Interval.Kind_Unavailable))
        if resource_msa_id:
            intervals = intervals.filter(resource__app=request.app, resource__msa_id=resource_msa_id)
            memberships = ResourceMembership.objects.filter(resource__app=request.app, resource__msa_id=resource_msa_id)
        else:
            resource_ids = org.get_resource_ids()
            intervals = intervals.filter(resource_id__in=resource_ids)
            memberships = ResourceMembership.objects.filter(resource_id__in=resource_ids)

        for membership in memberships.prefetch_related('schedule_intervals'):
            membership.extend_schedule(end)  # продлеваем расписание до конечной просматриваемой даты

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

            if i.resource_id in other_org_interval_ranges and i.kind != i.Kind_OrganizationReserved:  # найден интервал ресурса, попадающий в др. орг-ию
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
    permission_classes = (permissions.HasValidApiKey,)
    lookup_field = 'msa_id'

    def destroy(self, request, *args, **kwargs):
        if 'organization' in request.GET:           # удалить только из указанной организации
            instance = self.get_object()
            organization_msa_id = request.GET.get('organization')
            organization = Organization.objects.get(app=request.app, msa_id=organization_msa_id)
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

        organization = Organization.objects.get(app=request.app, msa_id=organization_msa_id)
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
    permission_classes = (permissions.HasValidApiKey,)
    lookup_field = 'msa_id'

    @list_route(['POST'])
    def add_many(self, request):
        msa_ids = request.data.get('ids')
        msa_organization_id = request.data.get('organization')

        if not msa_ids:
            raise ParseError({'ids': _('This field is required.')})

        count = 0
        joined = 0
        if msa_organization_id:
            organization = get_object_or_404(Organization, app=request.app, msa_id=msa_organization_id)

        for msa_id in msa_ids:
            obj, created = Resource.objects.get_or_create(app=request.app, msa_id=msa_id)
            if msa_organization_id:
                obj.join_organization(organization, raise_if_joined=False)
                joined += 1
            if created:
                count += 1
        return Response({'created': count, 'joined': joined}, status=status.HTTP_201_CREATED)

    @detail_route(['GET', 'PUT', 'DELETE'])
    # @append_events_data()
    def membership(self, request, msa_id):
        obj = self.get_object()
        msa_organization_id = request.data.get('organization') or request.GET.get('organization')
        organization = get_object_or_404(Organization, app=request.app, msa_id=msa_organization_id)

        try:
            if request.method == 'GET':
                membership = obj.organization_memberships.get(organization=organization)
                serializer = serializers.ResourceMembershipScheduleSerializer(membership)
                return Response(serializer.data)
            elif request.method == 'PUT':
                obj.join_organization(organization, raise_if_joined=True)
            elif request.method == 'DELETE':
                obj.dismiss_from_organization(organization)
        except ObjectDoesNotExist:
            raise NotFound
        except (ValueError, exceptions.FormError) as e:
            raise ValidationError({'detail': str(e)})
        return Response()

    @detail_route(['POST'])
    @append_events_data()
    def apply_schedule(self, request, msa_id):
        organization_msa_id = request.data.get('organization') or request.GET.get('organization')
        start, end = parse_args(parse_datetime, request.data, True, 'start', 'end')
        intervals_raw = request.data.get('schedule_intervals')
        intervals = []

        permanent = not end
        do_clear = not intervals_raw
        if not do_clear:
            intervals_serializer = serializers.ScheduleIntervalSerializer(
                                        data=intervals_raw,
                                        many=True
                                    )
            intervals_serializer.is_valid(raise_exception=True)
            intervals = [ScheduleInterval(**kwargs) for kwargs in intervals_serializer.validated_data]

        membership = get_object_or_404(ResourceMembership,
                                       resource__msa_id=msa_id, organization__msa_id=organization_msa_id)
        detail_str = _('Resource schedule for this organization has been %s.')

        if do_clear:
            detail_str %= _('cleared %s')
        elif membership.schedule_intervals.count():
            detail_str %= _('updated %s')
        else:
            detail_str %= _('created %s')

        if start and end:
            detail_str %= _('from %s to %s') % (localize(start), localize(end))
        elif start:
            detail_str %= _('starting %s') % localize(start)
        elif end:
            detail_str %= _('to %s') % localize(end)
        else:
            detail_str %= _('from now on')

        if not start:
            start = datetime.datetime.now(get_default_timezone())

        if not end:
            end = start + Interval.EXTENDABLE_INTERVALS_MIN_DURATION

        if membership.apply_schedule(start, end, intervals, save_as_default=permanent):
            if permanent or not membership.schedule_extended_date or membership.schedule_extended_date < end:
                membership.schedule_extended_date = end
                membership.save()
            manager_id = request.GET.get('author_id')
            EventDispatcher.push_event_to_responce(kind='apply-schedule',
                                                   manager=manager_id,
                                                   resource=msa_id,
                                                   organization=organization_msa_id,
                                                   permanent=permanent,
                                                   duration=[start, end])
        else:
            detail_str = _('Schedule wasn\'t changed.')

        return Response({'detail': detail_str, 'has_schedule': membership.has_schedule})

    @detail_route()
    def intervals(self, request, msa_id):
        start, end = parse_args(parse_datetime, request.GET, False, 'start', 'end')
        resource = self.get_object()

        for membership in resource.organization_memberships.all():
            membership.extend_schedule(end)      # продлеваем расписание до конечной просматриваемой даты

        intervals = Interval.objects.between(start, end).filter(resource=resource)
        data = serializers.IntervalSerializer(intervals, many=True).data
        return Response(data)

    @detail_route(['POST'])
    @append_events_data()
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

    @append_events_data()
    def create(self, request, *args, **kwargs):
        kind = request.data.get('kind')
        if isinstance(kind, str):
            kind = Interval.kind_from_str(kind)
            request.data['kind'] = kind
        ret = super().create(request, *args, **kwargs)

        msg = _('Specified interval has been %s.')
        detail_msg = {
            Interval.Kind_OrganizationReserved: msg % _('reserved for organization'),
            Interval.Kind_ManagerReserved: msg % _('reserved'),
            Interval.Kind_Unavailable: msg % _('marked as unavailable for working'),
        }
        if kind in detail_msg:
            ret.data = {'detail': detail_msg[kind]}
        return ret

    @append_events_data()
    def update(self, request, *args, **kwargs):
        ret = super().update(request, *args, **kwargs)
        ret.data = {'detail': _('Interval has been updated.')}
        return ret

    @list_route(['DELETE'])
    @append_events_data()
    def delete_many(self, request):
        ids = request.data.get('ids')
        for id in ids:
            self.kwargs = {'pk': id}
            self.destroy(request)
        return Response()
