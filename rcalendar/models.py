from typing import List, Optional, Union
import uuid
import datetime

from django.db import models
from django.db.models import Q, Min, Max
from django.db.models.query import QuerySet
from django.utils.translation import ugettext_lazy as _
from django.utils.timezone import get_default_timezone
from pytz import utc as UTC

from . import utils, exceptions
from .middleware import EventDispatchMiddleware as EventDispatcher

DateOrDatetime = [datetime.date, datetime.datetime]


class ApiModelMixIn(models.Model):
    """
    Миксин с полями (app, msa_id) для того, чтобы отделять сущности с одинаковыми msa_id из разных сервисов,
    использующих данный msa.
    При запросах по api-ключу, выборки всех объектов, использующих данный миксин,
    должны фильтроваться по ApiKey.app
    """
    app = models.CharField(max_length=30)
    msa_id = models.IntegerField()

    class Meta:
        abstract = True
        unique_together = ('app', 'msa_id')


class Organization(ApiModelMixIn):
    """
    Организация обозначает сущность, в рамках которой происходит взаимодействие менеджеров и ресурсов.
    Имеет отношение к Manager (m2m) и ResourceMembership (fk)
    """
    def get_resource_ids(self) -> QuerySet:
        """Возвращает список ids ресурсов, принадлежащих данной организации"""
        return self.resource_members.values_list('resource_id', flat=True)

    def __str__(self):
        return '%s: %d' % (self._meta.object_name, self.msa_id)


class Manager(ApiModelMixIn):
    """
    Сущность, обозначающая лицо, уполномоченное выделять интервалы времени для ресурсов.
    Модель относится к Organization (m2m) и Interval (fk).
    """
    organizations = models.ManyToManyField(Organization, related_name="managers")

    def organizations_for_resource(self, resource: 'Resource') -> QuerySet:
        """Возвращает QS с организациями, в которые вовлечен данный ресурс"""
        return self.organizations.filter(resource_members__resource=resource)


class Resource(ApiModelMixIn):
    """
    Сущность, обозначающая работника организации.
    """
    def join_organization(self, organization: Organization, raise_if_joined=False):
        """
        Создает (при отсутствии) объект ResourceMembership, связывающий данный ресурс и организацию.
        Если стоит флаг raise_if_joined, вызывает ValueError при наличии объекта.
        """
        obj, created = ResourceMembership.objects.get_or_create(resource=self, organization=organization)
        if not created and raise_if_joined:
            raise ValueError(_('%s is already member of this organization.') % str(self))

    def dismiss_from_organization(self, organization: Organization):
        """
        Удаляет объект ResourceMembership, связывающий данный ресурс и организацию,
        и все интервалы ресурса, относящиеся к организации в будущем времени.
        При отсутствиии ResourceMembership вызовется исключение.
        """
        obj = ResourceMembership.objects.get(resource=self, organization=organization)
        obj.strip_organization_time()
        obj.delete()

    def clear_unvailable_interval(self, start: datetime.datetime, end: datetime.datetime) -> bool:
        """
        Для данного ресурса удаляет интервалы недоступности, попавшие по времени между start и end.
        Сервис, вызвавший данную функцию, получит в ответе информацию о событиях с меткой 'clear-unavailable-interval',
        в котором отмечены менеджер, организация, start, end.
        """
        i = Interval(resource=self, start=start, end=end, kind=Interval.Kind_Unavailable)
        changed = i.substract_from_existing()
        if changed:
            affected_managers = Interval.objects.filter(resource=self).between(i.start, i.end).managers()
            for m in affected_managers:
                orgs = m.organizations_for_resource(self)
                EventDispatcher.push_event_to_response(kind='clear-unavailable-interval',
                                                       resource=self.msa_id,
                                                       manager=m.msa_id,
                                                       organization=orgs[0].msa_id if orgs else None,
                                                       duration=[start, end],
                                                       timedelta=end - start)
        return changed

    def __str__(self):
        return '%s: %d' % (self._meta.object_name, self.msa_id)


