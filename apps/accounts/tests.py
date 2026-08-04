from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.template.loader import render_to_string
from django.test import (
    RequestFactory,
    TestCase,
    override_settings,
)
from django.urls import reverse

from apps.accounts.context_processors import current_user_context
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
    "ENTRA_APPLICATION_SCOPES": (
        "https://graph.microsoft.com/.default",
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

    def test_fetches_user_groups_with_application_token(self):
        first_group_id = (
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        )
        second_group_id = (
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        )

        self.msal_client.acquire_token_for_client.return_value = {
            "access_token": "application-token",
        }

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

        group_ids = (
            self.adapter
            .fetch_user_group_ids_app_only(
                external_id=(
                    "99999999-9999-9999-9999-999999999999"
                )
            )
        )

        self.assertEqual(
            group_ids,
            frozenset(
                {
                    first_group_id,
                    second_group_id,
                }
            ),
        )

        self.msal_client.acquire_token_for_client.assert_called_once_with(
            scopes=[
                "https://graph.microsoft.com/.default",
            ]
        )

    def test_application_token_failure_is_closed(self):
        self.msal_client.acquire_token_for_client.return_value = {
            "error": "invalid_client",
            "correlation_id": "test-correlation-id",
        }

        with self.assertRaises(
            IdentityProviderError
        ):
            (
                self.adapter
                .fetch_user_group_ids_app_only(
                    external_id=(
                        "99999999-9999-9999-9999-999999999999"
                    )
                )
            )

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.contracts import (
    ExternalIdentity,
    IdentityFailureReason,
    IdentityProvider,
    IdentityValidationError,
)
from apps.accounts.models import Role
from apps.accounts.services.identity_service import IdentityService


IdentityUser = get_user_model()

IDENTITY_SERVICE_SETTINGS = {
    "ENTRA_TENANT_ID": (
        "11111111-1111-1111-1111-111111111111"
    ),
    "ENTRA_ACCESS_GROUP_ID": (
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    ),
    "ENTRA_GROUP_ROLE_MAPPING": {
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb": (
            Role.ADMINISTRADOR
        ),
        "cccccccc-cccc-cccc-cccc-cccccccccccc": (
            Role.SUPERVISOR
        ),
        "dddddddd-dddd-dddd-dddd-dddddddddddd": (
            Role.COBRADOR
        ),
        "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee": (
            Role.CONSULTA_AUDITORIA
        ),
    },
}


