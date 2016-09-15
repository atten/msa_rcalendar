import uuid
import datetime

from django.db import models
from django.db.models import Q, Min, Max
from django.db.models.query import QuerySet
from django.utils.translation import ugettext_lazy as _
from django.utils.timezone import get_default_timezone, UTC

from . import utils, exceptions
from .middleware import EventDispatchMiddleware as EventDispatcher


class ApiModelMixIn(models.Model):
    app = models.CharField(max_length=30)
    msa_id = models.IntegerField()

    class Meta:
        abstract = True


class Organization(ApiModelMixIn):
    def get_resource_ids(self):
        return self.resource_members.values_list('resource_id', flat=True)

    def __str__(self):
        return '%s: %d' % (self._meta.object_name, self.msa_id)


class Manager(ApiModelMixIn):
    organizations = models.ManyToManyField(Organization, related_name="managers")


class Resource(ApiModelMixIn):
    def join_organization(self, organization, raise_if_joined=False):
        obj, created = ResourceMembership.objects.get_or_create(resource=self, organization=organization)
        if not created and raise_if_joined:
            raise ValueError(_('%s is already member of this organization.') % str(self))

    def dismiss_from_organization(self, organization):
        obj = ResourceMembership.objects.get(resource=self, organization=organization)
        obj.strip_organization_time()
        obj.delete()

    def clear_unvailable_interval(self, start, end):
        i = Interval(resource=self, start=start, end=end, kind=Interval.Kind_Unavailable)
        return i.clear_existing()

    def __str__(self):
        return '%s: %d' % (self._meta.object_name, self.msa_id)


class IntervalQuerySet(QuerySet):
    def between(self, start, end, include_end_date=True):
        if not isinstance(start, datetime.datetime):
            start = utils.datetime_from_date(start)
        if not isinstance(end, datetime.datetime):
            if include_end_date:
                end += datetime.timedelta(days=1)
            end = utils.datetime_from_date(end)
        return self.filter(Q(start__lte=start, end__gte=end) |
                           Q(start__range=(start, end)) |
                           Q(end__range=(start, end))) \
                   .exclude(start=end).exclude(end=start)

    def at_date(self, dt):
        if not isinstance(dt, datetime.datetime):
            dt = utils.datetime_from_date(dt)
        return self.filter(Q(start__lt=dt, end__gt=dt))

    def similar(self, interval):
        q = Q(resource=interval.resource, kind=interval.kind, organization=interval.organization)

        # если интервал для организации, разных менеджеров не учитываем
        if interval.kind != Interval.Kind_OrganizationReserved:
            q &= Q(manager=interval.manager)

        qs = self.filter(q)

        if interval.id:
            qs = qs.exclude(id=interval.id)
        return qs


