from django.contrib import admin

from .models import (
    Customer,
    CustomerContact,
    Document,
    DocumentStatus,
    DocumentSubStatus,
    DocumentTag,
)


class CustomerContactInline(admin.TabularInline):
    model = CustomerContact
    extra = 0
    fields = ("name", "email", "phone", "position", "is_primary")


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "rut", "external_id", "cluster", "is_active", "updated_at")
    list_filter = ("cluster", "is_active")
    search_fields = ("name", "rut", "external_id", "email", "phone")
    readonly_fields = ("created_at", "updated_at")
    inlines = [CustomerContactInline]


@admin.register(CustomerContact)
class CustomerContactAdmin(admin.ModelAdmin):
    list_display = ("name", "customer", "email", "phone", "position", "is_primary")
    list_filter = ("is_primary",)
    search_fields = ("name", "email", "phone", "customer__name", "customer__rut")


@admin.register(DocumentStatus)
class DocumentStatusAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(DocumentSubStatus)
class DocumentSubStatusAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(DocumentTag)
class DocumentTagAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = (
        "document_number",
        "document_type",
        "customer",
        "issue_date",
        "due_date",
        "original_amount",
        "balance_amount",
        "status",
        "sub_status",
        "external_source",
    )
    list_filter = (
        "document_type",
        "status",
        "sub_status",
        "tags",
        "external_source",
        "due_date",
    )
    search_fields = (
        "trans_id",
        "document_number",
        "customer__name",
        "customer__rut",
        "customer__external_id",
    )
    autocomplete_fields = ("customer", "status", "sub_status", "tags")
    readonly_fields = ("created_at", "updated_at")
    filter_horizontal = ("tags",)
    date_hierarchy = "due_date"