@override_settings(**IDENTITY_SERVICE_SETTINGS)
class IdentityServiceTests(TestCase):
    def build_identity(self, **overrides):
        values = {
            "provider": IdentityProvider.MICROSOFT_ENTRA_ID,
            "external_id": (
                "99999999-9999-9999-9999-999999999999"
            ),
            "tenant_id": (
                IDENTITY_SERVICE_SETTINGS["ENTRA_TENANT_ID"]
            ),
            "username": "test.user@example.com",
            "email": "test.user@example.com",
            "first_name": "Test",
            "last_name": "User",
            "display_name": "Test User",
            "group_ids": frozenset(
                {
                    IDENTITY_SERVICE_SETTINGS[
                        "ENTRA_ACCESS_GROUP_ID"
                    ],
                    (
                        "cccccccc-cccc-cccc-cccc-"
                        "cccccccccccc"
                    ),
                }
            ),
            "claims": {},
        }
        values.update(overrides)
        return ExternalIdentity(**values)

    def test_creates_user_and_synchronizes_role(self):
        result = IdentityService.authorize(
            self.build_identity()
        )

        self.assertTrue(result.created)
        self.assertFalse(result.linked)
        self.assertEqual(
            result.user.external_id,
            "99999999-9999-9999-9999-999999999999",
        )
        self.assertTrue(result.user.is_identity_active)
        self.assertTrue(
            result.user.roles.filter(
                code=Role.SUPERVISOR
            ).exists()
        )

    def test_reuses_existing_external_identity(self):
        first_result = IdentityService.authorize(
            self.build_identity()
        )

        second_result = IdentityService.authorize(
            self.build_identity(
                first_name="Updated",
            )
        )

        self.assertEqual(
            first_result.user.pk,
            second_result.user.pk,
        )
        self.assertFalse(second_result.created)
        self.assertFalse(second_result.linked)

        second_result.user.refresh_from_db()
        self.assertEqual(
            second_result.user.first_name,
            "Updated",
        )

    def test_links_existing_local_user_by_email(self):
        local_user = IdentityUser.objects.create_user(
            username="local-user",
            email="test.user@example.com",
            password="temporary-password",
        )

        result = IdentityService.authorize(
            self.build_identity()
        )

        self.assertFalse(result.created)
        self.assertTrue(result.linked)
        self.assertEqual(
            result.user.pk,
            local_user.pk,
        )

        result.user.refresh_from_db()

        self.assertEqual(
            result.user.external_id,
            "99999999-9999-9999-9999-999999999999",
        )

    def test_rejects_user_without_access_group(self):
        identity = self.build_identity(
            group_ids=frozenset(
                {
                    (
                        "cccccccc-cccc-cccc-cccc-"
                        "cccccccccccc"
                    ),
                }
            ),
        )

        with self.assertRaises(
            IdentityValidationError
        ) as context:
            IdentityService.authorize(identity)

        self.assertEqual(
            context.exception.reason,
            IdentityFailureReason.ACCESS_DENIED,
        )

    def test_rejects_user_without_functional_role(self):
        identity = self.build_identity(
            group_ids=frozenset(
                {
                    IDENTITY_SERVICE_SETTINGS[
                        "ENTRA_ACCESS_GROUP_ID"
                    ],
                }
            ),
        )

        with self.assertRaises(
            IdentityValidationError
        ) as context:
            IdentityService.authorize(identity)

        self.assertEqual(
            context.exception.reason,
            IdentityFailureReason.ACCESS_DENIED,
        )

    def test_rejects_disabled_local_user(self):
        user = IdentityUser.objects.create_user(
            username="disabled-user",
            email="test.user@example.com",
            password="temporary-password",
            is_active=False,
        )

        with self.assertRaises(
            IdentityValidationError
        ):
            IdentityService.authorize(
                self.build_identity()
            )

        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_rejects_different_external_identity_link(self):
        user = IdentityUser.objects.create_user(
            username="linked-user",
            email="test.user@example.com",
            password="temporary-password",
        )
        user.external_id = (
            "88888888-8888-8888-8888-888888888888"
        )
        user.identity_provider = (
            IdentityProvider.MICROSOFT_ENTRA_ID.value
        )
        user.save(
            update_fields=[
                "external_id",
                "identity_provider",
            ]
        )

        with self.assertRaises(
            IdentityValidationError
        ) as context:
            IdentityService.authorize(
                self.build_identity()
            )

        self.assertEqual(
            context.exception.reason,
            IdentityFailureReason.ACCESS_DENIED,
        )

    def test_replaces_previous_roles_during_login(self):
        user = IdentityUser.objects.create_user(
            username="existing-user",
            email="test.user@example.com",
            password="temporary-password",
        )

        administrator = Role.objects.get(
            code=Role.ADMINISTRADOR
        )
        user.roles.add(administrator)

        result = IdentityService.authorize(
            self.build_identity()
        )

        result.user.refresh_from_db()

        role_codes = set(
            result.user.roles.values_list(
                "code",
                flat=True,
            )
        )

        self.assertEqual(
            role_codes,
            {Role.SUPERVISOR},
        )

from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.contracts import (
    ExternalIdentity,
    IdentityProvider,
)
from apps.accounts.models import Role
from apps.accounts.services.identity_service import (
    IdentityServiceResult,
)


AuthViewUser = get_user_model()


AUTH_VIEW_SETTINGS = {
    "ENTRA_AUTH_ENABLED": True,
    "DEV_LOGIN_ENABLED": True,
    "ENTRA_GLOBAL_LOGOUT_ENABLED": False,
    "ENTRA_AUTHORITY": (
        "https://login.microsoftonline.com/"
        "11111111-1111-1111-1111-111111111111"
    ),
    "ENTRA_POST_LOGOUT_REDIRECT_URI": (
        "http://localhost:8000/accounts/login/"
    ),
    "INACTIVITY_TIMEOUT_MINUTES": 30,
    "LOGIN_REDIRECT_URL": "/",
}


