from .settings import *  # noqa: F403, F401


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "test_invoice_flow.sqlite3",  # noqa: F405
    }
}

# Acelera la creación de usuarios durante las pruebas.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Evita que las pruebas dependan de servicios externos.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Configuración segura y controlada para pruebas.
DEBUG = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False