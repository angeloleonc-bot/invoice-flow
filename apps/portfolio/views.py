from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from decimal import Decimal
from datetime import datetime, time
from .models import Customer, CustomerContact, Document, DocumentAssignment, PaymentRecord
from apps.management.models import CollectionAction, PaymentPromise
from apps.management.services.operational_portfolio import OperationalPortfolioService
from .services.workload import WorkloadRecommendationService, WorkloadService
from apps.portfolio.models import (
    CreditNoteApplication,
    ManualReconciliationApplication,
)
from apps.management.forms import CollectionActionForm, WorkspacePaymentPromiseForm
from django.core.exceptions import ValidationError
from apps.management.services.actions import (
    create_collection_action_batch,
)

from apps.portfolio.models import CustomerStatement
from apps.portfolio.services.customer_statements import (
    CATEGORY_DUE_TODAY,
    CATEGORY_OVERDUE,
    CATEGORY_UPCOMING,
    CustomerStatementService,
)
from apps.management.services.promises import (
    create_payment_promise_batch,
    create_payment_promise_from_selection,
)

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.utils.dateparse import parse_date



def _build_customer_last_management(
    *,
    actions,
    sent_customer_statements,
):
    """
    Devuelve el último hito comercial visible del cliente.

    Fuentes válidas:
    - CollectionAction;
    - CustomerStatement efectivamente enviado.

    CustomerStatement no se transforma en CollectionAction: continúa
    siendo un evento de cliente independiente y no se asocia
    artificialmente a un documento.
    """

    last_management = None

    latest_action = max(
        actions,
        key=lambda action: (
            action.action_date
            or action.created_at
        ),
        default=None,
    )

    if latest_action:
        action_user = latest_action.performed_by

        if action_user:
            action_user_label = (
                action_user.get_full_name()
                or action_user.email
                or action_user.username
            )
        else:
            action_user_label = "Sistema"

        last_management = {
            "type": "collection_action",
            "date": (
                latest_action.action_date
                or latest_action.created_at
            ),
            "title": (
                latest_action.title
                or "Gestión registrada"
            ),
            "description": (
                latest_action.description
                or ""
            ),
            "user_label": action_user_label,
        }

    latest_statement = next(
        iter(sent_customer_statements),
        None,
    )

    if latest_statement:
        statement_date = (
            latest_statement.sent_at
            or latest_statement.updated_at
        )

        current_date = (
            last_management["date"]
            if last_management
            else None
        )

        if (
            statement_date
            and (
                current_date is None
                or statement_date > current_date
            )
        ):
            recipient_text = ", ".join(
                latest_statement.to_emails or []
            )

            statement_user = latest_statement.created_by

            if statement_user:
                statement_user_label = (
                    statement_user.get_full_name()
                    or statement_user.email
                    or statement_user.username
                )
            else:
                statement_user_label = "Sistema"

            description_parts = []

            if recipient_text:
                description_parts.append(
                    f"Para: {recipient_text}"
                )

            description_parts.append(
                (
                    f"{latest_statement.document_count} "
                    f"documento"
                    f"{'s' if latest_statement.document_count != 1 else ''}"
                )
            )

            last_management = {
                "type": "customer_statement",
                "date": statement_date,
                "title": "Estado de cuenta enviado",
                "description": " · ".join(
                    description_parts
                ),
                "user_label": statement_user_label,
            }

    return last_management

def _promise_relation_document_number(relation):
    """
    Devuelve el número de documento visible de una PromiseDocument.

    - Relaciones históricas/operacionales:
      usa Document.document_number.

    - Relaciones anticipadas:
      usa el snapshot source_document_number.

    Nunca exige que relation.document exista.
    """
    if relation.document_id is not None and relation.document is not None:
        return str(
            relation.document.document_number or ""
        ).strip()

    return str(
        relation.source_document_number or ""
    ).strip()


