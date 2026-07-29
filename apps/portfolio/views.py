from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from datetime import datetime, time
from .models import Customer, CustomerContact, Document, DocumentAssignment, PaymentRecord
from apps.management.models import CollectionAction, PaymentPromise
from .services.workload import WorkloadRecommendationService, WorkloadService
from apps.portfolio.models import CreditNoteApplication
from apps.management.forms import CollectionActionForm, WorkspacePaymentPromiseForm
from django.core.exceptions import ValidationError
from apps.management.services.actions import (
    create_collection_action_batch,
)

from apps.management.services.promises import (
    create_payment_promise_batch,
)

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

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


def customer_detail(request, customer_id):
    today = timezone.localdate()

    customer = get_object_or_404(Customer, id=customer_id)
    action_form = CollectionActionForm()
    promise_form = WorkspacePaymentPromiseForm()

    documents_base = (
        Document.objects.filter(customer=customer)
        .select_related("customer", "status", "sub_status")
        .prefetch_related(
            "tags",
            "credit_note_applications",
            "payments",
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
            document_ids = request.POST.getlist("document_ids")

            if not document_ids:
                messages.error(request, "Debe seleccionar al menos un documento.")
                return redirect("portfolio:customer_detail", customer_id=customer.id)

            selected_documents = Document.objects.filter(
                customer=customer,
                id__in=document_ids,
            )

            promise_form = WorkspacePaymentPromiseForm(
                request.POST,
                request.FILES,
            )

            if promise_form.is_valid():
                try:
                    result = create_payment_promise_batch(
                        form=promise_form,
                        customer=customer,
                        documents=selected_documents,
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
            relation.document.document_number
            for relation in promise.promise_documents.all()
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
            "documents": documents,
            "account_show_all": account_show_all,
            "account_documents_count": account_documents_count,
            "account_view": account_view,
            "open_documents": open_documents,
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
            "payments": payments,
            "total_paid": total_paid,
            "credit_notes": credit_notes,
            "total_credit_notes": credit_note_kpis["total_credit_notes"] or 0,
            "total_credit_note_count": credit_note_kpis["total_credit_note_count"] or 0,
            "recommended_document": recommended_document,
            "recent_show_all": recent_show_all,
            "recent_events_count": recent_events_count,
            "action_form": action_form,
            "promise_form": promise_form,
        },
    )

def unassigned_documents(request):
    User = get_user_model()

    collectors = User.objects.filter(is_active=True).order_by(
        "first_name",
        "last_name",
        "username",
    )

    documents = (
        Document.objects.select_related(
            "customer",
            "status",
            "sub_status",
        )
        .filter(~Q(assignments__is_active=True))
        .distinct()
        .order_by(
            "due_date",
            "customer__name",
            "document_number",
        )
    )

    total_documents = documents.count()

    total_balance = (
        documents.aggregate(total=Sum("balance_amount"))["total"]
        or 0
    )

    workload_service = WorkloadService()
    workloads = workload_service.get_workloads()

    recommendation_service = WorkloadRecommendationService()
    recommendation = recommendation_service.recommend_collector()

    return render(
        request,
        "portfolio/unassigned_documents.html",
        {
            "documents": documents,
            "collectors": collectors,
            "workloads": workloads,
            "recommendation": recommendation,
            "total_documents": total_documents,
            "total_balance": total_balance,
        },
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