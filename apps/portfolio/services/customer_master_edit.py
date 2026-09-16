from dataclasses import dataclass
import re

from django.core.exceptions import ValidationError
from django.core.validators import validate_email


_EMAIL_SPLIT_RE = re.compile(r"[,;\s]+")


class CustomerMasterValidationError(ValueError):
    pass


@dataclass(frozen=True)
class CustomerMasterContactData:
    billing_emails: tuple[str, ...]
    billing_emails_raw: str
    phone: str


def normalize_billing_emails(raw_value):
    raw = str(raw_value or "").strip()

    if not raw:
        return (), ""

    emails = []
    seen = set()

    for value in _EMAIL_SPLIT_RE.split(raw):
        email = (
            value
            .strip()
            .strip('"')
            .strip("'")
            .strip("<>")
            .strip()
            .lower()
        )

        if not email:
            continue

        try:
            validate_email(email)
        except ValidationError as exc:
            raise CustomerMasterValidationError(
                f"Email inválido: {email}"
            ) from exc

        if email in seen:
            continue

        seen.add(email)
        emails.append(email)

    normalized = ", ".join(emails)

    # SN_Vta_Reg.Email_FV es nvarchar(500).
    if len(normalized) > 500:
        raise CustomerMasterValidationError(
            "La lista de correos supera los 500 caracteres."
        )

    return tuple(emails), normalized


def normalize_phone(raw_value):
    phone = str(raw_value or "").strip()

    # SN_Vta_Reg.Telefono es nvarchar(100).
    if len(phone) > 100:
        raise CustomerMasterValidationError(
            "El teléfono supera los 100 caracteres."
        )

    return phone


def validate_customer_master_contact(
    *,
    billing_emails_raw,
    phone,
):
    emails, normalized_emails = normalize_billing_emails(
        billing_emails_raw
    )

    normalized_phone = normalize_phone(phone)

    return CustomerMasterContactData(
        billing_emails=emails,
        billing_emails_raw=normalized_emails,
        phone=normalized_phone,
    )
