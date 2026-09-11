from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import Role
from apps.portfolio.models import Customer
from apps.portfolio.statement_views import _can_send_statement


class CustomerStatementPermissionTests(TestCase):

    def setUp(self):

        User = get_user_model()

        self.customer = Customer.objects.create(
            external_id="STATEMENT-PERM-CUSTOMER",
            rut="11111111-1",
            name="Cliente Permisos Statement",
        )

        self.collector = User.objects.create_user(
            username="collector_statement",
            email="collector.statement@mosaico.cl",
            password="test",
        )

        self.other_collector = User.objects.create_user(
            username="other_collector_statement",
            email="other.collector.statement@mosaico.cl",
            password="test",
        )

        self.supervisor = User.objects.create_user(
            username="supervisor_statement",
            email="supervisor.statement@mosaico.cl",
            password="test",
        )

        self.admin = User.objects.create_user(
            username="admin_statement",
            email="admin.statement@mosaico.cl",
            password="test",
        )

        self.readonly = User.objects.create_user(
            username="readonly_statement",
            email="readonly.statement@mosaico.cl",
            password="test",
        )

    @patch(
        "apps.portfolio.statement_views."
        "RoleService.get_effective_role_code"
    )
    def test_supervisor_is_allowed(
        self,
        role_mock,
    ):
        role_mock.return_value = "SUPERVISOR"

        self.assertTrue(
            _can_send_statement(
                self.supervisor,
                self.customer,
            )
        )

    @patch(
        "apps.portfolio.statement_views."
        "RoleService.get_effective_role_code"
    )
    def test_administrator_is_allowed(
        self,
        role_mock,
    ):
        role_mock.return_value = "ADMINISTRADOR"

        self.assertTrue(
            _can_send_statement(
                self.admin,
                self.customer,
            )
        )

    @patch(
        "apps.portfolio.statement_views."
        "RoleService.get_effective_role_code"
    )
    def test_collector_is_allowed_without_assignment(
        self,
        role_mock,
    ):
        role_mock.return_value = "COBRADOR"

        self.assertTrue(
            _can_send_statement(
                self.collector,
                self.customer,
            )
        )

    @patch(
        "apps.portfolio.statement_views."
        "RoleService.get_effective_role_code"
    )
    def test_other_collector_is_also_allowed(
        self,
        role_mock,
    ):
        role_mock.return_value = "COBRADOR"

        self.assertTrue(
            _can_send_statement(
                self.other_collector,
                self.customer,
            )
        )

    @patch(
        "apps.portfolio.statement_views."
        "RoleService.get_effective_role_code"
    )
    def test_non_statement_role_is_denied(
        self,
        role_mock,
    ):
        role_mock.return_value = "CONSULTA"

        self.assertFalse(
            _can_send_statement(
                self.readonly,
                self.customer,
            )
        )

    def test_unauthenticated_user_is_denied(self):

        class AnonymousLikeUser:
            is_authenticated = False

        self.assertFalse(
            _can_send_statement(
                AnonymousLikeUser(),
                self.customer,
            )
        )
