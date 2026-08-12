from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

import msal
import requests

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction


GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]

SQL_TABLE = "dbo.Fact_scan_url2"

REQUEST_TIMEOUT_SECONDS = 90
MAX_HTTP_RETRIES = 6
SQL_BATCH_SIZE = 500


class Command(BaseCommand):
    help = (
        "Sincroniza respaldos documentales desde OneDrive "
        "hacia dbo.Fact_scan_url2."
    )

    def handle(self, *args, **options):
        self.tenant_id = self._required_env(
            "DOCUMENT_SUPPORT_ENTRA_TENANT_ID"
        )
        self.client_id = self._required_env(
            "DOCUMENT_SUPPORT_ENTRA_CLIENT_ID"
        )
        self.client_secret = self._required_env(
            "DOCUMENT_SUPPORT_ENTRA_CLIENT_SECRET"
        )
        self.onedrive_user = self._required_env(
            "DOCUMENT_SUPPORT_ONEDRIVE_USER"
        )
        self.onedrive_folder = self._required_env(
            "DOCUMENT_SUPPORT_ONEDRIVE_FOLDER"
        )

        self.stdout.write(
            "Iniciando sincronización de respaldos documentales."
        )
        self.stdout.write(
            f"OneDrive user: {self.onedrive_user}"
        )
        self.stdout.write(
            f"Carpeta: {self.onedrive_folder}"
        )
        self.stdout.write(
            f"Tabla destino: {SQL_TABLE}"
        )

        self._validate_sql_table()

        access_token = self._get_graph_access_token()

        session = requests.Session()
        session.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            }
        )

        found = 0
        inserted = 0
        existing = 0
        skipped = 0

        batch: list[tuple[int, str]] = []

        try:
            for item in self._walk_files(
                session,
                self._build_folder_children_url(),
            ):
                found += 1

                filename = str(
                    item.get("name", "")
                ).strip()

                web_url = str(
                    item.get("webUrl", "")
                ).strip()

                doc_num = self._extract_doc_num(
                    filename
                )

                if doc_num is None:
                    skipped += 1

                    self.stderr.write(
                        self.style.WARNING(
                            "Omitido por nombre sin número "
                            f"válido: {filename}"
                        )
                    )
                    continue

                if not web_url:
                    skipped += 1

                    self.stderr.write(
                        self.style.WARNING(
                            f"Omitido por webUrl vacío: {filename}"
                        )
                    )
                    continue

                batch.append(
                    (
                        doc_num,
                        web_url,
                    )
                )

                if len(batch) >= SQL_BATCH_SIZE:
                    (
                        new_count,
                        existing_count,
                    ) = self._insert_batch(batch)

                    inserted += new_count
                    existing += existing_count

                    self.stdout.write(
                        "Procesados "
                        f"{found} archivos | "
                        f"insertados={inserted} | "
                        f"existentes={existing} | "
                        f"omitidos={skipped}"
                    )

                    batch.clear()

            if batch:
                (
                    new_count,
                    existing_count,
                ) = self._insert_batch(batch)

                inserted += new_count
                existing += existing_count

        finally:
            session.close()

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "Sincronización de respaldos "
                "documentales completada."
            )
        )
        self.stdout.write(
            f"Archivos encontrados: {found}"
        )
        self.stdout.write(
            f"Registros insertados: {inserted}"
        )
        self.stdout.write(
            f"URLs ya existentes: {existing}"
        )
        self.stdout.write(
            f"Archivos omitidos: {skipped}"
        )

    def _required_env(self, name: str) -> str:
        value = os.getenv(
            name,
            "",
        ).strip()

        if not value:
            raise CommandError(
                f"La variable {name} no está configurada."
            )

        return value

    def _get_graph_access_token(self) -> str:
        app = msal.ConfidentialClientApplication(
            client_id=self.client_id,
            authority=(
                "https://login.microsoftonline.com/"
                f"{self.tenant_id}"
            ),
            client_credential=self.client_secret,
        )

        result = app.acquire_token_for_client(
            scopes=GRAPH_SCOPE
        )

        access_token = result.get(
            "access_token"
        )

        if not access_token:
            description = result.get(
                "error_description",
                str(result),
            )

            raise CommandError(
                "No fue posible obtener token "
                f"de Microsoft Graph: {description}"
            )

        return access_token

    def _graph_get(
        self,
        session: requests.Session,
        url: str,
    ) -> dict:
        for attempt in range(
            1,
            MAX_HTTP_RETRIES + 1,
        ):
            try:
                response = session.get(
                    url,
                    timeout=(
                        REQUEST_TIMEOUT_SECONDS
                    ),
                )
            except requests.RequestException as exc:
                if attempt >= MAX_HTTP_RETRIES:
                    raise CommandError(
                        "Error consultando Microsoft "
                        f"Graph: {exc}"
                    ) from exc

                wait_seconds = min(
                    2 ** attempt,
                    60,
                )

                self.stderr.write(
                    self.style.WARNING(
                        "Error temporal consultando "
                        "Microsoft Graph. "
                        f"Reintento {attempt}/"
                        f"{MAX_HTTP_RETRIES} en "
                        f"{wait_seconds}s."
                    )
                )

                time.sleep(
                    wait_seconds
                )
                continue

            if response.status_code == 200:
                return response.json()

            if response.status_code in {
                429,
                500,
                502,
                503,
                504,
            }:
                retry_after = (
                    response.headers.get(
                        "Retry-After"
                    )
                )

                wait_seconds = (
                    int(retry_after)
                    if (
                        retry_after
                        and retry_after.isdigit()
                    )
                    else min(
                        2 ** attempt,
                        60,
                    )
                )

                self.stderr.write(
                    self.style.WARNING(
                        "Microsoft Graph respondió "
                        f"HTTP {response.status_code}. "
                        f"Reintento {attempt}/"
                        f"{MAX_HTTP_RETRIES} en "
                        f"{wait_seconds}s."
                    )
                )

                time.sleep(
                    wait_seconds
                )
                continue

            try:
                details = response.json()
            except ValueError:
                details = response.text

            raise CommandError(
                "Microsoft Graph respondió "
                f"HTTP {response.status_code}: "
                f"{details}"
            )

        raise CommandError(
            "Microsoft Graph no respondió "
            "correctamente después de "
            f"{MAX_HTTP_RETRIES} intentos."
        )

    def _build_folder_children_url(
        self,
    ) -> str:
        user = quote(
            self.onedrive_user,
            safe="@.",
        )

        folder = quote(
            self.onedrive_folder.strip("/"),
            safe="/",
        )

        return (
            f"{GRAPH_BASE_URL}/users/{user}"
            "/drive/root:"
            f"/{folder}:/children"
            "?$select="
            "id,name,webUrl,file,folder,"
            "parentReference"
            "&$top=200"
        )

    def _list_children(
        self,
        session: requests.Session,
        initial_url: str,
    ) -> Iterator[dict]:
        next_url: str | None = (
            initial_url
        )

        while next_url:
            payload = self._graph_get(
                session,
                next_url,
            )

            for item in payload.get(
                "value",
                [],
            ):
                yield item

            next_url = payload.get(
                "@odata.nextLink"
            )

    def _walk_files(
        self,
        session: requests.Session,
        folder_url: str,
    ) -> Iterator[dict]:
        for item in self._list_children(
            session,
            folder_url,
        ):
            if "file" in item:
                yield item
                continue

            if "folder" in item:
                item_id = quote(
                    str(item["id"]),
                    safe="",
                )

                user = quote(
                    self.onedrive_user,
                    safe="@.",
                )

                child_url = (
                    f"{GRAPH_BASE_URL}/users/"
                    f"{user}/drive/items/"
                    f"{item_id}/children"
                    "?$select="
                    "id,name,webUrl,file,folder,"
                    "parentReference"
                    "&$top=200"
                )

                yield from self._walk_files(
                    session,
                    child_url,
                )

    def _extract_doc_num(
        self,
        filename: str,
    ) -> int | None:
        stem = Path(
            filename
        ).stem.strip()

        number_groups = re.findall(
            r"\d+",
            stem,
        )

        if not number_groups:
            return None

        numeric_text = max(
            number_groups,
            key=len,
        )

        doc_num = int(
            numeric_text
        )

        if not (
            -2_147_483_648
            <= doc_num
            <= 2_147_483_647
        ):
            return None

        return doc_num

    def _validate_sql_table(self):
        sql = f"""
            IF OBJECT_ID(
                N'{SQL_TABLE}',
                N'U'
            ) IS NULL
                THROW 50001,
                    'No existe la tabla {SQL_TABLE}.',
                    1;

            IF COL_LENGTH(
                N'{SQL_TABLE}',
                'doc_num'
            ) IS NULL
                THROW 50002,
                    'No existe la columna doc_num.',
                    1;

            IF COL_LENGTH(
                N'{SQL_TABLE}',
                'url'
            ) IS NULL
                THROW 50003,
                    'No existe la columna url.',
                    1;
        """

        with connection.cursor() as cursor:
            cursor.execute(
                sql
            )

    def _insert_batch(
        self,
        rows: list[tuple[int, str]],
    ) -> tuple[int, int]:
        if not rows:
            return 0, 0

        unique_rows_by_url = {}

        for doc_num, url in rows:
            unique_rows_by_url.setdefault(
                url,
                (
                    doc_num,
                    url,
                ),
            )

        unique_rows = list(
            unique_rows_by_url.values()
        )

        urls = [
            url
            for _, url in unique_rows
        ]

        existing_urls: set[str] = (
            set()
        )

        url_chunk_size = 900

        with connection.cursor() as cursor:
            for start in range(
                0,
                len(urls),
                url_chunk_size,
            ):
                chunk = urls[
                    start:
                    start + url_chunk_size
                ]

                placeholders = ",".join(
                    "%s"
                    for _ in chunk
                )

                cursor.execute(
                    (
                        f"SELECT url "
                        f"FROM {SQL_TABLE} "
                        f"WHERE url IN "
                        f"({placeholders})"
                    ),
                    chunk,
                )

                existing_urls.update(
                    row[0]
                    for row in cursor.fetchall()
                )

        new_rows = [
            (
                doc_num,
                url,
            )
            for doc_num, url
            in unique_rows
            if url not in existing_urls
        ]

        if new_rows:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.executemany(
                        (
                            f"INSERT INTO "
                            f"{SQL_TABLE} "
                            "(doc_num, url) "
                            "VALUES (%s, %s)"
                        ),
                        new_rows,
                    )

        duplicate_in_batch_count = (
            len(rows)
            - len(unique_rows)
        )

        existing_count = (
            len(unique_rows)
            - len(new_rows)
            + duplicate_in_batch_count
        )

        return (
            len(new_rows),
            existing_count,
        )