from datetime import date, datetime

from django import template
from django.utils import timezone

register = template.Library()


def _as_local_date(value):
    if not value:
        return None

    if isinstance(value, datetime):
        return timezone.localtime(value).date()

    if isinstance(value, date):
        return value

    return None


@register.filter
def human_due(value):
    local_date = _as_local_date(value)

    if not local_date:
        return "-"

    today = timezone.localdate()
    delta = (local_date - today).days

    if delta < 0:
        days = abs(delta)
        return f"Hace {days} día" if days == 1 else f"Hace {days} días"

    if delta == 0:
        return "Hoy"

    if delta == 1:
        return "Mañana"

    if delta <= 30:
        return f"En {delta} días"

    return local_date.strftime("%d/%m/%Y")


@register.filter
def human_datetime(value):
    if not value:
        return "-"

    local_value = timezone.localtime(value)
    now = timezone.localtime(timezone.now())

    delta = now - local_value
    seconds = int(delta.total_seconds())

    if seconds < 60:
        return "Hace un momento"

    minutes = seconds // 60

    if minutes < 60:
        return f"Hace {minutes} minuto" if minutes == 1 else f"Hace {minutes} minutos"

    hours = minutes // 60

    if hours < 24 and local_value.date() == now.date():
        return f"Hace {hours} hora" if hours == 1 else f"Hace {hours} horas"

    if (now.date() - local_value.date()).days == 1:
        return f"Ayer {local_value.strftime('%H:%M')}"

    days = (now.date() - local_value.date()).days

    if days <= 7:
        return f"Hace {days} día" if days == 1 else f"Hace {days} días"

    return local_value.strftime("%d/%m/%Y %H:%M")


@register.filter
def local_datetime(value):
    if not value:
        return "-"

    return timezone.localtime(value).strftime("%d/%m/%Y %H:%M")