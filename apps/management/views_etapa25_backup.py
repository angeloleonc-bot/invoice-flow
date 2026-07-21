from datetime import timedelta,datetime, time
from django.db.models import Count, Q, Max, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.portfolio.models import (
    Document,
    DocumentAssignment,
    DocumentStatus,
    DocumentSubStatus,
    PaymentRecord,
    CreditNoteApplication,
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

from django.views.decorators.http import require_POST
from apps.management.models import OperationalAlert
from apps.management.services.alerts import OperationalAlertService

from django.db.models.functions import Coalesce
from decimal import Decimal

def my_work(request):
    selected_filter = request.GET.get("filter", "all")
    selected_attention = request.GET.get("attention")
    selected_aging = request.GET.get("aging")

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

    active_alerts = OperationalAlertService.get_active_alerts()

    alerts_by_document = {}
    for alert in active_alerts.filter(document__isnull=False):
        alerts_by_document.setdefault(alert.document_id, 0)
        alerts_by_document[alert.document_id] += 1



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

    for item in work_items:
        document = item.get("document")
        item["active_alerts_count"] = alerts_by_document.get(document.id, 0) if document else 0

    requires_action_count = sum(
        1
        for item in work_items
        if (
            WorklistPriorityService.RULE_PROMISE_EXPIRED in item["priority_reason_codes"]
            or WorklistPriorityService.RULE_NO_MANAGEMENT_7_DAYS in item["priority_reason_codes"]
            or WorklistPriorityService.RULE_DOCUMENT_OVERDUE in item["priority_reason_codes"]
            or item.get("active_alerts_count", 0) > 0
        )
    )

    if selected_attention in ["7", "14", "30"]:
        cutoff = timezone.now() - timedelta(days=int(selected_attention))

        recent_document_ids = (
            CollectionAction.objects
            .filter(document_id__isnull=False)
            .values("document_id")
            .annotate(last_action_date=Max("action_date"))
            .filter(last_action_date__gte=cutoff)
            .values_list("document_id", flat=True)
        )

        work_items = [
            item
            for item in work_items
            if item["document"].id not in recent_document_ids
        ]

        selected_filter = f"attention_{selected_attention}"

    if selected_aging in ["current", "days_1_30", "days_31_60", "days_61_90", "days_90_plus"]:
        today = timezone.localdate()

        if selected_aging == "current":
            work_items = [
                item for item in work_items
                if item["document"].due_date and item["document"].due_date >= today
            ]
        elif selected_aging == "days_1_30":
            work_items = [
                item for item in work_items
                if item["document"].due_date
                and item["document"].due_date < today
                and item["document"].due_date >= today - timedelta(days=30)
            ]
        elif selected_aging == "days_31_60":
            work_items = [
                item for item in work_items
                if item["document"].due_date
                and item["document"].due_date < today - timedelta(days=30)
                and item["document"].due_date >= today - timedelta(days=60)
            ]
        elif selected_aging == "days_61_90":
            work_items = [
                item for item in work_items
                if item["document"].due_date
                and item["document"].due_date < today - timedelta(days=60)
                and item["document"].due_date >= today - timedelta(days=90)
            ]
        elif selected_aging == "days_90_plus":
            work_items = [
                item for item in work_items
                if item["document"].due_date
                and item["document"].due_date < today - timedelta(days=90)
            ]

        selected_filter = f"aging_{selected_aging}"   

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
    elif selected_filter == "requires_action":
        work_items = [
            item
            for item in work_items
            if (
                WorklistPriorityService.RULE_PROMISE_EXPIRED in item["priority_reason_codes"]
                or WorklistPriorityService.RULE_NO_MANAGEMENT_7_DAYS in item["priority_reason_codes"]
                or WorklistPriorityService.RULE_DOCUMENT_OVERDUE in item["priority_reason_codes"]
                or item.get("active_alerts_count", 0) > 0
            )
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
        "selected_attention": selected_attention,
        "total_documents": total_documents,
        "total_balance": total_balance,
        "high_priority_count": high_priority_count,
        "expired_promises_count": expired_promises_count,
        "promises_today_count": promises_today_count,
        "no_management_7_days_count": no_management_7_days_count,
        "critical_portfolio_count": critical_portfolio_count,
        "requires_action_count": requires_action_count,
        "selected_aging": selected_aging,
    }

    for item in work_items:
        print(
            item["document"].document_number,
            item.get("active_alerts_count")
        )

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

    credit_notes = (
        CreditNoteApplication.objects
        .filter(document=document)
        .order_by("-issue_date", "-created_at")
    )

    total_credit_notes = (
        credit_notes.aggregate(total=Coalesce(Sum("credit_amount"), Decimal("0")))["total"]
    )

    payments = (
        PaymentRecord.objects.filter(document=document)
        .select_related("document", "customer")
        .order_by("-payment_date", "-created_at")
    )

    total_paid = (
        payments.aggregate(
            total=Coalesce(Sum("amount"), Decimal("0"))
        )["total"]
    )

    financial_summary = {
        "original_amount": document.original_amount,
        "total_credit_notes": total_credit_notes,
        "total_paid": total_paid,
        "balance_amount": document.balance_amount,
        "overpayment_amount": document.overpayment_amount,
    }

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
                messages.success(request, "Gestión registrada correctamente.")

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
                        f"Monto comprometido: $ {promise.promised_amount:,.0f}".replace(",", ".")
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

                
                messages.success(request, "Promesa registrada correctamente.")
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



    actions = CollectionAction.objects.select_related(
        "document",
        "customer",
        "performed_by",
    ).filter(
        document=document,
    )

    def normalize_timeline_date(value):
        if value is None:
            return timezone.now()

        if isinstance(value, datetime):
            if timezone.is_naive(value):
                return timezone.make_aware(value)
            return value

        return timezone.make_aware(datetime.combine(value, time.min))

    timeline_events = []

    for action in actions:
        timeline_events.append(
            {
                "type": "action",
                "date": action.action_date,
                "label": action.get_action_type_display(),
                "title": action.title,
                "description": action.description,
                "amount": None,
                "user": action.performed_by,
                "icon": "bi-activity",
            }
        )

    for payment in payments:
        timeline_events.append(
            {
                "type": "payment",
                "date": payment.payment_date,
                "label": "Pago",
                "title": "Pago aplicado",
                "description": payment.source_reference or payment.notes,
                "amount": payment.amount,
                "user": None,
                "icon": "bi-cash-coin",
            }
        )

    for nc in credit_notes:
        timeline_events.append(
            {
                "type": "credit_note",
                "date": nc.issue_date,
                "label": "Nota de crédito",
                "title": f"NC {nc.credit_document_number} aplicada",
                "description": nc.comment,
                "amount": nc.credit_amount,
                "user": None,
                "icon": "bi-receipt-cutoff",
                "status": nc.status,
                "reason": nc.reason,
                "invoice_amount": nc.invoice_amount,
            }
        )

    timeline_events = sorted(
        timeline_events,
        key=lambda event: normalize_timeline_date(event["date"]),
        reverse=True,
    )

    latest_timeline_events = timeline_events[:15]

    financial_movements = []

    for payment in payments:
        financial_movements.append(
            {
                "type": "payment",
                "date": payment.payment_date,
                "label": "Pago",
                "title": "Pago aplicado",
                "amount": payment.amount,
                "reference": payment.source_reference or payment.external_payment_id,
                "description": payment.notes,
                "icon": "bi-cash-coin",
            }
        )

    for nc in credit_notes:
        financial_movements.append(
            {
                "type": "credit_note",
                "date": nc.issue_date,
                "label": "Nota de crédito",
                "title": f"NC {nc.credit_document_number}",
                "amount": nc.credit_amount,
                "reference": nc.credit_document_number,
                "description": nc.comment,
                "icon": "bi-receipt-cutoff",
            }
        )

    financial_movements = sorted(
        financial_movements,
        key=lambda movement: normalize_timeline_date(movement["date"]),
        reverse=True,
    )

    latest_financial_movements = financial_movements[:10]

    context = {
        "document": document,
        "customer": document.customer,
        "form": action_form,
        "action_form": action_form,
        "promise_form": promise_form,
        "active_promises": active_promises,
        "expired_promises": expired_promises,
        "historical_promises": historical_promises,
        "timeline_events": timeline_events,
        "payments": payments,
        "total_paid": total_paid,
        "collectors": collectors,
        "current_assignment": current_assignment,
        "credit_notes": credit_notes,
        "total_credit_notes": total_credit_notes,
        "financial_summary": financial_summary,
        "financial_movements": financial_movements,
        "latest_financial_movements": latest_financial_movements,
        "timeline_events": timeline_events,
        "latest_timeline_events": latest_timeline_events,
    }

    return render(request, "management/document_detail.html", context)

def alerts_center(request):
    alerts = OperationalAlertService.get_visible_active_alerts(request.user)

    default_filter = "mine"

    if OperationalAlertService.get_effective_role(request.user) in OperationalAlertService.FULL_VISIBILITY_ROLES:
        default_filter = "all"

    selected_filter = request.GET.get("filter", default_filter)

    if selected_filter == "mine":
        alerts = alerts.filter(assigned_to=request.user)
    elif selected_filter == "all":
        alerts = alerts
    elif selected_filter == "critical":
        alerts = alerts.filter(severity=OperationalAlert.Severity.CRITICAL)
    elif selected_filter == "reopened":
        alerts = alerts.filter(status=OperationalAlert.AlertStatus.REOPENED)
    elif selected_filter == "postponed":
        alerts = alerts.filter(status=OperationalAlert.AlertStatus.POSTPONED)
    elif selected_filter == "promises":
        alerts = alerts.filter(
            alert_type__in=[
                OperationalAlert.AlertType.PROMISE_EXPIRED,
                OperationalAlert.AlertType.PROMISE_DUE_TODAY,
            ]
        )
    elif selected_filter in ["no_management", "without_management"]:
        alerts = alerts.filter(
            alert_type=OperationalAlert.AlertType.NO_MANAGEMENT_7_DAYS
        )
    elif selected_filter == "unassigned":
        alerts = alerts.filter(
            alert_type=OperationalAlert.AlertType.UNASSIGNED_DOCUMENT
        )
    elif selected_filter == "critical_customers":
        alerts = alerts.filter(
            alert_type=OperationalAlert.AlertType.CRITICAL_CUSTOMER
        )
    elif selected_filter == "high_priority":
        alerts = alerts.filter(
            alert_type=OperationalAlert.AlertType.HIGH_PRIORITY_DOCUMENT
        )

    all_active_alerts = OperationalAlertService.get_visible_active_alerts(request.user)

    alerts = alerts.order_by("-created_at")[:50]

    context = {
        "alerts": alerts,
        "selected_filter": selected_filter,
        "kpi_new": all_active_alerts.filter(
            status=OperationalAlert.AlertStatus.NEW
        ).count(),
        "kpi_critical": all_active_alerts.filter(
            severity=OperationalAlert.Severity.CRITICAL
        ).count(),
        "kpi_high": all_active_alerts.filter(
            severity=OperationalAlert.Severity.HIGH
        ).count(),
        "kpi_in_progress": all_active_alerts.filter(
            status=OperationalAlert.AlertStatus.IN_PROGRESS
        ).count(),
        "kpi_reopened": all_active_alerts.filter(
            status=OperationalAlert.AlertStatus.REOPENED
        ).count(),
        "kpi_postponed": all_active_alerts.filter(
            status=OperationalAlert.AlertStatus.POSTPONED
        ).count(),
        "aging_summary": OperationalAlertService.aging_summary_for_user(request.user),
        "alerts_by_responsible": OperationalAlertService.alerts_by_responsible(request.user),
        "critical_customer_summary": OperationalAlertService.critical_customer_summary_for_user(request.user),
    }

    return render(request, "management/alerts.html", context)


@require_POST
def alert_resolve(request, alert_id):
    alert = get_object_or_404(OperationalAlert, id=alert_id)
    OperationalAlertService.mark_resolved(alert, user=request.user)
    messages.success(request, "Alerta resuelta correctamente.")
    return redirect("management:alerts_center")


@require_POST
def alert_postpone(request, alert_id):
    alert = get_object_or_404(OperationalAlert, id=alert_id)
    OperationalAlertService.mark_postponed(alert, user=request.user)
    messages.info(request, "Alerta pospuesta correctamente.")
    return redirect("management:alerts_center")


@require_POST
def alert_dismiss(request, alert_id):
    alert = get_object_or_404(OperationalAlert, id=alert_id)
    OperationalAlertService.mark_dismissed(alert, user=request.user)
    messages.warning(request, "Alerta descartada correctamente.")
    return redirect("management:alerts_center")