class IntervalQuerySet(QuerySet):
    """Менеджер объектов для модели Interval."""

    def between(self, start: DateOrDatetime, end: DateOrDatetime, include_end_date=True) -> QuerySet:
        """Возвращает QS со всеми интервалами, попавшими, пересекающимися и включающими указаный промежуток времени.
        Если в end передано значение datetime.datetime, то при наличии флага include_end_date
        учитывается время до 00:00 следующего дня, иначе - 00:00 для end."""
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

    def at_date(self, dt: DateOrDatetime) -> QuerySet:
        """Возвращает QS с интервалами, пересекающимися (не пограничными) с указанной dt."""
        if not isinstance(dt, datetime.datetime):
            dt = utils.datetime_from_date(dt)
        return self.filter(Q(start__lt=dt, end__gt=dt))

    def similar(self, interval: 'Interval') -> QuerySet:
        """
        Возвращает QS с интервалами, совпадающими с переданным interval по основным полям:
        resource, kind, organization, manager.
        """
        q = Q(resource=interval.resource, kind=interval.kind,
              organization=interval.organization, manager=interval.manager)

        # если интервал для организации, разных менеджеров не учитываем
        # if interval.kind != Interval.Kind_OrganizationReserved:
        #     q &= Q()

        qs = self.filter(q)

        if interval.id:
            qs = qs.exclude(id=interval.id)
        return qs

    def is_continuous(self, start: datetime.datetime, end: datetime.datetime) -> bool:
        """
        Определяет, представляют ли интервалы в данном QS непрерывный отрезок во времени между start и end.
        Возвращает результат проверки (bool).
        """
        existing = []
        for interval in self:
            interval.join_with_existing(existing, timedelta=0)
            existing.append(interval)
        return len(existing) == 1 and existing[0].start <= start and existing[0].end >= end

    def managers(self) -> QuerySet:
        """
        Возвращает QS с объектами Manager, которые фигурируют в интервалах данного QS.
        Выбираются только интервалы с типами ManagerReserved и OrganizationReserved.
        """
        q = Q(manager__isnull=False) & (Q(kind=Interval.Kind_ManagerReserved) |
                                        Q(kind=Interval.Kind_OrganizationReserved))
        manager_ids = self.filter(q).order_by('manager').distinct('manager').values_list('manager', flat=True)
        return Manager.objects.filter(id__in=manager_ids)


