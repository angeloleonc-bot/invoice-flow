from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction

from apps.accounts.contracts import (
    ExternalIdentity,
    IdentityFailureReason,
    IdentityProvider,
    IdentityValidationError,
)
from apps.accounts.services.role_service import RoleService


User = get_user_model()


@dataclass(frozen=True)
class IdentityServiceResult:
    """
    Resultado interno de autorización.

    No crea una sesión Django. Solamente deja al usuario sincronizado y
    autorizado para que la vista de callback decida si ejecuta login().
    """

    user: Any
    identity: ExternalIdentity
    role_resolution: Any
    created: bool
    linked: bool


class IdentityService:
    """
    Orquesta la autorización de identidades externas en Django.

    Microsoft Entra ID autentica la identidad y entrega sus grupos.
    Django conserva la decisión final de acceso, roles y sesión.
    """

    @classmethod
    @transaction.atomic
    def authorize(
        cls,
        identity: ExternalIdentity,
    ) -> IdentityServiceResult:
        cls._validate_identity(identity)
        cls._validate_access_group(identity)

        role_resolution = RoleService.resolve_from_group_ids(
            identity.group_ids
        )

        cls._validate_role_resolution(role_resolution)

        user, created, linked = cls._resolve_user(identity)

        cls._validate_local_user(user)
        cls._synchronize_identity_fields(
            user=user,
            identity=identity,
        )

        RoleService.synchronize_user_roles(
            user=user,
            role_codes=role_resolution.role_codes,
        )

        return IdentityServiceResult(
            user=user,
            identity=identity,
            role_resolution=role_resolution,
            created=created,
            linked=linked,
        )

    @staticmethod
    def _validate_identity(identity: ExternalIdentity) -> None:
        if not identity:
            raise IdentityValidationError(
                "No se recibió una identidad externa.",
                reason=IdentityFailureReason.INVALID_TOKEN,
            )

        if identity.provider != IdentityProvider.MICROSOFT_ENTRA_ID:
            raise IdentityValidationError(
                "El proveedor de identidad no está autorizado.",
                reason=IdentityFailureReason.INVALID_TOKEN,
                metadata={
                    "received_provider": str(identity.provider),
                },
            )

        configured_tenant_id = str(
            settings.ENTRA_TENANT_ID
        ).strip().lower()

        received_tenant_id = str(
            identity.tenant_id
        ).strip().lower()

        if received_tenant_id != configured_tenant_id:
            raise IdentityValidationError(
                "La identidad pertenece a un tenant no autorizado.",
                reason=IdentityFailureReason.INVALID_TENANT,
                metadata={
                    "received_tenant_id": received_tenant_id,
                },
            )

        if not str(identity.external_id).strip():
            raise IdentityValidationError(
                "La identidad no posee un identificador externo estable.",
                reason=IdentityFailureReason.MISSING_REQUIRED_CLAIMS,
                metadata={
                    "missing_claims": ["oid"],
                },
            )

        if not str(identity.email).strip():
            raise IdentityValidationError(
                "La identidad no posee un correo utilizable.",
                reason=IdentityFailureReason.MISSING_REQUIRED_CLAIMS,
                metadata={
                    "missing_claims": ["email"],
                },
            )

    @staticmethod
    def _validate_access_group(identity: ExternalIdentity) -> None:
        access_group_id = str(
            settings.ENTRA_ACCESS_GROUP_ID
        ).strip().lower()

        if not access_group_id:
            raise IdentityValidationError(
                "No está configurado el grupo general de acceso.",
                reason=IdentityFailureReason.ACCESS_DENIED,
                metadata={
                    "missing_settings": [
                        "ENTRA_ACCESS_GROUP_ID",
                    ],
                },
            )

        normalized_group_ids = {
            str(group_id).strip().lower()
            for group_id in identity.group_ids
            if str(group_id).strip()
        }

        if access_group_id not in normalized_group_ids:
            raise IdentityValidationError(
                "El usuario no pertenece al grupo autorizado para Invoice Flow.",
                reason=IdentityFailureReason.ACCESS_DENIED,
            )

    @staticmethod
    def _validate_role_resolution(role_resolution: Any) -> None:
        """
        Además del grupo general, el usuario debe poseer al menos un grupo
        funcional reconocido por Django.
        """

        role_codes = getattr(
            role_resolution,
            "role_codes",
            None,
        )

        if role_codes is None:
            roles = getattr(
                role_resolution,
                "roles",
                (),
            )
            role_codes = tuple(
                getattr(role, "code", role)
                for role in roles
            )

        if not role_codes:
            raise IdentityValidationError(
                "El usuario tiene acceso general, pero no posee un rol "
                "funcional válido.",
                reason=IdentityFailureReason.ACCESS_DENIED,
            )

    @classmethod
    def _resolve_user(
        cls,
        identity: ExternalIdentity,
    ) -> tuple[Any, bool, bool]:
        """
        Orden de resolución:

        1. Busca una vinculación existente por proveedor + external_id.
        2. Si no existe, busca un usuario local por correo.
        3. Si el usuario local no está vinculado, lo vincula.
        4. Si no existe, crea un usuario nuevo.

        Nunca reemplaza una vinculación externa diferente.
        """

        external_id = str(
            identity.external_id
        ).strip().lower()

        provider_value = cls._provider_database_value(
            identity.provider
        )

        existing_external_user = (
            User.objects
            .select_for_update()
            .filter(
                external_id=external_id,
                identity_provider=provider_value,
            )
            .first()
        )

        if existing_external_user:
            return existing_external_user, False, False

        email = str(identity.email).strip().lower()

        email_matches = list(
            User.objects
            .select_for_update()
            .filter(email__iexact=email)
            .order_by("pk")[:2]
        )

        if len(email_matches) > 1:
            raise IdentityValidationError(
                "Existe más de un usuario local con el correo autenticado.",
                reason=IdentityFailureReason.ACCESS_DENIED,
                metadata={
                    "conflict": "duplicate_email",
                },
            )

        if email_matches:
            user = email_matches[0]

            current_external_id = str(
                getattr(user, "external_id", "") or ""
            ).strip().lower()

            current_provider = str(
                getattr(user, "identity_provider", "") or ""
            ).strip()

            if (
                current_external_id
                and current_external_id != external_id
            ):
                raise IdentityValidationError(
                    "El usuario local ya está vinculado a otra identidad.",
                    reason=IdentityFailureReason.ACCESS_DENIED,
                    metadata={
                        "conflict": "external_identity_mismatch",
                    },
                )

            if (
                current_provider
                and current_provider != provider_value
            ):
                raise IdentityValidationError(
                    "El usuario local ya está vinculado a otro proveedor.",
                    reason=IdentityFailureReason.ACCESS_DENIED,
                    metadata={
                        "conflict": "identity_provider_mismatch",
                    },
                )

            return user, False, True

        username = cls._build_unique_username(identity)

        user = User(
            username=username,
            email=email,
        )

        user.set_unusable_password()
        user.save()

        return user, True, False

    @classmethod
    def _synchronize_identity_fields(
        cls,
        *,
        user: Any,
        identity: ExternalIdentity,
    ) -> None:
        """
        Sincroniza únicamente datos controlados por Entra.

        No reactiva `is_active`: esa bandera sigue siendo una decisión
        administrativa local de Django.
        """

        provider_value = cls._provider_database_value(
            identity.provider
        )

        values = {
            "external_id": str(
                identity.external_id
            ).strip().lower(),
            "identity_provider": provider_value,
            "is_identity_active": True,
            "email": str(identity.email).strip().lower(),
            "first_name": str(
                identity.first_name or ""
            ).strip(),
            "last_name": str(
                identity.last_name or ""
            ).strip(),
        }

        update_fields: list[str] = []

        for field_name, new_value in values.items():
            current_value = getattr(
                user,
                field_name,
                None,
            )

            if current_value != new_value:
                setattr(user, field_name, new_value)
                update_fields.append(field_name)

        if update_fields:
            if hasattr(user, "updated_at"):
                update_fields.append("updated_at")

            user.save(update_fields=update_fields)

    @staticmethod
    def _validate_local_user(user: Any) -> None:
        if not user.is_active:
            raise IdentityValidationError(
                "El usuario está deshabilitado localmente en Invoice Flow.",
                reason=IdentityFailureReason.ACCESS_DENIED,
            )

    @classmethod
    def _build_unique_username(
        cls,
        identity: ExternalIdentity,
    ) -> str:
        base_username = str(
            identity.username
            or identity.email.split("@", 1)[0]
            or "entra-user"
        ).strip().lower()

        base_username = (
            base_username
            .replace(" ", ".")
            .replace("@", ".")
        )

        max_length = User._meta.get_field(
            User.USERNAME_FIELD
        ).max_length or 150

        base_username = base_username[:max_length]

        candidate = base_username
        suffix = 1

        while User.objects.filter(
            username__iexact=candidate
        ).exists():
            suffix_text = f"-{suffix}"
            candidate = (
                base_username[
                    : max_length - len(suffix_text)
                ]
                + suffix_text
            )
            suffix += 1

        return candidate

    @staticmethod
    def _provider_database_value(
        provider: IdentityProvider,
    ) -> str:
        return getattr(
            provider,
            "value",
            str(provider),
        )