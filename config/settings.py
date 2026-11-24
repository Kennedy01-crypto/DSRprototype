from pathlib import Path
import os


BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "development-only-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "dsr",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
]
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "dsr"),
        "USER": os.environ.get("POSTGRES_USER", "dsr"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "dsr"),
        "HOST": os.environ.get("POSTGRES_HOST", "db"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ("dsr.authentication.PatientPortalJWTAuthentication",),
    # Fail closed: every API view must declare ``allowed_roles``.
    "DEFAULT_PERMISSION_CLASSES": ("dsr.permissions.RolePermission",),
    # Workflow errors from dsr.services become 400/403/404/409 responses here.
    "EXCEPTION_HANDLER": "dsr.exceptions.api_exception_handler",
}
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_BEAT_SCHEDULE = {
    "monitor-dsr-slas-every-15-minutes": {
        "task": "dsr.tasks.monitor_sla_deadlines",
        "schedule": 900.0,
    },
    # Re-sends integration jobs that are due (retries, or lost messages).
    "dispatch-integration-jobs-every-minute": {
        "task": "dsr.tasks.dispatch_integration_jobs",
        "schedule": 60.0,
    },
}

# Source systems that can be searched automatically, by source-system code.
# Replace a mock with a real connector class here; see dsr/integrations/base.py.
DSR_SOURCE_CONNECTORS = {
    "PARAS": "dsr.integrations.mock.MockSourceConnector",
    "CIMS": "dsr.integrations.mock.MockSourceConnector",
    "ERPS": "dsr.integrations.mock.MockSourceConnector",
    "DYNAMICS": "dsr.integrations.mock.MockSourceConnector",
}
DSR_PATIENT_NOTIFIER = "dsr.integrations.mock.PortalOnlyNotifier"
