from django.apps import AppConfig


class KilasiSmsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'kilasi_sms'
    verbose_name = 'Kilasi SMS Hub'

    # KILASI_V2_PHASE5B: register post-commit queue hooks, never send inside saves.
    def ready(self):
        from . import auto_signals  # noqa: F401
