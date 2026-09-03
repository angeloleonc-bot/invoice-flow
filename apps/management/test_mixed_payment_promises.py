from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.management.forms import WorkspacePaymentPromiseForm
from apps.management.models import (
    CollectionAction,
    PaymentPromise,
    PromiseDocument,
)
from apps.management.services.promises import (
    create_payment_promise_from_selection,
)
from apps.portfolio.models import (
    Customer,
    Document,
    DocumentAssignment,
    DocumentStatus,
    DocumentSubStatus,
)
from apps.portfolio.services.customer_statements import (
    CATEGORY_OVERDUE,
    CATEGORY_UPCOMING,
    StatementDocument,
)


class MixedPaymentPromiseServiceTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            external_id="TEST-MIXED-001",
            rut="99999998-8",
            name="Cliente Test Mixto",
        )

        self.other_customer = Customer.objects.create(
            external_id="TEST-MIXED-OTHER",
            rut="99999997-7",
            name="Otro Cliente",
        )

        self.status_open = DocumentStatus.objects.create(
            name="Abierto Test",
            is_active=True,
        )

        self.substatus_open = DocumentSubStatus.objects.create(
            name="Normal Test",
            is_active=True,
        )

        self.status_scheduled, _ = (
            DocumentStatus.objects.get_or_create(
                name="Pago programado",
                defaults={
                    "is_active": True,
                },
            )
        )

        if not self.status_scheduled.is_active:
            self.status_scheduled.is_active = True
            self.status_scheduled.save(
                update_fields=["is_active"]
            )

        self.substatus_promise, _ = (
            DocumentSubStatus.objects.get_or_create(
                name="Promesa vigente",
                defaults={
                    "is_active": True,
                },
            )
        )

        if not self.substatus_promise.is_active:
            self.substatus_promise.is_active = True
            self.substatus_promise.save(
                update_fields=["is_active"]
            )

        self.document = Document.objects.create(
            customer=self.customer,
            external_source=Document.SOURCE_FACT_VTA_REG,
            trans_id="TRANS-REAL-001",
            source_doc_entry="DOCENTRY-REAL-001",
            document_type="Factura",
            document_number="REAL-001",
            issue_date=timezone.localdate() - timedelta(days=60),
            due_date=timezone.localdate() - timedelta(days=30),
            original_amount=Decimal("100000.00"),
            balance_amount=Decimal("80000.00"),
            overpayment_amount=Decimal("0"),
            status=self.status_open,
            sub_status=self.substatus_open,
        )

        self.real_item = StatementDocument(
            selection_key=f"document:{self.document.id}",
            source="Document",
            document_id=self.document.id,
            trans_id=self.document.trans_id,
            doc_entry=self.document.source_doc_entry,
            document_number=self.document.document_number,
            document_type=self.document.document_type,
            issue_date=self.document.issue_date,
            due_date=self.document.due_date,
            original_amount=self.document.original_amount,
            balance_amount=self.document.balance_amount,
            category=CATEGORY_OVERDUE,
            category_label="Vencido",
            days_from_due=30,
        )

        self.upcoming_item = StatementDocument(
            selection_key=(
                "upcoming:TRANS-UPCOMING-001:"
                "DOCENTRY-UPCOMING-001:UPCOMING-001"
            ),
            source="Fact_Vta_Sin_Vencer",
            document_id=None,
            trans_id="TRANS-UPCOMING-001",
            doc_entry="DOCENTRY-UPCOMING-001",
            document_number="UPCOMING-001",
            document_type="Factura",
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=30),
            original_amount=Decimal("250000.00"),
            balance_amount=Decimal("250000.00"),
            category=CATEGORY_UPCOMING,
            category_label="Por vencer",
            days_from_due=30,
        )

    def make_form(self):
        form = WorkspacePaymentPromiseForm(
            data={
                "promise_date": (
                    timezone.localdate()
                    + timedelta(days=10)
                ).isoformat(),
                "notes": "Promesa mixta de prueba",
            }
        )

        self.assertTrue(
            form.is_valid(),
            form.errors.as_json(),
        )

        return form

    def test_upcoming_only_creates_promise_and_external_relation(self):
        before_documents = Document.objects.count()
        before_assignments = DocumentAssignment.objects.count()
        before_actions = CollectionAction.objects.count()

        result = create_payment_promise_from_selection(
            form=self.make_form(),
            customer=self.customer,
            selected_documents=[self.upcoming_item],
        )

        promise = result["promise"]

        self.assertEqual(
            promise.promised_amount,
            Decimal("250000.00"),
        )

        self.assertEqual(
            PromiseDocument.objects.filter(
                promise=promise
            ).count(),
            1,
        )

        relation = PromiseDocument.objects.get(
            promise=promise
        )

        self.assertIsNone(relation.document_id)
        self.assertEqual(
            relation.source,
            "Fact_Vta_Sin_Vencer",
        )
        self.assertEqual(
            relation.source_customer_external_id,
            self.customer.external_id,
        )
        self.assertEqual(
            relation.source_trans_id,
            self.upcoming_item.trans_id,
        )
        self.assertEqual(
            relation.source_doc_entry,
            self.upcoming_item.doc_entry,
        )
        self.assertEqual(
            relation.source_document_number,
            self.upcoming_item.document_number,
        )
        self.assertEqual(
            relation.source_due_date,
            self.upcoming_item.due_date,
        )
        self.assertEqual(
            relation.source_original_amount,
            self.upcoming_item.original_amount,
        )
        self.assertEqual(
            relation.source_balance_amount,
            self.upcoming_item.balance_amount,
        )

        self.assertEqual(
            Document.objects.count(),
            before_documents,
        )
        self.assertEqual(
            DocumentAssignment.objects.count(),
            before_assignments,
        )
        self.assertEqual(
            CollectionAction.objects.count(),
            before_actions,
        )

    def test_real_document_keeps_historical_operational_effects(self):
        result = create_payment_promise_from_selection(
            form=self.make_form(),
            customer=self.customer,
            selected_documents=[self.real_item],
        )

        promise = result["promise"]

        relation = PromiseDocument.objects.get(
            promise=promise
        )

        self.assertEqual(
            relation.document_id,
            self.document.id,
        )

        self.assertEqual(
            CollectionAction.objects.filter(
                document=self.document,
                action_type=CollectionAction.ActionType.PROMISE,
            ).count(),
            1,
        )

        self.document.refresh_from_db()

        self.assertEqual(
            self.document.status_id,
            self.status_scheduled.id,
        )

        self.assertEqual(
            self.document.sub_status_id,
            self.substatus_promise.id,
        )

    def test_mixed_selection_creates_one_promise_with_two_relations(self):
        result = create_payment_promise_from_selection(
            form=self.make_form(),
            customer=self.customer,
            selected_documents=[
                self.real_item,
                self.upcoming_item,
            ],
        )

        promise = result["promise"]

        self.assertEqual(
            PaymentPromise.objects.filter(
                id=promise.id
            ).count(),
            1,
        )

        self.assertEqual(
            promise.promised_amount,
            Decimal("330000.00"),
        )

        relations = PromiseDocument.objects.filter(
            promise=promise
        )

        self.assertEqual(
            relations.count(),
            2,
        )

        self.assertEqual(
            relations.filter(
                document=self.document
            ).count(),
            1,
        )

        self.assertEqual(
            relations.filter(
                document__isnull=True,
                source="Fact_Vta_Sin_Vencer",
            ).count(),
            1,
        )

        self.assertEqual(
            CollectionAction.objects.filter(
                document=self.document,
                action_type=CollectionAction.ActionType.PROMISE,
            ).count(),
            1,
        )

        self.assertEqual(
            len(result["actions"]),
            1,
        )

        self.assertEqual(
            len(result["real_relations"]),
            1,
        )

        self.assertEqual(
            len(result["external_relations"]),
            1,
        )

    def test_upcoming_snapshot_does_not_change_after_source_object_changes(self):
        original_balance = self.upcoming_item.balance_amount

        result = create_payment_promise_from_selection(
            form=self.make_form(),
            customer=self.customer,
            selected_documents=[self.upcoming_item],
        )

        relation = result["external_relations"][0]

        # StatementDocument es frozen; no intentamos mutarlo.
        # Confirmamos que PromiseDocument conserva exactamente
        # el snapshot recibido al crear la promesa.
        self.assertEqual(
            relation.source_balance_amount,
            original_balance,
        )

    def test_rejects_real_document_from_another_customer(self):
        other_document = Document.objects.create(
            customer=self.other_customer,
            external_source=Document.SOURCE_FACT_VTA_REG,
            trans_id="TRANS-OTHER-001",
            source_doc_entry="DOCENTRY-OTHER-001",
            document_type="Factura",
            document_number="OTHER-001",
            issue_date=timezone.localdate() - timedelta(days=10),
            due_date=timezone.localdate() - timedelta(days=5),
            original_amount=Decimal("50000.00"),
            balance_amount=Decimal("50000.00"),
            overpayment_amount=Decimal("0"),
            status=self.status_open,
            sub_status=self.substatus_open,
        )

        other_item = StatementDocument(
            selection_key=f"document:{other_document.id}",
            source="Document",
            document_id=other_document.id,
            trans_id=other_document.trans_id,
            doc_entry=other_document.source_doc_entry,
            document_number=other_document.document_number,
            document_type=other_document.document_type,
            issue_date=other_document.issue_date,
            due_date=other_document.due_date,
            original_amount=other_document.original_amount,
            balance_amount=other_document.balance_amount,
            category=CATEGORY_OVERDUE,
            category_label="Vencido",
            days_from_due=5,
        )

        with self.assertRaises(ValueError):
            create_payment_promise_from_selection(
                form=self.make_form(),
                customer=self.customer,
                selected_documents=[other_item],
            )

        self.assertEqual(
            PaymentPromise.objects.count(),
            0,
        )

    def test_rejects_tampered_real_document_identity(self):
        tampered = StatementDocument(
            selection_key=self.real_item.selection_key,
            source="Document",
            document_id=self.real_item.document_id,
            trans_id="TRANS-MANIPULADO",
            doc_entry=self.real_item.doc_entry,
            document_number=self.real_item.document_number,
            document_type=self.real_item.document_type,
            issue_date=self.real_item.issue_date,
            due_date=self.real_item.due_date,
            original_amount=self.real_item.original_amount,
            balance_amount=self.real_item.balance_amount,
            category=self.real_item.category,
            category_label=self.real_item.category_label,
            days_from_due=self.real_item.days_from_due,
        )

        with self.assertRaises(ValueError):
            create_payment_promise_from_selection(
                form=self.make_form(),
                customer=self.customer,
                selected_documents=[tampered],
            )

        self.assertEqual(
            PaymentPromise.objects.count(),
            0,
        )

    def test_multiple_upcoming_documents_can_share_one_promise(self):
        second_upcoming = replace(
            self.upcoming_item,
            selection_key=(
                "upcoming:TRANS-UPCOMING-002:"
                "DOCENTRY-UPCOMING-002:UPCOMING-002"
            ),
            trans_id="TRANS-UPCOMING-002",
            doc_entry="DOCENTRY-UPCOMING-002",
            document_number="UPCOMING-002",
            original_amount=Decimal("125000.00"),
            balance_amount=Decimal("125000.00"),
        )

        result = create_payment_promise_from_selection(
            form=self.make_form(),
            customer=self.customer,
            selected_documents=[
                self.upcoming_item,
                second_upcoming,
            ],
        )

        promise = result["promise"]

        relations = PromiseDocument.objects.filter(
            promise=promise,
            document__isnull=True,
        )

        self.assertEqual(relations.count(), 2)
        self.assertEqual(
            promise.promised_amount,
            Decimal("375000.00"),
        )
        self.assertEqual(
            len(result["external_relations"]),
            2,
        )
        self.assertEqual(
            len(result["real_relations"]),
            0,
        )
        self.assertEqual(
            len(result["actions"]),
            0,
        )

    def test_rejects_invalid_external_identity(self):
        invalid = StatementDocument(
            selection_key="upcoming:::UPCOMING-X",
            source="Fact_Vta_Sin_Vencer",
            document_id=None,
            trans_id="",
            doc_entry="",
            document_number="UPCOMING-X",
            document_type="Factura",
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=30),
            original_amount=Decimal("10000.00"),
            balance_amount=Decimal("10000.00"),
            category=CATEGORY_UPCOMING,
            category_label="Por vencer",
            days_from_due=30,
        )

        with self.assertRaises(ValueError):
            create_payment_promise_from_selection(
                form=self.make_form(),
                customer=self.customer,
                selected_documents=[invalid],
            )

        self.assertEqual(
            PaymentPromise.objects.count(),
            0,
        )
