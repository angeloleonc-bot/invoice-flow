from django.conf import settings


class SessionPolicy:
    @staticmethod
    def inactivity_timeout_minutes() -> int:
        return getattr(settings, "INACTIVITY_TIMEOUT_MINUTES", 30)

    @staticmethod
    def absolute_session_timeout_hours() -> int:
        return getattr(settings, "ABSOLUTE_SESSION_TIMEOUT_HOURS", 10)