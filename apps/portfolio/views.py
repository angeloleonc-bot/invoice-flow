from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.shortcuts import render

from .models import Customer, Document, DocumentAssignment


def documents_list(request):
    documents = (
        Document.objects.select_related("customer", "status", "sub_status")
        .prefetch_related("tags")
        .order_by("due_date", "customer__name", "document_number")
    )

    return render(request, "portfolio/documents_list.html", {"documents": documents})


def customers_list(request):
    customers = (
        Customer.objects.annotate(
            documents_count=Count("documents"),
            total_balance=Sum("documents__balance_amount"),
        )
        .order_by("name")
    )

    return render(request, "portfolio/customers_list.html", {"customers": customers})


def unassigned_documents(request):
    documents = (
        Document.objects.select_related("customer", "status", "sub_status")
        .filter(~Q(assignments__is_active=True))
        .distinct()
        .order_by("due_date", "customer__name", "document_number")
    )

    total_documents = documents.count()
    total_balance = documents.aggregate(total=Sum("balance_amount"))["total"] or 0

    return render(
        request,
        "portfolio/unassigned_documents.html",
        {
            "documents": documents,
            "total_documents": total_documents,
            "total_balance": total_balance,
        },
    )


def assignment_workloads(request):
    User = get_user_model()

    collectors = (
        User.objects.filter(portfolio_assignments_received__is_active=True)
        .annotate(
            active_documents=Count(
                "portfolio_assignments_received__document",
                filter=Q(portfolio_assignments_received__is_active=True),
                distinct=True,
            ),
            total_balance=Sum(
                "portfolio_assignments_received__document__balance_amount",
                filter=Q(portfolio_assignments_received__is_active=True),
            ),
        )
        .order_by("username")
    )

    unassigned_documents_count = (
        Document.objects.filter(~Q(assignments__is_active=True)).distinct().count()
    )

    unassigned_balance = (
        Document.objects.filter(~Q(assignments__is_active=True))
        .distinct()
        .aggregate(total=Sum("balance_amount"))["total"]
        or 0
    )

    return render(
        request,
        "portfolio/assignment_workloads.html",
        {
            "collectors": collectors,
            "unassigned_documents_count": unassigned_documents_count,
            "unassigned_balance": unassigned_balance,
        },
    )
