from django.db import transaction

from apps.management.models import CollectionAction
from apps.management.services.attachments import (
    create_operational_attachments,
)


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
    if not form.is_bound:
        raise ValueError(
            "El formulario debe estar asociado a datos antes de crear la gestión."
        )

    if form.errors:
        raise ValueError(
            "No se puede crear una gestión con un formulario inválido."
        )

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