class Interval(models.Model):
    """
    Временной промежуток, характеризующийся обязательными параметрами (start, end, kind, resource)
    и необязательными (organization, manager, comment)
    """
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
    organization = models.ForeignKey(Organization, related_name='reserved_intervals', null=True,
                                     on_delete=models.CASCADE)
    manager = models.ForeignKey(Manager, related_name='reserved_intervals', null=True, on_delete=models.CASCADE)
    comment = models.TextField(null=True, blank=True)

    objects = IntervalQuerySet.as_manager()

    class Meta:
        ordering = ('kind', 'manager')

    def __str__(self):
        return '%s interval [%s - %s]' % (self.get_kind_display(), self.start, self.end)

    @classmethod
    def kind_from_str(cls, kind_str: str) -> int:
        """Возвращает значение флага KIND_CHOICES по имени метки (при отсутствии совпадений вернет 0)."""
        for choice in cls.KIND_CHOICES:
            if choice[1] == kind_str:
                return choice[0]
        return 0

    def get_object(self, msa_id_only=False) -> [int, Organization, Manager, None]:
        """
        Если интервал имеет тип OrganizationReserved,
        вернёт экземпляр организации или её msa_id (если стоит флаг msa_id_only) или None при отсутствии.
        Если интервал имеет тип ManagerReserved,
        вернёт экземпляр менеджера или его msa_id (если стоит флаг msa_id_only) или None при отсутствии.
        """
        if self.kind == self.Kind_OrganizationReserved:
            return self.organization.msa_id if msa_id_only and self.organization else self.organization
        if self.kind == self.Kind_ManagerReserved:
            return self.manager.msa_id if msa_id_only and self.manager else self.manager
        return None

    def as_schedule_intervals(self) -> 'ScheduleIntervalList':
        """Разбивает интервал на отрезки по дням недели. Возвращает список из ScheduleInterval."""
        ret = []
        # перебираем дни с первого по последний, начиная с 0
        for i in range((self.end.date() - self.start.date()).days + 1):
            dt = (self.start + datetime.timedelta(days=i)).date()
            start_time = self.start.timetz() if i == 0 else datetime.time(tzinfo=self.start.tzinfo)
            if dt == self.end.date():
                end_time = self.end.timetz()
            else:
                end_time = datetime.time(23, 59, 59, tzinfo=self.start.tzinfo)
            week_day = (dt.weekday() + 1) % 7                  # в нашем случае первый день недели - ВС, а не ПН
            ret.append(ScheduleInterval(day_of_week=week_day, start=start_time, end=end_time))
        return ret

    # def trim(self):
    #     """обрезает границы интервала на основе имеющихся интервалов с тем же kind"""
    #     if self.kind == Interval.Kind_OrganizationReserved:
    #         others = Interval.objects.filter(resource=self.resource, organization=self.organization, kind=self.kind)\
    #                                  .between(self.start, self.end)
    #         for other in others:
    #             if self.start >= other.start and self.end <= other.end:  # новый внутри
    #                 raise exceptions.FormError('', _('Interval is already reserved for organization.'))
    #             if self.start <= other.start and self.end >= other.end:  # новый снаружи
    #                 self.end = other.start
    #             if other.start <= self.start < other.end:  # пересекаются
    #                 self.start = other.end
    #             if other.start < self.end <= other.end:  # пересекаются
    #                 self.end = other.start

    def join_with_existing(self, existing: Union[QuerySet, 'IntervalList']=None,
                           timedelta: datetime.timedelta='default') -> bool:
        """
        Cклеивает данный интервал с другими интервалами, переданными в existing или взятыми из БД.
        Под склейкой понимается действие, преобращующее множество перекрывающихся, накладывающихся
        и соприкасающихся интервалов в множество непересекающихся и несоприкасающихся.

        Если в existing передан QuerySet или IntervalList, склейка осуществляется с ними,
        иначе с Interval.objects.similar(). При наличии пересечений с имеющимися интервалами,
        они удаляются из списка (при наличии qs - из базы), а данному интервалу назначаются новые start и end.

        :param timedelta: временной промежуток для границ склеивания
        :param existing: если не указан, интервалы берутся из базы
        :return: bool (сделаны изменения или нет).
        """
        if timedelta == 'default':
            timedelta = Interval.JOIN_GAP
        elif not timedelta:
            timedelta = datetime.timedelta()
        if existing is not None:
            qs = existing
        else:
            qs = Interval.objects.similar(self).between(self.start - timedelta, self.end + timedelta)
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
                if interval.start >= self.start and interval.end <= self.end:   # имеющийся - внутри
                    existing.remove(interval)
                    changed = True
                elif interval.start < self.start and interval.end > self.end:  # имеющийся - снаружи
                    existing.remove(interval)
                    self.start = interval.start
                    self.end = interval.end
                    changed = True
                elif interval.start < self.start < interval.end or\
                        (self.start > interval.end and self.start - interval.end < timedelta):
                    # пересекаются или соприкасаются
                    self.start = interval.start
                    existing.remove(interval)
                    changed = True
                elif interval.start < self.end < interval.end or\
                        (interval.start > self.end and interval.start - self.end < timedelta):
                    # пересекаются или соприкасаются
                    self.end = interval.end
                    existing.remove(interval)
                    changed = True

        return changed

    def substract_from_existing(self, existing: Union[QuerySet, 'IntervalList']=None) -> bool:
        """
        Исключает интервал из имеющихся интервалов, удаляя их или обрезая.
        Функция противоположна по смыслу join_with_existing и обладает теми же особенностями.

        :param existing: список рассматриваемых интервалов, любо None (в этом случае берется qs похожих из базы)
        :return: значение bool, показывающее, были ли сделаны изменения в список интервалов (или в бд) или нет.
        """
        qs = existing if existing is not None else Interval.objects.similar(self).between(self.start, self.end)
        do_save = isinstance(qs, models.QuerySet)
        do_append = isinstance(existing, list)
        changed = False

        for interval in qs:
            if interval.start < self.start and interval.end > self.end:        # снаружи
                changed = True
                end_old = interval.end
                interval.end = self.start
                if do_save:
                    interval.save(join_existing=False, trim=False, events=False)
                i2 = Interval(start=self.end,
                              end=end_old,
                              kind=interval.kind,
                              resource=interval.resource,
                              manager=interval.manager,
                              organization=interval.organization,
                              comment=interval.comment)
                if do_save:
                    i2.save(join_existing=False, trim=False, events=False)
                if do_append:
                    existing.append(i2)

            elif interval.start < self.start < interval.end:                   # пересекаются
                changed = True
                interval.end = self.start
                if do_save:
                    interval.save(join_existing=False, trim=False, events=False)

            elif interval.start < self.end < interval.end:                     # пересекаются
                changed = True
                interval.start = self.end
                if do_save:
                    interval.save(join_existing=False, trim=False, events=False)

            elif interval.start >= self.start and interval.end <= self.end:    # внутри
                changed = True
                if do_save:
                    interval.delete(events=False)
                if do_append:
                    existing.remove(interval)
        return changed

    # noinspection PyUnresolvedReferences
    def save(self, join_existing=True, trim=True, events=True, *args, **kwargs):
        """
        Переопределяет функцию Model.save с доп. аргументами.
        Перед сохранением производит необходимые проверки на валидность данного интервала
        (при ошибке вызывается ValidationError).

        :param join_existing: склеивать с имеющимися интервалами в бд или нет
        :param trim: обрезать имеющиеся интервалы в бд или нет
        :param events: генерировать ли пользовательские события или нет
        """
        if self.start >= self.end:
            raise exceptions.FormError('end', _('End date must be greater than start date.'))

        # не указывать организацию можно только для интервалов недоступности
        if not self.organization_id and self.kind != self.Kind_Unavailable:
            raise exceptions.FormError('organization', _('You must specify organization for this interval.'))

        # указанный менеджер должен состоять в указанной организации
        if self.organization_id and self.manager_id and self.organization not in self.manager.organizations.all():
            raise exceptions.FormError('', _('Only managers can reserve time for organization.'))

        # указанный ресурс должен состоять в указанной организации
        if self.organization_id and self.resource_id \
                and not self.organization.resource_members.filter(resource=self.resource).count():
            raise exceptions.FormError('', _('Resource is not in specified organization.'))

        qs = Interval.objects.between(self.start, self.end).filter(resource=self.resource)
        if self.pk:
            qs = qs.exclude(id=self.id)

        if self.kind == Interval.Kind_ManagerReserved:
            if not self.manager_id:
                raise exceptions.FormError('manager', _('You must specify manager for this interval.'))

            if not qs.filter(kind=Interval.Kind_OrganizationReserved, organization=self.organization)\
                     .is_continuous(self.start, self.end):
                raise exceptions.FormError('', _('This period is\'t fall within organization time.'))

            if qs.filter(kind=Interval.Kind_ManagerReserved).exclude(manager=self.manager):
                raise exceptions.FormError('', _('This period is reserved for another manager.'))

            if qs.filter(kind=Interval.Kind_ManagerReserved, organization=self.organization, manager=self.manager)\
                 .is_continuous(self.start, self.end):
                raise exceptions.FormError('', _('This period is already reserved.'))

        elif self.kind == Interval.Kind_OrganizationReserved:
            if qs.filter(kind=Interval.Kind_OrganizationReserved, organization=self.organization)\
                 .is_continuous(self.start, self.end):
                raise exceptions.FormError('', _('This period is already reserved for organization.'))

            if qs.filter(kind=Interval.Kind_OrganizationReserved).exclude(organization=self.organization):
                raise exceptions.FormError('', _('This period falls within another organization.'))

            for membership in self.resource.organization_memberships.exclude(organization=self.organization):
                if membership.schedule_intervals.has_intersection(self):
                    raise exceptions.FormError('', _('This period falls within another organization\'s schedule.'))

        joined = False
        if join_existing:
            joined = self.join_with_existing()
        #
        # if trim:
        #     self.trim()

        # created = self.pk is None and not joined
        super().save(*args, **kwargs)

        if events:
            EventDispatcher.push_event_to_response(kind='create-interval', **self.get_event_context())

            if self.kind == Interval.Kind_Unavailable:
                for m in qs.managers():
                    orgs = m.organizations_for_resource(self.resource)
                    EventDispatcher.push_event_to_response(kind='add-unavailable-interval',
                                                           comment=self.comment,
                                                           resource=self.resource.msa_id,
                                                           manager=m.msa_id,
                                                           organization=orgs[0].msa_id if orgs else None,
                                                           duration=[self.start, self.end],
                                                           timedelta=self.end - self.start)

    def delete(self, events=True, **kwargs):
        """
        Переопределяет функцию Model.delete с доп. аргументом.

        :param events: генерировать ли пользовательские события или нет
        """
        if events:
            EventDispatcher.push_event_to_response(kind='delete-interval', **self.get_event_context())

            if self.kind == Interval.Kind_Unavailable:
                affected_managers = Interval.objects.filter(resource=self.resource)\
                    .between(self.start, self.end).managers()
                for m in affected_managers:
                    orgs = m.organizations_for_resource(self.resource)
                    EventDispatcher.push_event_to_response(kind='clear-unavailable-interval',
                                                           resource=self.resource.msa_id,
                                                           manager=m.msa_id,
                                                           organization=orgs[0].msa_id if orgs else None,
                                                           duration=[self.start, self.end],
                                                           timedelta=self.end - self.start)
        return super().delete(**kwargs)

    def get_event_context(self) -> dict:
        """Возвращает словарь аттрибутов данного интервала, полезный при создании пользовательских событий."""
        d = dict(
            interval_kind=self.get_kind_display(),  # if created else 'change-interval',
            organization=self.organization.msa_id if self.organization else None,
            resource=self.resource.msa_id if self.resource else None,
            manager=self.manager.msa_id if self.manager else None,
            comment=self.comment,
            start=self.start,
            end=self.end,
            duration=[self.start, self.end],
            timedelta=self.end-self.start
        )
        return d


