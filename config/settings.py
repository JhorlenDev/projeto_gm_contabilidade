"""Django settings for projeto_gm."""

from pathlib import Path

from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent


def _cast_debug(value):
    if isinstance(value, bool):
        return value

    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on", "dev", "development", "debug", "local"}:
        return True
    if normalized in {"0", "false", "no", "off", "prod", "production", "release"}:
        return False

    raise ValueError(f"Invalid DEBUG value: {value}")


SECRET_KEY = config("SECRET_KEY", default="dev-secret-key")
DEBUG = config("DEBUG", default=True, cast=_cast_debug)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="127.0.0.1,localhost", cast=Csv())

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "app",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("POSTGRES_DB", default="projeto_gm"),
        "USER": config("POSTGRES_USER", default="projeto_gm"),
        "PASSWORD": config("POSTGRES_PASSWORD", default="projeto_gm"),
        "HOST": config("POSTGRES_HOST", default="127.0.0.1"),
        "PORT": config("POSTGRES_PORT", default="8002"),
    }
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
PRIVATE_MEDIA_ROOT = BASE_DIR / "private_media"
FILE_ENCRYPTION_KEY = config("FILE_ENCRYPTION_KEY", default="")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CORS_ALLOWED_ORIGINS = config(
    "CORS_ALLOWED_ORIGINS",
    default="http://localhost:8000,http://127.0.0.1:8000",
    cast=Csv(),
)

KEYCLOAK_REALM_URL = config("KEYCLOAK_REALM_URL", default="").rstrip("/")
KEYCLOAK_CLIENT_ID = config("KEYCLOAK_CLIENT_ID", default="")
KEYCLOAK_JWKS_URL = config("KEYCLOAK_JWKS_URL", default="").rstrip("/")
KEYCLOAK_PUBLIC_KEY = config("KEYCLOAK_PUBLIC_KEY", default="")
KEYCLOAK_ISSUER = config("KEYCLOAK_ISSUER", default=KEYCLOAK_REALM_URL).rstrip("/")
KEYCLOAK_ALGORITHM = config("KEYCLOAK_ALGORITHM", default="RS256")
KEYCLOAK_VERIFY_AUDIENCE = config("KEYCLOAK_VERIFY_AUDIENCE", default=True, cast=bool)

if not KEYCLOAK_JWKS_URL and KEYCLOAK_REALM_URL:
    KEYCLOAK_JWKS_URL = f"{KEYCLOAK_REALM_URL}/protocol/openid-connect/certs"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "permissions.authentication.KeycloakJWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "loggers": {
        "app": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "permissions": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "services": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.server": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
