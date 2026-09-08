from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.management.services.attachments import upload_attachment_to_s3


class AttachmentUnicodeMetadataTests(SimpleTestCase):

    @patch(
        "apps.management.services.attachments."
        "validate_attachment_target"
    )
    @patch(
        "apps.management.services.attachments."
        "validate_operational_attachment"
    )
    @patch(
        "apps.management.services.attachments."
        "build_storage_key"
    )
    @patch(
        "apps.management.services.attachments."
        "get_s3_client"
    )
    def test_unicode_original_filename_is_encoded_for_s3_metadata(
        self,
        get_s3_client_mock,
        build_storage_key_mock,
        validate_operational_attachment_mock,
        validate_attachment_target_mock,
    ):
        uploaded_file = MagicMock()

        validate_operational_attachment_mock.return_value = {
            "original_filename": "Cotización José Ñuñoa.pdf",
            "extension": ".pdf",
            "mime_type": "application/pdf",
            "size_bytes": 1234,
        }

        build_storage_key_mock.return_value = (
            "operational-attachments/"
            "collectionaction/2026/09/1/test.pdf"
        )

        s3_client = MagicMock()
        get_s3_client_mock.return_value = s3_client

        target = MagicMock()
        target.pk = 1
        target._meta.label_lower = "management.collectionaction"

        upload_attachment_to_s3(
            target=target,
            uploaded_file=uploaded_file,
        )

        kwargs = s3_client.upload_fileobj.call_args.kwargs

        metadata = kwargs["ExtraArgs"]["Metadata"]

        self.assertEqual(
            metadata["original-filename"],
            (
                "Cotizaci%C3%B3n%20Jos%C3%A9%20"
                "%C3%91u%C3%B1oa.pdf"
            ),
        )

        self.assertEqual(
            validate_operational_attachment_mock.return_value[
                "original_filename"
            ],
            "Cotización José Ñuñoa.pdf",
        )
