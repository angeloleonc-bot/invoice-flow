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

from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from apps.accounts.adapters.azure_identity import AzureIdentityAdapter
from apps.accounts.contracts import (
    IdentityFailureReason,
    IdentityProvider,
    IdentityProviderError,
    IdentityValidationError,
)


ENTRA_TEST_SETTINGS = {
    "ENTRA_TENANT_ID": "11111111-1111-1111-1111-111111111111",
    "ENTRA_CLIENT_ID": "22222222-2222-2222-2222-222222222222",
    "ENTRA_CLIENT_SECRET": "test-secret",
    "ENTRA_AUTHORITY": (
        "https://login.microsoftonline.com/"
        "11111111-1111-1111-1111-111111111111"
    ),
    "ENTRA_REDIRECT_URI": (
        "http://localhost:8000/accounts/auth/callback/"
    ),
    "ENTRA_SCOPES": (
        "User.Read",
        "GroupMember.Read.All",
    ),
}


@override_settings(**ENTRA_TEST_SETTINGS)
class AzureIdentityAdapterTests(SimpleTestCase):
    def setUp(self):
        client_patcher = patch(
            "apps.accounts.adapters.azure_identity."
            "msal.ConfidentialClientApplication"
        )
        self.addCleanup(client_patcher.stop)

        client_class = client_patcher.start()
        self.msal_client = Mock()
        client_class.return_value = self.msal_client

        self.http_session = Mock()

        self.adapter = AzureIdentityAdapter(
            http_session=self.http_session,
        )

    def build_valid_claims(self, **overrides):
        claims = {
            "oid": "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA",
            "tid": ENTRA_TEST_SETTINGS["ENTRA_TENANT_ID"],
            "aud": ENTRA_TEST_SETTINGS["ENTRA_CLIENT_ID"],
            "iss": (
                "https://login.microsoftonline.com/"
                f"{ENTRA_TEST_SETTINGS['ENTRA_TENANT_ID']}/v2.0"
            ),
            "exp": 9999999999,
            "preferred_username": "user@example.com",
            "email": "user@example.com",
            "name": "Test User",
            "given_name": "Test",
            "family_name": "User",
            "groups": [
                "BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB",
            ],
        }
        claims.update(overrides)
        return claims

    def test_initiates_auth_code_flow(self):
        self.msal_client.initiate_auth_code_flow.return_value = {
            "auth_uri": "https://login.microsoftonline.com/example",
            "state": "secure-state",
        }

        flow = self.adapter.initiate_auth_code_flow(
            state="secure-state",
        )

        self.assertEqual(
            flow["auth_uri"],
            "https://login.microsoftonline.com/example",
        )
        self.assertEqual(flow["state"], "secure-state")

    def test_builds_external_identity_from_token_groups(self):
        self.msal_client.acquire_token_by_auth_code_flow.return_value = {
            "access_token": "not-a-real-token",
            "id_token_claims": self.build_valid_claims(),
        }

        identity = self.adapter.complete_auth_code_flow(
            auth_code_flow={"state": "secure-state"},
            auth_response={
                "code": "authorization-code",
                "state": "secure-state",
            },
        )

        self.assertEqual(
            identity.provider,
            IdentityProvider.MICROSOFT_ENTRA_ID,
        )
        self.assertEqual(
            identity.external_id,
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        )
        self.assertEqual(identity.email, "user@example.com")
        self.assertEqual(
            identity.group_ids,
            frozenset(
                {
                    "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                }
            ),
        )

    def test_rejects_invalid_tenant(self):
        claims = self.build_valid_claims(
            tid="99999999-9999-9999-9999-999999999999",
        )

        self.msal_client.acquire_token_by_auth_code_flow.return_value = {
            "access_token": "not-a-real-token",
            "id_token_claims": claims,
        }

        with self.assertRaises(IdentityValidationError) as context:
            self.adapter.complete_auth_code_flow(
                auth_code_flow={"state": "secure-state"},
                auth_response={
                    "code": "authorization-code",
                    "state": "secure-state",
                },
            )

        self.assertEqual(
            context.exception.reason,
            IdentityFailureReason.INVALID_TENANT,
        )

    def test_rejects_invalid_audience(self):
        claims = self.build_valid_claims(
            aud="wrong-client-id",
        )

        self.msal_client.acquire_token_by_auth_code_flow.return_value = {
            "access_token": "not-a-real-token",
            "id_token_claims": claims,
        }

        with self.assertRaises(IdentityValidationError) as context:
            self.adapter.complete_auth_code_flow(
                auth_code_flow={"state": "secure-state"},
                auth_response={
                    "code": "authorization-code",
                    "state": "secure-state",
                },
            )

        self.assertEqual(
            context.exception.reason,
            IdentityFailureReason.INVALID_AUDIENCE,
        )

    def test_detects_group_overage(self):
        claims = self.build_valid_claims(
            groups=None,
            _claim_names={"groups": "src1"},
            _claim_sources={
                "src1": {
                    "endpoint": (
                        "https://graph.microsoft.com/"
                        "v1.0/users/example/getMemberObjects"
                    )
                }
            },
        )

        self.assertTrue(
            self.adapter._has_group_overage(claims)
        )

    def test_fetches_groups_from_graph_on_overage(self):
        first_group_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        second_group_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"

        claims = self.build_valid_claims(
            groups=None,
            _claim_names={"groups": "src1"},
        )

        graph_response = Mock()
        graph_response.status_code = 200
        graph_response.headers = {}
        graph_response.json.return_value = {
            "value": [
                {
                    "id": first_group_id.upper(),
                },
                {
                    "id": second_group_id.upper(),
                },
            ]
        }

        self.http_session.get.return_value = graph_response

        self.msal_client.acquire_token_by_auth_code_flow.return_value = {
            "access_token": "not-a-real-token",
            "id_token_claims": claims,
        }

        identity = self.adapter.complete_auth_code_flow(
            auth_code_flow={"state": "secure-state"},
            auth_response={
                "code": "authorization-code",
                "state": "secure-state",
            },
        )

        self.assertEqual(
            identity.group_ids,
            frozenset(
                {
                    first_group_id,
                    second_group_id,
                }
            ),
        )

    def test_fails_closed_when_graph_rejects_request(self):
        claims = self.build_valid_claims(
            groups=None,
            hasgroups=True,
        )

        graph_response = Mock()
        graph_response.status_code = 403
        graph_response.headers = {
            "request-id": "graph-correlation-id",
        }

        self.http_session.get.return_value = graph_response

        self.msal_client.acquire_token_by_auth_code_flow.return_value = {
            "access_token": "not-a-real-token",
            "id_token_claims": claims,
        }

        with self.assertRaises(IdentityProviderError) as context:
            self.adapter.complete_auth_code_flow(
                auth_code_flow={"state": "secure-state"},
                auth_response={
                    "code": "authorization-code",
                    "state": "secure-state",
                },
            )

        self.assertEqual(
            context.exception.reason,
            (
                IdentityFailureReason
                .GROUP_OVERAGE_RESOLUTION_FAILED
            ),
        )