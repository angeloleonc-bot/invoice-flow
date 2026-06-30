from django import template


register = template.Library()


@register.filter
def risk_pill_class(score):
    try:
        score = int(score or 0)
    except (TypeError, ValueError):
        score = 0

    if score >= 100:
        return "if-pill-risk-critical"

    if score >= 80:
        return "if-pill-risk-high"

    if score >= 50:
        return "if-pill-risk-medium"

    return "if-pill-risk-low"


@register.filter
def status_pill_class(status_key):
    status_key = str(status_key or "").lower()

    if status_key in {"overdue", "expired", "danger", "critical", "vencida", "vencido"}:
        return "if-pill-status-danger"

    if status_key in {"today", "warning", "pending", "due_today", "hoy"}:
        return "if-pill-status-warning"

    if status_key in {"success", "active", "fulfilled", "paid", "cumplida", "pagada", "vigente"}:
        return "if-pill-status-success"

    return "if-pill-status-info"
