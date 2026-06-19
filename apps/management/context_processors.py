from apps.management.models import OperationalAlert
from apps.management.services.alerts import OperationalAlertService


def operational_alerts_context(request):
    active_alerts = OperationalAlertService.get_active_alerts()

    return {
        "navbar_new_alerts_count": active_alerts.filter(
            status=OperationalAlert.AlertStatus.NEW
        ).count(),
        "navbar_recent_alerts": active_alerts.order_by("-created_at")[:5],
    }