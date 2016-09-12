from django.core.management.base import BaseCommand
from ...models import ApiKey


class Command(BaseCommand):
    help = 'Creates and displays new Api Key'

    def add_arguments(self, parser):
        parser.add_argument('-app', nargs='?', type=str, help='app name for data distinction')

    def handle(self, *args, **options):
        app = options.get('app')
        if not app:
            self.stderr.write('Usage: -app <app_name>')
            return

        k = ApiKey(app=app)
        k.save()
        print(k.key)