def documents_list(request):
    search_query = request.GET.get("q", "").strip()
    requested_status = request.GET.get("status", "").strip()
    selected_balance = request.GET.get("balance", "").strip()

    requested_due_from = request.GET.get("due_from", "").strip()
    requested_due_to = request.GET.get("due_to", "").strip()

    due_from = parse_date(requested_due_from)
    due_to = parse_date(requested_due_to)

    allowed_balance_filters = {
        "",
        "pending",
        "paid",
        "overpayment",
    }

    if selected_balance not in allowed_balance_filters:
        selected_balance = ""

    allowed_sort_fields = {
        "document": "document_number",
        "customer": "customer__name",
        "status": "status__name",
        "balance": "balance_amount",
        "due_date": "due_date",
    }

    requested_sort = request.GET.get(
        "sort",
        "due_date",
    ).strip()

    requested_direction = request.GET.get(
        "dir",
        "asc",
    ).strip().lower()

    selected_sort = (
        requested_sort
        if requested_sort in allowed_sort_fields
        else "due_date"
    )

    selected_direction = (
        requested_direction
        if requested_direction in {"asc", "desc"}
        else "asc"
    )

    allowed_page_sizes = {
        "25",
        "50",
        "100",
    }

    selected_page_size = request.GET.get(
        "page_size",
        "25",
    ).strip()

    if selected_page_size not in allowed_page_sizes:
        selected_page_size = "25"

    documents = (
        Document.objects
        .select_related(
            "customer",
            "status",
            "sub_status",
        )
        .prefetch_related("tags")
    )

    if search_query:
        documents = documents.filter(
            Q(document_number__icontains=search_query)
            | Q(customer__name__icontains=search_query)
            | Q(customer__rut__icontains=search_query)
        )

    selected_status = ""

    if requested_status:
        try:
            status_id = int(requested_status)
        except (TypeError, ValueError):
            status_id = None

        if status_id is not None:
            status_exists = (
                Document.objects
                .filter(status_id=status_id)
                .exists()
            )

            if status_exists:
                selected_status = str(status_id)
                documents = documents.filter(
                    status_id=status_id,
                )

    if selected_balance == "pending":
        documents = documents.filter(
            balance_amount__gt=0,
        )

    elif selected_balance == "paid":
        documents = documents.filter(
            balance_amount=0,
        )

    elif selected_balance == "overpayment":
        documents = documents.filter(
            overpayment_amount__gt=0,
        )

    if due_from is not None:
        documents = documents.filter(
            due_date__gte=due_from,
        )

    if due_to is not None:
        documents = documents.filter(
            due_date__lte=due_to,
        )

    order_field = allowed_sort_fields[selected_sort]

    if selected_direction == "desc":
        order_field = f"-{order_field}"

    documents = documents.order_by(
        order_field,
        "customer__name",
        "document_number",
    )

    status_options = list(
        Document.objects
        .filter(status__isnull=False)
        .values(
            "status_id",
            "status__name",
        )
        .distinct()
        .order_by("status__name")
    )

    paginator = Paginator(
        documents,
        int(selected_page_size),
    )

    page_obj = paginator.get_page(
        request.GET.get("page", "1")
    )

    documents = page_obj.object_list

    pagination_params = request.GET.copy()
    pagination_params.pop("page", None)

    pagination_query = urlencode(
        pagination_params,
        doseq=True,
    )

    def build_sort_url(sort_key, default_direction="asc"):
        params = request.GET.copy()
        params.pop("page", None)

        if (
            selected_sort == sort_key
            and selected_direction == "asc"
        ):
            next_direction = "desc"

        elif (
            selected_sort == sort_key
            and selected_direction == "desc"
        ):
            next_direction = "asc"

        else:
            next_direction = default_direction

        params["sort"] = sort_key
        params["dir"] = next_direction

        return f"?{urlencode(params, doseq=True)}"

    sort_urls = {
        "document": build_sort_url(
            "document",
            default_direction="asc",
        ),
        "customer": build_sort_url(
            "customer",
            default_direction="asc",
        ),
        "status": build_sort_url(
            "status",
            default_direction="asc",
        ),
        "balance": build_sort_url(
            "balance",
            default_direction="desc",
        ),
        "due_date": build_sort_url(
            "due_date",
            default_direction="asc",
        ),
    }

    context = {
        "documents": documents,
        "page_obj": page_obj,
        "pagination_query": pagination_query,
        "total_documents": paginator.count,

        "search_query": search_query,
        "selected_status": selected_status,
        "selected_balance": selected_balance,
        "selected_due_from": (
            requested_due_from
            if due_from is not None
            else ""
        ),
        "selected_due_to": (
            requested_due_to
            if due_to is not None
            else ""
        ),

        "selected_sort": selected_sort,
        "selected_direction": selected_direction,
        "selected_page_size": selected_page_size,
        "status_options": status_options,
        "sort_urls": sort_urls,
    }

    return render(
        request,
        "portfolio/documents_list.html",
        context,
    )

