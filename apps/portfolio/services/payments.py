from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from apps.management.models import CollectionAction, PaymentPromise, PromiseDocument
from apps.portfolio.models import DocumentStatus, DocumentSubStatus, PaymentRecord


def _get_total_paid(document):
    return document.payments.aggregate(total=Sum("amount"))["total"] or Decimal("0")


def _get_or_create_status(name, sort_order=0):
    status, _ = DocumentStatus.objects.get_or_create(
        name=name,
        defaults={
            "is_active": True,
            "sort_order": sort_order,
        },
    )
    return status


def _get_or_create_sub_status(name, sort_order=0):
    sub_status, _ = DocumentSubStatus.objects.get_or_create(
        name=name,
        defaults={
            "is_active": True,
            "sort_order": sort_order,
        },
    )
    return sub_status


@transaction.atomic
def register_payment_record(
    *,
    document,
    payment_date,
    amount,
    source_reference="",
    external_payment_id=None,
    source_table="Pago_Vta_Reg",
    notes="",
    performed_by=None,
):
    payment = PaymentRecord.objects.create(
        document=document,
        customer=document.customer,
        payment_date=payment_date,
        amount=amount,
        source_reference=source_reference,
        external_payment_id=external_payment_id,
        source_table=source_table,
        notes=notes,
    )

    CollectionAction.objects.create(
        document=document,
        customer=document.customer,
        action_type=CollectionAction.ActionType.PAYMENT_INFO,
        performed_by=performed_by,
        title="Pago recibido",
        description=(
            f"Fecha pago: {payment.payment_date.strftime('%d-%m-%Y')}\n"
            f"Monto pago: {payment.amount}\n"
            f"Referencia externa: {payment.source_reference or 'Sin referencia'}"
        ),
        metadata={
            "payment_record_id": payment.id,
            "payment_date": payment.payment_date.isoformat(),
            "amount": str(payment.amount),
            "source_reference": payment.source_reference,
            "external_payment_id": payment.external_payment_id,
            "source_table": payment.source_table,
        },
    )

    update_document_payment_status(document)
    evaluate_related_promises(document, performed_by=performed_by)

    return payment


def update_document_payment_status(document):
    total_paid = _get_total_paid(document)
    remaining_balance = document.original_amount - total_paid

    if remaining_balance <= 0:
        document.balance_amount = Decimal("0")
        document.status = _get_or_create_status("Pagada", sort_order=90)
        document.sub_status = _get_or_create_sub_status("Pago total informado", sort_order=90)
    else:
        document.balance_amount = remaining_balance
        document.status = _get_or_create_status("Pago programado", sort_order=50)
        document.sub_status = _get_or_create_sub_status("Pago parcial informado", sort_order=50)

    document.save(update_fields=["balance_amount", "status", "sub_status", "updated_at"])


def evaluate_related_promises(document, performed_by=None):
    promise_links = (
        PromiseDocument.objects.select_related("promise")
        .filter(document=document)
        .distinct()
    )

    for link in promise_links:
        promise = link.promise
        related_documents = [
            relation.document
            for relation in PromiseDocument.objects.select_related("document").filter(promise=promise)
        ]

        if not related_documents:
            continue

        all_paid = True

        for related_document in related_documents:
            if _get_total_paid(related_document) < related_document.original_amount:
                all_paid = False
                break

        if all_paid and promise.status != PaymentPromise.Status.FULFILLED:
            promise.status = PaymentPromise.Status.FULFILLED
            promise.payment_confirmed = True
            promise.save(update_fields=["status", "payment_confirmed", "updated_at"])

            CollectionAction.objects.create(
                document=document,
                customer=document.customer,
                action_type=CollectionAction.ActionType.PROMISE,
                performed_by=performed_by,
                title="Promesa cumplida automáticamente",
                description="La promesa fue marcada como cumplida automáticamente por pagos informados.",
                metadata={
                    "payment_promise_id": promise.id,
                    "automatic_fulfillment": True,
                },
            )