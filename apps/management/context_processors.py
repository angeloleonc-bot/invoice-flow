from apps.management.models import OperationalAlert
from apps.management.services.alerts import OperationalAlertService


def operational_alerts_context(request):
    if not request.user.is_authenticated:
        return {
            "navbar_new_alerts_count": 0,
            "navbar_recent_alerts": [],
        }

    active_alerts = OperationalAlertService.get_visible_active_alerts(request.user)

    return {
        "navbar_new_alerts_count": active_alerts.filter(
            status=OperationalAlert.AlertStatus.NEW
        ).count(),
        "navbar_recent_alerts": active_alerts.order_by("-created_at")[:5],
    }