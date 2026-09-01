from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone
from django.utils.text import slugify

from apps.portfolio.models import (
    Customer,
    CustomerStatement,
)
from apps.portfolio.services.customer_statements import (
    CustomerStatementService,
)


class CustomerStatementPreviewService:
    PREVIEW_TTL_MINUTES = 30
    MAX_RECIPIENTS = 20
    MAX_MESSAGE_LENGTH = 5000

    @classmethod
    def create_preview(
        cls,
        *,
        customer: Customer,
        user,
        raw_to_emails: str,
        selected_keys,
        subject: str,
        message_body: str = "",
        raw_cc_emails: str = "",
    ) -> CustomerStatement:

        if not user or not user.is_authenticated:
            raise ValidationError(
                "Debe existir un usuario autenticado."
            )

        sender_email = str(
            getattr(user, "email", "") or ""
        ).strip().lower()

        if not sender_email:
            raise ValidationError(
                "El usuario autenticado no posee correo corporativo."
            )

        try:
            validate_email(sender_email)
        except ValidationError as exc:
            raise ValidationError(
                "El correo del usuario autenticado no es válido."
            ) from exc

        recipients = cls.normalize_recipients(
            raw_to_emails
        )

        cc_recipients = cls.normalize_recipients(
            raw_cc_emails,
            required=False,
        )

        normalized_subject = str(subject or "").strip()

        if not normalized_subject:
            raise ValidationError(
                "Debe indicar un asunto."
            )

        if len(normalized_subject) > 255:
            raise ValidationError(
                "El asunto no puede superar 255 caracteres."
            )

        normalized_message = str(
            message_body or ""
        ).strip()

        if len(normalized_message) > cls.MAX_MESSAGE_LENGTH:
            raise ValidationError(
                "El mensaje es demasiado extenso."
            )

        snapshot = CustomerStatementService.build_snapshot(
            customer=customer,
            selected_keys=selected_keys,
        )

        snapshot_hash = cls.calculate_snapshot_hash(
            snapshot
        )

        totals = snapshot["totals"]

        base_filename = cls.build_base_filename(
            customer=customer,
            as_of_date=snapshot["as_of_date"],
        )

        statement = CustomerStatement.objects.create(
            customer=customer,
            created_by=user,
            sender_email=sender_email,
            to_emails=recipients,
            cc_emails=cc_recipients,
            subject=normalized_subject,
            message_body=normalized_message,
            status=CustomerStatement.Status.PREVIEW,
            document_count=snapshot["document_count"],
            overdue_total=Decimal(
                totals["overdue"]
            ),
            due_today_total=Decimal(
                totals["due_today"]
            ),
            upcoming_total=Decimal(
                totals["upcoming"]
            ),
            grand_total=Decimal(
                totals["grand_total"]
            ),
            snapshot=snapshot,
            snapshot_hash=snapshot_hash,
            expires_at=(
                timezone.now()
                + timedelta(
                    minutes=cls.PREVIEW_TTL_MINUTES
                )
            ),
            pdf_filename=(
                f"{base_filename}.pdf"
            ),
            xlsx_filename=(
                f"{base_filename}.xlsx"
            ),
        )

        return statement

    @classmethod
    def normalize_recipients(
        cls,
        raw_value: str,
        *,
        required: bool = True,
    ) -> list[str]:

        tokens = re.split(
            r"[,;\n\r]+",
            str(raw_value or ""),
        )

        recipients = []

        for token in tokens:
            email = token.strip().lower()

            if not email:
                continue

            try:
                validate_email(email)
            except ValidationError as exc:
                raise ValidationError(
                    f"El correo '{email}' no es válido."
                ) from exc

            if email not in recipients:
                recipients.append(email)

        if not recipients and required:
            raise ValidationError(
                "Debe indicar al menos un destinatario."
            )

        if len(recipients) > cls.MAX_RECIPIENTS:
            raise ValidationError(
                f"Puede enviar como máximo a "
                f"{cls.MAX_RECIPIENTS} destinatarios."
            )

        return recipients

    @classmethod
    def calculate_snapshot_hash(
        cls,
        snapshot: dict,
    ) -> str:
        """
        El timestamp de generación no participa del fingerprint.
        Así podemos reconstruir y comparar el estado financiero.
        """

        fingerprint = {
            "version": snapshot.get("version"),
            "as_of_date": snapshot.get("as_of_date"),
            "customer": snapshot.get("customer"),
            "documents": snapshot.get("documents"),
            "document_count": snapshot.get(
                "document_count"
            ),
            "totals": snapshot.get("totals"),
        }

        canonical = json.dumps(
            fingerprint,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        return hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()

    @staticmethod
    def build_base_filename(
        *,
        customer: Customer,
        as_of_date: str,
    ) -> str:

        safe_customer = slugify(
            customer.name
        )[:60] or f"cliente-{customer.pk}"

        compact_date = str(as_of_date).replace(
            "-",
            "",
        )

        return (
            f"estado-cuenta-"
            f"{safe_customer}-"
            f"{compact_date}"
        )
