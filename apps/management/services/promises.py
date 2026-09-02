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
    Document,
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


@transaction.atomic
def create_payment_promise_from_selection(
    *,
    form,
    customer,
    selected_documents,
    created_by=None,
    uploaded_files=None,
):
    """
    Crea una única promesa desde una selección común StatementDocument.

    La selección debe haber sido resuelta previamente mediante
    CustomerStatementService.resolve_selected_documents().

    Reglas:

    - source == "Document":
      conserva los efectos operacionales históricos:
      PromiseDocument + CollectionAction + estado/subestado.

    - source == "Fact_Vta_Sin_Vencer":
      persiste únicamente PromiseDocument con identidad ERP y snapshot.
      No crea Document, CollectionAction ni modifica cartera operacional.

    - promised_amount:
      suma los balance_amount de toda la selección.
    """
    if not form.is_bound:
        raise ValueError(
            "El formulario debe estar asociado a datos."
        )

    if form.errors:
        raise ValueError(
            "No se puede crear una promesa con un formulario inválido."
        )

    selected_documents = list(selected_documents)

    if not selected_documents:
        raise ValueError(
            "Debe seleccionar al menos un documento."
        )

    allowed_sources = {
        "Document",
        "Fact_Vta_Sin_Vencer",
    }

    invalid_sources = [
        item.source
        for item in selected_documents
        if item.source not in allowed_sources
    ]

    if invalid_sources:
        raise ValueError(
            "La selección contiene un origen de documento no soportado."
        )

    # -------------------------------------------------------------
    # Resolver nuevamente todos los Document reales dentro del
    # alcance del Customer. Nunca se confía solamente en document_id.
    # -------------------------------------------------------------

    real_items = [
        item
        for item in selected_documents
        if item.source == "Document"
    ]

    real_document_ids = [
        item.document_id
        for item in real_items
    ]

    if any(
        document_id is None
        for document_id in real_document_ids
    ):
        raise ValueError(
            "Un elemento de origen Document no contiene document_id."
        )

    real_documents_by_id = {
        document.id: document
        for document in (
            Document.objects
            .filter(
                customer=customer,
                id__in=real_document_ids,
            )
            .select_related(
                "customer",
                "status",
                "sub_status",
            )
        )
    }

    if len(real_documents_by_id) != len(set(real_document_ids)):
        raise ValueError(
            "Uno o más documentos seleccionados no existen "
            "o no pertenecen al cliente."
        )

    # Validación defensiva adicional contra inconsistencias entre
    # StatementDocument y Document.
    for item in real_items:
        document = real_documents_by_id[item.document_id]

        if (
            str(document.trans_id or "").strip()
            != str(item.trans_id or "").strip()
        ):
            raise ValueError(
                "La identidad ERP de un documento seleccionado cambió."
            )

        if (
            str(document.source_doc_entry or "").strip()
            != str(item.doc_entry or "").strip()
        ):
            raise ValueError(
                "La identidad ERP de un documento seleccionado cambió."
            )

        if (
            str(document.document_number or "").strip()
            != str(item.document_number or "").strip()
        ):
            raise ValueError(
                "La identidad ERP de un documento seleccionado cambió."
            )

    # -------------------------------------------------------------
    # Validar identidad de externos antes de crear PaymentPromise.
    # -------------------------------------------------------------

    external_items = [
        item
        for item in selected_documents
        if item.source == "Fact_Vta_Sin_Vencer"
    ]

    if external_items and not str(
        customer.external_id or ""
    ).strip():
        raise ValueError(
            "El cliente no posee identidad ERP externa."
        )

    for item in external_items:
        required = (
            item.trans_id,
            item.doc_entry,
            item.document_number,
        )

        if any(
            not str(value or "").strip()
            for value in required
        ):
            raise ValueError(
                "Un documento no vencido no posee identidad ERP completa."
            )

        if item.document_id is not None:
            raise ValueError(
                "Un documento Fact_Vta_Sin_Vencer no debe "
                "tener document_id."
            )

    promised_amount = sum(
        (
            item.balance_amount or 0
            for item in selected_documents
        ),
        0,
    )

    if promised_amount <= 0:
        raise ValueError(
            "El monto comprometido debe ser mayor que cero."
        )

    promise = form.save(commit=False)
    promise.customer = customer
    promise.promised_amount = promised_amount
    promise.status = PaymentPromise.Status.ACTIVE
    promise.created_by = created_by

    promise.full_clean()
    promise.save()

    # -------------------------------------------------------------
    # Relaciones reales.
    # Mantener el comportamiento histórico.
    # -------------------------------------------------------------

    real_relations = []

    for item in real_items:
        document = real_documents_by_id[item.document_id]

        relation = PromiseDocument(
            promise=promise,
            document=document,
        )
        relation.full_clean()
        relation.save()
        real_relations.append(relation)

    # -------------------------------------------------------------
    # Relaciones externas.
    # Solo identidad persistente + snapshot histórico.
    # -------------------------------------------------------------

    external_relations = []

    for item in external_items:
        relation = PromiseDocument(
            promise=promise,
            document=None,
            source="Fact_Vta_Sin_Vencer",
            source_customer_external_id=str(
                customer.external_id or ""
            ).strip(),
            source_trans_id=str(
                item.trans_id or ""
            ).strip(),
            source_doc_entry=str(
                item.doc_entry or ""
            ).strip(),
            source_document_number=str(
                item.document_number or ""
            ).strip(),
            source_due_date=item.due_date,
            source_original_amount=item.original_amount,
            source_balance_amount=item.balance_amount,
        )

        # Importante:
        # objects.create()/bulk_create no garantizan esta validación.
        relation.full_clean()
        relation.save()

        external_relations.append(relation)

    # -------------------------------------------------------------
    # Efectos operacionales SOLO sobre Document real.
    # -------------------------------------------------------------

    status_payment_scheduled = find_active_catalog_item(
        DocumentStatus,
        DOCUMENT_STATUS_PAYMENT_SCHEDULED,
    )

    substatus_active_promise = find_active_catalog_item(
        DocumentSubStatus,
        DOCUMENT_SUBSTATUS_ACTIVE_PROMISE,
    )

    selected_document_numbers = [
        item.document_number
        for item in selected_documents
    ]

    base_metadata = {
        "payment_promise_id": promise.id,
        "promise_date": promise.promise_date.isoformat(),
        "promised_amount": str(promise.promised_amount),
        "selected_document_count": len(selected_documents),
        "selected_document_numbers": selected_document_numbers,
        "selected_external_document_count": len(external_items),
        "selected_operational_document_count": len(real_items),
    }

    actions = []

    for item in real_items:
        document = real_documents_by_id[item.document_id]

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
        "selected_documents": selected_documents,
        "real_relations": real_relations,
        "external_relations": external_relations,
        "attachment_count": len(files),
    }

