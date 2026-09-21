from django.test import SimpleTestCase

from apps.portfolio.management.commands.sync_fact_vta_reg import Command


class SyncFactVtaRegDuplicateGuardTests(SimpleTestCase):
    def test_duplicate_transids_are_reduced_to_one_document_row(self):
        rows = [
            {
                "TransId": 2264290,
                "Id": "96792430-C",
                "Num_doc": 530177,
            }
            for _ in range(7)
        ]

        desired_rows, duplicate_rows = (
            Command._deduplicate_document_rows(rows)
        )

        self.assertEqual(len(rows), 7)
        self.assertEqual(len(desired_rows), 1)
        self.assertEqual(duplicate_rows, 6)

        self.assertEqual(
            set(desired_rows.keys()),
            {"2264290"},
        )

    def test_duplicate_guard_preserves_distinct_transids(self):
        rows = (
            [
                {
                    "TransId": 2264290,
                    "Id": "96792430-C",
                    "Num_doc": 530177,
                }
                for _ in range(7)
            ]
            + [
                {
                    "TransId": 2264305,
                    "Id": "96792430-C",
                    "Num_doc": 530178,
                },
                {
                    "TransId": 2264288,
                    "Id": "96792430-C",
                    "Num_doc": 530176,
                },
            ]
        )

        desired_rows, duplicate_rows = (
            Command._deduplicate_document_rows(rows)
        )

        self.assertEqual(len(rows), 9)
        self.assertEqual(len(desired_rows), 3)
        self.assertEqual(duplicate_rows, 6)

        self.assertEqual(
            set(desired_rows.keys()),
            {
                "2264290",
                "2264305",
                "2264288",
            },
        )

    def test_last_duplicate_row_wins(self):
        rows = [
            {
                "TransId": 2264290,
                "Num_doc": 530177,
                "OC": "OLD",
            },
            {
                "TransId": 2264290,
                "Num_doc": 530177,
                "OC": "NEW",
            },
        ]

        desired_rows, duplicate_rows = (
            Command._deduplicate_document_rows(rows)
        )

        self.assertEqual(len(desired_rows), 1)
        self.assertEqual(duplicate_rows, 1)
        self.assertEqual(
            desired_rows["2264290"]["OC"],
            "NEW",
        )
