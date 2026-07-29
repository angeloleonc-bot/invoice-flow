from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from apps.accounts.models import Role
from apps.accounts.services.role_service import RoleService
from apps.core.services.dashboard import DashboardService


@login_required
def dashboard(request):
    effective_role = RoleService.get_effective_role_code(
        request.user
    )

    if effective_role == Role.COBRADOR:
        return redirect("management:my_work")

    service = DashboardService()
    context = service.get_context(request.user)

    return render(
        request,
        "core/dashboard.html",
        context,
    )