def customers_list(request):
    search_query = request.GET.get(
        "q",
        "",
    ).strip()

    requested_cluster = request.GET.get(
        "cluster",
        "",
    ).strip()

    selected_documents = request.GET.get(
        "documents",
        "",
    ).strip()

    selected_balance = request.GET.get(
        "balance",
        "",
    ).strip()

    allowed_document_filters = {
        "",
        "with_documents",
        "without_documents",
    }

    if selected_documents not in allowed_document_filters:
        selected_documents = ""

    allowed_balance_filters = {
        "",
        "pending",
        "without_balance",
    }

    if selected_balance not in allowed_balance_filters:
        selected_balance = ""

    allowed_sort_fields = {
        "customer": "name",
        "rut": "rut",
        "cluster": "cluster",
        "documents": "documents_count",
        "balance": "total_balance",
    }

    requested_sort = request.GET.get(
        "sort",
        "customer",
    ).strip()

    requested_direction = request.GET.get(
        "dir",
        "asc",
    ).strip().lower()

    selected_sort = (
        requested_sort
        if requested_sort in allowed_sort_fields
        else "customer"
    )

    selected_direction = (
        requested_direction
        if requested_direction in {"asc", "desc"}
        else "asc"
    )

    allowed_page_sizes = {
        "25",
        "50",
        "100",
    }

    selected_page_size = request.GET.get(
        "page_size",
        "25",
    ).strip()

    if selected_page_size not in allowed_page_sizes:
        selected_page_size = "25"

    customers = Customer.objects.annotate(
        documents_count=Count(
            "documents",
            distinct=True,
        ),
        total_balance=Sum(
            "documents__balance_amount",
        ),
    )

    if search_query:
        customers = customers.filter(
            Q(name__icontains=search_query)
            | Q(rut__icontains=search_query)
            | Q(email__icontains=search_query)
        )

    cluster_options = list(
        Customer.objects
        .exclude(cluster__isnull=True)
        .exclude(cluster="")
        .values_list(
            "cluster",
            flat=True,
        )
        .distinct()
        .order_by("cluster")
    )

    selected_cluster = ""

    if (
        requested_cluster
        and requested_cluster in cluster_options
    ):
        selected_cluster = requested_cluster

        customers = customers.filter(
            cluster=selected_cluster,
        )

    if selected_documents == "with_documents":
        customers = customers.filter(
            documents_count__gt=0,
        )

    elif selected_documents == "without_documents":
        customers = customers.filter(
            documents_count=0,
        )

    if selected_balance == "pending":
        customers = customers.filter(
            total_balance__gt=0,
        )

    elif selected_balance == "without_balance":
        customers = customers.filter(
            Q(total_balance=0)
            | Q(total_balance__isnull=True)
        )

    order_field = allowed_sort_fields[selected_sort]

    if selected_direction == "desc":
        order_field = f"-{order_field}"

    customers = customers.order_by(
        order_field,
        "name",
    )

    paginator = Paginator(
        customers,
        int(selected_page_size),
    )

    page_obj = paginator.get_page(
        request.GET.get("page", "1")
    )

    customers = page_obj.object_list


    # -----------------------------------------------------------------
    # Ultima gestion visible por cliente.
    #
    # Considera exclusivamente:
    # - CollectionAction comerciales definidas centralmente en
    #   OperationalPortfolioService;
    # - CustomerStatement efectivamente enviados.
    #
    # Los eventos tecnicos de OperationalAlert quedan excluidos.
    #
    # Se calcula solo para los clientes de la pagina actual para evitar
    # enriquecer innecesariamente los 300+ clientes del listado.
    # -----------------------------------------------------------------

    page_customer_ids = [
        customer.id
        for customer in customers
    ]

    action_type_labels = dict(
        CollectionAction.ActionType.choices
    )

    latest_actions_by_customer = {}

    if page_customer_ids:
        latest_actions = (
            OperationalPortfolioService
            .collection_actions()
            .filter(
                customer_id__in=page_customer_ids,
            )
            .values(
                "customer_id",
                "action_date",
                "action_type",
                "created_at",
            )
            .order_by(
                "customer_id",
                "-action_date",
                "-created_at",
            )
        )

        for action in latest_actions:
            customer_id = action["customer_id"]

            if customer_id not in latest_actions_by_customer:
                latest_actions_by_customer[customer_id] = action

    latest_statements_by_customer = {}

    if page_customer_ids:
        latest_statements = (
            CustomerStatement.objects
            .filter(
                customer_id__in=page_customer_ids,
                status=CustomerStatement.Status.SENT,
                sent_at__isnull=False,
            )
            .values(
                "customer_id",
                "sent_at",
                "created_at",
            )
            .order_by(
                "customer_id",
                "-sent_at",
                "-created_at",
            )
        )

        for statement in latest_statements:
            customer_id = statement["customer_id"]

            if customer_id not in latest_statements_by_customer:
                latest_statements_by_customer[customer_id] = statement

    for customer in customers:

        latest_action = latest_actions_by_customer.get(
            customer.id
        )

        latest_statement = latest_statements_by_customer.get(
            customer.id
        )

        action_date = (
            latest_action["action_date"]
            if latest_action
            else None
        )

        statement_date = (
            latest_statement["sent_at"]
            if latest_statement
            else None
        )

        customer.latest_management_date = None
        customer.latest_management_title = "Sin gestion"
        customer.latest_management_type = ""

        if statement_date and (
            not action_date
            or statement_date > action_date
        ):
            customer.latest_management_date = statement_date
            customer.latest_management_title = "Estado de cuenta"
            customer.latest_management_type = "customer_statement"

        elif action_date:
            customer.latest_management_date = action_date
            customer.latest_management_title = (
                action_type_labels.get(
                    latest_action["action_type"],
                    latest_action["action_type"],
                )
                or "Gestion registrada"
            )
            customer.latest_management_type = "collection_action"


    pagination_params = request.GET.copy()
    pagination_params.pop("page", None)

    pagination_query = urlencode(
        pagination_params,
        doseq=True,
    )

    def build_sort_url(
        sort_key,
        default_direction="asc",
    ):
        params = request.GET.copy()
        params.pop("page", None)

        if (
            selected_sort == sort_key
            and selected_direction == "asc"
        ):
            next_direction = "desc"

        elif (
            selected_sort == sort_key
            and selected_direction == "desc"
        ):
            next_direction = "asc"

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
        "rut": build_sort_url(
            "rut",
            default_direction="asc",
        ),
        "cluster": build_sort_url(
            "cluster",
            default_direction="asc",
        ),
        "documents": build_sort_url(
            "documents",
            default_direction="desc",
        ),
        "balance": build_sort_url(
            "balance",
            default_direction="desc",
        ),
    }

    context = {
        "customers": customers,
        "page_obj": page_obj,
        "pagination_query": pagination_query,
        "total_customers": paginator.count,

        "search_query": search_query,
        "cluster_options": cluster_options,
        "selected_cluster": selected_cluster,
        "selected_documents": selected_documents,
        "selected_balance": selected_balance,

        "selected_sort": selected_sort,
        "selected_direction": selected_direction,
        "selected_page_size": selected_page_size,
        "sort_urls": sort_urls,
    }

    return render(
        request,
        "portfolio/customers_list.html",
        context,
    )


