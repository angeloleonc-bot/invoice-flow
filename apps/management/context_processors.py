from apps.accounts.services.role_service import RoleService
from apps.management.models import OperationalAlert
from apps.management.services.alerts import OperationalAlertService


def operational_alerts_context(request):
    if not request.user.is_authenticated:
        return {
            "navbar_new_alerts_count": 0,
            "navbar_recent_alerts": [],
            "can_manage_assignments": False,
            "can_view_dashboard": False,
        }

    active_alerts = (
        OperationalAlertService
        .get_visible_active_alerts(request.user)
    )

    new_alerts_count = active_alerts.filter(
        status=OperationalAlert.AlertStatus.NEW
    ).count()

    recent_alerts = list(
        active_alerts.only(
            "id",
            "title",
            "severity",
            "created_at",
            "status",
            "due_at",
            "assigned_to",
            "customer",
            "document",
            "promise",
        ).order_by("-created_at")[:5]
    )

    return {
        "navbar_new_alerts_count": new_alerts_count,
        "navbar_recent_alerts": recent_alerts,
        "can_manage_assignments": (
            RoleService.can_manage_assignments(
                request.user
            )
        ),
        "can_view_dashboard": (
            RoleService.can_view_dashboard(
                request.user
            )
        ),
    }