from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'billing'
    verbose_name = 'Billing'

    def ready(self):
        # Connect the wallet-creation signal and register deployment checks.
        from . import checks, signals  # noqa: F401
