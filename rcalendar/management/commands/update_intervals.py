from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Updates reserved intervals for fulltime resources. Call it once per day'

    def handle(self, *args, **options):
        from rcalendar import tasks
        tasks.update_intervals()
