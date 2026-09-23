from django.apps import AppConfig


class DSRConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "dsr"

    def ready(self) -> None:
        from . import signals  # noqa: F401