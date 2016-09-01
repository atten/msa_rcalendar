import uuid
import datetime

from django.db import models
from django.db.models import Q, Min, Max
from django.utils.translation import ugettext_lazy as _
from django.utils.timezone import get_default_timezone, UTC

from . import utils, exceptions


class Organization(models.Model):
    def get_resource_ids(self, fulltime=True, parttime=True):
        ret = []
        if fulltime:
            ret += self.fulltime_resources.values_list('id', flat=True)
        if parttime:
            ret += self.parttime_resources.values_list('id', flat=True)
        return ret


class Manager(models.Model):
    organizations = models.ManyToManyField(Organization, related_name="managers")


class Resource(models.Model):
    fulltime_organization = models.ForeignKey(Organization, related_name='fulltime_resources', null=True)
    parttime_organizations = models.ManyToManyField(Organization, related_name='parttime_resources')

    def is_fulltime_member(self, organization):
        return self.fulltime_organization == organization

    def update_organization_reserve(self):
        """резервирует (продлевает) время ресурса для организации на период по умолчанию"""
        if not self.fulltime_organization_id:
            return
        i = Interval(resource=self,
                     organization=self.fulltime_organization,
                     kind=Interval.Kind_OrganizationReserved,
                     start=datetime.datetime.now(get_default_timezone()),
                     end=utils.today_plus_timedelta(aligned=True, days=Interval.DEFAULT_PROLONGATION_WEEKS * 7))
        i.save()

    def strip_organization_time(self, organization):
        """
        сокращает выделенное время ресурса для организации до окончания времени,
        выделенного каким-либо менеджером данной орг-ии
        """
        now = datetime.datetime.now(get_default_timezone())
        for i in Interval.objects.at_date(now).filter(resource=self, organization=organization):
            i.end = now
            i.save()

    def set_participation(self, organization, fulltime):
        if fulltime:
            if self.fulltime_organization and not self.is_fulltime_member(organization):
                raise ValueError(_('%s is already full-time member of another organization') % str(self))
            self.fulltime_organization = organization
            self.parttime_organizations.remove(organization)
            self.update_organization_reserve()
        else:
            if self.is_fulltime_member(organization):   # перевод вне штата
                self.fulltime_organization = None
                self.strip_organization_time(organization)
            self.parttime_organizations.add(organization)
        self.save()

    def dismiss_from_organization(self, organization):
        if self.fulltime_organization_id == organization.id:
            self.fulltime_organization = None
            self.save()
        else:
            self.parttime_organizations.remove(organization)
        self.strip_organization_time(organization)

    def clear_unvailable_interval(self, start, end):
        i = Interval(resource=self, start=start, end=end, kind=Interval.Kind_Unavailable)
        changed1 = i.clear_existing()
        i.kind = Interval.Kind_ScheduledUnavailable
        changed2 = i.clear_existing()
        return changed1 or changed2

    def apply_schedule(self, schedule_intervals=None, current_week=True, next_weeks=True):
        """
        создает новые интервалы ресурса
        :param schedule_intervals: список интервалов графика (если None, берет имеющиеся)
        :param current_week: включая текущую неделю
        :param next_weeks: включая последующие недели
        """
        today = datetime.date.today()
        weekday = today.weekday()
        start_day = today
        days = 0        # кол-во задействованных дней

        if current_week:
            days = 7 - weekday
        else:
            start_day += datetime.timedelta(days=7 - weekday)

        if next_weeks:
            days += Interval.DEFAULT_PROLONGATION_WEEKS * 7

        end_day = start_day + datetime.timedelta(days)

        # очищаем имеющиеся для выбранного отрезка времени
        Interval.objects.between(start_day, end_day, include_end_date=False)\
                        .filter(resource=self, kind=Interval.Kind_ScheduledUnavailable).delete()

        used_scedule_intervals = schedule_intervals if schedule_intervals is not None else self.schedule_intervals
        intervals = []      # создаваемые интервалы

        if used_scedule_intervals:
            # создаем новый интервал, из которого будем вычленять интервалы работы
            new_big_interval = Interval(resource=self,
                                        kind=Interval.Kind_ScheduledUnavailable,
                                        start=utils.datetime_from_date(start_day),
                                        end=utils.datetime_from_date(end_day))
            intervals = [new_big_interval]

        schedule_intervals_map = {}     # раскладываем интервалы графика в словарь {день_недели: интервал графика}
        for si in used_scedule_intervals:
            if si.start.tzinfo is None:             # добавляем временную зону
                si.start = si.start.replace(tzinfo=UTC())
            if si.end.tzinfo is None:
                si.end = si.end.replace(tzinfo=UTC())

            schedule_intervals_map.setdefault(si.day_of_week, [])
            schedule_intervals_map[si.day_of_week].append(si)

        for i in range(days):
            apply_day = start_day + datetime.timedelta(days=i)
            week_day = (apply_day.weekday() + 1) % 7    # в нашем случае первый день недели - ВС, а не ПН
            if week_day not in schedule_intervals_map:
                continue
            for si in schedule_intervals_map[week_day]:
                start = datetime.datetime.combine(apply_day, si.start)
                end = datetime.datetime.combine(apply_day, si.end)
                # если нач. дата больше конечной (такое бывает, например,
                # когда местное время старта меньше UTC смещения и преобразуется в UTC)
                if start > end:
                    start -= datetime.timedelta(days=1)
                interval = Interval(start=start, end=end)
                # дробим список недоступных интервалов, вычленяя из него интервалы доступности
                interval.clear_existing(intervals)

        for i in intervals:                        # убираем короткие интервалы
            if i.end - i.start < Interval.JOIN_GAP:
                intervals.remove(i)

        Interval.objects.bulk_create(intervals)    # записываем получившиеся интервалы

        # сохраняем расписание, если оно предназначено на недели вперёд
        if schedule_intervals is not None and next_weeks:
            for si in schedule_intervals:
                si.resource = self
            self.schedule_intervals.all().delete()
            ResourceScheduleInterval.objects.bulk_create(schedule_intervals)

    def __str__(self):
        return 'Resource: %d' % self.id

    # def save(self, *args, **kwargs):
    #     created = self.pk is None
    #     super().save(*args, **kwargs)
    #
    #     if created:
    #         ResourceScheduleInterval.make_resource_schedule(self)        # всем ресурсам по дефолтному графику
    #         self.apply_schedule(current_week=True, next_weeks=True)


