from time import monotonic

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Ejecuta la sincronización operacional completa de Invoice Flow "
        "en el orden requerido: facturas, notas de crédito, "
        "pagos y reconciliaciones manuales."
    )

    SYNC_COMMANDS = (
        ("sync_fact_vta_reg", "Facturas"),
        ("sync_nc_vta_reg", "Notas de crédito"),
        ("sync_pago_vta_reg", "Pagos"),
        (
            "sync_manual_reconciliation",
            "Reconciliaciones manuales",
        ),
    )

    def handle(self, *args, **options):
        verbosity = options["verbosity"]
        process_started_at = monotonic()

        self._write_separator()
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "INICIO DE SINCRONIZACIÓN OPERACIONAL"
            )
        )
        self._write_separator()

        completed_commands = []

        for command_name, label in self.SYNC_COMMANDS:
            started_at = monotonic()

            self.stdout.write("")
            self.stdout.write(
                self.style.MIGRATE_HEADING(
                    f"Ejecutando {label}: {command_name}"
                )
            )

            try:
                call_command(
                    command_name,
                    verbosity=verbosity,
                    stdout=self.stdout,
                    stderr=self.stderr,
                )
            except Exception as exc:
                elapsed = monotonic() - started_at

                self.stderr.write(
                    self.style.ERROR(
                        f"Error en {command_name} después de "
                        f"{elapsed:.2f} segundos."
                    )
                )

                raise CommandError(
                    "La sincronización operacional fue interrumpida. "
                    f"Falló el comando {command_name}."
                ) from exc

            elapsed = monotonic() - started_at
            completed_commands.append(command_name)

            self.stdout.write(
                self.style.SUCCESS(
                    f"{command_name} finalizado correctamente "
                    f"en {elapsed:.2f} segundos."
                )
            )

        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "Ejecutando validación final de Django: check"
            )
        )

        check_started_at = monotonic()

        try:
            call_command(
                "check",
                verbosity=verbosity,
                stdout=self.stdout,
                stderr=self.stderr,
            )
        except Exception as exc:
            elapsed = monotonic() - check_started_at

            self.stderr.write(
                self.style.ERROR(
                    "La sincronización terminó, pero la validación "
                    f"final falló después de {elapsed:.2f} segundos."
                )
            )

            raise CommandError(
                "Falló python manage.py check después de sincronizar."
            ) from exc

        check_elapsed = monotonic() - check_started_at
        total_elapsed = monotonic() - process_started_at

        self.stdout.write(
            self.style.SUCCESS(
                f"Validación Django completada en {check_elapsed:.2f} segundos."
            )
        )

        self.stdout.write("")
        self._write_separator()
        self.stdout.write(
            self.style.SUCCESS(
                "SINCRONIZACIÓN OPERACIONAL COMPLETADA CORRECTAMENTE"
            )
        )
        self.stdout.write(
            f"Comandos ejecutados: {', '.join(completed_commands)}"
        )
        self.stdout.write(f"Duración total: {total_elapsed:.2f} segundos.")
        self._write_separator()

    def _write_separator(self):
        self.stdout.write("=" * 68)
