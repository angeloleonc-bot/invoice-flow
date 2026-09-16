from django.core.management.base import (
    BaseCommand,
)
from django.db import (
    connection,
    transaction,
)
from django.utils import timezone

from apps.portfolio.models import (
    Customer,
    CustomerSAPProfile,
)


class Command(BaseCommand):
    help = (
        "Sincroniza dbo.SN_Vta_Reg hacia "
        "CustomerSAPProfile."
    )

    BATCH_SIZE = 500

    PROFILE_FIELDS = (
        "sap_name",
        "account_balance",
        "sales_order_balance",
        "delivery_note_balance",
        "credit_limit",
        "account_status",
        "sap_status",
        "inactive_comment",
        "billing_emails_raw",
        "phone",
        "portfolio_seller_name",
        "portfolio_seller_email",
        "source_created_at",
        "source_updated_at",
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Analiza cambios sin escribir "
                "CustomerSAPProfile."
            ),
        )

    @staticmethod
    def _clean(value):
        if value is None:
            return ""

        return str(value).strip()

    @staticmethod
    def _aware_datetime(value):
        if value is None:
            return None

        if timezone.is_naive(value):
            return timezone.make_aware(
                value,
                timezone.get_current_timezone(),
            )

        return value

    @classmethod
    def _normalize_rut(cls, value):
        return (
            cls._clean(value)
            .replace(".", "")
            .replace(" ", "")
            .upper()
        )

    def _fetch_rows(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    Rut,
                    Nombre,
                    Saldo_Cuenta,
                    Nota_Venta,
                    Guia_Despacho,
                    Limite_Credito,
                    Estado_Cuenta,
                    Estado_SAP,
                    Comentario_Inactivo,
                    Email_FV,
                    Telefono,
                    Vendedor_Cartera,
                    Email_Vendedor_Cartera,
                    Fecha_Creacion,
                    Fecha_Actualizacion
                FROM dbo.SN_Vta_Reg
                WITH (NOLOCK)
                ORDER BY Rut
                """
            )

            columns = [
                column[0]
                for column in cursor.description
            ]

            return [
                dict(zip(columns, row))
                for row in cursor.fetchall()
            ]

    def handle(
        self,
        *args,
        **options,
    ):
        dry_run = options["dry_run"]

        rows = self._fetch_rows()

        customers = {
            self._normalize_rut(customer.rut):
                customer
            for customer in Customer.objects.all()
        }

        existing_profiles = {
            profile.customer_id: profile
            for profile in (
                CustomerSAPProfile.objects
                .select_related("customer")
                .all()
            )
        }

        now = timezone.now()

        to_create = []
        to_update = []

        source_without_customer = []
        created = 0
        updated = 0
        unchanged = 0

        for row in rows:
            rut = self._normalize_rut(
                row["Rut"]
            )

            customer = customers.get(rut)

            if customer is None:
                source_without_customer.append(
                    self._clean(row["Rut"])
                )
                continue

            values = {
                "sap_name": self._clean(
                    row["Nombre"]
                ),
                "account_balance":
                    row["Saldo_Cuenta"],
                "sales_order_balance":
                    row["Nota_Venta"],
                "delivery_note_balance":
                    row["Guia_Despacho"],
                "credit_limit":
                    row["Limite_Credito"],
                "account_status": self._clean(
                    row["Estado_Cuenta"]
                ),
                "sap_status": self._clean(
                    row["Estado_SAP"]
                ),
                "inactive_comment":
                    self._clean(
                        row["Comentario_Inactivo"]
                    ),
                "billing_emails_raw":
                    self._clean(
                        row["Email_FV"]
                    ),
                "phone": self._clean(
                    row["Telefono"]
                ),
                "portfolio_seller_name":
                    self._clean(
                        row["Vendedor_Cartera"]
                    ),
                "portfolio_seller_email":
                    self._clean(
                        row[
                            "Email_Vendedor_Cartera"
                        ]
                    ),
                "source_created_at":
                    self._aware_datetime(
                        row["Fecha_Creacion"]
                    ),
                "source_updated_at":
                    self._aware_datetime(
                        row["Fecha_Actualizacion"]
                    ),
            }

            profile = existing_profiles.get(
                customer.id
            )

            if profile is None:
                profile = CustomerSAPProfile(
                    customer=customer,
                    synced_at=now,
                    **values,
                )

                to_create.append(profile)
                created += 1
                continue

            effective_values = dict(values)

            incoming_source_updated_at = (
                values["source_updated_at"]
            )

            clear_email_confirmation = False
            clear_phone_confirmation = False

            if (
                profile.billing_emails_sap_confirmed_at
                is not None
            ):
                if (
                    incoming_source_updated_at is None
                    or incoming_source_updated_at
                    < profile.billing_emails_sap_confirmed_at
                ):
                    effective_values[
                        "billing_emails_raw"
                    ] = profile.billing_emails_raw
                else:
                    clear_email_confirmation = True

            if (
                profile.phone_sap_confirmed_at
                is not None
            ):
                if (
                    incoming_source_updated_at is None
                    or incoming_source_updated_at
                    < profile.phone_sap_confirmed_at
                ):
                    effective_values[
                        "phone"
                    ] = profile.phone
                else:
                    clear_phone_confirmation = True

            changed = any(
                getattr(profile, field)
                != effective_values[field]
                for field in self.PROFILE_FIELDS
            )

            confirmation_changed = (
                clear_email_confirmation
                or clear_phone_confirmation
            )

            if not changed and not confirmation_changed:
                unchanged += 1
                continue

            for field in self.PROFILE_FIELDS:
                setattr(
                    profile,
                    field,
                    effective_values[field],
                )

            if clear_email_confirmation:
                profile.billing_emails_sap_confirmed_at = None

            if clear_phone_confirmation:
                profile.phone_sap_confirmed_at = None

            profile.synced_at = now

            to_update.append(profile)
            updated += 1

        self.stdout.write(
            "========================================"
        )
        self.stdout.write(
            "SN_Vta_Reg -> CustomerSAPProfile"
        )
        self.stdout.write(
            "========================================"
        )
        self.stdout.write(
            f"SOURCE_ROWS={len(rows)}"
        )
        self.stdout.write(
            f"CUSTOMERS={len(customers)}"
        )
        self.stdout.write(
            f"EXISTING_PROFILES="
            f"{len(existing_profiles)}"
        )
        self.stdout.write(
            f"TO_CREATE={created}"
        )
        self.stdout.write(
            f"TO_UPDATE={updated}"
        )
        self.stdout.write(
            f"UNCHANGED={unchanged}"
        )
        self.stdout.write(
            "SOURCE_WITHOUT_CUSTOMER="
            f"{len(source_without_customer)}"
        )

        if source_without_customer:
            self.stdout.write(
                "SOURCE_WITHOUT_CUSTOMER_RUTS="
                + ", ".join(
                    source_without_customer[:50]
                )
            )

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "DRY_RUN=YES"
                )
            )
            self.stdout.write(
                "DATABASE_WRITE=NOT_PERFORMED"
            )
            return

        with transaction.atomic():
            if to_create:
                CustomerSAPProfile.objects.bulk_create(
                    to_create,
                    batch_size=self.BATCH_SIZE,
                )

            if to_update:
                CustomerSAPProfile.objects.bulk_update(
                    to_update,
                    fields=[
                        *self.PROFILE_FIELDS,
                        "billing_emails_sap_confirmed_at",
                        "phone_sap_confirmed_at",
                        "synced_at",
                    ],
                    batch_size=self.BATCH_SIZE,
                )

        self.stdout.write(
            self.style.SUCCESS(
                "SYNC_COMPLETED=YES"
            )
        )
