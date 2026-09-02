from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.portfolio.models import Customer, Document
from apps.portfolio.services.statement_repository import (
    UpcomingStatementDocument,
    UpcomingStatementRepository,
)


CATEGORY_OVERDUE = "OVERDUE"
CATEGORY_DUE_TODAY = "DUE_TODAY"
CATEGORY_UPCOMING = "UPCOMING"

CATEGORY_LABELS = {
    CATEGORY_OVERDUE: "Vencido",
    CATEGORY_DUE_TODAY: "Vence hoy",
    CATEGORY_UPCOMING: "Por vencer",
}


@dataclass(frozen=True)
class StatementDocument:
    selection_key: str
    source: str

    document_id: int | None
    trans_id: str
    doc_entry: str
    document_number: str

    document_type: str

    issue_date: date
    due_date: date

    original_amount: Decimal
    balance_amount: Decimal

    category: str
    category_label: str

    days_from_due: int

    payment_terms: str = ""
    source_snapshot_date: str | None = None

    def snapshot(self) -> dict:
        return {
            "selection_key": self.selection_key,
            "source": self.source,
            "document_id": self.document_id,
            "trans_id": self.trans_id,
            "doc_entry": self.doc_entry,
            "document_number": self.document_number,
            "document_type": self.document_type,
            "issue_date": self.issue_date.isoformat(),
            "due_date": self.due_date.isoformat(),
            "original_amount": str(self.original_amount),
            "balance_amount": str(self.balance_amount),
            "category": self.category,
            "category_label": self.category_label,
            "days_from_due": self.days_from_due,
            "payment_terms": self.payment_terms,
            "source_snapshot_date": self.source_snapshot_date,
        }


