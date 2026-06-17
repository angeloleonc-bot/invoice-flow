from datetime import timedelta

from django.db.models import Sum
from django.shortcuts import render
from django.utils import timezone

from apps.portfolio.models import DocumentAssignment


def my_work(request):
    today = timezone.localdate()
    upcoming_limit = today + timedelta(days=7)
    selected_filter = request.GET.get("filter", "all")

    assignments = (
        DocumentAssignment.objects.select_related(
            "document",
            "document__customer",
            "document__status",
            "document__sub_status",
            "assigned_to",
        )
        .filter(is_active=True, assigned_to=request.user)
        .order_by("document__due_date", "document__customer__name", "document__document_number")
    )

    total_documents = assignments.count()
    total_balance = assignments.aggregate(
        total=Sum("document__balance_amount")
    )["total"] or 0
    overdue_documents = assignments.filter(document__due_date__lt=today).count()
    upcoming_documents = assignments.filter(
        document__due_date__gte=today,
        document__due_date__lte=upcoming_limit,
    ).count()

    if selected_filter == "overdue":
        assignments = assignments.filter(document__due_date__lt=today)
    elif selected_filter == "high_balance":
        assignments = assignments.filter(document__balance_amount__gte=1000000)
    elif selected_filter == "without_operational_status":
        assignments = assignments.filter(document__sub_status__isnull=True)

    context = {
        "assignments": assignments,
        "selected_filter": selected_filter,
        "total_documents": total_documents,
        "total_balance": total_balance,
        "overdue_documents": overdue_documents,
        "upcoming_documents": upcoming_documents,
    }

    return render(request, "management/my_work.html", context)