class IntervalManager(models.Manager):
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

    def similar(self, interval, start=None, end=None):
        q = Q(resource=interval.resource, kind=interval.kind, organization=interval.organization)

        # если интервал для организации, разных менеджеров не учитываем
        if interval.kind != Interval.Kind_OrganizationReserved:
            q &= Q(manager=interval.manager)

        if start and end:
            qs = self.between(start, end).filter(q)
        else:
            qs = self.filter(q)

        if interval.id:
            qs = qs.exclude(id=interval.id)
        return qs


class Interval(models.Model):
    DEFAULT_PROLONGATION_WEEKS = 2
    JOIN_GAP = datetime.timedelta(minutes=5)

    Kind_OrganizationReserved = 0
    Kind_ManagerReserved = 10
    Kind_ScheduledUnavailable = 90
    Kind_Unavailable = 100

    KIND_CHOICES = (
        (Kind_OrganizationReserved, 'organization'),
        (Kind_ManagerReserved, 'manager'),
        (Kind_Unavailable, 'unavailable'),
        (Kind_ScheduledUnavailable, 'scheduled unavailable'),
    )

    start = models.DateTimeField()
    end = models.DateTimeField()
    resource = models.ForeignKey("Resource", related_name='intervals', on_delete=models.CASCADE)
    kind = models.SmallIntegerField(_('Interval kind'), choices=KIND_CHOICES, default=KIND_CHOICES[0][0])
    organization = models.ForeignKey(Organization, related_name='reserved_intervals', null=True, on_delete=models.CASCADE)
    manager = models.ForeignKey(Manager, related_name='reserved_intervals', null=True, on_delete=models.CASCADE)
    comment = models.TextField(null=True, blank=True)

    objects = IntervalManager()

    class Meta:
        ordering = ('kind',)

    @classmethod
    def kind_from_str(cls, kind_str):
        for choice in cls.KIND_CHOICES:
            if choice[1] == kind_str:
                return choice[0]
        return 0

    def get_object(self, id_only=False):
        """возвращает менеджера или организацию, к которой относится"""
        if self.kind == self.Kind_OrganizationReserved:
            return self.organization_id if id_only else self.organization
        if self.kind == self.Kind_ManagerReserved:
            return self.manager_id if id_only else self.manager
        return None

    def join_existing(self, timedelta='default'):
        """склеивает с имеющимися интервалами с совпадающими параметрами, удаляя их"""
        if timedelta == 'default':
            timedelta = Interval.JOIN_GAP
        qs = Interval.objects.similar(self, self.start - timedelta, self.end + timedelta)
        d = qs.aggregate(Min('start'), Max('end'))
        if d['start__min'] and d['end__max']:
            self.start = min(d['start__min'], self.start)
            self.end = max(d['end__max'], self.end)
            qs.delete()
            return True
        return False

    def clear_existing(self, existing=None):
        """
        исключает интервал из имеющихся интервалов с совпадающими параметрами, удаляя их или обрезая
        :param existing: список рассматриваемых интервалов, любо None (в этом случае берется qs похожих из базы)
        """
        qs = existing or Interval.objects.similar(self, self.start, self.end)
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
            raise exceptions.FormError('organization', _('Manager is\'t in specified organization.'))

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

        elif self.kind == Interval.Kind_Unavailable:
            for i in Interval.objects.between(self.start, self.end) \
                                     .filter(kind=Interval.Kind_ScheduledUnavailable,
                                             resource=self.resource):
                if i.start <= self.start and i.end >= self.end:
                    raise exceptions.FormError('', _('This period falls within scheduled unavailable time.'))
                elif i.start <= self.start:
                    self.start = i.end
                elif i.end >= self.end:
                    self.end = i.start
                else:
                    next_part = Interval(start=i.end,
                                         end=self.end,
                                         resource=self.resource,
                                         kind=self.kind,
                                         manager=self.manager,
                                         comment=self.comment)
                    next_part.save(join_existing, *args, **kwargs)
                    self.end = i.start

        if join_existing:
            self.join_existing()
        super().save(*args, **kwargs)


class ResourceScheduleInterval(models.Model):
    resource = models.ForeignKey("Resource", related_name='schedule_intervals', on_delete=models.CASCADE)
    day_of_week = models.PositiveSmallIntegerField()
    start = models.TimeField()
    end = models.TimeField()

    class Meta:
        ordering = ('resource', 'day_of_week')


class ApiKey(models.Model):
    key = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
