from pathlib import Path

import magic
from django.conf import settings
from django.core.exceptions import ValidationError


DEFAULT_MAX_FILE_SIZE = 30 * 1024 * 1024


ALLOWED_FILE_TYPES = {
    ".pdf": {
        "mime_types": {
            "application/pdf",
        },
    },
    ".doc": {
        "mime_types": {
            "application/msword",
            "application/x-ole-storage",
            "application/vnd.ms-office",
            "application/octet-stream",
        },
    },
    ".docx": {
        "mime_types": {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/zip",
            "application/x-zip-compressed",
            "application/octet-stream",
        },
    },
    ".xls": {
        "mime_types": {
            "application/vnd.ms-excel",
            "application/x-ole-storage",
            "application/vnd.ms-office",
            "application/octet-stream",
        },
    },
    ".xlsx": {
        "mime_types": {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/zip",
            "application/x-zip-compressed",
            "application/octet-stream",
        },
    },
    ".ppt": {
        "mime_types": {
            "application/vnd.ms-powerpoint",
            "application/x-ole-storage",
            "application/vnd.ms-office",
            "application/octet-stream",
        },
    },
    ".pptx": {
        "mime_types": {
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/zip",
            "application/x-zip-compressed",
            "application/octet-stream",
        },
    },
    ".txt": {
        "mime_types": {
            "text/plain",
            "application/octet-stream",
        },
    },
    ".csv": {
        "mime_types": {
            "text/csv",
            "text/plain",
            "application/csv",
            "application/vnd.ms-excel",
            "application/octet-stream",
        },
    },
    ".jpg": {
        "mime_types": {
            "image/jpeg",
        },
    },
    ".jpeg": {
        "mime_types": {
            "image/jpeg",
        },
    },
    ".png": {
        "mime_types": {
            "image/png",
        },
    },
    ".webp": {
        "mime_types": {
            "image/webp",
        },
    },
    ".tif": {
        "mime_types": {
            "image/tiff",
        },
    },
    ".tiff": {
        "mime_types": {
            "image/tiff",
        },
    },
    ".zip": {
        "mime_types": {
            "application/zip",
            "application/x-zip-compressed",
            "application/octet-stream",
        },
    },
    ".msg": {
        "mime_types": {
            "application/vnd.ms-outlook",
            "application/x-ole-storage",
            "application/vnd.ms-office",
            "application/octet-stream",
        },
    },
    ".eml": {
        "mime_types": {
            "message/rfc822",
            "text/plain",
            "application/octet-stream",
        },
    },
}


BLOCKED_EXTENSIONS = {
    ".exe",
    ".dll",
    ".bat",
    ".cmd",
    ".com",
    ".msi",
    ".msp",
    ".ps1",
    ".psm1",
    ".vbs",
    ".vbe",
    ".js",
    ".jse",
    ".jar",
    ".scr",
    ".hta",
    ".reg",
    ".lnk",
    ".iso",
    ".img",
    ".apk",
    ".sh",
    ".bash",
    ".zsh",
    ".py",
    ".pyc",
    ".php",
    ".phtml",
    ".asp",
    ".aspx",
    ".jsp",
    ".cgi",
    ".pl",
    ".rb",
}


def sanitize_original_filename(filename):
    """
    Elimina cualquier ruta enviada por el navegador y conserva únicamente
    el nombre final del archivo.
    """
    clean_name = Path(filename or "").name.strip()

    if not clean_name:
        raise ValidationError(
            "El archivo no tiene un nombre válido."
        )

    if clean_name in {".", ".."}:
        raise ValidationError(
            "El archivo no tiene un nombre válido."
        )

    # Evita caracteres de control.
    clean_name = "".join(
        character
        for character in clean_name
        if character.isprintable()
    ).strip()

    if not clean_name:
        raise ValidationError(
            "El archivo no tiene un nombre válido."
        )

    return clean_name[:255]


def get_file_extension(filename):
    """
    Obtiene la última extensión del archivo en minúsculas.

    Ejemplo:
        documento.PDF -> .pdf
        archivo.pdf.exe -> .exe
    """
    return Path(filename).suffix.lower()


def detect_mime_type(uploaded_file):
    """
    Detecta el MIME usando el contenido real del archivo y restaura
    posteriormente la posición del stream.
    """
    try:
        original_position = uploaded_file.tell()
    except (AttributeError, OSError):
        original_position = 0

    try:
        uploaded_file.seek(0)
        header = uploaded_file.read(8192)
    except Exception as exc:
        raise ValidationError(
            "No fue posible inspeccionar el contenido del archivo."
        ) from exc
    finally:
        try:
            uploaded_file.seek(original_position)
        except Exception:
            pass

    if not header:
        raise ValidationError(
            "El archivo está vacío."
        )

    try:
        detected_mime = magic.from_buffer(
            header,
            mime=True,
        )
    except Exception as exc:
        raise ValidationError(
            "No fue posible determinar el tipo del archivo."
        ) from exc

    if not detected_mime:
        raise ValidationError(
            "No fue posible determinar el tipo del archivo."
        )

    return detected_mime.lower().strip()


def validate_file_size(uploaded_file):
    max_size = getattr(
        settings,
        "OPERATIONAL_ATTACHMENT_MAX_SIZE",
        DEFAULT_MAX_FILE_SIZE,
    )

    size = getattr(uploaded_file, "size", None)

    if size is None:
        raise ValidationError(
            "No fue posible determinar el tamaño del archivo."
        )

    if size <= 0:
        raise ValidationError(
            "El archivo está vacío."
        )

    if size > max_size:
        max_size_mb = max_size // (1024 * 1024)

        raise ValidationError(
            f"El archivo supera el límite máximo de {max_size_mb} MB."
        )

    return size


def validate_file_extension(filename):
    extension = get_file_extension(filename)

    if not extension:
        raise ValidationError(
            "El archivo debe tener una extensión."
        )

    if extension in BLOCKED_EXTENSIONS:
        raise ValidationError(
            f"La extensión {extension} está bloqueada por seguridad."
        )

    if extension not in ALLOWED_FILE_TYPES:
        allowed_extensions = ", ".join(
            sorted(ALLOWED_FILE_TYPES.keys())
        )

        raise ValidationError(
            "La extensión del archivo no está permitida. "
            f"Extensiones aceptadas: {allowed_extensions}."
        )

    return extension


def validate_mime_compatibility(extension, detected_mime):
    file_type_config = ALLOWED_FILE_TYPES[extension]
    accepted_mime_types = file_type_config["mime_types"]

    if detected_mime not in accepted_mime_types:
        raise ValidationError(
            "El contenido del archivo no coincide con su extensión. "
            f"Extensión declarada: {extension}. "
            f"Tipo detectado: {detected_mime}."
        )


def validate_operational_attachment(uploaded_file):
    """
    Valida y devuelve los metadatos necesarios para guardar el adjunto.

    No carga el archivo a S3.
    """
    if uploaded_file is None:
        raise ValidationError(
            "No se recibió ningún archivo."
        )

    original_filename = sanitize_original_filename(
        getattr(uploaded_file, "name", "")
    )

    size_bytes = validate_file_size(uploaded_file)
    extension = validate_file_extension(original_filename)
    detected_mime = detect_mime_type(uploaded_file)

    validate_mime_compatibility(
        extension,
        detected_mime,
    )

    try:
        uploaded_file.seek(0)
    except Exception:
        pass

    return {
        "original_filename": original_filename,
        "extension": extension,
        "mime_type": detected_mime,
        "size_bytes": size_bytes,
    }