def customer_detail(request, customer_id):
    today = timezone.localdate()

    customer = get_object_or_404(Customer, id=customer_id)

    active_customer_assignments = (
        DocumentAssignment.objects
        .filter(
            document__customer=customer,
            is_active=True,
            assigned_to__is_active=True,
        )
        .select_related("assigned_to")
        .order_by(
            "assigned_to__first_name",
            "assigned_to__last_name",
            "assigned_to__username",
        )
    )

    customer_responsibles = []
    seen_responsible_ids = set()

    for assignment in active_customer_assignments:
        responsible = assignment.assigned_to

        if responsible.id in seen_responsible_ids:
            continue

        seen_responsible_ids.add(responsible.id)

        display_name = responsible.get_full_name().strip()

        customer_responsibles.append(
            {
                "id": responsible.id,
                "display_name": display_name or responsible.username,
                "username": responsible.username,
            }
        )

    action_form = CollectionActionForm()
    promise_form = WorkspacePaymentPromiseForm()

    documents_base = (
        Document.objects.filter(customer=customer)
        .select_related("customer", "status", "sub_status")
        .prefetch_related(
            "tags",
            "credit_note_applications",
            "payments",
            "manual_reconciliations",
            "promise_documents__promise",
            "collection_actions",
        )
        .order_by("due_date", "document_number")
    )

    open_documents = documents_base.filter(balance_amount__gt=0).order_by(
        "-balance_amount",
        "due_date",
    )
    latest_documents = open_documents[:10]
    recommended_document = open_documents.first()

    # -----------------------------------------------------------------
    # FORMULARIOS + PROCESAMIENTO POST
    # -----------------------------------------------------------------

    action_form = CollectionActionForm()
    promise_form = WorkspacePaymentPromiseForm()

    if request.method == "POST":
        form_type = request.POST.get("form_type")

        if form_type == "action":
            raw_document_ids = request.POST.getlist("document_id")

            try:
                document_ids = list(
                    dict.fromkeys(
                        int(document_id)
                        for document_id in raw_document_ids
                    )
                )
            except (TypeError, ValueError):
                messages.error(
                    request,
                    "La selección de documentos no es válida.",
                )
                return redirect(
                    "portfolio:customer_detail",
                    customer_id=customer.id,
                )

            if not document_ids:
                messages.error(
                    request,
                    "Debe seleccionar al menos un documento.",
                )
                return redirect(
                    "portfolio:customer_detail",
                    customer_id=customer.id,
                )

            action_form = CollectionActionForm(request.POST)

            if action_form.is_valid():
                selected_documents = list(
                    Document.objects.filter(
                        customer=customer,
                        id__in=document_ids,
                    ).order_by(
                        "due_date",
                        "document_number",
                    )
                )

                if len(selected_documents) != len(document_ids):
                    messages.error(
                        request,
                        (
                            "Uno o más documentos seleccionados no existen "
                            "o no pertenecen al cliente."
                        ),
                    )
                    return redirect(
                        "portfolio:customer_detail",
                        customer_id=customer.id,
                    )

                uploaded_files = request.FILES.getlist("attachments")

                try:
                    result = create_collection_action_batch(
                        form=action_form,
                        customer=customer,
                        documents=selected_documents,
                        performed_by=(
                            request.user
                            if request.user.is_authenticated
                            else None
                        ),
                        uploaded_files=uploaded_files,
                    )

                except Exception as exc:
                    messages.error(
                        request,
                        f"No fue posible registrar la gestión: {exc}",
                    )

                else:
                    actions = result["actions"]
                    attachments_count = (
                        result["owner_action"].attachments.count()
                    )

                    success_message = (
                        f"Gestión registrada en {len(actions)} documento(s)."
                    )

                    if attachments_count:
                        success_message += (
                            f" Se cargaron {attachments_count} archivo(s)."
                        )

                    messages.success(
                        request,
                        success_message,
                    )

                    return redirect(
                        "portfolio:customer_detail",
                        customer_id=customer.id,
                    )

        elif form_type == "promise":
            selected_keys = request.POST.getlist("selection_keys")

            promise_form = WorkspacePaymentPromiseForm(
                request.POST,
                request.FILES,
            )

            if promise_form.is_valid():
                try:
                    selected_documents = (
                        CustomerStatementService
                        .resolve_selected_documents(
                            customer=customer,
                            selected_keys=selected_keys,
                        )
                    )

                    result = create_payment_promise_from_selection(
                        form=promise_form,
                        customer=customer,
                        selected_documents=selected_documents,
                        created_by=(
                            request.user
                            if request.user.is_authenticated
                            else None
                        ),
                        uploaded_files=request.FILES.getlist(
                            "attachments"
                        ),
                    )

                except (ValueError, ValidationError) as exc:
                    messages.error(
                        request,
                        str(exc),
                    )

                else:
                    attachment_count = result["attachment_count"]

                    if attachment_count:
                        success_message = (
                            "Compromiso registrado correctamente con "
                            f"{attachment_count} archivo"
                            f"{'s' if attachment_count != 1 else ''}."
                        )
                    else:
                        success_message = (
                            "Compromiso registrado correctamente."
                        )

                    messages.success(
                        request,
                        success_message,
                    )

                    return redirect(
                        "portfolio:customer_detail",
                        customer_id=customer.id,
                    )

    promise_selection_documents = (
        CustomerStatementService.available_documents(
            customer=customer,
        )
    )

    promise_overdue_documents = [
        item
        for item in promise_selection_documents
        if item.category == CATEGORY_OVERDUE
    ]

    promise_due_today_documents = [
        item
        for item in promise_selection_documents
        if item.category == CATEGORY_DUE_TODAY
    ]

    promise_upcoming_documents = [
        item
        for item in promise_selection_documents
        if item.category == CATEGORY_UPCOMING
    ]

    account_view = request.GET.get("view", "pending")

    if account_view == "paid":
        documents = documents_base.filter(balance_amount=0)
    elif account_view == "partial":
        documents = documents_base.filter(
            balance_amount__gt=0,
            payments__isnull=False,
        ).distinct()
    elif account_view == "promises":
        documents = documents_base.filter(
            promise_documents__promise__status="ACTIVE",
        ).distinct()
    elif account_view == "all":
        documents = documents_base
    else:
        account_view = "pending"
        documents = documents_base.filter(balance_amount__gt=0)

    account_show_all = request.GET.get("show") == "all"
    account_documents_count = documents.count()

    if not account_show_all:
        documents = documents[:10]

    document_ids = list(documents_base.values_list("id", flat=True))

    credit_notes = (
        CreditNoteApplication.objects.filter(document_id__in=document_ids)
        .select_related("document", "customer")
        .order_by("-issue_date", "-created_at")
    )

    credit_note_kpis = credit_notes.aggregate(
        total_credit_notes=Sum("credit_amount"),
        total_credit_note_count=Count("id"),
    )

    manual_reconciliations = (
        ManualReconciliationApplication.objects
        .filter(customer=customer)
        .select_related(
            "document",
            "customer",
        )
        .order_by(
            "-timeline_order_at",
            "-created_at",
        )
    )

    manual_reconciliation_kpis = (
        manual_reconciliations.aggregate(
            total_manual_reconciliations_source=Sum("amount"),
            total_manual_reconciliation_count=Count("id"),
        )
    )

    total_manual_reconciliations_applied = Decimal("0")

    document_kpis = documents_base.aggregate(
        total_balance=Sum("balance_amount"),
        total_documents=Count("id"),
        overdue_documents=Count("id", filter=Q(due_date__lt=today)),
        total_overpayment=Sum("overpayment_amount"),
    )

    promises = (
        PaymentPromise.objects.filter(customer=customer)
        .select_related(
            "customer",
            "created_by",
        )
        .prefetch_related(
            "promise_documents__document",
            "attachments",
        )
        .order_by(
            "-promise_date",
            "-created_at",
        )
    )

    promise_kpis = promises.aggregate(
        active_promises=Count("id", filter=Q(status="ACTIVE")),
        expired_promises=Count("id", filter=Q(status="EXPIRED")),
    )

    actions = list(
        CollectionAction.objects
        .filter(document_id__in=document_ids)
        .select_related(
            "document",
            "performed_by",
        )
        .prefetch_related(
            "attachments",
        )
        .order_by("-created_at")
    )

    payments = (
        PaymentRecord.objects.filter(customer=customer)
        .select_related("document", "customer")
        .order_by("-payment_date", "-created_at")
    )

    total_paid = payments.aggregate(total=Sum("amount"))["total"] or 0

    latest_payments = payments[:10]
    latest_credit_notes = credit_notes[:10]
    latest_promises = promises[:10]
    latest_actions = actions[:10]

    contacts = CustomerContact.objects.filter(customer=customer).order_by("name")
    last_action = actions[0] if actions else None

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

    latest_promises = list(promises[:10])

    for promise in latest_promises:
        promise.attachment_viewmodels = (
            build_attachment_viewmodels(
                promise.attachments.all()
            )
        )

    timeline = []

    sent_customer_statements = (
        CustomerStatement.objects
        .filter(
            customer=customer,
            status=CustomerStatement.Status.SENT,
        )
        .select_related(
            "created_by",
        )
        .order_by(
            "-sent_at",
            "-created_at",
        )
    )

    last_management = _build_customer_last_management(
        actions=actions,
        sent_customer_statements=sent_customer_statements,
    )

    for statement in sent_customer_statements:

        recipient_text = ", ".join(
            statement.to_emails or []
        )

        cc_text = ", ".join(
            statement.cc_emails or []
        )

        description_parts = [
            f"Para: {recipient_text}",
            (
                f"{statement.document_count} documento"
                f"{'s' if statement.document_count != 1 else ''}"
            ),
            "PDF + Excel",
        ]

        if cc_text:
            description_parts.insert(
                1,
                f"CC: {cc_text}",
            )

        if statement.created_by:
            sender_name = (
                statement.created_by.get_full_name()
                or statement.created_by.email
                or statement.created_by.username
            )

            description_parts.append(
                f"Enviado por: {sender_name}"
            )

        timeline.append(
            {
                "type": "customer_statement",
                "label": "Estado de cuenta",
                "date": (
                    statement.sent_at
                    or statement.updated_at
                ),
                "title": "Estado de cuenta enviado",
                "description": " · ".join(
                    description_parts
                ),
                "document": None,
                "amount": statement.grand_total,
                "status": "Enviado",
                "reason": None,
                "attachments": [],
                "hide_date": False,
            }
        )

    processed_action_batches = set()

    for action in actions:
        metadata = action.metadata or {}
        batch_id = metadata.get("batch_id")

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

        attachment_viewmodels = build_attachment_viewmodels(
            event_attachments
        )

        if batch_id:
            if batch_id in processed_action_batches:
                continue

            processed_action_batches.add(batch_id)

            document_numbers = metadata.get(
                "selected_document_numbers",
                [],
            )

            selected_count = metadata.get(
                "selected_document_count",
                len(document_numbers),
            )

            description_parts = []

            if document_numbers:
                description_parts.append(
                    "Documentos gestionados: "
                    + ", ".join(document_numbers)
                )

            if action.description:
                description_parts.append(
                    f"Comentario: {action.description}"
                )

            timeline.append(
                {
                    "type": "gestion",
                    "label": "Gestión",
                    "date": action.action_date,
                    "title": (
                        f"{action.title} · "
                        f"{selected_count} documento"
                        f"{'s' if selected_count != 1 else ''}"
                    ),
                    "description": " · ".join(description_parts),
                    "document": None,
                    "amount": None,
                    "status": None,
                    "reason": None,
                    "attachments": attachment_viewmodels,
                }
            )

        else:
            timeline.append(
                {
                    "type": "gestion",
                    "label": "Gestión",
                    "date": action.action_date,
                    "title": action.title,
                    "description": action.description,
                    "document": action.document,
                    "amount": None,
                    "status": None,
                    "reason": None,
                    "attachments": attachment_viewmodels,

                }
            )

    for promise in promises:
        promise_docs = [
            document_number
            for relation in promise.promise_documents.all()
            if (
                document_number
                := _promise_relation_document_number(relation)
            )
        ]

        docs_text = ", ".join(promise_docs)

        description_parts = []

        if docs_text:
            description_parts.append(f"Documentos comprometidos: {docs_text}")

        if promise.notes:
            description_parts.append(f"Comentario: {promise.notes}")

        promise_attachment_viewmodels = (
            build_attachment_viewmodels(
                promise.attachments.all()
            )
        )

        timeline.append(
            {
                "type": "promesa",
                "label": "Promesa",
                "date": timezone.make_aware(
                    datetime.combine(promise.promise_date, time.min)
                ),
                "title": f"Compromiso de pago para el {promise.promise_date.strftime('%d/%m/%Y')}",
                "description": " · ".join(description_parts),
                "document": None,
                "amount": promise.promised_amount,
                "status": promise.get_status_display(),
                "reason": None,
                "attachments": promise_attachment_viewmodels,
            }
        )

    for payment in payments:
        timeline.append(
            {
                "type": "payment",
                "label": "Pago",
                "date": timezone.make_aware(
                    datetime.combine(payment.payment_date, time.min)
                ),
                "title": f"Pago aplicado a {payment.document.document_number}",
                "description": payment.source_reference or payment.notes,
                "document": payment.document,
                "amount": payment.amount,
                "status": None,
                "reason": None,
                "attachments": [],
            }
        )

    for nc in credit_notes:
        timeline.append(
            {
                "type": "credit_note",
                "label": "Nota de crédito",
                "date": (
                    timezone.make_aware(datetime.combine(nc.issue_date, time.min))
                    if nc.issue_date
                    else timezone.now()
                ),
                "title": f"NC {nc.credit_document_number} aplicada",
                "description": nc.comment,
                "document": nc.document,
                "amount": nc.credit_amount,
                "status": nc.status,
                "reason": nc.reason,
                "attachments": [],
            }
        )

    for reconciliation in manual_reconciliations:
        reconciliation_document = reconciliation.document

        reconciliation_credit_notes = (
            CreditNoteApplication.objects
            .filter(document=reconciliation_document)
            .aggregate(
                total=Coalesce(
                    Sum("credit_amount"),
                    Decimal("0"),
                )
            )["total"]
        )

        reconciliation_payments = (
            PaymentRecord.objects
            .filter(document=reconciliation_document)
            .aggregate(
                total=Coalesce(
                    Sum("amount"),
                    Decimal("0"),
                )
            )["total"]
        )

        balance_before_manual = (
            reconciliation_document.original_amount
            - reconciliation_credit_notes
            - reconciliation_payments
        )

        reconciliation_net_amount = max(
            reconciliation.amount
            - reconciliation_credit_notes,
            Decimal("0"),
        )

        reconciliation_applied_amount = min(
            reconciliation_net_amount,
            max(
                balance_before_manual,
                Decimal("0"),
            ),
        )

        total_manual_reconciliations_applied += (
            reconciliation_applied_amount
        )

        timeline.append(
            {
                "type": "manual_reconciliation",
                "label": "Reconciliación manual",
                "date": reconciliation.timeline_order_at,
                "title": "Reconciliación manual aplicada",
                "description": None,
                "document": reconciliation.document,
                "amount": reconciliation_applied_amount,
                "status": None,
                "reason": None,
                "attachments": [],
                "hide_date": True,
            }
        )

    timeline = sorted(
        timeline,
        key=lambda event: event["date"] or timezone.now(),
        reverse=True,
    )

    recent_show_all = request.GET.get("recent") == "all"
    recent_events_count = len(timeline)

    if not recent_show_all:
        timeline = timeline[:5]

    return render(
        request,
        "portfolio/customer_detail.html",
        {
            "customer": customer,
            "customer_responsibles": customer_responsibles,
            "documents": documents,
            "account_show_all": account_show_all,
            "account_documents_count": account_documents_count,
            "account_view": account_view,
            "open_documents": open_documents,
            "promise_selection_documents": promise_selection_documents,
            "promise_overdue_documents": promise_overdue_documents,
            "promise_due_today_documents": promise_due_today_documents,
            "promise_upcoming_documents": promise_upcoming_documents,
            "latest_documents": latest_documents,
            "latest_payments": latest_payments,
            "latest_credit_notes": latest_credit_notes,
            "latest_promises": latest_promises,
            "latest_actions": latest_actions,
            "total_overpayment": document_kpis["total_overpayment"] or 0,
            "promises": promises,
            "actions": actions,
            "contacts": contacts,
            "timeline": timeline,
            "total_balance": document_kpis["total_balance"] or 0,
            "total_documents": document_kpis["total_documents"] or 0,
            "overdue_documents": document_kpis["overdue_documents"] or 0,
            "active_promises": promise_kpis["active_promises"] or 0,
            "expired_promises": promise_kpis["expired_promises"] or 0,
            "last_action": last_action,
            "last_management": last_management,
            "payments": payments,
            "total_paid": total_paid,
            "credit_notes": credit_notes,
            "total_credit_notes": (
                credit_note_kpis["total_credit_notes"] or 0
            ),
            "total_credit_note_count": (
                credit_note_kpis[
                    "total_credit_note_count"
                ] or 0
            ),
            "manual_reconciliations": manual_reconciliations,
            "total_manual_reconciliations": (
                total_manual_reconciliations_applied
            ),
            "total_manual_reconciliations_source": (
                manual_reconciliation_kpis[
                    "total_manual_reconciliations_source"
                ] or 0
            ),
            "total_manual_reconciliation_count": (
                manual_reconciliation_kpis[
                    "total_manual_reconciliation_count"
                ] or 0
            ),
            "recommended_document": recommended_document,
            "recent_show_all": recent_show_all,
            "recent_events_count": recent_events_count,
            "action_form": action_form,
            "promise_form": promise_form,
        },
    )