class Interval(models.Model):
    EXTENDABLE_INTERVALS_MIN_DURATION = datetime.timedelta(days=40)
    JOIN_GAP = datetime.timedelta(minutes=5)

    Kind_OrganizationReserved = 0
    Kind_ManagerReserved = 10
    Kind_Unavailable = 100

    KIND_CHOICES = (
        (Kind_OrganizationReserved, 'organization'),
        (Kind_ManagerReserved, 'manager'),
        (Kind_Unavailable, 'unavailable'),
    )

    start = models.DateTimeField()
    end = models.DateTimeField()
    resource = models.ForeignKey("Resource", related_name='intervals', on_delete=models.CASCADE)
    kind = models.SmallIntegerField(_('Interval kind'), choices=KIND_CHOICES, default=KIND_CHOICES[0][0])
    organization = models.ForeignKey(Organization, related_name='reserved_intervals', null=True, on_delete=models.CASCADE)
    manager = models.ForeignKey(Manager, related_name='reserved_intervals', null=True, on_delete=models.CASCADE)
    comment = models.TextField(null=True, blank=True)

    objects = IntervalQuerySet.as_manager()

    class Meta:
        ordering = ('kind',)

    @classmethod
    def kind_from_str(cls, kind_str):
        for choice in cls.KIND_CHOICES:
            if choice[1] == kind_str:
                return choice[0]
        return 0

    def get_object(self, msa_id_only=False):
        """возвращает менеджера или организацию, к которой относится"""
        if self.kind == self.Kind_OrganizationReserved:
            return self.organization.msa_id if msa_id_only and self.organization else self.organization
        if self.kind == self.Kind_ManagerReserved:
            return self.manager.msa_id if msa_id_only and self.manager else self.manager
        return None

    def join_existing(self, existing=None, timedelta='default'):
        """
        склеивает с имеющимися интервалами с совпадающими параметрами
        :param timedelta: временной промежуток для склеивания
        :param existing: если не указан, интервалы берутся из базы, склеиваемые удаляются. Если указан список интервалов, то склеиваемый добавляется к ним
        """
        if timedelta == 'default':
            timedelta = Interval.JOIN_GAP
        qs = existing if existing is not None else Interval.objects.similar(self).between(self.start - timedelta, self.end + timedelta)
        do_save = isinstance(qs, models.QuerySet)
        do_append = isinstance(existing, list)
        changed = False

        if do_save:
            d = qs.aggregate(Min('start'), Max('end'))
            if d['start__min'] and d['end__max']:
                self.start = min(d['start__min'], self.start)
                self.end = max(d['end__max'], self.end)
                qs.delete()
                changed = True
        elif do_append:
            for interval in existing:
                if interval.start >= self.start and interval.end <= self.end:   # имеющийся внутри
                    existing.remove(interval)
                    changed = True
                elif interval.start < self.start < interval.end or (self.start > interval.end and self.start - interval.end < timedelta):  # пересекаются или соприкасаются
                    self.start = interval.start
                    existing.remove(interval)
                    changed = True
                elif interval.start < self.end < interval.end or (interval.start > self.end and interval.start - self.end < timedelta):   # пересекаются или соприкасаются
                    self.end = interval.end
                    existing.remove(interval)
                    changed = True

        return changed

    def clear_existing(self, existing=None):
        """
        исключает интервал из имеющихся интервалов с совпадающими параметрами, удаляя их или обрезая
        :param existing: список рассматриваемых интервалов, любо None (в этом случае берется qs похожих из базы)
        """
        qs = existing or Interval.objects.similar(self).between(self.start, self.end)
        do_save = isinstance(qs, models.QuerySet)
        do_append = isinstance(existing, list)
        changed = False

        for interval in qs:
            if interval.start < self.start and interval.end > self.end:        # снаружи
                changed = True
                end_old = interval.end
                interval.end = self.start
                if do_save:
                    interval.save(join_existing=False)
                i2 = Interval(start=self.end,
                              end=end_old,
                              kind=interval.kind,
                              resource=interval.resource,
                              manager=interval.manager,
                              organization=interval.manager,
                              comment=interval.comment)
                if do_save:
                    i2.save(join_existing=False)
                if do_append:
                    existing.append(i2)

            elif interval.start < self.start < interval.end:                   # пересекаются
                changed = True
                interval.end = self.start
                if do_save:
                    interval.save(join_existing=False)

            elif interval.start < self.end < interval.end:                     # пересекаются
                changed = True
                interval.start = self.end
                if do_save:
                    interval.save(join_existing=False)

            elif interval.start >= self.start and interval.end <= self.end:    # внутри
                changed = True
                if do_save:
                    interval.delete()
                if do_append:
                    existing.remove(interval)
        return changed

    def save(self, join_existing=True, *args, **kwargs):
        if self.start >= self.end:
            raise exceptions.FormError('end', _('End date must be greater than start date.'))

        # не указывать организацию можно только для интервалов недоступности
        if not self.organization_id and self.kind not in (self.Kind_Unavailable, self.Kind_ScheduledUnavailable):
            raise exceptions.FormError('organization', _('You must specify organization for this interval.'))

        # указанный менеджер должен состоять в указанной организации
        if self.organization_id and self.manager_id and self.organization not in self.manager.organizations.all():
            raise exceptions.FormError('', _('Only managers can reserve time for organization.'))

        if self.kind == Interval.Kind_ManagerReserved:
            if not self.manager_id:
                raise exceptions.FormError('manager', _('You must specify manager for this interval.'))

            if not Interval.objects.filter(kind=Interval.Kind_OrganizationReserved,
                                           organization=self.organization,
                                           resource=self.resource,
                                           start__lte=self.start,
                                           end__gte=self.end).exists():
                raise exceptions.FormError('', _('This period is\'t fall within organization time.'))

            if Interval.objects.between(self.start, self.end) \
                               .filter(kind=Interval.Kind_ManagerReserved,
                                       resource=self.resource) \
                               .exclude(manager=self.manager):
                raise exceptions.FormError('', _('This period is reserved for another manager.'))

        elif self.kind == Interval.Kind_OrganizationReserved:
            if Interval.objects.between(self.start, self.end) \
                               .filter(kind=Interval.Kind_OrganizationReserved,
                                       resource=self.resource) \
                               .exclude(organization=self.organization):
                raise exceptions.FormError('', _('This period falls within another organization.'))

        if join_existing:
            self.join_existing()
        super().save(*args, **kwargs)


