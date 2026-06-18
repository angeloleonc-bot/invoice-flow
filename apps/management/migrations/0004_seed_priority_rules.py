from django.db import migrations


INITIAL_PRIORITY_RULES = [
    {
        "code": "PROMISE_EXPIRED",
        "name": "Promesa vencida",
        "score": 100,
        "description": "Aplica cuando el documento tiene una promesa pendiente vencida.",
        "evaluation_order": 10,
    },
    {
        "code": "PROMISE_DUE_TODAY",
        "name": "Promesa vence hoy",
        "score": 90,
        "description": "Aplica cuando el documento tiene una promesa pendiente con fecha de hoy.",
        "evaluation_order": 20,
    },
    {
        "code": "DOCUMENT_OVERDUE",
        "name": "Documento vencido",
        "score": 60,
        "description": "Aplica cuando el documento está vencido y tiene saldo pendiente.",
        "evaluation_order": 30,
    },
    {
        "code": "HIGH_BALANCE",
        "name": "Saldo alto",
        "score": 50,
        "description": "Aplica cuando el documento supera el umbral operacional de saldo alto.",
        "evaluation_order": 40,
    },
    {
        "code": "CUSTOMER_CRITICAL",
        "name": "Cliente crítico",
        "score": 25,
        "description": "Aplica cuando el documento o cliente está marcado como Cliente crítico.",
        "evaluation_order": 50,
    },
    {
        "code": "NO_MANAGEMENT_7_DAYS",
        "name": "Sin gestión 7 días",
        "score": 20,
        "description": "Aplica cuando el documento no registra acciones de gestión en los últimos 7 días.",
        "evaluation_order": 60,
    },
    {
        "code": "UPCOMING_DUE_DATE",
        "name": "Próximo vencimiento",
        "score": 15,
        "description": "Aplica cuando el documento vence dentro de los próximos 7 días.",
        "evaluation_order": 70,
    },
    {
        "code": "SUPERVISOR_REQUIRED",
        "name": "Requiere supervisor",
        "score": 10,
        "description": "Aplica cuando el documento requiere intervención de supervisor.",
        "evaluation_order": 80,
    },
]


def seed_priority_rules(apps, schema_editor):
    PriorityRule = apps.get_model("management", "PriorityRule")

    for rule in INITIAL_PRIORITY_RULES:
        obj, created = PriorityRule.objects.get_or_create(
            code=rule["code"],
            defaults={
                "name": rule["name"],
                "score": rule["score"],
                "description": rule["description"],
                "evaluation_order": rule["evaluation_order"],
                "is_active": True,
            },
        )

        if not created:
            obj.name = rule["name"]
            obj.description = rule["description"]
            obj.evaluation_order = rule["evaluation_order"]

            if obj.score is None:
                obj.score = rule["score"]

            obj.save(
                update_fields=[
                    "name",
                    "description",
                    "evaluation_order",
                    "score",
                    "updated_at",
                ]
            )


class Migration(migrations.Migration):

    dependencies = [
        ("management", "0003_priorityrule"),
    ]

    operations = [
        migrations.RunPython(seed_priority_rules, migrations.RunPython.noop),
    ]