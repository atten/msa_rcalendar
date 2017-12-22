#!/usr/bin/env python
import os
import sys

from django_docker_helpers.db import ensure_databases_alive, migrate
from django_docker_helpers.files import collect_static
from django_docker_helpers.management import run_gunicorn


def do_prepare():
    collect_static()
    migrate()


if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "msa_rcalendar.settings")

    if len(sys.argv) == 2:
        if sys.argv[1] == 'runuwsgi':
            from django.conf import settings

            os.environ.setdefault('PORT', str(settings.COMMON_BASE_PORT))
            # WARNING: production unsafe security option! DO NOT USE IT ON PRODUCTION
            # we treat an empty string and non-falsy values as the enabled static-safe option
            static_safe = getattr(settings, 'UWSGI_STATIC_SAFE', None)
            if static_safe or static_safe == '':
                os.environ.setdefault('UWSGI_STATIC_SAFE', str(settings.UWSGI_STATIC_SAFE))

        if sys.argv[1] == 'gunicorn':
            from msa_rcalendar.wsgi import application
            ensure_databases_alive(100)
            do_prepare()
            gunicorn_module_name = os.environ.get('GUNICORN_MODULE_NAME', 'gunicorn_dev')
            run_gunicorn(application, gunicorn_module_name=gunicorn_module_name)
            exit()

    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
