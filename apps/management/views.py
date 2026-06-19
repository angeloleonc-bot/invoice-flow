from datetime import timedelta

from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.portfolio.models import (
    Document,
    DocumentAssignment,
    DocumentStatus,
    DocumentSubStatus,
    PaymentRecord,
)
from .forms import CollectionActionForm, PaymentPromiseForm
from .models import CollectionAction, PaymentPromise, PromiseDocument
from apps.portfolio.constants import (
    DOCUMENT_STATUS_PAYMENT_SCHEDULED,
    DOCUMENT_SUBSTATUS_ACTIVE_PROMISE,
)
from .services.prioritization import WorklistPriorityService
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction

def my_work(request):
    selected_filter = request.GET.get("filter", "all")

    assignments = (
        DocumentAssignment.objects.select_related(
            "document",
            "document__customer",
            "document__status",
            "document__sub_status",
            "assigned_to",
        )
       .prefetch_related(
            "document__tags",
            "document__promise_documents__promise",
            "document__collection_actions",
        )
        .filter(is_active=True, assigned_to=request.user)
    )

    total_documents = assignments.count()
    total_balance = assignments.aggregate(
        total=Sum("document__balance_amount")
    )["total"] or 0

    priority_service = WorklistPriorityService()
    work_items = []

    for assignment in assignments:
        priority_data = priority_service.evaluate_assignment(assignment)
        document = assignment.document

        item = {
            "assignment": assignment,
            "document": document,
            "customer": document.customer,
            "assigned_to": assignment.assigned_to,
            **priority_data,
        }

        work_items.append(item)

    high_priority_count = sum(
        1 for item in work_items if item["priority_score"] >= 80
    )
    expired_promises_count = sum(
        1
        for item in work_items
        if WorklistPriorityService.RULE_PROMISE_EXPIRED
        in item["priority_reason_codes"]
    )
    promises_today_count = sum(
        1
        for item in work_items
        if WorklistPriorityService.RULE_PROMISE_DUE_TODAY
        in item["priority_reason_codes"]
    )
    no_management_7_days_count = sum(
        1
        for item in work_items
        if WorklistPriorityService.RULE_NO_MANAGEMENT_7_DAYS
        in item["priority_reason_codes"]
    )
    critical_portfolio_count = sum(
        1
        for item in work_items
        if WorklistPriorityService.RULE_CUSTOMER_CRITICAL
        in item["priority_reason_codes"]
    )

    if selected_filter == "high_priority":
        work_items = [
            item for item in work_items if item["priority_score"] >= 80
        ]
    elif selected_filter == "promise_expired":
        work_items = [
            item
            for item in work_items
            if WorklistPriorityService.RULE_PROMISE_EXPIRED
            in item["priority_reason_codes"]
        ]
    elif selected_filter == "no_management_7_days":
        work_items = [
            item
            for item in work_items
            if WorklistPriorityService.RULE_NO_MANAGEMENT_7_DAYS
            in item["priority_reason_codes"]
        ]
    elif selected_filter == "high_balance":
        work_items = [
            item
            for item in work_items
            if WorklistPriorityService.RULE_HIGH_BALANCE
            in item["priority_reason_codes"]
        ]
    elif selected_filter == "overdue":
        work_items = [
            item
            for item in work_items
            if WorklistPriorityService.RULE_DOCUMENT_OVERDUE
            in item["priority_reason_codes"]
        ]

    work_items = priority_service.sort_items(work_items)

    context = {
        "assignments": assignments,
        "work_items": work_items,
        "selected_filter": selected_filter,
        "total_documents": total_documents,
        "total_balance": total_balance,
        "high_priority_count": high_priority_count,
        "expired_promises_count": expired_promises_count,
        "promises_today_count": promises_today_count,
        "no_management_7_days_count": no_management_7_days_count,
        "critical_portfolio_count": critical_portfolio_count,
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

    User = get_user_model()

    collectors = User.objects.filter(is_active=True).order_by(
        "first_name",
        "last_name",
        "username",
    )

    current_assignment = (
        DocumentAssignment.objects.select_related("assigned_to", "assigned_by")
        .filter(document=document, is_active=True)
        .first()
    )

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
                    name__iexact=DOCUMENT_STATUS_PAYMENT_SCHEDULED,
                    is_active=True,
                ).first()

                substatus_promesa_vigente = DocumentSubStatus.objects.filter(
                    name__iexact=DOCUMENT_SUBSTATUS_ACTIVE_PROMISE,
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
        
        elif form_type == "reassignment":
            collector_id = request.POST.get("collector_id")

            if not collector_id:
                messages.error(request, "Debe seleccionar un cobrador para reasignar.")
                return redirect("management:document_detail", id=document.id)

            collector = get_object_or_404(
                User,
                pk=collector_id,
                is_active=True,
            )

            with transaction.atomic():
                DocumentAssignment.objects.filter(
                    document=document,
                    is_active=True,
                ).update(is_active=False)

                DocumentAssignment.objects.create(
                    document=document,
                    assigned_to=collector,
                    assigned_by=request.user,
                    assignment_type=DocumentAssignment.ASSIGNMENT_TYPE_REASSIGNMENT,
                    is_active=True,
                )

            messages.success(request, "Documento reasignado correctamente.")
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

    payments = (
        PaymentRecord.objects.filter(document=document)
        .select_related("document", "customer")
        .order_by("-payment_date", "-created_at")
    )

    total_paid = payments.aggregate(total=Sum("amount"))["total"] or 0

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
        "payments": payments,
        "total_paid": total_paid,
        "collectors": collectors,
        "current_assignment": current_assignment,
    }

    return render(request, "management/document_detail.html", context)