class ResourceMembership(models.Model):
    """
    Модель, связывающая организацию и ресурс.
    Имеет отношение с ScheduleInterval (fk).
    """
    resource = models.ForeignKey(Resource, related_name='organization_memberships', on_delete=models.CASCADE)
    organization = models.ForeignKey(Organization, related_name='resource_members', on_delete=models.CASCADE)
    schedule_extended_date = models.DateTimeField(null=True)

    class Meta:
        unique_together = ('resource', 'organization')

    @property
    def has_schedule(self) -> bool:
        """Возвращает True при наличии объектов ScheduleInterval, связанных с данным."""
        return self.schedule_intervals.count() > 0

    def strip_organization_time(self):
        """
        Cокращает выделенное время ресурса для организации до окончания времени,
        выделенного каким-либо менеджером данной орг-ии
        """
        now = datetime.datetime.now(get_default_timezone())
        self.schedule_extended_date = now
        for i in Interval.objects.at_date(now).filter(resource=self.resource, organization=self.organization):
            i.end = now
            i.save()

    def extend_schedule(self, end: datetime.datetime):
        """
        Создаёт интервалы, отмечающие время работы данного ресурса в данной организации, в промежутке между
        self.schedule_extended_date и end.
        Если значение self.schedule_extended_date меньше end, ничего не делает.
        """
        if self.schedule_extended_date and self.schedule_extended_date >= end:
            return
        if self.apply_schedule(self.schedule_extended_date, end):
            self.schedule_extended_date = end
            self.save()

    def apply_schedule(self, start: datetime.datetime, end: datetime.datetime,
                       schedule_intervals: 'ScheduleIntervalList'=None, save_as_default=False) -> bool:
        """
        Cоздает новые интервалы доступности ресурса для организации взамен старых.

        :param schedule_intervals: список интервалов графика (если None, берет имеющиеся)
        :param save_as_default: сохранять переданные schedule_intervals в качестве постоянных или нет
        :param start: дата и время начала
        :param end: дата и время окончания
        :return: были созданы новые интервалы или нет
        """
        if not (schedule_intervals or self.schedule_intervals.count()):
            return False
        if not start or not end or start >= end:
            return False

        used_scedule_intervals = schedule_intervals if schedule_intervals is not None else self.schedule_intervals.all()

        # очищаем имеющиеся интервалы работы для выбранного отрезка времени
        work_interval = Interval(start=start, end=end)
        qs = Interval.objects.between(start, end)\
                             .filter(resource=self.resource,
                                     organization=self.organization,
                                     kind=Interval.Kind_OrganizationReserved)
        work_interval.substract_from_existing(qs)

        intervals = []      # создаваемые интервалы
        schedule_intervals_map = {}     # раскладываем интервалы графика в словарь {день_недели: интервал графика}

        for si in used_scedule_intervals:
            if si.start.tzinfo is None:                  # добавляем временную зону
                si.start = si.start.replace(tzinfo=UTC())
            if si.end.tzinfo is None:
                si.end = si.end.replace(tzinfo=UTC())

            schedule_intervals_map.setdefault(si.day_of_week, [])
            schedule_intervals_map[si.day_of_week].append(si)

        for i in range((end.date() - start.date()).days + 1):      # перебираем дни с первого по последний, начиная с 0
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
                interval.join_with_existing(intervals)
                intervals.append(interval)

        for i in intervals:                        # убираем короткие интервалы
            if i.end - i.start < Interval.JOIN_GAP:
                intervals.remove(i)

        intervals.sort(key=lambda d: d.start)      # сортируем получившиеся интервалы по возрастанию
        if len(intervals):                         # склеиваем первый (и последний) с имеющимися
            intervals[0].join_with_existing()
            if len(intervals) > 1:
                intervals[-1].join_with_existing()
        Interval.objects.bulk_create(intervals)    # записываем получившиеся интервалы

        # сохраняем расписание
        if save_as_default and schedule_intervals is not None:
            for si in schedule_intervals:
                si.membership = self
            self.schedule_intervals.all().delete()
            ScheduleInterval.objects.bulk_create(schedule_intervals)
        return True


