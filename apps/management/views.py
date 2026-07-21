from datetime import timedelta,datetime, time
from django.db.models import (
    Case,
    Count,
    DateField,
    DecimalField,
    Exists,
    IntegerField,
    Max,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.core.paginator import Paginator
from apps.portfolio.models import (
    Customer,
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
from urllib.parse import urlencode
from django.db.models.functions import Cast, Coalesce
from decimal import Decimal
from apps.portfolio.services.document_supports import (
    build_document_support_viewmodels,
)


from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import (
    Http404,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404

from apps.management.models import OperationalAttachment
from apps.management.services.attachments import (
    build_attachment_download_response,
)

from apps.management.services.actions import (
    create_collection_action,
)

from apps.management.services.promises import (
    create_payment_promise,
)

def my_work(request):
    """
    Gestión operacional centrada en clientes.

    Cada fila representa un cliente y resume exclusivamente los documentos
    que pertenecen al alcance visible del usuario:

    - Cobrador: documentos asignados activamente al usuario.
    - Supervisor: Mi cartera o Toda la cartera.
    """

    today = timezone.localdate()
    now = timezone.now()

    role = OperationalAlertService.get_effective_role(request.user)
    is_supervisor = role in OperationalAlertService.FULL_VISIBILITY_ROLES

    requested_scope = request.GET.get("scope", "my")

    if not is_supervisor:
        selected_scope = "my"
    elif requested_scope == "all":
        selected_scope = "all"
    else:
        selected_scope = "my"

    search_customer = request.GET.get("customer", "").strip()
    search_rut = request.GET.get("rut", "").strip()
    selected_collector = request.GET.get("collector", "").strip()
    selected_filter = request.GET.get("filter", "").strip()

    allowed_sort_keys = {
        "customer",
        "total_balance",
        "current_balance",
        "aging_0_15",
        "aging_16_30",
        "aging_31_45",
        "aging_46_60",
        "aging_61_90",
        "aging_91_120",
        "aging_121_plus",
        "latest_event",
    }

    requested_sort = request.GET.get("sort", "").strip()
    requested_direction = request.GET.get("dir", "").strip().lower()

    if (
        requested_sort in allowed_sort_keys
        and requested_direction in {"asc", "desc"}
    ):
        selected_sort = requested_sort
        selected_direction = requested_direction

        request.session["management_portfolio_sort"] = selected_sort
        request.session["management_portfolio_direction"] = selected_direction

    else:
        session_sort = request.session.get(
            "management_portfolio_sort",
            "",
        )
        session_direction = request.session.get(
            "management_portfolio_direction",
            "",
        )

        if (
            session_sort in allowed_sort_keys
            and session_direction in {"asc", "desc"}
        ):
            selected_sort = session_sort
            selected_direction = session_direction
        else:
            selected_sort = ""
            selected_direction = ""

    allowed_page_sizes = {"25", "50", "100", "all"}

    requested_page_size = request.GET.get("page_size", "").strip().lower()

    if requested_page_size in allowed_page_sizes:
        selected_page_size = requested_page_size
        request.session["management_portfolio_page_size"] = selected_page_size
    else:
        selected_page_size = request.session.get(
            "management_portfolio_page_size",
            "25",
        )

        if selected_page_size not in allowed_page_sizes:
            selected_page_size = "25"

    zero_decimal = Value(
        Decimal("0.00"),
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )

    active_assignment_filter = Q(
        documents__assignments__is_active=True,
    )

    if selected_scope == "my":
        active_assignment_filter &= Q(
            documents__assignments__assigned_to=request.user,
        )
    elif selected_collector:
        active_assignment_filter &= Q(
            documents__assignments__assigned_to_id=selected_collector,
        )

    open_document_filter = (
        active_assignment_filter
        & Q(documents__balance_amount__gt=0)
    )

    current_filter = (
        open_document_filter
        & Q(documents__due_date__gte=today)
    )

    aging_0_15_filter = (
        open_document_filter
        & Q(documents__due_date__lt=today)
        & Q(documents__due_date__gte=today - timedelta(days=15))
    )

    aging_16_30_filter = (
        open_document_filter
        & Q(documents__due_date__lt=today - timedelta(days=15))
        & Q(documents__due_date__gte=today - timedelta(days=30))
    )

    aging_31_45_filter = (
        open_document_filter
        & Q(documents__due_date__lt=today - timedelta(days=30))
        & Q(documents__due_date__gte=today - timedelta(days=45))
    )

    aging_46_60_filter = (
        open_document_filter
        & Q(documents__due_date__lt=today - timedelta(days=45))
        & Q(documents__due_date__gte=today - timedelta(days=60))
    )

    aging_61_90_filter = (
        open_document_filter
        & Q(documents__due_date__lt=today - timedelta(days=60))
        & Q(documents__due_date__gte=today - timedelta(days=90))
    )

    aging_91_120_filter = (
        open_document_filter
        & Q(documents__due_date__lt=today - timedelta(days=90))
        & Q(documents__due_date__gte=today - timedelta(days=120))
    )

    aging_121_plus_filter = (
        open_document_filter
        & Q(documents__due_date__lt=today - timedelta(days=120))
    )

    scoped_actions = CollectionAction.objects.filter(
        customer_id=OuterRef("pk"),
        document__assignments__is_active=True,
    )

    if selected_scope == "my":
        scoped_actions = scoped_actions.filter(
            document__assignments__assigned_to=request.user,
        )
    elif selected_collector:
        scoped_actions = scoped_actions.filter(
            document__assignments__assigned_to_id=selected_collector,
        )

    scoped_actions = scoped_actions.order_by(
        "-action_date",
        "-created_at",
    )

    relevant_promises = (
        PaymentPromise.objects
        .filter(
            customer_id=OuterRef("pk"),
            status__in=[
                PaymentPromise.Status.ACTIVE,
                PaymentPromise.Status.PENDING,
                PaymentPromise.Status.EXPIRED,
            ],
        )
        .annotate(
            operational_order=Case(
                When(
                    status=PaymentPromise.Status.EXPIRED,
                    then=Value(0),
                ),
                When(
                    status=PaymentPromise.Status.ACTIVE,
                    then=Value(1),
                ),
                When(
                    status=PaymentPromise.Status.PENDING,
                    then=Value(2),
                ),
                default=Value(3),
                output_field=IntegerField(),
            )
        )
        .order_by(
            "operational_order",
            "promise_date",
            "-created_at",
        )
    )

    expired_promises = PaymentPromise.objects.filter(
        customer_id=OuterRef("pk"),
        status=PaymentPromise.Status.EXPIRED,
    )

    active_promises = PaymentPromise.objects.filter(
        customer_id=OuterRef("pk"),
        status__in=[
            PaymentPromise.Status.ACTIVE,
            PaymentPromise.Status.PENDING,
        ],
    )

    active_alert_statuses = [
        OperationalAlert.AlertStatus.NEW,
        OperationalAlert.AlertStatus.VIEWED,
        OperationalAlert.AlertStatus.IN_PROGRESS,
        OperationalAlert.AlertStatus.REOPENED,
    ]

    customer_active_alerts = OperationalAlert.objects.filter(
        customer_id=OuterRef("pk"),
    ).filter(
        Q(status__in=active_alert_statuses)
        | Q(
            status=OperationalAlert.AlertStatus.POSTPONED,
            due_at__lte=now,
        )
    )

    critical_customer_alerts = customer_active_alerts.filter(
        alert_type=OperationalAlert.AlertType.CRITICAL_CUSTOMER,
    )

    customers = (
        Customer.objects
        .filter(
            is_active=True,
            documents__assignments__is_active=True,
        )
    )

    if selected_scope == "my":
        customers = customers.filter(
            documents__assignments__assigned_to=request.user,
        )
    elif selected_collector:
        customers = customers.filter(
            documents__assignments__assigned_to_id=selected_collector,
        )

    customers = (
        customers
        .annotate(
            total_balance=Coalesce(
                Sum(
                    "documents__balance_amount",
                    filter=open_document_filter,
                ),
                zero_decimal,
            ),
            current_balance=Coalesce(
                Sum(
                    "documents__balance_amount",
                    filter=current_filter,
                ),
                zero_decimal,
            ),
            aging_0_15=Coalesce(
                Sum(
                    "documents__balance_amount",
                    filter=aging_0_15_filter,
                ),
                zero_decimal,
            ),
            aging_16_30=Coalesce(
                Sum(
                    "documents__balance_amount",
                    filter=aging_16_30_filter,
                ),
                zero_decimal,
            ),
            aging_31_45=Coalesce(
                Sum(
                    "documents__balance_amount",
                    filter=aging_31_45_filter,
                ),
                zero_decimal,
            ),
            aging_46_60=Coalesce(
                Sum(
                    "documents__balance_amount",
                    filter=aging_46_60_filter,
                ),
                zero_decimal,
            ),
            aging_61_90=Coalesce(
                Sum(
                    "documents__balance_amount",
                    filter=aging_61_90_filter,
                ),
                zero_decimal,
            ),
            aging_91_120=Coalesce(
                Sum(
                    "documents__balance_amount",
                    filter=aging_91_120_filter,
                ),
                zero_decimal,
            ),
            aging_121_plus=Coalesce(
                Sum(
                    "documents__balance_amount",
                    filter=aging_121_plus_filter,
                ),
                zero_decimal,
            ),
            total_overpayment=Coalesce(
                Sum(
                    "documents__overpayment_amount",
                    filter=active_assignment_filter,
                ),
                zero_decimal,
            ),
            open_documents_count=Count(
                "documents",
                filter=open_document_filter,
                distinct=True,
            ),
            last_action_date=Subquery(
                scoped_actions.values("action_date")[:1]
            ),
            last_action_title=Subquery(
                scoped_actions.values("title")[:1]
            ),
            last_action_type=Subquery(
                scoped_actions.values("action_type")[:1]
            ),
            promise_date=Subquery(
                relevant_promises.values("promise_date")[:1]
            ),
            promise_amount=Subquery(
                relevant_promises.values("promised_amount")[:1]
            ),
            promise_status=Subquery(
                relevant_promises.values("status")[:1]
            ),
            has_expired_promise=Exists(expired_promises),
            has_active_promise=Exists(active_promises),
            has_active_alert=Exists(customer_active_alerts),
            is_critical=Exists(critical_customer_alerts),
        )

        .annotate(
            latest_event_date=Coalesce(
                "promise_date",
                Cast(
                    "last_action_date",
                    output_field=DateField(),
                ),
            ),
        )

        .filter(
            Q(total_balance__gt=0)
            | Q(total_overpayment__gt=0)
        )
        .distinct()
    )

    kpi_queryset = customers

    kpis = kpi_queryset.aggregate(
        active_customers=Count("id", distinct=True),
        pending_balance=Coalesce(
            Sum("total_balance"),
            zero_decimal,
        ),
        attention_customers=Count(
            "id",
            filter=(
                Q(has_expired_promise=True)
                | Q(has_active_alert=True)
                | Q(last_action_date__isnull=True)
            ),
            distinct=True,
        ),
        expired_promises=Count(
            "id",
            filter=Q(has_expired_promise=True),
            distinct=True,
        ),
    )

    if search_customer:
        customers = customers.filter(
            name__icontains=search_customer,
        )

    if search_rut:
        customers = customers.filter(
            rut__icontains=search_rut,
        )

    if selected_filter == "with_promises":
        customers = customers.filter(
            Q(has_active_promise=True)
            | Q(has_expired_promise=True)
        )
    elif selected_filter == "without_management":
        customers = customers.filter(
            last_action_date__isnull=True,
        )
    elif selected_filter == "with_credit":
        customers = customers.filter(
            total_overpayment__gt=0,
        )
    elif selected_filter == "critical":
        customers = customers.filter(
            is_critical=True,
        )

    sort_fields = {
        "customer": "name",
        "total_balance": "total_balance",
        "current_balance": "current_balance",
        "aging_0_15": "aging_0_15",
        "aging_16_30": "aging_16_30",
        "aging_31_45": "aging_31_45",
        "aging_46_60": "aging_46_60",
        "aging_61_90": "aging_61_90",
        "aging_91_120": "aging_91_120",
        "aging_121_plus": "aging_121_plus",
        "latest_event": "latest_event_date",
    }

    if selected_sort in sort_fields and selected_direction in {"asc", "desc"}:
        order_field = sort_fields[selected_sort]

        if selected_direction == "desc":
            order_field = f"-{order_field}"

        customers = customers.order_by(
            order_field,
            "name",
        )
    else:
        selected_sort = ""
        selected_direction = ""

        customers = customers.order_by(
            "-has_expired_promise",
            "-is_critical",
            "-has_active_alert",
            "-total_balance",
            "name",
        )

    page_number = request.GET.get("page", "1")

    if selected_page_size == "all":
        customers = list(customers)

        page_obj = None
        pagination_page_range = []
        total_filtered_customers = len(customers)
        result_start = 1 if customers else 0
        result_end = total_filtered_customers
        is_paginated = False

    else:
        paginator = Paginator(
            customers,
            int(selected_page_size),
        )

        page_obj = paginator.get_page(page_number)
        customers = list(page_obj.object_list)

        pagination_page_range = list(
            paginator.get_elided_page_range(
                page_obj.number,
                on_each_side=1,
                on_ends=1,
            )
        )

        total_filtered_customers = paginator.count

        if paginator.count:
            result_start = page_obj.start_index()
            result_end = page_obj.end_index()
        else:
            result_start = 0
            result_end = 0

        is_paginated = paginator.num_pages > 1

    assignment_rows = (
        DocumentAssignment.objects
        .filter(
            is_active=True,
            document__customer_id__in=[
                customer.id
                for customer in customers
            ],
        )
        .select_related(
            "assigned_to",
            "document__customer",
        )
        .values(
            "document__customer_id",
            "assigned_to_id",
            "assigned_to__first_name",
            "assigned_to__last_name",
            "assigned_to__username",
        )
        .distinct()
        .order_by(
            "assigned_to__first_name",
            "assigned_to__last_name",
            "assigned_to__username",
        )
    )

    if selected_scope == "my":
        assignment_rows = assignment_rows.filter(
            assigned_to=request.user,
        )
    elif selected_collector:
        assignment_rows = assignment_rows.filter(
            assigned_to_id=selected_collector,
        )

    collectors_by_customer = {}

    for assignment in assignment_rows:
        customer_id = assignment["document__customer_id"]

        full_name = " ".join(
            part
            for part in [
                assignment["assigned_to__first_name"],
                assignment["assigned_to__last_name"],
            ]
            if part
        ).strip()

        collector_name = (
            full_name
            or assignment["assigned_to__username"]
        )

        collectors_by_customer.setdefault(
            customer_id,
            [],
        ).append(collector_name)

    action_type_labels = dict(
        CollectionAction.ActionType.choices
    )

    promise_status_labels = dict(
        PaymentPromise.Status.choices
    )

    for customer in customers:
        collector_names = collectors_by_customer.get(
            customer.id,
            [],
        )

        customer.collector_names = collector_names
        customer.collector_display = (
            ", ".join(collector_names)
            if collector_names
            else "Sin cobrador"
        )

        customer.last_action_type_label = (
            action_type_labels.get(
                customer.last_action_type,
                customer.last_action_type,
            )
            if customer.last_action_type
            else ""
        )

        customer.promise_status_label = (
            promise_status_labels.get(
                customer.promise_status,
                customer.promise_status,
            )
            if customer.promise_status
            else ""
        )

        if customer.has_expired_promise:
            customer.operational_status = "promise_expired"
            customer.operational_label = "Promesa vencida"

        elif customer.is_critical:
            customer.operational_status = "critical"
            customer.operational_label = "Cliente crítico"

        elif customer.total_overpayment > 0:
            customer.operational_status = "credit"
            customer.operational_label = "Saldo a favor"

        elif customer.has_active_promise:
            customer.operational_status = "promise_active"
            customer.operational_label = "Promesa vigente"

        elif not customer.last_action_date:
            customer.operational_status = "without_management"
            customer.operational_label = "Sin gestión"

        elif customer.has_active_alert:
            customer.operational_status = "attention"
            customer.operational_label = "Requiere atención"

        else:
            customer.operational_status = "normal"
            customer.operational_label = "Sin novedades"

    collectors = []

    if is_supervisor:
        User = get_user_model()

        collectors = (
            User.objects
            .filter(
                is_active=True,
                portfolio_assignments_received__is_active=True,
            )
            .distinct()
            .order_by(
                "first_name",
                "last_name",
                "username",
            )
        )

    def build_sort_url(sort_key, default_direction="desc"):
        params = request.GET.copy()
        params.pop("page", None)

        if (
            selected_sort == sort_key
            and selected_direction == "desc"
        ):
            next_direction = "asc"
        elif (
            selected_sort == sort_key
            and selected_direction == "asc"
        ):
            next_direction = "desc"
        else:
            next_direction = default_direction

        params["sort"] = sort_key
        params["dir"] = next_direction

        return f"?{urlencode(params, doseq=True)}"


    sort_urls = {
        "customer": build_sort_url(
            "customer",
            default_direction="asc",
        ),
        "total_balance": build_sort_url("total_balance"),
        "current_balance": build_sort_url("current_balance"),
        "aging_0_15": build_sort_url("aging_0_15"),
        "aging_16_30": build_sort_url("aging_16_30"),
        "aging_31_45": build_sort_url("aging_31_45"),
        "aging_46_60": build_sort_url("aging_46_60"),
        "aging_61_90": build_sort_url("aging_61_90"),
        "aging_91_120": build_sort_url("aging_91_120"),
        "aging_121_plus": build_sort_url("aging_121_plus"),
        "latest_event": build_sort_url("latest_event"),
    }

    pagination_params = request.GET.copy()
    pagination_params.pop("page", None)
    pagination_params["page_size"] = selected_page_size

    pagination_query = urlencode(
        pagination_params,
        doseq=True,
    )


    def build_page_size_url(page_size):
        params = request.GET.copy()

        params.pop("page", None)
        params["page_size"] = page_size

        return f"?{urlencode(params, doseq=True)}"


    page_size_urls = {
        "25": build_page_size_url("25"),
        "50": build_page_size_url("50"),
        "100": build_page_size_url("100"),
        "all": build_page_size_url("all"),
    }

    context = {
        "customers": customers,
        "is_supervisor": is_supervisor,
        "selected_scope": selected_scope,
        "search_customer": search_customer,
        "search_rut": search_rut,
        "selected_collector": selected_collector,
        "selected_filter": selected_filter,
        "selected_sort": selected_sort,
        "selected_direction": selected_direction,
        "sort_urls": sort_urls,
        "collectors": collectors,

        "page_obj": page_obj,
        "pagination_page_range": pagination_page_range,
        "pagination_query": pagination_query,
        "selected_page_size": selected_page_size,
        "page_size_urls": page_size_urls,
        "total_filtered_customers": total_filtered_customers,
        "result_start": result_start,
        "result_end": result_end,
        "is_paginated": is_paginated,

        "kpi_active_customers": kpis["active_customers"] or 0,
        "kpi_pending_balance": kpis["pending_balance"] or 0,
        "kpi_attention_customers": kpis["attention_customers"] or 0,
        "kpi_expired_promises": kpis["expired_promises"] or 0,
    }

    return render(
        request,
        "management/my_work.html",
        context,
    )

def document_detail(request, id):
    document = get_object_or_404(
        Document.objects.select_related(
            "customer",
            "status",
            "sub_status",
        ).prefetch_related("tags"),
        id=id,
    )

    document_supports = build_document_support_viewmodels(document)

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
            action_form = CollectionActionForm(
                request.POST,
                request.FILES,
            )

            if action_form.is_valid():
                performed_by = (
                    request.user
                    if request.user.is_authenticated
                    else None
                )

                try:
                    create_collection_action(
                        form=action_form,
                        document=document,
                        performed_by=performed_by,
                        uploaded_files=request.FILES.getlist(
                            "attachments"
                        ),
                    )

                except ValidationError as exc:
                    action_form.add_error(
                        None,
                        exc,
                    )

                else:
                    messages.success(
                        request,
                        "Gestión registrada correctamente.",
                    )

                    return redirect(
                        "management:document_detail",
                        id=document.id,
                    )

        elif form_type == "promise":
            print(
                "\n=== DEBUG ADJUNTOS PROMESA ===",
                flush=True,
            )
            print(
                "request.FILES:",
                request.FILES,
                flush=True,
            )
            print(
                "attachments:",
                request.FILES.getlist("attachments"),
                flush=True,
            )
            print(
                "cantidad:",
                len(
                    request.FILES.getlist(
                        "attachments"
                    )
                ),
                flush=True,
            )

            promise_form = PaymentPromiseForm(
                request.POST,
                request.FILES,
            )

            promise_is_valid = promise_form.is_valid()

            print(
                "promise_form válido:",
                promise_is_valid,
                flush=True,
            )
            print(
                "promise_form errors:",
                promise_form.errors.as_json(),
                flush=True,
            )

            if promise_is_valid:
                created_by = (
                    request.user
                    if request.user.is_authenticated
                    else None
                )

                try:
                    create_payment_promise(
                        form=promise_form,
                        document=document,
                        created_by=created_by,
                        uploaded_files=request.FILES.getlist(
                            "attachments"
                        ),
                    )

                except ValidationError as exc:
                    promise_form.add_error(
                        None,
                        exc,
                    )

                else:
                    messages.success(
                        request,
                        "Promesa registrada correctamente.",
                    )

                    return redirect(
                        "management:document_detail",
                        id=document.id,
                    )

        elif form_type == "reassignment":
            collector_id = request.POST.get(
                "collector_id"
            )

            if not collector_id:
                messages.error(
                    request,
                    "Debe seleccionar un cobrador para reasignar.",
                )

                return redirect(
                    "management:document_detail",
                    id=document.id,
                )

            collector = get_object_or_404(
                User,
                pk=collector_id,
                is_active=True,
            )

            with transaction.atomic():
                DocumentAssignment.objects.filter(
                    document=document,
                    is_active=True,
                ).update(
                    is_active=False
                )

                DocumentAssignment.objects.create(
                    document=document,
                    assigned_to=collector,
                    assigned_by=request.user,
                    assignment_type=(
                        DocumentAssignment
                        .ASSIGNMENT_TYPE_REASSIGNMENT
                    ),
                    is_active=True,
                )

            messages.success(
                request,
                "Documento reasignado correctamente.",
            )

            return redirect(
                "management:document_detail",
                id=document.id,
            )

    promise_links = (
        PromiseDocument.objects.select_related(
            "promise",
            "promise__customer",
            "promise__created_by",
            "document",
        )
        .prefetch_related(
            "promise__attachments",
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



    actions = list(
        CollectionAction.objects
        .select_related(
            "document",
            "customer",
            "performed_by",
        )
        .prefetch_related(
            "attachments",
        )
        .filter(
            document=document,
        )
    )

    attachment_owner_ids = set()

    for action in actions:
        metadata = action.metadata or {}

        owner_action_id = metadata.get(
            "attachment_owner_action_id"
        )

        try:
            owner_action_id = int(owner_action_id)
        except (TypeError, ValueError):
            owner_action_id = None

        if owner_action_id:
            attachment_owner_ids.add(owner_action_id)

    attachment_owner_actions = {
        owner_action.id: owner_action
        for owner_action in (
            CollectionAction.objects
            .prefetch_related("attachments")
            .filter(id__in=attachment_owner_ids)
        )
    }

    def normalize_timeline_date(value):
        if value is None:
            return timezone.now()

        if isinstance(value, datetime):
            if timezone.is_naive(value):
                return timezone.make_aware(value)
            return value

        return timezone.make_aware(datetime.combine(value, time.min))
    
    def format_attachment_size(size_bytes):
        if not size_bytes:
            return "0 KB"

        size = float(size_bytes)

        if size < 1024:
            return f"{int(size)} B"

        size /= 1024

        if size < 1024:
            return f"{size:.1f} KB"

        size /= 1024

        return f"{size:.1f} MB"


    def get_attachment_icon(extension):
        extension = (extension or "").lower().lstrip(".")

        icon_map = {
            "pdf": "bi-file-earmark-pdf",
            "doc": "bi-file-earmark-word",
            "docx": "bi-file-earmark-word",
            "xls": "bi-file-earmark-excel",
            "xlsx": "bi-file-earmark-excel",
            "csv": "bi-file-earmark-spreadsheet",
            "ppt": "bi-file-earmark-slides",
            "pptx": "bi-file-earmark-slides",
            "jpg": "bi-file-earmark-image",
            "jpeg": "bi-file-earmark-image",
            "png": "bi-file-earmark-image",
            "webp": "bi-file-earmark-image",
            "tif": "bi-file-earmark-image",
            "tiff": "bi-file-earmark-image",
            "msg": "bi-envelope",
            "eml": "bi-envelope",
            "zip": "bi-file-earmark-zip",
            "txt": "bi-file-earmark-text",
        }

        return icon_map.get(
            extension,
            "bi-file-earmark",
        )


    def build_attachment_viewmodels(attachments):
        return [
            {
                "id": attachment.id,
                "name": attachment.original_filename,
                "extension": attachment.extension,
                "mime_type": attachment.mime_type,
                "size": format_attachment_size(
                    attachment.size_bytes
                ),
                "icon": get_attachment_icon(
                    attachment.extension
                ),
            }
            for attachment in attachments
        ]
    
    promises_by_id = {
        promise_link.promise_id: promise_link.promise
        for promise_link in promise_links
    }

    timeline_events = []

    for action in actions:
        metadata = action.metadata or {}

        owner_action_id = metadata.get(
            "attachment_owner_action_id"
        )

        try:
            owner_action_id = int(owner_action_id)
        except (TypeError, ValueError):
            owner_action_id = None

        attachment_owner = (
            attachment_owner_actions.get(owner_action_id)
            if owner_action_id
            else None
        )

        if attachment_owner:
            event_attachments = list(
                attachment_owner.attachments.all()
            )
        else:
            event_attachments = list(
                action.attachments.all()
            )

        promise = None

        if action.action_type == CollectionAction.ActionType.PROMISE:
            promise_id = (
                action.metadata or {}
            ).get(
                "payment_promise_id"
            )

            try:
                promise_id = int(promise_id)
            except (TypeError, ValueError):
                promise_id = None

            if promise_id:
                promise = promises_by_id.get(
                    promise_id
                )

            if promise:
                event_attachments.extend(
                    list(
                        promise.attachments.all()
                    )
                )

        unique_attachments = {
            attachment.id: attachment
            for attachment in event_attachments
        }

        timeline_events.append(
            {
                "type": "promise" if promise else "action",
                "date": action.action_date,
                "label": action.get_action_type_display(),
                "title": action.title,
                "description": action.description,
                "amount": (
                    promise.promised_amount
                    if promise
                    else None
                ),
                "promise_date": (
                    promise.promise_date
                    if promise
                    else None
                ),
                "user": action.performed_by,
                "icon": (
                    "bi-calendar-check"
                    if promise
                    else "bi-activity"
                ),
                "attachments": build_attachment_viewmodels(
                    unique_attachments.values()
                ),
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
        "document_supports": document_supports,
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


@login_required
def operational_attachment_download(request, attachment_id):
    attachment = get_object_or_404(
        OperationalAttachment.objects.select_related(
            "content_type",
            "uploaded_by",
        ),
        pk=attachment_id,
    )

    if not request.user.has_perm(
        "management.view_operationalattachment"
    ):
        raise Http404

    if attachment.attached_to is None:
        raise Http404

    try:
        return build_attachment_download_response(
            attachment,
        )
    except ValidationError as exc:
        raise Http404(str(exc)) from exc