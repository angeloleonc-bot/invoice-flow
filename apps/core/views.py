from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.core.services.dashboard import DashboardService


@login_required
def dashboard(request):
    service = DashboardService()
    context = service.get_context()

    return render(request, "core/dashboard.html", context)