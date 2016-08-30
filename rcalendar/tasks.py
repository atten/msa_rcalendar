from celery import shared_task

from rcalendar.models import Resource


@shared_task
def update_intervals():
    for r in Resource.objects.filter(fulltime_organization__isnull=False):
        r.update_organization_reserve()


@shared_task
def apply_schedules():
    for r in Resource.objects.all():
        r.apply_schedule(current_week=False, next_weeks=True)
