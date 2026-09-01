from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date
from html import escape

from django.db import transaction
from django.utils import timezone

from apps.accounts.services.delegated_graph import (
    DelegatedGraphAuthenticationError,
    DelegatedGraphProviderError,
    DelegatedGraphService,
    DelegatedGraphUnavailableError,
    GraphMailTransport,
)
from apps.portfolio.models import CustomerStatement
from apps.portfolio.services.customer_statements import (
    CustomerStatementService,
)
from apps.portfolio.services.statement_exports import (
    CustomerStatementExportService,
)
from apps.portfolio.services.statement_preview import (
    CustomerStatementPreviewService,
)


class StatementDeliveryError(Exception):
    pass


class StatementNotSendableError(
    StatementDeliveryError
):
    pass


class StatementFinancialStateChangedError(
    StatementDeliveryError
):
    pass


class StatementSenderMismatchError(
    StatementDeliveryError
):
    pass


@dataclass(frozen=True)
class StatementRevalidationResult:
    statement_id: int
    public_id: str
    valid: bool
    previous_hash: str
    current_hash: str


@dataclass(frozen=True)
class StatementDeliveryResult:
    statement_id: int
    public_id: str
    status: str
    request_id: str


class CustomerStatementDeliveryService:

    MAX_ATTACHMENT_BYTES = 3 * 1024 * 1024

    @classmethod
    def _selected_keys(
        cls,
        statement: CustomerStatement,
    ) -> list[str]:

        documents = (
            statement.snapshot
            or {}
        ).get(
            "documents",
            [],
        )

        keys = []

        for document in documents:
            selection_key = str(
                document.get(
                    "selection_key",
                    "",
                )
                or ""
            ).strip()

            if not selection_key:
                raise StatementDeliveryError(
                    "El snapshot contiene un documento "
                    "sin selection_key."
                )

            keys.append(selection_key)

        if not keys:
            raise StatementDeliveryError(
                "El Estado de Cuenta no contiene documentos."
            )

        return keys

    @classmethod
    def revalidate_financial_snapshot(
        cls,
        *,
        statement: CustomerStatement,
    ) -> StatementRevalidationResult:

        if (
            statement.status
            != CustomerStatement.Status.PREVIEW
        ):
            raise StatementNotSendableError(
                "El Estado de Cuenta ya no está "
                "en estado PREVIEW."
            )

        if (
            statement.expires_at
            and statement.expires_at <= timezone.now()
        ):
            statement.status = (
                CustomerStatement.Status.INVALIDATED
            )

            statement.error_message = (
                "La vista previa expiró antes del envío."
            )

            statement.save(
                update_fields=[
                    "status",
                    "error_message",
                    "updated_at",
                ]
            )

            raise StatementNotSendableError(
                "La vista previa expiró."
            )

        selected_keys = cls._selected_keys(
            statement
        )

        stored_snapshot = statement.snapshot or {}

        raw_date = stored_snapshot.get(
            "as_of_date"
        )

        if not raw_date:
            raise StatementDeliveryError(
                "El snapshot no contiene as_of_date."
            )

        try:
            as_of_date = date.fromisoformat(
                str(raw_date)
            )
        except ValueError as exc:
            raise StatementDeliveryError(
                "El as_of_date del snapshot es inválido."
            ) from exc

        current_snapshot = (
            CustomerStatementService
            .build_snapshot(
                customer=statement.customer,
                selected_keys=selected_keys,
                as_of_date=as_of_date,
            )
        )

        current_hash = (
            CustomerStatementPreviewService
            .calculate_snapshot_hash(
                current_snapshot
            )
        )

        previous_hash = str(
            statement.snapshot_hash or ""
        )

        if current_hash != previous_hash:

            statement.status = (
                CustomerStatement.Status.INVALIDATED
            )

            statement.error_message = (
                "La información financiera cambió "
                "después de generar la vista previa."
            )

            statement.save(
                update_fields=[
                    "status",
                    "error_message",
                    "updated_at",
                ]
            )

            raise StatementFinancialStateChangedError(
                "La información financiera cambió "
                "desde la generación de la vista previa. "
                "Debe generar una nueva."
            )

        return StatementRevalidationResult(
            statement_id=statement.id,
            public_id=str(statement.public_id),
            valid=True,
            previous_hash=previous_hash,
            current_hash=current_hash,
        )

    @staticmethod
    def _money(value) -> str:
        from decimal import Decimal

        amount = Decimal(str(value or 0))

        return (
            "$ "
            + f"{amount:,.0f}".replace(
                ",",
                ".",
            )
        )

    @classmethod
    def build_html_body(
        cls,
        statement: CustomerStatement,
    ) -> str:

        snapshot = statement.snapshot or {}
        totals = snapshot.get("totals", {})

        message = escape(
            statement.message_body or ""
        ).replace(
            "\n",
            "<br>",
        )

        customer_name = escape(
            statement.customer.name
        )

        as_of_date = escape(
            str(
                snapshot.get(
                    "as_of_date",
                    "",
                )
            )
        )

        return f"""
<!doctype html>
<html>
<body style="
    margin:0;
    padding:0;
    background:#f5f7f6;
    font-family:Arial,Helvetica,sans-serif;
    color:#29352f;
">
  <div style="
      max-width:720px;
      margin:0 auto;
      padding:28px 18px;
  ">
    <div style="
        background:#ffffff;
        border:1px solid #e0e7e3;
        border-radius:14px;
        padding:26px;
    ">

      <div style="
          font-size:12px;
          font-weight:700;
          letter-spacing:.06em;
          color:#69766f;
      ">
        ESTADO DE CUENTA
      </div>

      <h2 style="
          margin:6px 0 4px;
          font-size:22px;
          color:#202c26;
      ">
        {customer_name}
      </h2>

      <div style="
          font-size:13px;
          color:#7a8680;
          margin-bottom:22px;
      ">
        Información al {as_of_date}
      </div>

      <div style="
          font-size:14px;
          line-height:1.6;
          margin-bottom:24px;
      ">
        {message}
      </div>

      <table style="
          width:100%;
          border-collapse:collapse;
          margin-bottom:22px;
      ">
        <tr>
          <td style="padding:10px;border:1px solid #e2e8e5;">
            <div style="font-size:11px;color:#7d8882;">Vencido</div>
            <strong>{cls._money(totals.get("overdue"))}</strong>
          </td>

          <td style="padding:10px;border:1px solid #e2e8e5;">
            <div style="font-size:11px;color:#7d8882;">Vence hoy</div>
            <strong>{cls._money(totals.get("due_today"))}</strong>
          </td>

          <td style="padding:10px;border:1px solid #e2e8e5;">
            <div style="font-size:11px;color:#7d8882;">Por vencer</div>
            <strong>{cls._money(totals.get("upcoming"))}</strong>
          </td>

          <td style="padding:10px;border:1px solid #e2e8e5;">
            <div style="font-size:11px;color:#7d8882;">Total</div>
            <strong>{cls._money(totals.get("grand_total"))}</strong>
          </td>
        </tr>
      </table>

      <div style="
          padding:12px 14px;
          background:#f6f8f7;
          border-radius:8px;
          font-size:12px;
          color:#68746e;
      ">
        Se adjuntan el Estado de Cuenta en PDF y su detalle en Excel.
      </div>

    </div>
  </div>
</body>
</html>
""".strip()

    @classmethod
    def build_graph_payload(
        cls,
        *,
        statement: CustomerStatement,
        pdf_bytes: bytes,
        xlsx_bytes: bytes,
    ) -> dict:

        total_attachment_bytes = (
            len(pdf_bytes)
            + len(xlsx_bytes)
        )

        if (
            total_attachment_bytes
            > cls.MAX_ATTACHMENT_BYTES
        ):
            raise StatementDeliveryError(
                "Los archivos adjuntos superan el tamaño "
                "permitido para el envío directo."
            )

        def recipients(values):
            return [
                {
                    "emailAddress": {
                        "address": email,
                    }
                }
                for email in values
            ]

        return {
            "message": {
                "subject": statement.subject,
                "body": {
                    "contentType": "HTML",
                    "content": cls.build_html_body(
                        statement
                    ),
                },
                "toRecipients": recipients(
                    statement.to_emails
                ),
                "ccRecipients": recipients(
                    statement.cc_emails
                ),
                "attachments": [
                    {
                        "@odata.type": (
                            "#microsoft.graph.fileAttachment"
                        ),
                        "name": statement.pdf_filename,
                        "contentType": "application/pdf",
                        "contentBytes": (
                            base64.b64encode(
                                pdf_bytes
                            ).decode("ascii")
                        ),
                    },
                    {
                        "@odata.type": (
                            "#microsoft.graph.fileAttachment"
                        ),
                        "name": statement.xlsx_filename,
                        "contentType": (
                            "application/vnd.openxmlformats-"
                            "officedocument.spreadsheetml.sheet"
                        ),
                        "contentBytes": (
                            base64.b64encode(
                                xlsx_bytes
                            ).decode("ascii")
                        ),
                    },
                ],
            },
            "saveToSentItems": True,
        }

    @classmethod
    def _mark_failed(
        cls,
        *,
        statement_id: int,
        message: str,
    ) -> None:

        with transaction.atomic():

            statement = (
                CustomerStatement.objects
                .select_for_update()
                .get(pk=statement_id)
            )

            if (
                statement.status
                == CustomerStatement.Status.SENDING
            ):
                statement.status = (
                    CustomerStatement.Status.FAILED
                )

                statement.error_message = str(
                    message
                )[:5000]

                statement.save(
                    update_fields=[
                        "status",
                        "error_message",
                        "updated_at",
                    ]
                )

    @classmethod
    def deliver(
        cls,
        *,
        statement_id: int,
        request,
    ) -> StatementDeliveryResult:

        user_email = str(
            getattr(
                request.user,
                "email",
                "",
            )
            or ""
        ).strip().lower()

        if not user_email:
            raise StatementSenderMismatchError(
                "El usuario no tiene correo corporativo."
            )

        # Primero validamos que existe una sesión delegada.
        # Esto no cambia estado ni envía correo.
        token_result = (
            DelegatedGraphService
            .acquire_access_token(
                request=request
            )
        )

        with transaction.atomic():

            statement = (
                CustomerStatement.objects
                .select_related(
                    "customer",
                    "created_by",
                )
                .select_for_update()
                .get(
                    pk=statement_id
                )
            )

            if (
                statement.created_by_id
                != request.user.id
            ):
                raise StatementNotSendableError(
                    "Sólo el usuario que creó la vista previa "
                    "puede realizar el envío."
                )

            if (
                statement.sender_email.lower()
                != user_email
            ):
                raise StatementSenderMismatchError(
                    "El remitente de la vista previa no coincide "
                    "con el usuario autenticado."
                )

            cls.revalidate_financial_snapshot(
                statement=statement
            )

            statement.status = (
                CustomerStatement.Status.SENDING
            )

            statement.error_message = ""

            statement.save(
                update_fields=[
                    "status",
                    "error_message",
                    "updated_at",
                ]
            )

            snapshot = statement.snapshot
            pdf_filename = statement.pdf_filename
            xlsx_filename = statement.xlsx_filename

        try:
            pdf_bytes = (
                CustomerStatementExportService
                .build_pdf(snapshot)
            )

            xlsx_bytes = (
                CustomerStatementExportService
                .build_xlsx(snapshot)
            )

            # Recargar datos inmutables del statement.
            statement = (
                CustomerStatement.objects
                .select_related(
                    "customer",
                    "created_by",
                )
                .get(
                    pk=statement_id
                )
            )

            payload = cls.build_graph_payload(
                statement=statement,
                pdf_bytes=pdf_bytes,
                xlsx_bytes=xlsx_bytes,
            )

            provider_result = (
                GraphMailTransport.send_mail(
                    access_token=(
                        token_result.access_token
                    ),
                    payload=payload,
                )
            )

        except (
            DelegatedGraphAuthenticationError,
            DelegatedGraphUnavailableError,
            DelegatedGraphProviderError,
            StatementDeliveryError,
            Exception,
        ) as exc:

            cls._mark_failed(
                statement_id=statement_id,
                message=str(exc),
            )

            raise

        with transaction.atomic():

            statement = (
                CustomerStatement.objects
                .select_for_update()
                .get(
                    pk=statement_id
                )
            )

            if (
                statement.status
                != CustomerStatement.Status.SENDING
            ):
                raise StatementDeliveryError(
                    "El estado del envío cambió inesperadamente."
                )

            statement.status = (
                CustomerStatement.Status.SENT
            )

            statement.sent_at = timezone.now()

            statement.provider = (
                "microsoft_graph"
            )

            statement.provider_message_id = (
                provider_result.request_id
                or ""
            )

            statement.pdf_filename = pdf_filename
            statement.xlsx_filename = xlsx_filename
            statement.error_message = ""

            statement.save(
                update_fields=[
                    "status",
                    "sent_at",
                    "provider",
                    "provider_message_id",
                    "pdf_filename",
                    "xlsx_filename",
                    "error_message",
                    "updated_at",
                ]
            )

        return StatementDeliveryResult(
            statement_id=statement.id,
            public_id=str(
                statement.public_id
            ),
            status=statement.status,
            request_id=(
                statement.provider_message_id
            ),
        )
