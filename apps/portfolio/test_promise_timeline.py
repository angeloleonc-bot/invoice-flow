from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.portfolio.views import (
    _promise_relation_document_number,
)


class PromiseTimelineDocumentNumberTests(SimpleTestCase):
    def test_historical_relation_uses_document_number(self):
        relation = SimpleNamespace(
            document_id=10,
            document=SimpleNamespace(
                document_number="FACT-100"
            ),
            source_document_number="SNAPSHOT-100",
        )

        self.assertEqual(
            _promise_relation_document_number(relation),
            "FACT-100",
        )

    def test_external_relation_uses_snapshot_document_number(self):
        relation = SimpleNamespace(
            document_id=None,
            document=None,
            source_document_number="FACT-FUTURA-200",
        )

        self.assertEqual(
            _promise_relation_document_number(relation),
            "FACT-FUTURA-200",
        )

    def test_linked_document_has_precedence_over_snapshot(self):
        relation = SimpleNamespace(
            document_id=20,
            document=SimpleNamespace(
                document_number="FACT-REAL-300"
            ),
            source_document_number="FACT-SNAPSHOT-300",
        )

        self.assertEqual(
            _promise_relation_document_number(relation),
            "FACT-REAL-300",
        )

    def test_empty_external_number_returns_empty_string(self):
        relation = SimpleNamespace(
            document_id=None,
            document=None,
            source_document_number="",
        )

        self.assertEqual(
            _promise_relation_document_number(relation),
            "",
        )

    def test_whitespace_is_normalized(self):
        relation = SimpleNamespace(
            document_id=None,
            document=None,
            source_document_number="  FACT-400  ",
        )

        self.assertEqual(
            _promise_relation_document_number(relation),
            "FACT-400",
        )

    def test_mixed_relations_can_build_document_list(self):
        relations = [
            SimpleNamespace(
                document_id=1,
                document=SimpleNamespace(
                    document_number="FACT-REAL"
                ),
                source_document_number="",
            ),
            SimpleNamespace(
                document_id=None,
                document=None,
                source_document_number="FACT-FUTURA",
            ),
        ]

        document_numbers = [
            number
            for relation in relations
            if (
                number
                := _promise_relation_document_number(relation)
            )
        ]

        self.assertEqual(
            document_numbers,
            [
                "FACT-REAL",
                "FACT-FUTURA",
            ],
        )

        self.assertEqual(
            ", ".join(document_numbers),
            "FACT-REAL, FACT-FUTURA",
        )
