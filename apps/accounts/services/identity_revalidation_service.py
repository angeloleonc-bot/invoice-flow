from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import transaction

from apps.accounts.adapters.azure_identity import (
    AzureIdentityAdapter,
)
from apps.accounts.contracts import (
    IdentityFailureReason,
    IdentityValidationError,
)
from apps.accounts.services.role_service import (
    RoleService,
)


@dataclass(frozen=True)
class IdentityRevalidationResult:
    user: Any
    group_ids: frozenset[str]
    role_resolution: Any


class IdentityRevalidationService:
    """
    Revalida acceso y roles de un usuario ya autenticado.

    La identidad externa permanece en Entra.
    Django conserva la decisión final de autorización.
    """

    @classmethod
    @transaction.atomic
    def revalidate(
        cls,
        *,
        user: Any,
        adapter: AzureIdentityAdapter | None = None,
    ) -> IdentityRevalidationResult:
        cls._validate_local_user(user)

        external_id = str(
            getattr(user, "external_id", "") or ""
        ).strip().lower()

        if not external_id:
            raise IdentityValidationError(
                "El usuario no posee identidad externa.",
                reason=(
                    IdentityFailureReason
                    .MISSING_REQUIRED_CLAIMS
                ),
            )

        identity_adapter = (
            adapter or AzureIdentityAdapter()
        )

        group_ids = (
            identity_adapter
            .fetch_user_group_ids_app_only(
                external_id=external_id,
            )
        )

        cls._validate_access_group(group_ids)

        role_resolution = (
            RoleService.resolve_from_group_ids(
                group_ids
            )
        )

        if not role_resolution.role_codes:
            raise IdentityValidationError(
                "El usuario ya no posee un rol funcional válido.",
                reason=(
                    IdentityFailureReason
                    .ACCESS_DENIED
                ),
            )

        RoleService.synchronize_user_roles(
            user=user,
            role_codes=role_resolution.role_codes,
        )

        update_fields: list[str] = []

        if not user.is_identity_active:
            user.is_identity_active = True
            update_fields.append(
                "is_identity_active"
            )

        if update_fields:
            if hasattr(user, "updated_at"):
                update_fields.append("updated_at")

            user.save(
                update_fields=update_fields
            )

        return IdentityRevalidationResult(
            user=user,
            group_ids=group_ids,
            role_resolution=role_resolution,
        )

    @staticmethod
    def _validate_local_user(user: Any) -> None:
        if (
            not user
            or not user.is_authenticated
            or not user.is_active
        ):
            raise IdentityValidationError(
                "El usuario local no está autorizado.",
                reason=(
                    IdentityFailureReason
                    .ACCESS_DENIED
                ),
            )

    @staticmethod
    def _validate_access_group(
        group_ids: frozenset[str],
    ) -> None:
        access_group_id = str(
            settings.ENTRA_ACCESS_GROUP_ID
        ).strip().lower()

        normalized_group_ids = {
            str(group_id).strip().lower()
            for group_id in group_ids
            if str(group_id).strip()
        }

        if (
            not access_group_id
            or access_group_id
            not in normalized_group_ids
        ):
            raise IdentityValidationError(
                "El usuario ya no pertenece al grupo general de acceso.",
                reason=(
                    IdentityFailureReason
                    .ACCESS_DENIED
                ),
            )