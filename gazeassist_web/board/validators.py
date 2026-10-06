"""Validators for uploaded patient reports.

A file is accepted only if ALL of these agree that it is a PDF, PNG or JPEG:
the file extension, the browser-reported content type, and the file's
first bytes ("magic number"). This stops e.g. an .exe renamed to .pdf.
"""
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.template.defaultfilters import filesizeformat

ALLOWED_REPORT_EXTENSIONS = ("pdf", "png", "jpg", "jpeg")

ALLOWED_CONTENT_TYPES = {
    "pdf": {"application/pdf"},
    "png": {"image/png"},
    "jpg": {"image/jpeg", "image/pjpeg"},
    "jpeg": {"image/jpeg", "image/pjpeg"},
}

FILE_SIGNATURES = {
    "pdf": (b"%PDF-",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
}


def _read_header(fileobj, size=8):
    """Return the first `size` bytes without disturbing the read position."""
    position = fileobj.tell()
    fileobj.seek(0)
    header = fileobj.read(size)
    fileobj.seek(position)
    return header


def validate_report_file(value):
    """Validate extension, size, content type and file signature of a report."""
    # Already-stored files were validated when they were uploaded.
    if getattr(value, "_committed", False):
        return

    # A model FieldFile wraps the UploadedFile in `.file`; a bare UploadedFile
    # carries content_type itself.
    content_type = getattr(value, "content_type", None) or getattr(
        getattr(value, "file", None), "content_type", None
    )
    extension = Path(value.name).suffix.lower().lstrip(".")

    if extension not in ALLOWED_REPORT_EXTENSIONS:
        raise ValidationError(
            "Only PDF, PNG and JPG files are allowed.", code="invalid_extension"
        )

    max_bytes = settings.REPORT_MAX_UPLOAD_BYTES
    if value.size > max_bytes:
        raise ValidationError(
            "This file is %(size)s. The maximum allowed size is %(max)s.",
            code="file_too_large",
            params={"size": filesizeformat(value.size), "max": filesizeformat(max_bytes)},
        )

    if content_type and content_type not in ALLOWED_CONTENT_TYPES[extension]:
        raise ValidationError(
            "The file type does not match its .%(ext)s extension.",
            code="invalid_content_type",
            params={"ext": extension},
        )

    if not _read_header(value).startswith(FILE_SIGNATURES[extension]):
        raise ValidationError(
            "This file does not look like a real %(ext)s file.",
            code="invalid_signature",
            params={"ext": extension.upper()},
        )
