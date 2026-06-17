from django.db.models import Count, Sum
from django.shortcuts import render

from .models import Customer, Document


def documents_list(request):
    documents = (
        Document.objects.select_related(
            "customer",
            "status",
            "sub_status",
        )
        .prefetch_related("tags")
        .order_by("due_date", "customer__name", "document_number")
    )

    return render(
        request,
        "portfolio/documents_list.html",
        {
            "documents": documents,
        },
    )


def customers_list(request):
    customers = (
        Customer.objects.annotate(
            documents_count=Count("documents"),
            total_balance=Sum("documents__balance_amount"),
        )
        .order_by("name")
    )

    return render(
        request,
        "portfolio/customers_list.html",
        {
            "customers": customers,
        },
    )
