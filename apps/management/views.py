from datetime import timedelta

from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.portfolio.models import (
    Document,
    DocumentAssignment,
    DocumentStatus,
    DocumentSubStatus,
)
from .forms import CollectionActionForm, PaymentPromiseForm
from .models import CollectionAction, PaymentPromise, PromiseDocument


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


def document_detail(request, id):
    document = get_object_or_404(
        Document.objects.select_related(
            "customer",
            "status",
            "sub_status",
        ).prefetch_related("tags"),
        id=id,
    )

    action_form = CollectionActionForm()
    promise_form = PaymentPromiseForm()

    if request.method == "POST":
        form_type = request.POST.get("form_type")

        if form_type == "action":
            action_form = CollectionActionForm(request.POST)

            if action_form.is_valid():
                action = action_form.save(commit=False)
                action.document = document
                action.customer = document.customer

                if request.user.is_authenticated:
                    action.performed_by = request.user

                action.save()

                return redirect("management:document_detail", id=document.id)

        elif form_type == "promise":
            promise_form = PaymentPromiseForm(request.POST)

            if promise_form.is_valid():
                promise = promise_form.save(commit=False)
                promise.customer = document.customer
                promise.status = PaymentPromise.Status.ACTIVE

                if request.user.is_authenticated:
                    promise.created_by = request.user

                promise.save()

                PromiseDocument.objects.create(
                    promise=promise,
                    document=document,
                )

                CollectionAction.objects.create(
                    document=document,
                    customer=document.customer,
                    action_type=CollectionAction.ActionType.PROMISE,
                    performed_by=request.user if request.user.is_authenticated else None,
                    title="Promesa de pago registrada",
                    description=(
                        f"Fecha compromiso: {promise.promise_date.strftime('%d-%m-%Y')}\n"
                        f"Monto comprometido: {promise.promised_amount}"
                    ),
                    metadata={
                        "payment_promise_id": promise.id,
                        "promise_date": promise.promise_date.isoformat(),
                        "promised_amount": str(promise.promised_amount),
                    },
                )

                status_pago_programado = DocumentStatus.objects.filter(
                    name__iexact="Pago programado",
                    is_active=True,
                ).first()

                substatus_promesa_vigente = DocumentSubStatus.objects.filter(
                    name__iexact="Promesa vigente",
                    is_active=True,
                ).first()

                changed_fields = []

                if status_pago_programado:
                    document.status = status_pago_programado
                    changed_fields.append("status")

                if substatus_promesa_vigente:
                    document.sub_status = substatus_promesa_vigente
                    changed_fields.append("sub_status")

                if changed_fields:
                    changed_fields.append("updated_at")
                    document.save(update_fields=changed_fields)

                return redirect("management:document_detail", id=document.id)

    promise_links = (
        PromiseDocument.objects.select_related(
            "promise",
            "promise__customer",
            "promise__created_by",
            "document",
        )
        .filter(document=document)
        .order_by("-promise__created_at")
    )

    active_promises = promise_links.filter(
        promise__status=PaymentPromise.Status.ACTIVE,
    )

    expired_promises = promise_links.filter(
        promise__status=PaymentPromise.Status.EXPIRED,
    )

    historical_promises = promise_links.exclude(
        promise__status=PaymentPromise.Status.ACTIVE,
    )

    timeline_actions = CollectionAction.objects.select_related(
        "document",
        "customer",
        "performed_by",
    ).filter(
        document=document,
    )

    context = {
        "document": document,
        "customer": document.customer,
        "form": action_form,
        "action_form": action_form,
        "promise_form": promise_form,
        "active_promises": active_promises,
        "expired_promises": expired_promises,
        "historical_promises": historical_promises,
        "timeline_actions": timeline_actions,
    }

    return render(request, "management/document_detail.html", context)