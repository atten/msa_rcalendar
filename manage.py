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

    if len(sys.argv) == 2 and sys.argv[1] == 'gunicorn':
        from msa_rcalendar.wsgi import application
        ensure_databases_alive(100)
        do_prepare()
        gunicorn_module_name = os.environ.get('GUNICORN_MODULE_NAME', 'gunicorn_dev')
        run_gunicorn(application, gunicorn_module_name=gunicorn_module_name)
        exit()

    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
