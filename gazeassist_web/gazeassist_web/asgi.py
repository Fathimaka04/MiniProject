"""ASGI config for gazeassist_web."""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gazeassist_web.settings")

application = get_asgi_application()
