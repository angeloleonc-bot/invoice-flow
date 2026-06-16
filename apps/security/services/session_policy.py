from django.conf import settings


class SessionPolicy:
    """
    Define la política base de sesión.

    No aplica validaciones reales todavía.
    """

    inactivity_timeout_minutes = getattr(settings, "INACTIVITY_TIMEOUT_MINUTES", 30)
    absolute_timeout_hours = getattr(settings, "ABSOLUTE_SESSION_TIMEOUT_HOURS", 10)