import re


SEPARATOR_PATTERN = re.compile(
    r"[,;\s]+"
)


def parse_billing_emails(raw_value):
    """
    Convierte U_Email_FV en una lista limpia para presentación.

    No modifica la fuente ni decide todavía qué direcciones
    podrán escribirse en SAP.
    """

    if not raw_value:
        return []

    emails = []
    seen = set()

    normalized = str(raw_value).replace(
        "\r",
        " ",
    ).replace(
        "\n",
        " ",
    )

    for token in SEPARATOR_PATTERN.split(
        normalized
    ):
        email = (
            token
            .strip()
            .strip('"')
            .strip("'")
            .strip("<>")
            .strip()
        )

        if not email:
            continue

        key = email.lower()

        if key in seen:
            continue

        seen.add(key)
        emails.append(email)

    return emails
