from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Удаляет все организации, ресурсы и менеджеров."""
    help = 'Clears organization, resources and managers'

    def handle(self, *args, **options):
        from rcalendar import models
        models.Resource.objects.all().delete()
        models.Manager.objects.all().delete()
        models.Organization.objects.all().delete()
