"""
Django settings for the GazeAssist caregiver dashboard.

All secrets and environment-specific values are read from the .env file
via python-decouple. See .env.example for the full list.
"""
from pathlib import Path

from decouple import Csv, config
from django.contrib.messages import constants as message_constants

BASE_DIR = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------
SECRET_KEY = config("SECRET_KEY")
DEBUG = config("DEBUG", default=False, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="127.0.0.1,localhost", cast=Csv())
CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "rest_framework",
    "rest_framework.authtoken",
    "board.apps.BoardConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "gazeassist_web.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "board.context_processors.caregiver_nav",
            ],
        },
    },
]

WSGI_APPLICATION = "gazeassist_web.wsgi.application"

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "board:dashboard"
LOGOUT_REDIRECT_URL = "login"

# --------------------------------------------------------------------------
# Internationalisation
# --------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = config("TIME_ZONE", default="Asia/Kolkata")
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------
# Static files and private uploads
# --------------------------------------------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Uploaded reports live outside static/ and are never served directly.
# They are streamed by a permission-checked view (Phase 2).
MEDIA_ROOT = BASE_DIR / "private_media"
MEDIA_URL = "/protected-media/"

REPORT_MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB

# --------------------------------------------------------------------------
# GazeAssist behaviour
# --------------------------------------------------------------------------
# A patient counts as "online" if the gaze app sent a heartbeat this recently.
GAZEASSIST_ONLINE_WINDOW_SECONDS = config("ONLINE_WINDOW_SECONDS", default=15, cast=int)

# A "Connect gaze app" code works for this many minutes.
PAIRING_CODE_TTL_MINUTES = config("PAIRING_CODE_TTL_MINUTES", default=10, cast=int)

# Dashboard pages poll the server this often (milliseconds) for live updates.
GAZEASSIST_POLL_INTERVAL_MS = config("POLL_INTERVAL_MS", default=3000, cast=int)

# --------------------------------------------------------------------------
# REST API for the gaze app (token authentication only)
# --------------------------------------------------------------------------
REST_FRAMEWORK = {
    # Only tokens: the gaze app never uses browser sessions, so no CSRF issues
    # and a caregiver's browser session cannot call the gaze API.
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.TokenAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_THROTTLE_RATES": {
        # Heartbeat every 5 s = 12/min; status and message polling add a little more.
        "gaze": config("GAZE_API_RATE", default="240/min"),
        # Entering a pairing code: per IP address, to make guessing codes useless.
        "pair": config("PAIR_API_RATE", default="10/min"),
    },
}

# --------------------------------------------------------------------------
# Messages -> Bootstrap colour names
# --------------------------------------------------------------------------
MESSAGE_TAGS = {message_constants.ERROR: "danger"}

# --------------------------------------------------------------------------
# Security
# --------------------------------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

# Turn these on when the site is served over HTTPS.
SECURE_COOKIES = config("SECURE_COOKIES", default=False, cast=bool)
SESSION_COOKIE_SECURE = SECURE_COOKIES
CSRF_COOKIE_SECURE = SECURE_COOKIES
SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=False, cast=bool)
SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=0, cast=int)
