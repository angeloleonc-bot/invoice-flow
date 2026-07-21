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

# Session policy
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = False
SESSION_COOKIE_SAMESITE = "Lax"

SESSION_COOKIE_AGE = 60 * 60 * 10  # 10 hours absolute timeout
SESSION_SAVE_EVERY_REQUEST = True

INACTIVITY_TIMEOUT_MINUTES = 30
ABSOLUTE_SESSION_TIMEOUT_HOURS = 60

WORKLIST_HIGH_BALANCE_THRESHOLD = 1000000

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