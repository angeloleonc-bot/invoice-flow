from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Role
from apps.accounts.services.role_service import (
    RoleConfigurationError,
    RoleService,
)


User = get_user_model()


class RoleServiceResolutionTests(TestCase):
    @override_settings(
        ENTRA_GROUP_ROLE_MAPPING={
            "group-admin": Role.ADMINISTRADOR,
            "group-supervisor": Role.SUPERVISOR,
            "group-collector": Role.COBRADOR,
            "group-auditor": Role.CONSULTA_AUDITORIA,
        }
    )
    def test_resolves_all_roles_from_group_ids(self):
        result = RoleService.resolve_from_group_ids(
            {
                "GROUP-ADMIN",
                "group-collector",
                "unrelated-group",
            }
        )

        self.assertEqual(
            result.role_codes,
            frozenset(
                {
                    Role.ADMINISTRADOR,
                    Role.COBRADOR,
                }
            ),
        )
        self.assertEqual(
            result.effective_role_code,
            Role.ADMINISTRADOR,
        )
        self.assertEqual(
            result.matched_group_ids,
            frozenset(
                {
                    "group-admin",
                    "group-collector",
                }
            ),
        )

    @override_settings(
        ENTRA_GROUP_ROLE_MAPPING={
            "group-supervisor": Role.SUPERVISOR,
            "group-collector": Role.COBRADOR,
        }
    )
    def test_uses_role_priority_for_effective_role(self):
        result = RoleService.resolve_from_group_ids(
            {
                "group-collector",
                "group-supervisor",
            }
        )

        self.assertEqual(
            result.effective_role_code,
            Role.SUPERVISOR,
        )

    @override_settings(
        ENTRA_GROUP_ROLE_MAPPING={
            "group-admin": Role.ADMINISTRADOR,
        }
    )
    def test_returns_empty_resolution_without_matching_groups(self):
        result = RoleService.resolve_from_group_ids(
            {"unrelated-group"}
        )

        self.assertFalse(result.has_functional_role)
        self.assertIsNone(result.effective_role_code)
        self.assertEqual(result.matched_group_ids, frozenset())

    @override_settings(
        ENTRA_GROUP_ROLE_MAPPING={
            "group-invalid": "UNKNOWN_ROLE",
        }
    )
    def test_rejects_invalid_configured_role(self):
        with self.assertRaises(RoleConfigurationError):
            RoleService.resolve_from_group_ids(
                {"group-invalid"}
            )


class RoleServiceUserTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="collector@example.com",
            email="collector@example.com",
        )

        self.admin_role = Role.objects.get(
            code=Role.ADMINISTRADOR,
        )
        self.supervisor_role = Role.objects.get(
            code=Role.SUPERVISOR,
        )
        self.collector_role = Role.objects.get(
            code=Role.COBRADOR,
        )
        self.auditor_role = Role.objects.get(
            code=Role.CONSULTA_AUDITORIA,
        )

    def test_gets_effective_role_from_user_roles(self):
        self.user.roles.set(
            [
                self.collector_role,
                self.supervisor_role,
            ]
        )

        effective_role = RoleService.get_effective_role_code(
            self.user
        )

        self.assertEqual(
            effective_role,
            Role.SUPERVISOR,
        )

    def test_superuser_is_treated_as_administrator(self):
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])

        effective_role = RoleService.get_effective_role_code(
            self.user
        )

        self.assertEqual(
            effective_role,
            Role.ADMINISTRADOR,
        )

    def test_synchronizes_user_roles(self):
        synchronized_roles = RoleService.synchronize_user_roles(
            user=self.user,
            role_codes={
                Role.COBRADOR,
                Role.CONSULTA_AUDITORIA,
            },
        )

        self.assertEqual(
            [role.code for role in synchronized_roles],
            [
                Role.COBRADOR,
                Role.CONSULTA_AUDITORIA,
            ],
        )

        self.assertSetEqual(
            set(
                self.user.roles.values_list(
                    "code",
                    flat=True,
                )
            ),
            {
                Role.COBRADOR,
                Role.CONSULTA_AUDITORIA,
            },
        )

    def test_role_sync_replaces_previous_roles(self):
        self.user.roles.set(
            [
                self.admin_role,
                self.supervisor_role,
            ]
        )

        RoleService.synchronize_user_roles(
            user=self.user,
            role_codes={Role.COBRADOR},
        )

        self.assertSetEqual(
            set(
                self.user.roles.values_list(
                    "code",
                    flat=True,
                )
            ),
            {Role.COBRADOR},
        )

    def test_role_sync_fails_when_role_does_not_exist(self):
        self.collector_role.delete()

        with self.assertRaises(RoleConfigurationError):
            RoleService.synchronize_user_roles(
                user=self.user,
                role_codes={Role.COBRADOR},
            )