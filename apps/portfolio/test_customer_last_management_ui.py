from datetime import timedelta
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from apps.portfolio.views import (
    _build_customer_last_management,
)


def user(name, email):
    return SimpleNamespace(
        get_full_name=lambda: name,
        email=email,
        username=email,
    )


class CustomerLastManagementTests(SimpleTestCase):

    def test_recent_statement_wins_over_old_action(self):
        now = timezone.now()

        action = SimpleNamespace(
            action_date=now - timedelta(hours=2),
            created_at=now - timedelta(hours=2),
            title="Llamada",
            description="Contacto telefónico",
            performed_by=user(
                "Cobrador Acción",
                "accion@example.com",
            ),
        )

        statement = SimpleNamespace(
            sent_at=now,
            updated_at=now,
            to_emails=["cliente@example.com"],
            document_count=5,
            created_by=user(
                "Cobrador Estado",
                "estado@example.com",
            ),
        )

        result = _build_customer_last_management(
            actions=[action],
            sent_customer_statements=[statement],
        )

        self.assertEqual(
            result["type"],
            "customer_statement",
        )
        self.assertEqual(
            result["title"],
            "Estado de cuenta enviado",
        )
        self.assertEqual(
            result["date"],
            now,
        )
        self.assertIn(
            "cliente@example.com",
            result["description"],
        )
        self.assertIn(
            "5 documentos",
            result["description"],
        )
        self.assertEqual(
            result["user_label"],
            "Cobrador Estado",
        )

    def test_recent_action_wins_over_old_statement(self):
        now = timezone.now()

        action = SimpleNamespace(
            action_date=now,
            created_at=now,
            title="WhatsApp",
            description="Cliente contactado",
            performed_by=user(
                "Cobrador Acción",
                "accion@example.com",
            ),
        )

        statement = SimpleNamespace(
            sent_at=now - timedelta(hours=3),
            updated_at=now - timedelta(hours=3),
            to_emails=["cliente@example.com"],
            document_count=2,
            created_by=user(
                "Cobrador Estado",
                "estado@example.com",
            ),
        )

        result = _build_customer_last_management(
            actions=[action],
            sent_customer_statements=[statement],
        )

        self.assertEqual(
            result["type"],
            "collection_action",
        )
        self.assertEqual(
            result["title"],
            "WhatsApp",
        )
        self.assertEqual(
            result["date"],
            now,
        )

    def test_statement_without_action_is_valid_management(self):
        now = timezone.now()

        statement = SimpleNamespace(
            sent_at=now,
            updated_at=now,
            to_emails=["cliente@example.com"],
            document_count=1,
            created_by=user(
                "Cobrador",
                "cobrador@example.com",
            ),
        )

        result = _build_customer_last_management(
            actions=[],
            sent_customer_statements=[statement],
        )

        self.assertEqual(
            result["type"],
            "customer_statement",
        )
        self.assertEqual(
            result["title"],
            "Estado de cuenta enviado",
        )
        self.assertIn(
            "1 documento",
            result["description"],
        )

    def test_without_events_returns_none(self):
        result = _build_customer_last_management(
            actions=[],
            sent_customer_statements=[],
        )

        self.assertIsNone(result)