def unassigned_documents(request):
    User = get_user_model()

    search_query = request.GET.get(
        "q",
        "",
    ).strip()

    requested_status = request.GET.get(
        "status",
        "",
    ).strip()

    selected_balance = request.GET.get(
        "balance",
        "",
    ).strip()

    requested_due_from = request.GET.get(
        "due_from",
        "",
    ).strip()

    requested_due_to = request.GET.get(
        "due_to",
        "",
    ).strip()

    due_from = parse_date(requested_due_from)
    due_to = parse_date(requested_due_to)

    allowed_balance_filters = {
        "",
        "pending",
        "paid",
        "overpayment",
    }

    if selected_balance not in allowed_balance_filters:
        selected_balance = ""

    allowed_sort_fields = {
        "document": "document_number",
        "customer": "customer__name",
        "status": "status__name",
        "balance": "balance_amount",
        "due_date": "due_date",
    }

    requested_sort = request.GET.get(
        "sort",
        "due_date",
    ).strip()

    requested_direction = request.GET.get(
        "dir",
        "asc",
    ).strip().lower()

    selected_sort = (
        requested_sort
        if requested_sort in allowed_sort_fields
        else "due_date"
    )

    selected_direction = (
        requested_direction
        if requested_direction in {"asc", "desc"}
        else "asc"
    )

    allowed_page_sizes = {
        "25",
        "50",
        "100",
    }

    selected_page_size = request.GET.get(
        "page_size",
        "25",
    ).strip()

    if selected_page_size not in allowed_page_sizes:
        selected_page_size = "25"

    collectors = (
        User.objects
        .filter(is_active=True)
        .order_by(
            "first_name",
            "last_name",
            "username",
        )
    )

    documents_base = (
        Document.objects
        .select_related(
            "customer",
            "status",
            "sub_status",
        )
        .filter(
            ~Q(assignments__is_active=True)
        )
        .distinct()
    )

    status_options = list(
        documents_base
        .filter(status__isnull=False)
        .values(
            "status_id",
            "status__name",
        )
        .distinct()
        .order_by("status__name")
    )

    allowed_status_ids = {
        str(status["status_id"])
        for status in status_options
    }

    selected_status = (
        requested_status
        if requested_status in allowed_status_ids
        else ""
    )

    documents = documents_base

    if search_query:
        documents = documents.filter(
            Q(document_number__icontains=search_query)
            | Q(customer__name__icontains=search_query)
            | Q(customer__rut__icontains=search_query)
        )

    if selected_status:
        documents = documents.filter(
            status_id=int(selected_status),
        )

    if selected_balance == "pending":
        documents = documents.filter(
            balance_amount__gt=0,
        )

    elif selected_balance == "paid":
        documents = documents.filter(
            balance_amount=0,
        )

    elif selected_balance == "overpayment":
        documents = documents.filter(
            overpayment_amount__gt=0,
        )

    if due_from is not None:
        documents = documents.filter(
            due_date__gte=due_from,
        )

    if due_to is not None:
        documents = documents.filter(
            due_date__lte=due_to,
        )

    total_documents = documents.count()

    total_balance = (
        documents.aggregate(
            total=Sum("balance_amount")
        )["total"]
        or 0
    )

    order_field = allowed_sort_fields[selected_sort]

    if selected_direction == "desc":
        order_field = f"-{order_field}"

    documents = documents.order_by(
        order_field,
        "customer__name",
        "document_number",
    )

    paginator = Paginator(
        documents,
        int(selected_page_size),
    )

    page_obj = paginator.get_page(
        request.GET.get("page", "1")
    )

    documents = page_obj.object_list

    pagination_params = request.GET.copy()
    pagination_params.pop("page", None)

    pagination_query = urlencode(
        pagination_params,
        doseq=True,
    )

    def build_sort_url(
        sort_key,
        default_direction="asc",
    ):
        params = request.GET.copy()
        params.pop("page", None)

        if (
            selected_sort == sort_key
            and selected_direction == "asc"
        ):
            next_direction = "desc"

        elif (
            selected_sort == sort_key
            and selected_direction == "desc"
        ):
            next_direction = "asc"

        else:
            next_direction = default_direction

        params["sort"] = sort_key
        params["dir"] = next_direction

        return f"?{urlencode(params, doseq=True)}"

    sort_urls = {
        "document": build_sort_url(
            "document",
            default_direction="asc",
        ),
        "customer": build_sort_url(
            "customer",
            default_direction="asc",
        ),
        "status": build_sort_url(
            "status",
            default_direction="asc",
        ),
        "balance": build_sort_url(
            "balance",
            default_direction="desc",
        ),
        "due_date": build_sort_url(
            "due_date",
            default_direction="asc",
        ),
    }

    workload_service = WorkloadService()
    workloads = workload_service.get_workloads()

    recommendation_service = WorkloadRecommendationService()
    recommendation = (
        recommendation_service.recommend_collector()
    )

    context = {
        "documents": documents,
        "collectors": collectors,
        "workloads": workloads,
        "recommendation": recommendation,

        "total_documents": total_documents,
        "total_balance": total_balance,

        "page_obj": page_obj,
        "pagination_query": pagination_query,

        "search_query": search_query,
        "status_options": status_options,
        "selected_status": selected_status,
        "selected_balance": selected_balance,

        "selected_due_from": (
            requested_due_from
            if due_from is not None
            else ""
        ),
        "selected_due_to": (
            requested_due_to
            if due_to is not None
            else ""
        ),

        "selected_sort": selected_sort,
        "selected_direction": selected_direction,
        "selected_page_size": selected_page_size,
        "sort_urls": sort_urls,
    }

    return render(
        request,
        "portfolio/unassigned_documents.html",
        context,
    )

