from django.contrib import admin
from .models import PriorityRule
from .models import OperationalAlert


@admin.register(PriorityRule)
class PriorityRuleAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "score",
        "is_active",
        "evaluation_order",
    )

    list_filter = (
        "is_active",
    )

    search_fields = (
        "code",
        "name",
    )

    list_editable = (
        "score",
        "is_active",
        "evaluation_order",
    )

    ordering = (
        "evaluation_order",
        "code",
    )

@admin.register(OperationalAlert)
class OperationalAlertAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "alert_type",
        "severity",
        "status",
        "customer",
        "document",
        "assigned_to",
        "created_at",
        "due_at",
    )
    list_filter = ("alert_type", "severity", "status", "created_at")
    search_fields = ("title", "message", "customer__name", "document__number")
    readonly_fields = ("created_at", "resolved_at")