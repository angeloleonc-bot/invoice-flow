from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from datetime import datetime, time
from .models import Customer, CustomerContact, Document, DocumentAssignment, PaymentRecord
from apps.management.models import CollectionAction, PaymentPromise, PromiseDocument
from .services.workload import WorkloadRecommendationService, WorkloadService

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

    documents = (
        Document.objects.filter(customer=customer)
        .select_related("customer", "status", "sub_status")
        .prefetch_related("tags")
        .order_by("due_date", "document_number")
    )

    document_ids = list(documents.values_list("id", flat=True))

    document_kpis = documents.aggregate(
        total_balance=Sum("balance_amount"),
        total_documents=Count("id"),
        overdue_documents=Count("id", filter=Q(due_date__lt=today)),
    )

    promises = (
        PaymentPromise.objects.filter(customer=customer)
        .select_related("customer", "created_by")
        .prefetch_related("promise_documents__document")
        .order_by("-promise_date", "-created_at")
    )

    promise_kpis = promises.aggregate(
        active_promises=Count("id", filter=Q(status="ACTIVE")),
        expired_promises=Count("id", filter=Q(status="EXPIRED")),
    )

    actions = (
        CollectionAction.objects.filter(document_id__in=document_ids)
        .select_related("document", "performed_by")
        .order_by("-created_at")
    )

    payments = (
        PaymentRecord.objects.filter(customer=customer)
        .select_related("document", "customer")
        .order_by("-payment_date", "-created_at")
    )

    total_paid = payments.aggregate(total=Sum("amount"))["total"] or 0

    contacts = CustomerContact.objects.filter(customer=customer).order_by("name")

    last_action = actions.first()

    timeline = []

    for action in actions:
        timeline.append(
            {
                "type": "gestion",
                "label": "Gestión",
                "date": action.action_date,
                "title": action.title,
                "description": action.description,
                "document": action.document,
                "amount": None,
            }
        )

    for promise in promises:
        timeline.append(
            {
                "type": "promesa",
                "label": "Promesa",
                "date": timezone.make_aware(datetime.combine(promise.promise_date, time.min)),
                "title": promise.status,
                "description": promise.notes,
                "document": None,
                "amount": promise.promised_amount,
            }
        )

    timeline = sorted(
        timeline,
        key=lambda event: event["date"] or timezone.now(),
        reverse=True,
    )

    return render(
        request,
        "portfolio/customer_detail.html",
        {
            "customer": customer,
            "documents": documents,
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