from pathlib import Path

from django.test import SimpleTestCase


BASE = Path(__file__).resolve().parents[2]


class CustomerPromiseUIIntegrationTests(SimpleTestCase):
    def read(self, relative_path):
        return (
            BASE / relative_path
        ).read_text(encoding="utf-8")

    def test_promise_post_uses_selection_keys(self):
        text = self.read("apps/portfolio/views.py")

        self.assertIn(
            'request.POST.getlist("selection_keys")',
            text,
        )

        self.assertIn(
            "resolve_selected_documents(",
            text,
        )

        self.assertIn(
            "create_payment_promise_from_selection(",
            text,
        )

    def test_historical_promise_batch_is_not_used_in_customer_post(self):
        text = self.read("apps/portfolio/views.py")

        start = text.index(
            'elif form_type == "promise":'
        )

        end = text.index(
            'account_view = request.GET.get("view", "pending")',
            start,
        )

        block = text[start:end]

        self.assertNotIn(
            "create_payment_promise_batch(",
            block,
        )

    def test_action_flow_keeps_document_id(self):
        text = self.read(
            "templates/portfolio/customer_detail.html"
        )

        self.assertIn(
            'input.name = "document_id";',
            text,
        )

    def test_promise_modal_posts_selection_keys(self):
        text = self.read(
            "templates/portfolio/customer_detail.html"
        )

        self.assertIn(
            'name="selection_keys"',
            text,
        )

        self.assertIn(
            "data-promise-select",
            text,
        )

    def test_promise_modal_contains_upcoming_group(self):
        text = self.read(
            "templates/portfolio/customer_detail.html"
        )

        self.assertIn(
            "promise_upcoming_documents",
            text,
        )

        self.assertIn(
            ">Por vencer<",
            text,
        )

    def test_customer_actions_have_distinct_visual_semantics(self):
        account = self.read(
            "templates/portfolio/components/_account_state.html"
        )

        css = self.read(
            "static/css/invoice-flow.css"
        )

        self.assertIn(
            "is-management",
            account,
        )

        self.assertIn(
            "is-promise",
            account,
        )

        self.assertIn(
            ".if-customer-action-btn.is-management",
            css,
        )

        self.assertIn(
            ".if-customer-action-btn.is-promise",
            css,
        )

    def test_management_button_is_disabled_without_selection(self):
        text = self.read(
            "templates/portfolio/components/_account_state.html"
        )

        self.assertIn(
            "data-open-action-modal",
            text,
        )

        self.assertIn(
            "disabled",
            text,
        )

    def test_account_state_has_select_all_control(self):
        text = self.read(
            "templates/portfolio/components/_account_state.html"
        )

        self.assertIn(
            "data-account-select-all",
            text,
        )

        self.assertIn(
            "Seleccionar todo",
            text,
        )

    def test_select_all_updates_account_document_checkboxes(self):
        text = self.read(
            "templates/portfolio/customer_detail.html"
        )

        self.assertIn(
            "const selectAllCheckbox =",
            text,
        )

        self.assertIn(
            "selectAllCheckbox.indeterminate",
            text,
        )

        self.assertIn(
            "checkbox.checked = checked;",
            text,
        )
