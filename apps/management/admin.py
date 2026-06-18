from django.contrib import admin
from .models import PriorityRule


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