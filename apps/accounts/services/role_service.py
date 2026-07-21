from __future__ import annotations

from collections.abc import Iterable, Mapping

from django.conf import settings
from django.db import transaction

from apps.accounts.contracts import RoleResolution
from apps.accounts.models import Role, User


class RoleConfigurationError(ValueError):
    """
    Configuración inválida del mapeo grupo corporativo -> rol local.
    """


class RoleService:
    """
    Servicio central para resolución y sincronización de roles.

    Microsoft Entra ID informa grupos.
    Invoice Flow traduce esos grupos a roles funcionales locales.
    """

    ROLE_PRIORITY = (
        Role.ADMINISTRADOR,
        Role.SUPERVISOR,
        Role.COBRADOR,
        Role.CONSULTA_AUDITORIA,
    )

    VALID_ROLE_CODES = frozenset(ROLE_PRIORITY)

    @classmethod
    def get_group_role_mapping(cls) -> dict[str, str]:
        """
        Obtiene desde settings el mapeo:

            group_object_id -> role_code

        Los identificadores de grupo se normalizan a minúsculas.
        """

        configured_mapping = getattr(
            settings,
            "ENTRA_GROUP_ROLE_MAPPING",
            {},
        )

        if not isinstance(configured_mapping, Mapping):
            raise RoleConfigurationError(
                "ENTRA_GROUP_ROLE_MAPPING debe ser un diccionario."
            )

        normalized_mapping: dict[str, str] = {}

        for raw_group_id, raw_role_code in configured_mapping.items():
            group_id = str(raw_group_id).strip().lower()
            role_code = str(raw_role_code).strip().upper()

            if not group_id:
                raise RoleConfigurationError(
                    "ENTRA_GROUP_ROLE_MAPPING contiene un Group Object ID vacío."
                )

            if role_code not in cls.VALID_ROLE_CODES:
                raise RoleConfigurationError(
                    f"Rol no reconocido en ENTRA_GROUP_ROLE_MAPPING: {role_code}"
                )

            if group_id in normalized_mapping:
                raise RoleConfigurationError(
                    f"Group Object ID duplicado: {group_id}"
                )

            normalized_mapping[group_id] = role_code

        return normalized_mapping

    @classmethod
    def normalize_group_ids(
        cls,
        group_ids: Iterable[str] | None,
    ) -> frozenset[str]:
        if not group_ids:
            return frozenset()

        return frozenset(
            str(group_id).strip().lower()
            for group_id in group_ids
            if str(group_id).strip()
        )

    @classmethod
    def resolve_from_group_ids(
        cls,
        group_ids: Iterable[str] | None,
    ) -> RoleResolution:
        """
        Traduce grupos corporativos a todos los roles funcionales aplicables.
        """

        normalized_group_ids = cls.normalize_group_ids(group_ids)
        mapping = cls.get_group_role_mapping()

        matched_group_ids = normalized_group_ids.intersection(mapping.keys())

        role_codes = frozenset(
            mapping[group_id]
            for group_id in matched_group_ids
        )

        effective_role_code = cls.get_effective_role_code_from_codes(role_codes)

        return RoleResolution(
            role_codes=role_codes,
            effective_role_code=effective_role_code,
            matched_group_ids=frozenset(matched_group_ids),
        )

    @classmethod
    def get_effective_role_code_from_codes(
        cls,
        role_codes: Iterable[str] | None,
    ) -> str | None:
        normalized_codes = {
            str(role_code).strip().upper()
            for role_code in role_codes or []
        }

        for role_code in cls.ROLE_PRIORITY:
            if role_code in normalized_codes:
                return role_code

        return None

    @classmethod
    def get_user_role_codes(cls, user) -> frozenset[str]:
        if not user or not getattr(user, "is_authenticated", False):
            return frozenset()

        if getattr(user, "is_superuser", False):
            return frozenset({Role.ADMINISTRADOR})

        role_codes = user.roles.filter(
            is_active=True,
        ).values_list(
            "code",
            flat=True,
        )

        normalized_codes = {
            str(role_code).strip().upper()
            for role_code in role_codes
        }

        if (
            getattr(user, "is_staff", False)
            and Role.ADMINISTRADOR not in normalized_codes
        ):
            normalized_codes.add(Role.ADMINISTRADOR)

        return frozenset(normalized_codes)

    @classmethod
    def get_effective_role_code(cls, user) -> str | None:
        return cls.get_effective_role_code_from_codes(
            cls.get_user_role_codes(user)
        )

    @classmethod
    def user_has_role(cls, user, role_code: str) -> bool:
        normalized_role_code = str(role_code).strip().upper()

        if normalized_role_code not in cls.VALID_ROLE_CODES:
            return False

        return normalized_role_code in cls.get_user_role_codes(user)

    @classmethod
    @transaction.atomic
    def synchronize_user_roles(
        cls,
        *,
        user: User,
        role_codes: Iterable[str],
    ) -> list[Role]:
        """
        Reemplaza los roles funcionales del usuario por aquellos resueltos
        desde los grupos de Microsoft Entra ID.

        No crea códigos arbitrarios: todos deben existir en ROLE_CHOICES.
        """

        normalized_role_codes = {
            str(role_code).strip().upper()
            for role_code in role_codes
            if str(role_code).strip()
        }

        invalid_role_codes = normalized_role_codes.difference(
            cls.VALID_ROLE_CODES
        )

        if invalid_role_codes:
            invalid_display = ", ".join(sorted(invalid_role_codes))
            raise RoleConfigurationError(
                f"Se intentaron sincronizar roles inválidos: {invalid_display}"
            )

        existing_roles = {
            role.code: role
            for role in Role.objects.filter(
                code__in=normalized_role_codes,
                is_active=True,
            )
        }

        missing_role_codes = normalized_role_codes.difference(
            existing_roles.keys()
        )

        if missing_role_codes:
            missing_display = ", ".join(sorted(missing_role_codes))
            raise RoleConfigurationError(
                "Los siguientes roles no existen o están inactivos: "
                f"{missing_display}"
            )

        ordered_roles = [
            existing_roles[role_code]
            for role_code in cls.ROLE_PRIORITY
            if role_code in existing_roles
        ]

        user.roles.set(ordered_roles)

        return ordered_roles