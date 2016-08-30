from django.core.management.base import BaseCommand
from rcalendar.models import ApiKey


class Command(BaseCommand):
    help = 'Creates and displays new Api Key'

    def handle(self, *args, **options):
        k = ApiKey()
        k.save()
        print(k.key)
