from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.management.models import CollectionAction
from apps.management.services.attachments import (
    create_operational_attachments,
)


def _validate_bound_form(form):
    """
    Verifica que el formulario haya sido asociado a datos y que sea válido.

    El formulario debe haber ejecutado is_valid() antes de ingresar
    al servicio.
    """
    if not form.is_bound:
        raise ValueError(
            "El formulario debe estar asociado a datos antes "
            "de crear la gestión."
        )

    if form.errors:
        raise ValueError(
            "No se puede crear una gestión con un formulario inválido."
        )


def _normalize_batch_documents(*, customer, documents):
    """
    Normaliza y valida los documentos incluidos en una gestión en lote.

    Reglas:
    - debe existir al menos un documento;
    - todos deben estar persistidos;
    - todos deben pertenecer al cliente indicado;
    - no se admiten documentos duplicados.

    Conserva el orden recibido para que la acción propietaria pueda
    determinarse de forma estable y predecible.
    """
    normalized_documents = list(documents or [])

    if not normalized_documents:
        raise ValidationError(
            "Debe seleccionar al menos un documento para registrar "
            "la gestión."
        )

    seen_document_ids = set()

    for document in normalized_documents:
        if not document.pk:
            raise ValidationError(
                "Todos los documentos deben estar guardados antes "
                "de registrar la gestión."
            )

        if document.customer_id != customer.pk:
            raise ValidationError(
                "Todos los documentos seleccionados deben pertenecer "
                "al cliente de la gestión."
            )

        if document.pk in seen_document_ids:
            raise ValidationError(
                "La selección contiene documentos duplicados."
            )

        seen_document_ids.add(document.pk)

    return normalized_documents


@transaction.atomic
def create_collection_action(
    *,
    form,
    document,
    performed_by=None,
    uploaded_files=None,
):
    """
    Crea una gestión de cobranza y asocia sus adjuntos operacionales.

    La función espera un CollectionActionForm previamente validado.

    Args:
        form:
            CollectionActionForm válido.

        document:
            Documento sobre el cual se registra la gestión.

        performed_by:
            Usuario Django que ejecuta la gestión. Puede ser None
            para procesos internos o importaciones.

        uploaded_files:
            Iterable de archivos recibidos mediante request.FILES.getlist().

    Returns:
        CollectionAction:
            Gestión creada y persistida.

    Raises:
        ValueError:
            Si el formulario no ha sido validado correctamente.

        ValidationError:
            Si falla la validación o carga de algún adjunto.
    """
    _validate_bound_form(form)

    action = form.save(commit=False)

    action.document = document
    action.customer = document.customer
    action.performed_by = performed_by

    action.full_clean()
    action.save()

    files = list(uploaded_files or [])

    if files:
        create_operational_attachments(
            target=action,
            uploaded_files=files,
            uploaded_by=performed_by,
        )

    return action


@transaction.atomic
def create_collection_action_batch(
    *,
    form,
    customer,
    documents,
    performed_by=None,
    uploaded_files=None,
):
    """
    Crea una misma gestión operacional sobre uno o varios documentos.

    Se crea una CollectionAction por documento para conservar el historial
    individual de cada documento, pero los adjuntos se cargan una sola vez.

    La primera acción creada, según el orden recibido en documents, se
    considera la propietaria de los adjuntos.

    Todas las acciones comparten en metadata:
    - source;
    - batch_id;
    - attachment_owner_action_id;
    - selected_document_count;
    - selected_document_ids;
    - selected_document_numbers.

    Args:
        form:
            CollectionActionForm previamente validado.

        customer:
            Cliente propietario de todos los documentos.

        documents:
            Iterable ordenado de documentos seleccionados.

        performed_by:
            Usuario que registra la gestión.

        uploaded_files:
            Iterable de archivos recibidos mediante
            request.FILES.getlist("attachments").

    Returns:
        dict:
            {
                "actions": [...],
                "owner_action": CollectionAction,
                "batch_id": str,
            }

    Raises:
        ValueError:
            Si el formulario no está asociado a datos o es inválido.

        ValidationError:
            Si los documentos son inválidos o falla la carga de adjuntos.
    """
    _validate_bound_form(form)

    if customer is None or not customer.pk:
        raise ValidationError(
            "El cliente debe estar guardado antes de registrar la gestión."
        )

    normalized_documents = _normalize_batch_documents(
        customer=customer,
        documents=documents,
    )

    batch_id = uuid4().hex

    document_ids = [
        document.pk
        for document in normalized_documents
    ]

    document_numbers = [
        document.document_number
        for document in normalized_documents
    ]

    base_metadata = {
        "source": "customer_workspace",
        "batch_id": batch_id,
        "selected_document_count": len(normalized_documents),
        "selected_document_ids": document_ids,
        "selected_document_numbers": document_numbers,
    }

    actions = []

    for document in normalized_documents:
        action = CollectionAction(
            customer=customer,
            document=document,
            action_type=form.cleaned_data["action_type"],
            title=form.cleaned_data["title"],
            description=form.cleaned_data.get("description", ""),
            performed_by=performed_by,
            metadata=dict(base_metadata),
        )

        action.full_clean()
        action.save()

        actions.append(action)

    owner_action = actions[0]

    for action in actions:
        metadata = dict(action.metadata or {})
        metadata["attachment_owner_action_id"] = owner_action.pk

        action.metadata = metadata
        action.save(update_fields=["metadata"])

    files = list(uploaded_files or [])

    if files:
        create_operational_attachments(
            target=owner_action,
            uploaded_files=files,
            uploaded_by=performed_by,
        )

    return {
        "actions": actions,
        "owner_action": owner_action,
        "batch_id": batch_id,
    }