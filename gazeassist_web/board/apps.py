from django.apps import AppConfig


class BoardConfig(AppConfig):
    """App config for the caregiver board."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "board"
    verbose_name = "GazeAssist board"

    def ready(self):
        # Register signal handlers (file clean-up on report delete).
        from . import signals  # noqa: F401