def assignment_workloads(request):
    workload_service = WorkloadService()
    recommendation_service = WorkloadRecommendationService()

    workloads = workload_service.get_workloads()
    recommendation = recommendation_service.recommend_collector()

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
            "workloads": workloads,
            "recommendation": recommendation,
            "unassigned_documents_count": unassigned_documents_count,
            "unassigned_balance": unassigned_balance,
        },
    )

@login_required
def assign_documents(request):
    if request.method != "POST":
        return redirect("portfolio:unassigned_documents")

    collector_id = request.POST.get("collector_id")
    document_ids = request.POST.getlist("document_ids")

    if not collector_id:
        messages.error(request, "Debe seleccionar un cobrador.")
        return redirect("portfolio:unassigned_documents")

    if not document_ids:
        messages.error(request, "Debe seleccionar al menos un documento.")
        return redirect("portfolio:unassigned_documents")

    User = get_user_model()

    collector = get_object_or_404(
        User,
        pk=collector_id,
        is_active=True,
    )

    created_count = 0

    for document in Document.objects.filter(id__in=document_ids):
        has_active_assignment = DocumentAssignment.objects.filter(
            document=document,
            is_active=True,
        ).exists()

        if has_active_assignment:
            continue

        DocumentAssignment.objects.create(
            document=document,
            assigned_to=collector,
            assigned_by=request.user,
            assignment_type=DocumentAssignment.ASSIGNMENT_TYPE_INITIAL,
            is_active=True,
        )

        created_count += 1

    messages.success(
        request,
        f"{created_count} documento(s) asignado(s) correctamente.",
    )

    return redirect("portfolio:unassigned_documents")