class CustomerStatementService:
    """
    Construye la representacion financiera utilizada por Estado de Cuenta.

    IMPORTANTE:
    - no modifica Document;
    - no sincroniza Fact_Vta_Sin_Vencer;
    - no recalcula movimientos ERP;
    - para Document usa balance_amount;
    - para Fact_Vta_Sin_Vencer usa Saldo_Doc;
    - Document siempre prevalece ante solapamiento.
    """

    @classmethod
    def available_documents(
        cls,
        *,
        customer: Customer,
        as_of_date: date | None = None,
    ) -> list[StatementDocument]:

        as_of_date = (
            as_of_date
            or timezone.localdate()
        )

        portfolio_documents = list(
            Document.objects
            .filter(
                customer=customer,
                balance_amount__gt=0,
            )
            .order_by(
                "due_date",
                "document_number",
                "id",
            )
        )

        portfolio_trans_ids = {
            str(document.trans_id or "").strip()
            for document in portfolio_documents
            if str(document.trans_id or "").strip()
        }

        upcoming_source = (
            UpcomingStatementRepository.for_customer(
                customer=customer,
                as_of_date=as_of_date,
            )
        )

        result: list[StatementDocument] = []

        for document in portfolio_documents:
            result.append(
                cls._from_document(
                    document=document,
                    as_of_date=as_of_date,
                )
            )

        for source_document in upcoming_source:

            # Si ya entro al dominio Document,
            # la verdad operacional de Invoice Flow prevalece.
            if (
                source_document.trans_id
                and source_document.trans_id
                in portfolio_trans_ids
            ):
                continue

            result.append(
                cls._from_upcoming(
                    document=source_document,
                    as_of_date=as_of_date,
                )
            )

        return sorted(
            result,
            key=lambda item: (
                cls._category_order(item.category),
                item.due_date,
                item.document_number,
                item.trans_id,
            ),
        )

    @classmethod
    def resolve_selected_documents(
        cls,
        *,
        customer: Customer,
        selected_keys: Iterable[str],
        as_of_date: date | None = None,
    ) -> list[StatementDocument]:
        """
        Resuelve una selección enviada por el cliente contra la
        representación vigente de documentos disponibles.

        La selection_key nunca se considera una identidad confiable
        por sí sola. Siempre se vuelve a resolver dentro del alcance
        del Customer recibido.

        Garantías:
        - elimina claves vacías y duplicadas conservando el orden;
        - exige al menos un documento;
        - impide seleccionar documentos de otro cliente;
        - impide seleccionar documentos que dejaron de estar vigentes;
        - permite mezclar Document y Fact_Vta_Sin_Vencer;
        - no crea ni modifica Document.
        """
        as_of_date = (
            as_of_date
            or timezone.localdate()
        )

        normalized_keys = list(
            dict.fromkeys(
                str(value or "").strip()
                for value in selected_keys
                if str(value or "").strip()
            )
        )

        if not normalized_keys:
            raise ValidationError(
                "Debe seleccionar al menos un documento."
            )

        available = cls.available_documents(
            customer=customer,
            as_of_date=as_of_date,
        )

        by_key = {
            item.selection_key: item
            for item in available
        }

        invalid = [
            key
            for key in normalized_keys
            if key not in by_key
        ]

        if invalid:
            raise ValidationError(
                "La selección contiene uno o más documentos "
                "que no existen, no pertenecen al cliente o "
                "ya no están disponibles."
            )

        return [
            by_key[key]
            for key in normalized_keys
        ]

    @classmethod
    def build_snapshot(
        cls,
        *,
        customer: Customer,
        selected_keys: Iterable[str],
        as_of_date: date | None = None,
    ) -> dict:

        as_of_date = (
            as_of_date
            or timezone.localdate()
        )

        documents = cls.resolve_selected_documents(
            customer=customer,
            selected_keys=selected_keys,
            as_of_date=as_of_date,
        )

        totals = {
            CATEGORY_OVERDUE: Decimal("0"),
            CATEGORY_DUE_TODAY: Decimal("0"),
            CATEGORY_UPCOMING: Decimal("0"),
        }

        for document in documents:
            totals[document.category] += (
                document.balance_amount
            )

        grand_total = sum(
            totals.values(),
            Decimal("0"),
        )

        return {
            "version": 1,
            "generated_at": (
                timezone.now().isoformat()
            ),
            "as_of_date": as_of_date.isoformat(),
            "customer": {
                "id": customer.id,
                "external_id": customer.external_id,
                "rut": customer.rut,
                "name": customer.name,
            },
            "documents": [
                item.snapshot()
                for item in documents
            ],
            "document_count": len(documents),
            "totals": {
                "overdue": str(
                    totals[CATEGORY_OVERDUE]
                ),
                "due_today": str(
                    totals[CATEGORY_DUE_TODAY]
                ),
                "upcoming": str(
                    totals[CATEGORY_UPCOMING]
                ),
                "current": str(
                    totals[CATEGORY_DUE_TODAY]
                    + totals[CATEGORY_UPCOMING]
                ),
                "grand_total": str(grand_total),
            },
        }

    @staticmethod
    def _classify(
        due_date: date,
        as_of_date: date,
    ) -> tuple[str, int]:

        delta = (due_date - as_of_date).days

        if delta < 0:
            return CATEGORY_OVERDUE, abs(delta)

        if delta == 0:
            return CATEGORY_DUE_TODAY, 0

        return CATEGORY_UPCOMING, delta

    @classmethod
    def _from_document(
        cls,
        *,
        document: Document,
        as_of_date: date,
    ) -> StatementDocument:

        category, days_from_due = cls._classify(
            document.due_date,
            as_of_date,
        )

        return StatementDocument(
            selection_key=f"document:{document.id}",
            source="Document",
            document_id=document.id,
            trans_id=str(
                document.trans_id or ""
            ).strip(),
            doc_entry=str(
                document.source_doc_entry or ""
            ).strip(),
            document_number=str(
                document.document_number or ""
            ).strip(),
            document_type=(
                document.get_document_type_display()
            ),
            issue_date=document.issue_date,
            due_date=document.due_date,
            original_amount=Decimal(
                str(document.original_amount or 0)
            ),
            balance_amount=Decimal(
                str(document.balance_amount or 0)
            ),
            category=category,
            category_label=CATEGORY_LABELS[
                category
            ],
            days_from_due=days_from_due,
            payment_terms=str(
                document.payment_terms or ""
            ).strip(),
            source_snapshot_date=(
                document.source_snapshot_date.isoformat()
                if document.source_snapshot_date
                else None
            ),
        )

    @classmethod
    def _from_upcoming(
        cls,
        *,
        document: UpcomingStatementDocument,
        as_of_date: date,
    ) -> StatementDocument:

        category, days_from_due = cls._classify(
            document.due_date,
            as_of_date,
        )

        # El repositorio nunca debe devolver documentos
        # ya vencidos desde Fact_Vta_Sin_Vencer.
        if category == CATEGORY_OVERDUE:
            raise ValidationError(
                "Fact_Vta_Sin_Vencer entregó un documento "
                "ya vencido fuera del dominio operacional."
            )

        return StatementDocument(
            selection_key=document.selection_key,
            source="Fact_Vta_Sin_Vencer",
            document_id=None,
            trans_id=document.trans_id,
            doc_entry=document.doc_entry,
            document_number=document.document_number,
            document_type=document.document_type,
            issue_date=document.issue_date,
            due_date=document.due_date,
            original_amount=document.original_amount,
            balance_amount=document.balance_amount,
            category=category,
            category_label=CATEGORY_LABELS[
                category
            ],
            days_from_due=days_from_due,
            payment_terms=document.payment_terms,
            source_snapshot_date=(
                document.source_snapshot_date.isoformat()
                if document.source_snapshot_date
                else None
            ),
        )

    @staticmethod
    def _category_order(category: str) -> int:
        return {
            CATEGORY_OVERDUE: 0,
            CATEGORY_DUE_TODAY: 1,
            CATEGORY_UPCOMING: 2,
        }.get(category, 99)
