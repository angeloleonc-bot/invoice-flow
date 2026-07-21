from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

from django.db.models import QuerySet

from apps.portfolio.models import Document, DocumentSupport


@dataclass(frozen=True, slots=True)
class DocumentSupportViewModel:
    id: int
    title: str
    filename: str
    url: str
    provider: str
    provider_label: str
    onedrive_item_id: str | None


def normalize_document_number(document_number: object) -> int | None:
    """
    Convierte el número de documento de Invoice Flow al formato utilizado
    por Fact_scan_url2.

    Fact_scan_url2.doc_num es INTEGER, mientras que
    Document.document_number es VARCHAR.
    """
    if document_number is None:
        return None

    normalized_value = str(document_number).strip()

    if not normalized_value:
        return None

    if not normalized_value.isdigit():
        return None

    return int(normalized_value)


def get_document_supports(document: Document) -> QuerySet[DocumentSupport]:
    """
    Retorna los respaldos activos asociados a un documento.
    """
    normalized_number = normalize_document_number(document.document_number)

    if normalized_number is None:
        return DocumentSupport.objects.none()

    return (
        DocumentSupport.objects
        .filter(
            document_number=normalized_number,
            is_deleted=False,
        )
        .exclude(url__isnull=True)
        .exclude(url="")
        .order_by("id")
    )


def get_support_filename(url: str) -> str:
    """
    Obtiene un nombre legible desde la última parte de la URL.
    """
    parsed_url = urlparse(url)
    filename = PurePosixPath(unquote(parsed_url.path)).name

    return filename or "Documento escaneado"


def build_document_support_viewmodels(
    document: Document,
) -> list[DocumentSupportViewModel]:
    """
    Transforma los registros externos en objetos preparados para la UI.
    """
    supports = get_document_supports(document)

    return [
        DocumentSupportViewModel(
            id=support.id,
            title="Documento escaneado",
            filename=get_support_filename(support.url),
            url=support.url,
            provider="onedrive",
            provider_label="OneDrive",
            onedrive_item_id=support.onedrive_item_id,
        )
        for support in supports
    ]