class ScheduleIntervalManager(models.QuerySet):
    """Используется в качестве ScheduleInterval.objects"""

    def has_intersection(self, interval: Interval) -> bool:
        """Пересекает ли указанный интервал данное расписание (по дням недели и времени)."""
        splitted_schedule_intervals = interval.as_schedule_intervals()
        days_of_week = set([i.day_of_week for i in splitted_schedule_intervals])

        for i in self.filter(day_of_week__in=days_of_week):
            for j in splitted_schedule_intervals:
                if i.has_intersection(j):
                    return True
        return False


class ScheduleInterval(models.Model):
    """
    Фрагмент расписания ресурса в организации. Характеризуется днём недели, временем начала и завершения.
    """
    membership = models.ForeignKey(ResourceMembership, related_name='schedule_intervals', on_delete=models.CASCADE)
    day_of_week = models.PositiveSmallIntegerField()
    start = models.TimeField()
    end = models.TimeField()

    objects = ScheduleIntervalManager.as_manager()

    def __init__(self, *args, **kwargs):
        """Указывает UTC в качестве временной зоны для 'наивных' self.start и self.end"""
        super().__init__(*args, **kwargs)
        if self.start.tzinfo is None:
            self.start = self.start.replace(tzinfo=UTC())
        if self.end.tzinfo is None:
            self.end = self.end.replace(tzinfo=UTC())

    def has_intersection(self, other: 'ScheduleInterval'):
        """Пересекаются ли данный и указанный фрагменты расписания между собой"""
        return self.day_of_week == other.day_of_week and (
            other.start < self.start < other.end or self.start < other.start < self.end
        )

    class Meta:
        ordering = ('membership', 'day_of_week')


class ApiKey(models.Model):
    """
    Ключ для доступа к данному msa. Характеризуется названием сервиса (app),
    по которому должны фильтроваться выборки всех объектов, наследованных от ApiModelMixIn.
    """
    key = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    app = models.CharField(max_length=30)


ScheduleIntervalList = List[ScheduleInterval]
IntervalList = List[Interval]
