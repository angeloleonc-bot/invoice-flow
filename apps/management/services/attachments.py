from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.management.models import (
    CollectionAction,
    OperationalAttachment,
    PaymentPromise,
)
from apps.management.storage.s3 import get_s3_client
from apps.management.validators.attachments import (
    validate_operational_attachment,
)
from botocore.exceptions import ClientError

from urllib.parse import quote

from django.http import StreamingHttpResponse


ALLOWED_TARGET_MODELS = (
    CollectionAction,
    PaymentPromise,
)


def validate_attachment_target(target):
    """
    Confirma que el adjunto se asocie exclusivamente a una gestión
    o a una promesa de pago ya guardada.
    """
    if not isinstance(target, ALLOWED_TARGET_MODELS):
        raise ValidationError(
            "El adjunto solo puede asociarse a una gestión "
            "o a una promesa de pago."
        )

    if not target.pk:
        raise ValidationError(
            "La gestión o promesa debe estar guardada antes "
            "de adjuntar archivos."
        )


def build_storage_key(*, target, original_filename):
    """
    Genera una clave interna única para S3.

    Ejemplo:
    operational-attachments/collectionaction/2026/07/145/uuid.pdf
    """
    extension = (
        Path(original_filename)
        .suffix
        .lower()
        .lstrip(".")
    )

    now = timezone.now()

    key_parts = [
        settings.AWS_S3_ATTACHMENT_PREFIX.strip("/"),
        target._meta.model_name,
        str(now.year),
        f"{now.month:02d}",
        str(target.pk),
        uuid4().hex,
    ]

    storage_key = "/".join(key_parts)

    if extension:
        storage_key = f"{storage_key}.{extension}"

    return storage_key


def upload_attachment_to_s3(*, target, uploaded_file):
    """
    Valida el archivo y lo carga de manera privada en S3.

    Retorna:
        storage_key
        metadata
    """
    validate_attachment_target(target)

    metadata = validate_operational_attachment(uploaded_file)

    storage_key = build_storage_key(
        target=target,
        original_filename=metadata["original_filename"],
    )

    s3_client = get_s3_client()

    try:
        uploaded_file.seek(0)

        s3_client.upload_fileobj(
            Fileobj=uploaded_file,
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=storage_key,
            ExtraArgs={
                "ContentType": metadata["mime_type"],
                "Metadata": {
                    # Los metadatos personalizados de S3 viajan como
                    # cabeceras HTTP. El nombre visible puede contener
                    # Unicode (tildes, ñ, etc.), por lo que se codifica
                    # únicamente para S3. El nombre original se conserva
                    # intacto en OperationalAttachment.original_filename.
                    "original-filename": quote(
                        metadata["original_filename"],
                        safe="",
                    ),
                    "target-model": target._meta.label_lower,
                    "target-id": str(target.pk),
                },
            },
        )

    except Exception as exc:
        raise ValidationError(
            "No fue posible cargar el archivo en el repositorio."
        ) from exc

    return storage_key, metadata


def delete_s3_object(storage_key):
    """
    Elimina un objeto directamente desde S3.
    """
    if not storage_key:
        return

    try:
        get_s3_client().delete_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=storage_key,
        )
    except Exception as exc:
        raise ValidationError(
            "No fue posible eliminar el archivo del repositorio."
        ) from exc


@transaction.atomic
def create_operational_attachment(
    *,
    target,
    uploaded_file,
    uploaded_by=None,
):
    """
    Carga un archivo a S3 y crea su registro SQL.

    Si falla la creación SQL, intenta eliminar el objeto cargado en S3.
    """
    storage_key = None

    try:
        storage_key, metadata = upload_attachment_to_s3(
            target=target,
            uploaded_file=uploaded_file,
        )

        content_type = ContentType.objects.get_for_model(
            target,
            for_concrete_model=False,
        )

        attachment = OperationalAttachment(
            content_type=content_type,
            object_id=target.pk,
            original_filename=metadata["original_filename"],
            storage_key=storage_key,
            mime_type=metadata["mime_type"],
            extension=metadata["extension"],
            size_bytes=metadata["size_bytes"],
            uploaded_by=uploaded_by,
        )

        attachment.full_clean()
        attachment.save()

        return attachment

    except Exception:
        if storage_key:
            try:
                delete_s3_object(storage_key)
            except ValidationError:
                pass

        raise


