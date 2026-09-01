from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from django.db import connection

from apps.portfolio.models import Customer


@dataclass(frozen=True)
class UpcomingStatementDocument:
    customer_external_id: str
    customer_rut: str
    customer_name: str

    trans_id: str
    doc_entry: str
    document_number: str

    document_type: str
    issue_date: date
    due_date: date

    original_amount: Decimal
    balance_amount: Decimal

    payment_terms: str
    source_snapshot_date: datetime | None

    @property
    def selection_key(self) -> str:
        return (
            f"upcoming:"
            f"{self.trans_id}:"
            f"{self.doc_entry}:"
            f"{self.document_number}"
        )


class UpcomingStatementRepository:
    """
    Acceso READ ONLY a dbo.Fact_Vta_Sin_Vencer.

    Esta fuente nunca crea ni modifica Document.

    Reglas:
    - relacion cliente: Fact_Vta_Sin_Vencer.Id -> Customer.external_id;
    - solo saldo positivo;
    - solo documentos cuya fecha de vencimiento sea hoy o futura;
    - deduplicacion fisica antes de entregar resultados;
    - una factura logica se identifica defensivamente por
      Id + TransId + DocEntry + Num_doc.
    """

    SOURCE_TABLE = "dbo.Fact_Vta_Sin_Vencer"

    @classmethod
    def for_customer(
        cls,
        *,
        customer: Customer,
        as_of_date: date,
    ) -> list[UpcomingStatementDocument]:

        sql = f"""
        WITH deduplicated AS (
            SELECT
                Id,
                Rut,
                Nombre,
                TransId,
                DocEntry,
                Num_doc,
                Tipo_Doc,
                Fecha_Documento,
                Vencimiento_Documento,
                Total_Doc,
                Saldo_Doc,
                Condicion_Pago,
                Fecha_Informe,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        Id,
                        TransId,
                        DocEntry,
                        Num_doc
                    ORDER BY Id_PK DESC
                ) AS rn
            FROM {cls.SOURCE_TABLE}
            WHERE
                Id = %s
                AND Saldo_Doc > 0
                AND Vencimiento_Documento >= %s
        )
        SELECT
            Id,
            Rut,
            Nombre,
            TransId,
            DocEntry,
            Num_doc,
            Tipo_Doc,
            Fecha_Documento,
            Vencimiento_Documento,
            Total_Doc,
            Saldo_Doc,
            Condicion_Pago,
            Fecha_Informe
        FROM deduplicated
        WHERE rn = 1
        ORDER BY
            Vencimiento_Documento,
            Num_doc,
            TransId
        """

        with connection.cursor() as cursor:
            cursor.execute(
                sql,
                [
                    str(customer.external_id),
                    as_of_date,
                ],
            )

            columns = [
                column[0]
                for column in cursor.description
            ]

            raw_rows = [
                dict(zip(columns, row))
                for row in cursor.fetchall()
            ]

        results: list[UpcomingStatementDocument] = []

        for row in raw_rows:
            issue_date = row["Fecha_Documento"]
            due_date = row["Vencimiento_Documento"]

            if isinstance(issue_date, datetime):
                issue_date = issue_date.date()

            if isinstance(due_date, datetime):
                due_date = due_date.date()

            results.append(
                UpcomingStatementDocument(
                    customer_external_id=str(
                        row["Id"] or ""
                    ).strip(),
                    customer_rut=str(
                        row["Rut"] or ""
                    ).strip(),
                    customer_name=str(
                        row["Nombre"] or ""
                    ).strip(),
                    trans_id=str(
                        row["TransId"] or ""
                    ).strip(),
                    doc_entry=str(
                        row["DocEntry"] or ""
                    ).strip(),
                    document_number=str(
                        row["Num_doc"] or ""
                    ).strip(),
                    document_type=str(
                        row["Tipo_Doc"] or "Factura"
                    ).strip(),
                    issue_date=issue_date,
                    due_date=due_date,
                    original_amount=Decimal(
                        str(row["Total_Doc"] or 0)
                    ),
                    balance_amount=Decimal(
                        str(row["Saldo_Doc"] or 0)
                    ),
                    payment_terms=str(
                        row["Condicion_Pago"] or ""
                    ).strip(),
                    source_snapshot_date=row[
                        "Fecha_Informe"
                    ],
                )
            )

        return results
