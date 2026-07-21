from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class IdentityProvider(StrEnum):
    """
    Proveedores externos de identidad reconocidos por Invoice Flow.
    """

    MICROSOFT_ENTRA_ID = "microsoft_entra_id"


class IdentityFailureReason(StrEnum):
    """
    Razones funcionales y técnicas por las que una identidad puede ser rechazada.
    """

    INVALID_STATE = "invalid_state"
    INVALID_NONCE = "invalid_nonce"
    INVALID_TOKEN = "invalid_token"
    INVALID_TENANT = "invalid_tenant"
    INVALID_AUDIENCE = "invalid_audience"
    TOKEN_EXPIRED = "token_expired"
    MISSING_REQUIRED_CLAIMS = "missing_required_claims"

    ACCESS_GROUP_MISSING = "access_group_missing"
    FUNCTIONAL_GROUP_MISSING = "functional_group_missing"
    GROUP_OVERAGE_RESOLUTION_FAILED = "group_overage_resolution_failed"

    USER_EMAIL_COLLISION = "user_email_collision"
    EXTERNAL_ID_COLLISION = "external_id_collision"
    USER_DISABLED = "user_disabled"
    IDENTITY_DISABLED = "identity_disabled"

    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_CONFIGURATION_ERROR = "provider_configuration_error"
    UNKNOWN_ERROR = "unknown_error"


@dataclass(frozen=True, slots=True)
class ExternalIdentity:
    """
    Identidad corporativa normalizada.

    Este contrato es independiente de la estructura concreta que entregue
    Microsoft Entra ID. El adapter es responsable de transformar los claims
    del proveedor a este formato.
    """

    provider: IdentityProvider
    external_id: str
    tenant_id: str
    username: str
    email: str
    first_name: str = ""
    last_name: str = ""
    display_name: str = ""
    group_ids: frozenset[str] = field(default_factory=frozenset)
    claims: dict[str, Any] = field(default_factory=dict)

    def has_group(self, group_id: str) -> bool:
        normalized_group_id = group_id.strip().lower()

        return normalized_group_id in {
            current_group_id.strip().lower()
            for current_group_id in self.group_ids
        }


@dataclass(frozen=True, slots=True)
class RoleResolution:
    """
    Resultado del mapeo entre grupos corporativos y roles locales.
    """

    role_codes: frozenset[str]
    effective_role_code: str | None
    matched_group_ids: frozenset[str] = field(default_factory=frozenset)

    @property
    def has_functional_role(self) -> bool:
        return bool(self.role_codes)


@dataclass(frozen=True, slots=True)
class IdentityAuthorizationResult:
    """
    Resultado de autorización previo a crear o renovar una sesión Django.
    """

    is_authorized: bool
    identity: ExternalIdentity | None = None
    role_resolution: RoleResolution | None = None
    failure_reason: IdentityFailureReason | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def authorized(
        cls,
        *,
        identity: ExternalIdentity,
        role_resolution: RoleResolution,
        metadata: dict[str, Any] | None = None,
    ) -> "IdentityAuthorizationResult":
        return cls(
            is_authorized=True,
            identity=identity,
            role_resolution=role_resolution,
            metadata=metadata or {},
        )

    @classmethod
    def denied(
        cls,
        *,
        reason: IdentityFailureReason,
        identity: ExternalIdentity | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "IdentityAuthorizationResult":
        return cls(
            is_authorized=False,
            identity=identity,
            failure_reason=reason,
            metadata=metadata or {},
        )


class IdentityError(Exception):
    """
    Error base de integración de identidad.
    """

    default_reason = IdentityFailureReason.UNKNOWN_ERROR

    def __init__(
        self,
        message: str,
        *,
        reason: IdentityFailureReason | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.reason = reason or self.default_reason
        self.metadata = metadata or {}


class IdentityConfigurationError(IdentityError):
    default_reason = IdentityFailureReason.PROVIDER_CONFIGURATION_ERROR


class IdentityProviderError(IdentityError):
    default_reason = IdentityFailureReason.PROVIDER_UNAVAILABLE


class IdentityValidationError(IdentityError):
    default_reason = IdentityFailureReason.INVALID_TOKEN


class IdentityAuthorizationError(IdentityError):
    default_reason = IdentityFailureReason.ACCESS_GROUP_MISSING