def create_operational_attachments(
    *,
    target,
    uploaded_files,
    uploaded_by=None,
):
    """
    Carga múltiples archivos.

    Si falla uno, elimina de S3 y SQL los adjuntos creados durante
    esta misma operación.
    """
    created_attachments = []

    try:
        for uploaded_file in uploaded_files:
            attachment = create_operational_attachment(
                target=target,
                uploaded_file=uploaded_file,
                uploaded_by=uploaded_by,
            )
            created_attachments.append(attachment)

        return created_attachments

    except Exception:
        for attachment in created_attachments:
            try:
                delete_s3_object(attachment.storage_key)
            except ValidationError:
                pass

            attachment.delete()

        raise

def get_attachment_s3_object(attachment):
    """
    Recupera el objeto privado desde S3.

    Retorna la respuesta de boto3, incluyendo:
    - Body
    - ContentLength
    - ContentType
    """
    if not isinstance(attachment, OperationalAttachment):
        raise ValidationError(
            "El adjunto solicitado no es válido."
        )

    try:
        return get_s3_client().get_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=attachment.storage_key,
        )

    except ClientError as exc:
        error_code = (
            exc.response
            .get("Error", {})
            .get("Code")
        )

        if error_code in {
            "NoSuchKey",
            "404",
            "NotFound",
        }:
            raise ValidationError(
                "El archivo ya no existe en el repositorio."
            ) from exc

        raise ValidationError(
            "No fue posible recuperar el archivo del repositorio."
        ) from exc
    
def _stream_s3_body(streaming_body, chunk_size=64 * 1024):
    """
    Transmite el objeto desde S3 en bloques y garantiza el cierre
    de la conexión al finalizar o interrumpirse la descarga.
    """
    try:
        while True:
            chunk = streaming_body.read(chunk_size)

            if not chunk:
                break

            yield chunk

    finally:
        streaming_body.close()


def build_attachment_download_response(attachment):
    """
    Recupera el adjunto privado desde S3 y construye una respuesta
    HTTP de descarga sin exponer una URL directa del bucket.
    """
    s3_object = get_attachment_s3_object(attachment)
    streaming_body = s3_object["Body"]

    content_type = (
        s3_object.get("ContentType")
        or attachment.mime_type
        or "application/octet-stream"
    )

    response = StreamingHttpResponse(
        streaming_content=_stream_s3_body(streaming_body),
        content_type=content_type,
    )

    encoded_filename = quote(
        attachment.original_filename,
        safe="",
    )

    response["Content-Disposition"] = (
        "attachment; "
        f"filename*=UTF-8''{encoded_filename}"
    )

    content_length = s3_object.get("ContentLength")

    if content_length is not None:
        response["Content-Length"] = str(content_length)

    response["Cache-Control"] = "private, no-store, max-age=0"
    response["Pragma"] = "no-cache"
    response["X-Content-Type-Options"] = "nosniff"

    return response


def generate_attachment_download_url(attachment):
    """
    Genera una URL prefirmada temporal para descargar el archivo.
    """
    if not isinstance(attachment, OperationalAttachment):
        raise ValidationError(
            "El adjunto solicitado no es válido."
        )

    try:
        return get_s3_client().generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": settings.AWS_STORAGE_BUCKET_NAME,
                "Key": attachment.storage_key,
                "ResponseContentDisposition": (
                    "attachment; "
                    f'filename="{attachment.original_filename}"'
                ),
                "ResponseContentType": attachment.mime_type,
            },
            ExpiresIn=settings.AWS_S3_PRESIGNED_URL_EXPIRATION,
        )
    except Exception as exc:
        raise ValidationError(
            "No fue posible generar el enlace de descarga."
        ) from exc


def delete_operational_attachment(attachment):
    """
    Elimina primero el objeto de S3 y después el registro SQL.
    """
    if not isinstance(attachment, OperationalAttachment):
        raise ValidationError(
            "El adjunto solicitado no es válido."
        )

    delete_s3_object(attachment.storage_key)
    attachment.delete()