@override_settings(**AUTH_VIEW_SETTINGS)
class EntraAuthenticationViewTests(TestCase):
    def setUp(self):
        self.user = AuthViewUser.objects.create_user(
            username="entra-view-user",
            email="entra-view-user@example.com",
            password="temporary-password",
        )

        self.role_resolution = Mock()
        self.role_resolution.role_codes = frozenset(
            {
                Role.SUPERVISOR,
            }
        )

        self.identity = ExternalIdentity(
            provider=(
                IdentityProvider.MICROSOFT_ENTRA_ID
            ),
            external_id=(
                "99999999-9999-9999-9999-999999999999"
            ),
            tenant_id=(
                "11111111-1111-1111-1111-111111111111"
            ),
            username="entra-view-user@example.com",
            email="entra-view-user@example.com",
            first_name="Entra",
            last_name="User",
            display_name="Entra User",
            group_ids=frozenset(),
            claims={},
        )

    def test_login_page_is_available(self):
        response = self.client.get(
            reverse("accounts:login")
        )

        self.assertEqual(
            response.status_code,
            200,
        )

    @patch(
        "apps.accounts.views.AzureIdentityAdapter"
    )
    def test_entra_login_stores_flow_in_session(
        self,
        adapter_class,
    ):
        adapter = adapter_class.return_value
        adapter.initiate_auth_code_flow.return_value = {
            "auth_uri": (
                "https://login.microsoftonline.com/"
                "example/authorize"
            ),
            "state": "secure-state",
        }

        response = self.client.get(
            reverse("accounts:entra_login"),
            {
                "next": "/portfolio/",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            response.url,
            (
                "https://login.microsoftonline.com/"
                "example/authorize"
            ),
        )

        session = self.client.session

        self.assertIn(
            "entra_auth_code_flow",
            session,
        )

        self.assertEqual(
            session["entra_auth_next"],
            "/portfolio/",
        )

    def test_entra_login_rejects_external_next_url(self):
        with patch(
            "apps.accounts.views.AzureIdentityAdapter"
        ) as adapter_class:
            adapter = adapter_class.return_value
            adapter.initiate_auth_code_flow.return_value = {
                "auth_uri": (
                    "https://login.microsoftonline.com/"
                    "example/authorize"
                ),
                "state": "secure-state",
            }

            self.client.get(
                reverse("accounts:entra_login"),
                {
                    "next": "https://malicious.example/",
                },
            )

        session = self.client.session

        self.assertEqual(
            session["entra_auth_next"],
            "/",
        )

    @patch(
        "apps.accounts.views.IdentityService.authorize"
    )
    @patch(
        "apps.accounts.views.AzureIdentityAdapter"
    )
    def test_callback_logs_user_in(
        self,
        adapter_class,
        authorize_mock,
    ):
        session = self.client.session
        session["entra_auth_code_flow"] = {
            "state": "secure-state",
        }
        session["entra_auth_next"] = "/portfolio/"
        session.save()

        adapter = adapter_class.return_value
        adapter.complete_auth_code_flow.return_value = (
            self.identity
        )

        authorize_mock.return_value = (
            IdentityServiceResult(
                user=self.user,
                identity=self.identity,
                role_resolution=self.role_resolution,
                created=False,
                linked=False,
            )
        )

        response = self.client.get(
            reverse("accounts:entra_callback"),
            {
                "code": "authorization-code",
                "state": "secure-state",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            response.url,
            "/portfolio/",
        )

        self.assertEqual(
            int(
                self.client.session[
                    "_auth_user_id"
                ]
            ),
            self.user.pk,
        )

        self.assertIn(
            "identity_last_validated_at",
            self.client.session,
        )

    @patch(
        "apps.accounts.views.AzureIdentityAdapter"
    )
    def test_callback_without_flow_returns_to_login(
        self,
        adapter_class,
    ):
        from apps.accounts.contracts import (
            IdentityFailureReason,
            IdentityValidationError,
        )

        adapter = adapter_class.return_value

        adapter.complete_auth_code_flow.side_effect = (
            IdentityValidationError(
                "Invalid state.",
                reason=(
                    IdentityFailureReason.INVALID_STATE
                ),
            )
        )

        response = self.client.get(
            reverse("accounts:entra_callback"),
            {
                "code": "authorization-code",
                "state": "invalid-state",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            response.url,
            reverse("accounts:login"),
        )

        self.assertNotIn(
            "_auth_user_id",
            self.client.session,
        )

    def test_logout_requires_post(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("accounts:logout")
        )

        self.assertEqual(
            response.status_code,
            405,
        )

    def test_local_logout_clears_session(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("accounts:logout")
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            response.url,
            reverse("accounts:login"),
        )

        self.assertNotIn(
            "_auth_user_id",
            self.client.session,
        )

    @override_settings(
        ENTRA_GLOBAL_LOGOUT_ENABLED=True
    )
    def test_global_logout_redirects_to_microsoft(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("accounts:logout")
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertTrue(
            response.url.startswith(
                AUTH_VIEW_SETTINGS[
                    "ENTRA_AUTHORITY"
                ]
                + "/oauth2/v2.0/logout?"
            )
        )

        self.assertIn(
            "post_logout_redirect_uri=",
            response.url,
        )

from apps.accounts.services.identity_revalidation_service import (
    IdentityRevalidationService,
)


@override_settings(**IDENTITY_SERVICE_SETTINGS)
class IdentityRevalidationServiceTests(TestCase):
    def setUp(self):
        self.user = IdentityUser.objects.create_user(
            username="revalidation-user",
            email="revalidation@example.com",
            password="temporary-password",
        )

        self.user.external_id = (
            "99999999-9999-9999-9999-999999999999"
        )
        self.user.identity_provider = (
            IdentityProvider
            .MICROSOFT_ENTRA_ID
            .value
        )
        self.user.is_identity_active = True

        self.user.save(
            update_fields=[
                "external_id",
                "identity_provider",
                "is_identity_active",
            ]
        )

    def test_revalidates_and_synchronizes_roles(self):
        adapter = Mock()

        adapter.fetch_user_group_ids_app_only.return_value = (
            frozenset(
                {
                    IDENTITY_SERVICE_SETTINGS[
                        "ENTRA_ACCESS_GROUP_ID"
                    ],
                    (
                        "cccccccc-cccc-cccc-cccc-"
                        "cccccccccccc"
                    ),
                }
            )
        )

        result = (
            IdentityRevalidationService.revalidate(
                user=self.user,
                adapter=adapter,
            )
        )

        self.assertEqual(
            result.role_resolution.role_codes,
            frozenset(
                {
                    Role.SUPERVISOR,
                }
            ),
        )

        self.assertTrue(
            self.user.roles.filter(
                code=Role.SUPERVISOR
            ).exists()
        )

    def test_rejects_removed_access_group(self):
        adapter = Mock()

        adapter.fetch_user_group_ids_app_only.return_value = (
            frozenset(
                {
                    (
                        "cccccccc-cccc-cccc-cccc-"
                        "cccccccccccc"
                    ),
                }
            )
        )

        with self.assertRaises(
            IdentityValidationError
        ) as context:
            IdentityRevalidationService.revalidate(
                user=self.user,
                adapter=adapter,
            )

        self.assertEqual(
            context.exception.reason,
            IdentityFailureReason.ACCESS_DENIED,
        )

    def test_rejects_removed_functional_roles(self):
        adapter = Mock()

        adapter.fetch_user_group_ids_app_only.return_value = (
            frozenset(
                {
                    IDENTITY_SERVICE_SETTINGS[
                        "ENTRA_ACCESS_GROUP_ID"
                    ],
                }
            )
        )

        with self.assertRaises(
            IdentityValidationError
        ):
            IdentityRevalidationService.revalidate(
                user=self.user,
                adapter=adapter,
            )

class LoginViewTests(TestCase):
    @override_settings(
        ENTRA_AUTH_ENABLED=True,
        LOGIN_REDIRECT_URL="/",
    )
    def test_login_renders_corporate_microsoft_action(self):
        response = self.client.get(
            reverse("accounts:login")
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "accounts/login.html",
        )
        self.assertContains(
            response,
            "Continuar con Microsoft",
        )
        self.assertContains(
            response,
            reverse("accounts:entra_login"),
        )
        self.assertNotContains(
            response,
            "modo desarrollo",
        )
        self.assertNotContains(
            response,
            "dev-login",
        )

    @override_settings(
        ENTRA_AUTH_ENABLED=False,
        LOGIN_REDIRECT_URL="/",
    )
    def test_login_shows_message_when_entra_is_disabled(self):
        response = self.client.get(
            reverse("accounts:login")
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "El acceso corporativo todavía no está habilitado.",
        )
        self.assertNotContains(
            response,
            "Continuar con Microsoft",
        )

    @override_settings(
        ENTRA_AUTH_ENABLED=True,
        LOGIN_REDIRECT_URL="/",
    )
    def test_authenticated_user_is_redirected_from_login(self):
        user = User.objects.create_user(
            username="authenticated.user",
            email="authenticated.user@example.com",
        )

        self.client.force_login(user)

        response = self.client.get(
            reverse("accounts:login")
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/")

    @override_settings(
        ENTRA_AUTH_ENABLED=True,
        LOGIN_REDIRECT_URL="/",
    )
    def test_login_rejects_external_next_url(self):
        response = self.client.get(
            reverse("accounts:login"),
            {
                "next": "https://example.com/malicious",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["next_url"],
            "/",
        )

    @override_settings(
        ENTRA_AUTH_ENABLED=True,
        LOGIN_REDIRECT_URL="/",
    )
    def test_login_accepts_internal_next_url(self):
        response = self.client.get(
            reverse("accounts:login"),
            {
                "next": "/portfolio/customers/",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["next_url"],
            "/portfolio/customers/",
        )


class CurrentUserContextTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _build_request(self, user):
        request = self.factory.get("/")
        request.user = user
        return request

    def test_uses_full_name_when_available(self):
        user = User.objects.create_user(
            username="angelo.leon",
            email="angelo.leon@example.com",
            first_name="Angelo",
            last_name="León",
        )

        context = current_user_context(
            self._build_request(user)
        )

        self.assertEqual(
            context["current_user_display_name"],
            "Angelo León",
        )
        self.assertEqual(
            context["current_user_initials"],
            "AL",
        )
        self.assertEqual(
            context["current_user_identifier"],
            "angelo.leon@example.com",
        )

    def test_uses_username_when_full_name_is_empty(self):
        user = User.objects.create_user(
            username="corporate.user",
            email="",
        )

        context = current_user_context(
            self._build_request(user)
        )

        self.assertEqual(
            context["current_user_display_name"],
            "corporate.user",
        )
        self.assertEqual(
            context["current_user_initials"],
            "CO",
        )
        self.assertEqual(
            context["current_user_identifier"],
            "corporate.user",
        )

    def test_returns_effective_role_display_name(self):
        user = User.objects.create_user(
            username="supervisor.user",
            first_name="Supervisor",
            last_name="User",
        )

        supervisor_role = Role.objects.get(
            code=Role.SUPERVISOR,
        )

        collector_role = Role.objects.get(
            code=Role.COBRADOR,
        )

        user.roles.set(
            [
                collector_role,
                supervisor_role,
            ]
        )

        context = current_user_context(
            self._build_request(user)
        )

        self.assertEqual(
            context["current_user_role_name"],
            "Supervisor",
        )

    def test_user_without_role_has_safe_fallback(self):
        user = User.objects.create_user(
            username="user.without.role",
        )

        context = current_user_context(
            self._build_request(user)
        )

        self.assertEqual(
            context["current_user_role_name"],
            "Sin rol asignado",
        )

    def test_superuser_is_displayed_as_administrator(self):
        user = User.objects.create_superuser(
            username="admin.user",
            email="admin@example.com",
            password="test-password",
        )

        context = current_user_context(
            self._build_request(user)
        )

        self.assertEqual(
            context["current_user_role_name"],
            "Administrador",
        )

    def test_anonymous_user_returns_empty_context(self):
        from django.contrib.auth.models import AnonymousUser

        context = current_user_context(
            self._build_request(AnonymousUser())
        )

        self.assertEqual(
            context["current_user_display_name"],
            "",
        )
        self.assertEqual(
            context["current_user_initials"],
            "",
        )
        self.assertEqual(
            context["current_user_role_name"],
            "",
        )
        self.assertEqual(
            context["current_user_identifier"],
            "",
        )


class UserNavbarTemplateTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

        self.user = User.objects.create_user(
            username="angelo.leon",
            email="angelo.leon@example.com",
            first_name="Angelo",
            last_name="León",
        )

        supervisor_role = Role.objects.get(
            code=Role.SUPERVISOR,
        )

        self.user.roles.set([supervisor_role])

    def test_navbar_renders_real_user_identity(self):
        request = self.factory.get("/")
        request.user = self.user

        user_context = current_user_context(request)

        html = render_to_string(
            "partials/navbar.html",
            {
                **user_context,
                "request": request,
                "navbar_new_alerts_count": 0,
                "navbar_recent_alerts": [],
            },
        )

        self.assertIn("Angelo León", html)
        self.assertIn("AL", html)
        self.assertIn("Supervisor", html)
        self.assertIn("angelo.leon@example.com", html)
        self.assertIn("Cerrar sesión", html)

    def test_navbar_logout_uses_post_form(self):
        request = self.factory.get("/")
        request.user = self.user

        user_context = current_user_context(request)

        html = render_to_string(
            "partials/navbar.html",
            {
                **user_context,
                "request": request,
                "navbar_new_alerts_count": 0,
                "navbar_recent_alerts": [],
            },
        )

        self.assertIn('method="post"', html)
        self.assertIn(
            f'action="{reverse("accounts:logout")}"',
            html,
        )
        self.assertNotIn(
            f'href="{reverse("accounts:logout")}"',
            html,
        )

    def test_navbar_does_not_render_nonexistent_profile_links(self):
        request = self.factory.get("/")
        request.user = self.user

        user_context = current_user_context(request)

        html = render_to_string(
            "partials/navbar.html",
            {
                **user_context,
                "request": request,
                "navbar_new_alerts_count": 0,
                "navbar_recent_alerts": [],
            },
        )

        self.assertNotIn("Mi perfil", html)
        self.assertNotIn("Configuración de cuenta", html)


class LogoutViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="logout.user",
            email="logout.user@example.com",
        )

    @override_settings(
        ENTRA_GLOBAL_LOGOUT_ENABLED=False,
    )
    def test_logout_rejects_get_request(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("accounts:logout")
        )

        self.assertEqual(response.status_code, 405)

    @override_settings(
        ENTRA_GLOBAL_LOGOUT_ENABLED=False,
    )
    def test_logout_accepts_post_and_redirects_to_login(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("accounts:logout")
        )

        self.assertRedirects(
            response,
            reverse("accounts:login"),
            fetch_redirect_response=False,
        )

    @override_settings(
        ENTRA_GLOBAL_LOGOUT_ENABLED=False,
    )
    def test_logout_removes_authenticated_user_from_session(self):
        self.client.force_login(self.user)

        self.client.post(
            reverse("accounts:logout")
        )

        response = self.client.get(
            reverse("accounts:login")
        )

        self.assertFalse(
            response.wsgi_request.user.is_authenticated
        )

    @override_settings(
        ENTRA_GLOBAL_LOGOUT_ENABLED=False,
    )
    def test_logout_adds_success_message(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("accounts:logout"),
            follow=True,
        )

        message_texts = [
            str(message)
            for message in get_messages(
                response.wsgi_request
            )
        ]

        self.assertIn(
            "Sesión cerrada correctamente.",
            message_texts,
        )

        self.assertContains(
            response,
            "Sesión cerrada correctamente.",
        )

    @override_settings(
        ENTRA_GLOBAL_LOGOUT_ENABLED=True,
        ENTRA_AUTHORITY=(
            "https://login.microsoftonline.com/"
            "test-tenant"
        ),
        ENTRA_POST_LOGOUT_REDIRECT_URI=(
            "https://invoice-flow.example.com/"
            "accounts/login/"
        ),
    )
    def test_global_logout_redirects_to_microsoft(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("accounts:logout")
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response["Location"].startswith(
                "https://login.microsoftonline.com/"
                "test-tenant/oauth2/v2.0/logout?"
            )
        )
        self.assertIn(
            "post_logout_redirect_uri=",
            response["Location"],
        )