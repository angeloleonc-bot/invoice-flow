from pathlib import Path
import os
from dotenv import load_dotenv
from decouple import config

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "unsafe-dev-secret-key")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv("DJANGO_DEBUG", "True") == "True"

ALLOWED_HOSTS = os.getenv(
    "DJANGO_ALLOWED_HOSTS",
    "localhost,127.0.0.1"
).split(",")


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    "apps.core",
    "apps.accounts",
    "apps.security",
    "apps.audit",
    "apps.portfolio",
    "apps.management",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "apps.security.middleware.session_timeout.CorporateSessionMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                "apps.management.context_processors.operational_alerts_context",
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

DB_TRUSTED_CONNECTION = config("DB_TRUSTED_CONNECTION", default="no").lower() == "yes"

db_options = {
    "driver": config("DB_DRIVER", default="ODBC Driver 17 for SQL Server"),
}

extra_params = []

if config("DB_TRUST_SERVER_CERTIFICATE", default="yes").lower() == "yes":
    extra_params.append("TrustServerCertificate=yes")

if extra_params:
    db_options["extra_params"] = ";".join(extra_params) + ";"

if DB_TRUSTED_CONNECTION:
    db_options["trusted_connection"] = "yes"

DATABASES = {
    "default": {
        "ENGINE": config("DB_ENGINE", default="mssql"),
        "NAME": config("DB_NAME"),
        "HOST": config("DB_HOST"),
        "PORT": config("DB_PORT", default="1433"),
        "OPTIONS": db_options,
    }
}

if not DB_TRUSTED_CONNECTION:
    DATABASES["default"]["USER"] = config("DB_USER")
    DATABASES["default"]["PASSWORD"] = config("DB_PASSWORD")


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = "America/Santiago"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = 'static/'

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

STATIC_URL = "static/"
STATICFILES_DIRS = [
    BASE_DIR / "static",
]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Authentication / identity
AUTH_USER_MODEL = "accounts.User"


# ============================================================
# Microsoft Entra ID — Identidad y autorización
# ============================================================

ENTRA_AUTH_ENABLED = (
    os.getenv("ENTRA_AUTH_ENABLED", "False").strip().lower()
    in {"1", "true", "yes", "on"}
)

ENTRA_TENANT_ID = os.getenv(
    "ENTRA_TENANT_ID",
    "",
).strip()

ENTRA_CLIENT_ID = os.getenv(
    "ENTRA_CLIENT_ID",
    "",
).strip()

ENTRA_CLIENT_SECRET = os.getenv(
    "ENTRA_CLIENT_SECRET",
    "",
).strip()

ENTRA_REDIRECT_URI = os.getenv(
    "ENTRA_REDIRECT_URI",
    "http://localhost:8000/accounts/auth/callback/",
).strip()

ENTRA_AUTHORITY = (
    f"https://login.microsoftonline.com/{ENTRA_TENANT_ID}"
    if ENTRA_TENANT_ID
    else ""
)

ENTRA_ACCESS_GROUP_ID = os.getenv(
    "ENTRA_ACCESS_GROUP_ID",
    "",
).strip().lower()

ENTRA_SCOPES = tuple(
    scope
    for scope in os.getenv(
        "ENTRA_SCOPES",
        "User.Read GroupMember.Read.All",
    ).split()
    if scope
)

ENTRA_GROUP_ROLE_MAPPING = {
    group_id.strip().lower(): role_code
    for group_id, role_code in {
        os.getenv(
            "ENTRA_GROUP_ADMIN_ID",
            "",
        ): "ADMINISTRADOR",
        os.getenv(
            "ENTRA_GROUP_SUPERVISOR_ID",
            "",
        ): "SUPERVISOR",
        os.getenv(
            "ENTRA_GROUP_COLLECTOR_ID",
            "",
        ): "COBRADOR",
        os.getenv(
            "ENTRA_GROUP_AUDITOR_ID",
            "",
        ): "CONSULTA_AUDITORIA",
    }.items()
    if group_id.strip()
}

ENTRA_POST_LOGOUT_REDIRECT_URI = os.getenv(
    "ENTRA_POST_LOGOUT_REDIRECT_URI",
    "http://localhost:8000/accounts/login/",
).strip()

IDENTITY_REVALIDATION_MINUTES = int(
    os.getenv(
        "IDENTITY_REVALIDATION_MINUTES",
        "15",
    )
)

IDENTITY_REVALIDATION_GRACE_MINUTES = int(
    os.getenv(
        "IDENTITY_REVALIDATION_GRACE_MINUTES",
        "5",
    )
)

ENTRA_GLOBAL_LOGOUT_ENABLED = (
    os.getenv(
        "ENTRA_GLOBAL_LOGOUT_ENABLED",
        "False",
    ).strip().lower()
    in {"1", "true", "yes", "on"}
)

DEV_LOGIN_ENABLED = (
    os.getenv(
        "DEV_LOGIN_ENABLED",
        "False",
    ).strip().lower()
    in {"1", "true", "yes", "on"}
)

ENTRA_APPLICATION_SCOPES = (
    "https://graph.microsoft.com/.default",
)

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "accounts:login"

# ============================================================
# Session policy
# ============================================================

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = False
SESSION_COOKIE_SAMESITE = "Lax"

INACTIVITY_TIMEOUT_MINUTES = int(
    os.getenv(
        "INACTIVITY_TIMEOUT_MINUTES",
        "30",
    )
)

ABSOLUTE_SESSION_TIMEOUT_HOURS = int(
    os.getenv(
        "ABSOLUTE_SESSION_TIMEOUT_HOURS",
        "10",
    )
)

SESSION_COOKIE_AGE = ABSOLUTE_SESSION_TIMEOUT_HOURS * 60 * 60
SESSION_SAVE_EVERY_REQUEST = True

# ============================================================
# AWS S3 — Adjuntos operacionales privados
# ============================================================

AWS_ACCESS_KEY_ID = config("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = config("AWS_SECRET_ACCESS_KEY")
AWS_SESSION_TOKEN = config("AWS_SESSION_TOKEN", default=None)

AWS_STORAGE_BUCKET_NAME = config("AWS_STORAGE_BUCKET_NAME")
AWS_S3_REGION_NAME = config("AWS_S3_REGION_NAME", default="us-east-1")

AWS_S3_ATTACHMENT_PREFIX = config(
    "AWS_S3_ATTACHMENT_PREFIX",
    default="operational-attachments",
)

AWS_S3_PRESIGNED_URL_EXPIRATION = config(
    "AWS_S3_PRESIGNED_URL_EXPIRATION",
    default=300,
    cast=int,
)

OPERATIONAL_ATTACHMENT_MAX_SIZE = 30 * 1024 * 1024

AWS_SESSION_TOKEN = config(
    "AWS_SESSION_TOKEN",
    default=None,
)