class ResourceMembership(models.Model):
    resource = models.ForeignKey(Resource, related_name='organization_memberships', on_delete=models.CASCADE)
    organization = models.ForeignKey(Organization, related_name='resource_members', on_delete=models.CASCADE)
    schedule_extended_date = models.DateTimeField(null=True)

    class Meta:
        unique_together = ('resource', 'organization')

    @property
    def has_schedule(self):
        return self.schedule_intervals.count() > 0

    def strip_organization_time(self):
        """
        сокращает выделенное время ресурса для организации до окончания времени,
        выделенного каким-либо менеджером данной орг-ии
        """
        now = datetime.datetime.now(get_default_timezone())
        self.schedule_extended_date = now
        for i in Interval.objects.at_date(now).filter(resource=self.resource, organization=self.organization):
            i.end = now
            i.save()

    def extend_schedule(self, end):
        if self.schedule_extended_date and self.schedule_extended_date >= end:
            return
        if self.apply_schedule(self.schedule_extended_date, end):
            self.schedule_extended_date = end
            self.save()

    def apply_schedule(self, start, end, schedule_intervals=None, save_as_default=False):
        """
        создает новые интервалы доступности ресурса для организации
        :param schedule_intervals: список интервалов графика (если None, берет имеющиеся)
        :param save_as_default: сохранять переданные schedule_intervals в качестве постоянных или нет
        :param start: дата и время начала
        :param end: дата и время окончания
        """
        if not (schedule_intervals or self.schedule_intervals.count()):
            return False
        if not start or not end or start >= end:
            return False

        used_scedule_intervals = schedule_intervals if schedule_intervals is not None else self.schedule_intervals.all()

        # очищаем имеющиеся интервалы работы для выбранного отрезка времени
        work_interval = Interval(resource=self.resource, organization=self.organization,
                                 kind=Interval.Kind_OrganizationReserved, start=start, end=end)
        work_interval.clear_existing()

        intervals = []      # создаваемые интервалы
        schedule_intervals_map = {}     # раскладываем интервалы графика в словарь {день_недели: интервал графика}

        for si in used_scedule_intervals:
            if si.start.tzinfo is None:                  # добавляем временную зону
                si.start = si.start.replace(tzinfo=UTC())
            if si.end.tzinfo is None:
                si.end = si.end.replace(tzinfo=UTC())

            schedule_intervals_map.setdefault(si.day_of_week, [])
            schedule_intervals_map[si.day_of_week].append(si)

        for i in range(max((end - start).days, 1)):      # перебираем дни с первого по последний, начиная с 0
            apply_date = (start + datetime.timedelta(days=i)).date()
            week_day = (apply_date.weekday() + 1) % 7    # в нашем случае первый день недели - ВС, а не ПН
            if week_day not in schedule_intervals_map:
                continue
            for si in schedule_intervals_map[week_day]:
                apply_start = datetime.datetime.combine(apply_date, si.start)
                apply_end = datetime.datetime.combine(apply_date, si.end)
                # если нач. дата больше конечной (такое бывает, например,
                # когда местное время старта меньше UTC смещения и преобразуется в UTC)
                if apply_start > apply_end:
                    apply_start -= datetime.timedelta(days=1)
                interval = Interval(start=apply_start, end=apply_end, kind=Interval.Kind_OrganizationReserved,
                                    resource=self.resource, organization=self.organization)
                # включаем интервал в список созданных интервалов, объединяя перекрывающиеся
                interval.join_existing(intervals)
                intervals.append(interval)

        for i in intervals:                        # убираем короткие интервалы
            if i.end - i.start < Interval.JOIN_GAP:
                intervals.remove(i)

        intervals.sort(key=lambda d: d.start)      # сортируем получившиеся интервалы по возрастанию
        if len(intervals):                         # склеиваем первый (и последний) с имеющимися
            intervals[0].join_existing()
            if len(intervals) > 1:
                intervals[-1].join_existing()
        Interval.objects.bulk_create(intervals)    # записываем получившиеся интервалы

        # сохраняем расписание
        if save_as_default and schedule_intervals is not None:
            for si in schedule_intervals:
                si.membership = self
            self.schedule_intervals.all().delete()
            ScheduleInterval.objects.bulk_create(schedule_intervals)
        return True


class ScheduleInterval(models.Model):
    membership = models.ForeignKey(ResourceMembership, related_name='schedule_intervals', on_delete=models.CASCADE)
    day_of_week = models.PositiveSmallIntegerField()
    start = models.TimeField()
    end = models.TimeField()

    class Meta:
        ordering = ('membership', 'day_of_week')


class ApiKey(models.Model):
    key = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    app = models.CharField(max_length=30)
