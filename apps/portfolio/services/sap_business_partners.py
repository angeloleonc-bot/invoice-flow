from dataclasses import dataclass

import requests

from django.conf import settings


class SAPServiceLayerConfigurationError(RuntimeError):
    pass


class SAPServiceLayerError(RuntimeError):
    pass


@dataclass(frozen=True)
class BusinessPartnerContact:
    card_code: str
    billing_emails_raw: str
    phone: str


class SAPBusinessPartnerService:
    """
    Acceso controlado al maestro de socios de negocio SAP B1.

    Credenciales exclusivamente vía variables de entorno.
    """

    LOGIN_PATH = "/b1s/v1/Login"
    LOGOUT_PATH = "/b1s/v1/Logout"
    BP_PATH = "/b1s/v1/BusinessPartners"

    def __init__(self):
        self.base_url = str(
            settings.SAP_SERVICE_LAYER_BASE_URL
            or ""
        ).strip().rstrip("/")
        self.company_db = str(
            settings.SAP_SERVICE_LAYER_COMPANY_DB
            or ""
        ).strip()
        self.username = str(
            settings.SAP_SERVICE_LAYER_USERNAME
            or ""
        ).strip()
        self.password = str(
            settings.SAP_SERVICE_LAYER_PASSWORD
            or ""
        )
        self.verify_ssl = bool(
            settings.SAP_SERVICE_LAYER_VERIFY_SSL
        )
        self.timeout = int(
            settings.SAP_SERVICE_LAYER_TIMEOUT
        )
        self._validate_configuration()

    def _validate_configuration(self):
        missing = []

        if not self.base_url:
            missing.append(
                "SAP_SERVICE_LAYER_BASE_URL"
            )

        if not self.company_db:
            missing.append(
                "SAP_SERVICE_LAYER_COMPANY_DB"
            )

        if not self.username:
            missing.append(
                "SAP_SERVICE_LAYER_USERNAME"
            )

        if not self.password:
            missing.append(
                "SAP_SERVICE_LAYER_PASSWORD"
            )

        if missing:
            raise SAPServiceLayerConfigurationError(
                "Falta configuración Service Layer: "
                + ", ".join(missing)
            )

    def _login(self, session):
        response = session.post(
            self.base_url + self.LOGIN_PATH,
            json={
                "CompanyDB": self.company_db,
                "UserName": self.username,
                "Password": self.password,
            },
            timeout=self.timeout,
            verify=self.verify_ssl,
        )

        if response.status_code != 200:
            raise SAPServiceLayerError(
                "SAP Service Layer rechazó el login "
                f"(HTTP {response.status_code})."
            )

    def _logout(self, session):
        try:
            session.post(
                self.base_url + self.LOGOUT_PATH,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
        except requests.RequestException:
            pass

    @staticmethod
    def _odata_key(value):
        return str(value).replace("'", "''")

    def _partner_url(self, card_code):
        key = self._odata_key(card_code)

        return (
            self.base_url
            + self.BP_PATH
            + f"('{key}')"
        )

    def _read_contact(self, session, card_code):
        response = session.get(
            self._partner_url(card_code),
            params={
                "$select":
                    "CardCode,U_Email_FV,Phone1"
            },
            timeout=self.timeout,
            verify=self.verify_ssl,
        )

        if response.status_code != 200:
            raise SAPServiceLayerError(
                "No fue posible leer el cliente desde SAP "
                f"(HTTP {response.status_code})."
            )

        data = response.json()

        return BusinessPartnerContact(
            card_code=str(
                data.get("CardCode") or ""
            ),
            billing_emails_raw=str(
                data.get("U_Email_FV") or ""
            ).strip(),
            phone=str(
                data.get("Phone1") or ""
            ).strip(),
        )

    def get_contact(self, card_code):
        with requests.Session() as session:
            self._login(session)

            try:
                return self._read_contact(
                    session,
                    card_code,
                )
            finally:
                self._logout(session)

    def update_contact(
        self,
        *,
        card_code,
        billing_emails_raw,
        phone,
    ):
        """
        GET previo -> PATCH sólo campos modificados -> GET confirmación.
        """

        with requests.Session() as session:
            self._login(session)

            try:
                before = self._read_contact(
                    session,
                    card_code,
                )

                payload = {}

                if (
                    before.billing_emails_raw
                    != billing_emails_raw
                ):
                    payload["U_Email_FV"] = (
                        billing_emails_raw
                    )

                if before.phone != phone:
                    payload["Phone1"] = phone

                if payload:
                    response = session.patch(
                        self._partner_url(card_code),
                        json=payload,
                        timeout=self.timeout,
                        verify=self.verify_ssl,
                    )

                    if response.status_code not in {
                        200,
                        204,
                    }:
                        sap_detail = ""

                        try:
                            error_data = response.json()

                            sap_error = (
                                error_data.get("error")
                                or {}
                            )

                            sap_code = sap_error.get(
                                "code"
                            )

                            sap_message = (
                                sap_error.get("message")
                                or {}
                            )

                            if isinstance(
                                sap_message,
                                dict,
                            ):
                                sap_message = (
                                    sap_message.get("value")
                                    or ""
                                )

                            parts = []

                            if sap_code is not None:
                                parts.append(
                                    f"code={sap_code}"
                                )

                            if sap_message:
                                parts.append(
                                    str(sap_message)
                                )

                            if parts:
                                sap_detail = (
                                    " | "
                                    + " | ".join(parts)
                                )

                        except Exception:
                            body = (
                                response.text
                                or ""
                            ).strip()

                            if body:
                                sap_detail = (
                                    " | "
                                    + body[:1000]
                                )

                        raise SAPServiceLayerError(
                            "SAP rechazó la actualización "
                            f"(HTTP {response.status_code})"
                            f"{sap_detail}."
                        )

                after = self._read_contact(
                    session,
                    card_code,
                )

                if (
                    after.billing_emails_raw
                    != billing_emails_raw
                ):
                    raise SAPServiceLayerError(
                        "SAP no confirmó U_Email_FV "
                        "con el valor solicitado."
                    )

                if after.phone != phone:
                    raise SAPServiceLayerError(
                        "SAP no confirmó Phone1 "
                        "con el valor solicitado."
                    )

                return before, after, tuple(
                    payload.keys()
                )

            finally:
                self._logout(session)
