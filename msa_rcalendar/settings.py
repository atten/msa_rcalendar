import socket
import os

from django_docker_helpers.utils import load_yaml_config

from . import __version__

HOSTNAME = socket.gethostname()

# PATHS
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_PATH = BASE_DIR
PROJECT_PATH = ROOT_PATH
PROJECT_NAME = os.path.basename(ROOT_PATH)
PROJECT_DATA_DIR = os.path.join(BASE_DIR, PROJECT_NAME, 'data')
__TEMPLATE_DIR = os.path.join(BASE_DIR, PROJECT_NAME, 'templates')

VIRTUAL_ENV_DIR = os.path.abspath(os.path.join(BASE_DIR, os.path.pardir))
LOGGING_DIR = os.path.join(VIRTUAL_ENV_DIR, 'log')

LOCAL_SETTINGS_FILE = os.path.join(BASE_DIR, PROJECT_NAME, 'local_settings.py')
SECRET_SETTINGS_FILE = os.path.join(BASE_DIR, PROJECT_NAME, 'secret_settings.py')


# =================== LOAD YAML CONFIG =================== #
CONFIG, configure = load_yaml_config(
    '',
    os.path.join(
        BASE_DIR, 'msa_rcalendar', 'config',
        os.environ.get('DJANGO_CONFIG_FILE_NAME', 'without-docker.yml')
    )
)
# ======================================================== #

DEBUG = configure('debug', False)

COMMON_BASE_HOST = configure('common.base.host', 'rcalendar.marfa.dev')  # 'web.marfa.dev' | 'marfa.team' | etc
COMMON_BASE_PORT = configure('common.base.port', 10546)  # 10546 etc
COMMON_BASE_SCHEME = configure('common.base.scheme', 'http')  # Either 'http' or 'https'


# ------
for path in [LOGGING_DIR, PROJECT_DATA_DIR, __TEMPLATE_DIR]:
    if not os.path.exists(path):
        os.makedirs(path, mode=0o755, exist_ok=True)

if not os.path.exists(SECRET_SETTINGS_FILE):
    with open(SECRET_SETTINGS_FILE, 'w') as f:
        from django.utils.crypto import get_random_string
        chars = 'abcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*(-_=+)'
        f.write("SECRET_KEY = '%s'\n" % get_random_string(50, chars))
        f.close()

if not os.path.exists(LOCAL_SETTINGS_FILE):
    with open(LOCAL_SETTINGS_FILE, 'w') as f:
        f.write('# -*- coding: utf-8 -*-\n')
        f.close()

# imports secret key
from .secret_settings import *  # noqa

# HOSTS
RELEASE_HOSTS = [
    'primary',
    'hatebase',
    'burble',
]
ALLOWED_HOSTS = [HOSTNAME] + configure('hosts', [])


INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.auth',
    # 'django.contrib.sessions',
    # 'django.contrib.messages',
    # 'django.contrib.staticfiles',
    # 'django.contrib.admin',
    # 'django.contrib.sites',
    # 'django_congen',
    'django_uwsgi',
    'rest_framework',
    'rcalendar',
]

# RAVEN
if configure('raven', False) and configure('raven.dsn', ''):
    import raven  # noqa

    INSTALLED_APPS += ['raven.contrib.django.raven_compat']
    RAVEN_CONFIG = {
        'dsn': configure('raven.dsn', None),
        'release': __version__,
    }


MIDDLEWARE_CLASSES = [
    # 'django.middleware.security.SecurityMiddleware',
    # 'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'rcalendar.middleware.EventDispatchMiddleware',
    # 'django.middleware.common.CommonMiddleware',
    # 'django.middleware.csrf.CsrfViewMiddleware',
    # 'django.contrib.auth.middleware.AuthenticationMiddleware',
    # 'django.contrib.auth.middleware.SessionAuthenticationMiddleware',
    # 'django.contrib.messages.middleware.MessageMiddleware',
    # 'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'msa_rcalendar.urls'

WSGI_APPLICATION = 'msa_rcalendar.wsgi.application'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [__TEMPLATE_DIR],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.i18n',
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.template.context_processors.media',
                'django.template.context_processors.csrf',
                'django.template.context_processors.tz',
                'django.template.context_processors.static',
            ],
        },
    },
]

DATABASES = {
    'default': {
        'ENGINE': configure('db.name', 'django.db.backends.postgresql'),
        'HOST': configure('db.host', 'localhost'),
        'PORT': configure('db.port', 5432),

        'NAME': configure('db.database', 'msa_rcalendar'),
        'USER': configure('db.user', 'msa_rcalendar'),
        'PASSWORD': configure('db.password', 'msa_rcalendar'),

        'CONN_MAX_AGE': int(configure('db.conn_max_age', 60)),
    }
}


TIME_ZONE = 'Europe/Moscow'
USE_I18N = True
USE_L10N = True
USE_TZ = True
LOCALE_PATHS = (
    os.path.abspath(os.path.join(ROOT_PATH, 'locale')),
)
# LANGUAGE_CODE = 'ru'
LANGUAGES = (
    ('en', 'en'),
    ('ru', 'ru'),
)


# BATTERIES
# =========
REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': ('rcalendar.permissions.HasValidApiKey',),
    'DEFAULT_AUTHENTICATION_CLASSES': [],
    'DEFAULT_RENDERER_CLASSES': ('rest_framework.renderers.JSONRenderer',),
    'PAGE_SIZE': 10,
    'DEFAULT_FILTER_BACKENDS': ('url_filter.integrations.drf.DjangoFilterBackend',)
}

UWSGI_STATIC_SAFE = configure('uwsgi.static_safe', False)

# REDEFINE
from .local_settings import *  # noqa
