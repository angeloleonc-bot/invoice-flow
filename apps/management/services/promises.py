from django.db import transaction

from apps.management.models import (
    CollectionAction,
    PaymentPromise,
    PromiseDocument,
)
from apps.management.services.attachments import (
    create_operational_attachments,
)
from apps.portfolio.models import (
    DocumentStatus,
    DocumentSubStatus,
)


DOCUMENT_STATUS_PAYMENT_SCHEDULED = "Pago programado"
DOCUMENT_SUBSTATUS_ACTIVE_PROMISE = "Promesa vigente"

def find_active_catalog_item(model, expected_name):
    expected_name = expected_name.strip().casefold()

    for item in model.objects.filter(is_active=True):
        if item.name.strip().casefold() == expected_name:
            return item

    return None


@transaction.atomic
def create_payment_promise(
    *,
    form,
    document,
    created_by=None,
    uploaded_files=None,
):
    """
    Crea una promesa de pago asociada a un documento.

    El formulario debe llegar previamente validado.

    También:
    - crea la relación PromiseDocument;
    - registra la acción automática en el timeline;
    - actualiza estado y subestado del documento;
    - carga los adjuntos operacionales en la promesa.
    """
    if not form.is_bound:
        raise ValueError(
            "El formulario debe estar asociado a datos."
        )

    if form.errors:
        raise ValueError(
            "No se puede crear una promesa con un formulario inválido."
        )

    promise = form.save(commit=False)
    promise.customer = document.customer
    promise.status = PaymentPromise.Status.ACTIVE
    promise.created_by = created_by

    promise.full_clean()
    promise.save()

    PromiseDocument.objects.create(
        promise=promise,
        document=document,
    )

    CollectionAction.objects.create(
        document=document,
        customer=document.customer,
        action_type=CollectionAction.ActionType.PROMISE,
        performed_by=created_by,
        title="Promesa de pago registrada",
        description=(
            f"Fecha compromiso: "
            f"{promise.promise_date.strftime('%d-%m-%Y')}\n"
            f"Monto comprometido: "
            f"$ {promise.promised_amount:,.0f}".replace(",", ".")
        ),
        metadata={
            "payment_promise_id": promise.id,
            "promise_date": promise.promise_date.isoformat(),
            "promised_amount": str(promise.promised_amount),
        },
    )

    status_payment_scheduled = find_active_catalog_item(
        DocumentStatus,
        DOCUMENT_STATUS_PAYMENT_SCHEDULED,
    )

    substatus_active_promise = find_active_catalog_item(
        DocumentSubStatus,
        DOCUMENT_SUBSTATUS_ACTIVE_PROMISE,
    )

    substatus_active_promise = (
        DocumentSubStatus.objects
        .filter(
            name__iexact=DOCUMENT_SUBSTATUS_ACTIVE_PROMISE,
            is_active=True,
        )
        .first()
    )

    changed_fields = []

    if status_payment_scheduled:
        document.status = status_payment_scheduled
        changed_fields.append("status")

    if substatus_active_promise:
        document.sub_status = substatus_active_promise
        changed_fields.append("sub_status")

    if changed_fields:
        changed_fields.append("updated_at")
        document.save(update_fields=changed_fields)

    files = list(uploaded_files or [])

    if files:
        create_operational_attachments(
            target=promise,
            uploaded_files=files,
            uploaded_by=created_by,
        )

    return promise

@transaction.atomic
def create_payment_promise_batch(
    *,
    form,
    customer,
    documents,
    created_by=None,
    uploaded_files=None,
):
    """
    Crea una única promesa de pago asociada a varios documentos.

    También:

    - valida que todos los documentos pertenezcan al cliente;
    - calcula el monto comprometido desde los saldos seleccionados;
    - crea las relaciones PromiseDocument;
    - registra una acción de promesa en cada documento;
    - actualiza estado y subestado de cada documento;
    - carga los adjuntos una sola vez sobre PaymentPromise.
    """
    if not form.is_bound:
        raise ValueError(
            "El formulario debe estar asociado a datos."
        )

    if form.errors:
        raise ValueError(
            "No se puede crear una promesa con un formulario inválido."
        )

    documents = list(documents)

    if not documents:
        raise ValueError(
            "Debe seleccionar al menos un documento."
        )

    invalid_documents = [
        document
        for document in documents
        if document.customer_id != customer.id
    ]

    if invalid_documents:
        raise ValueError(
            "Uno o más documentos no pertenecen al cliente."
        )

    promised_amount = sum(
        (
            document.balance_amount or 0
            for document in documents
        ),
        0,
    )

    promise = form.save(commit=False)
    promise.customer = customer
    promise.promised_amount = promised_amount
    promise.status = PaymentPromise.Status.ACTIVE
    promise.created_by = created_by

    promise.full_clean()
    promise.save()

    PromiseDocument.objects.bulk_create(
        [
            PromiseDocument(
                promise=promise,
                document=document,
            )
            for document in documents
        ]
    )

    status_payment_scheduled = find_active_catalog_item(
        DocumentStatus,
        DOCUMENT_STATUS_PAYMENT_SCHEDULED,
    )

    substatus_active_promise = find_active_catalog_item(
        DocumentSubStatus,
        DOCUMENT_SUBSTATUS_ACTIVE_PROMISE,
    )

    selected_document_numbers = [
        document.document_number
        for document in documents
    ]

    selected_document_count = len(documents)

    base_metadata = {
        "payment_promise_id": promise.id,
        "promise_date": promise.promise_date.isoformat(),
        "promised_amount": str(promise.promised_amount),
        "selected_document_count": selected_document_count,
        "selected_document_numbers": selected_document_numbers,
    }

    actions = []

    for document in documents:
        action = CollectionAction.objects.create(
            document=document,
            customer=customer,
            action_type=CollectionAction.ActionType.PROMISE,
            performed_by=created_by,
            title="Promesa de pago registrada",
            description=(
                f"Fecha compromiso: "
                f"{promise.promise_date.strftime('%d-%m-%Y')}\n"
                f"Monto comprometido: "
                f"$ {promise.promised_amount:,.0f}".replace(",", ".")
            ),
            metadata={
                **base_metadata,
                "document_id": document.id,
                "document_number": document.document_number,
            },
        )

        actions.append(action)

        changed_fields = []

        if status_payment_scheduled:
            document.status = status_payment_scheduled
            changed_fields.append("status")

        if substatus_active_promise:
            document.sub_status = substatus_active_promise
            changed_fields.append("sub_status")

        if changed_fields:
            changed_fields.append("updated_at")
            document.save(update_fields=changed_fields)

    files = list(uploaded_files or [])

    if files:
        create_operational_attachments(
            target=promise,
            uploaded_files=files,
            uploaded_by=created_by,
        )

    return {
        "promise": promise,
        "actions": actions,
        "documents": documents,
        "attachment_count": len(files),
    }