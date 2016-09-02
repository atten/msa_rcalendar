from django.core.management.base import BaseCommand
from rcalendar.models import ApiKey


class Command(BaseCommand):
    help = 'Displays list of saved Api Keys'

    def handle(self, *args, **options):
        for k in ApiKey.objects.all():
            print(k.key)