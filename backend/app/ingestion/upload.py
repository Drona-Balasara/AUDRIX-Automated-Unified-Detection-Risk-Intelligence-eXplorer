"""Upload intake: safe, bounded, allowlisted file handling.

Security controls applied before any parsing happens:
  * the file extension is checked against a strict allowlist (``.csv``/``.json``);
  * the declared Content-Type is used only as a weak hint, never as proof;
  * the body is read in bounded chunks and rejected the moment it exceeds the
    configured limit, so an oversized upload is never fully buffered;
  * the client filename is never used as a filesystem path — only a sanitized
    basename is kept, purely for echoing back in the report.

No uploaded bytes are written to disk: validation and import operate on the
in-memory content (bounded by the size limit), which removes an entire class of
path-handling and temp-file-cleanup concerns for this local-first prototype.
"""

from __future__ import annotations

import os

from fastapi import UploadFile

CSV = "csv"
JSON = "json"

# Extension -> canonical format. The sole accepted extensions.
_EXTENSION_FORMAT = {".csv": CSV, ".json": JSON}

_READ_CHUNK = 1024 * 1024  # 1 MiB


class UploadError(Exception):
    """Raised when an upload is rejected before parsing.

    ``status_code`` is the HTTP status the API should return; ``code`` is a
    stable machine-readable string; ``message`` is safe for clients.
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def safe_filename(filename: str | None) -> str:
    """Return a sanitized basename safe to echo in a report (never a path)."""
    if not filename:
        return "upload"
    base = os.path.basename(filename.replace("\\", "/"))
    base = base.strip() or "upload"
    return base[:128]


def determine_format(filename: str | None, content_type: str | None) -> str:
    """Resolve the canonical format from the extension allowlist.

    The Content-Type header is intentionally not authoritative; it is ignored
    for the decision and only the extension allowlist is trusted here. Content
    is additionally verified against the format during parsing.
    """
    name = safe_filename(filename).lower()
    _, ext = os.path.splitext(name)
    fmt = _EXTENSION_FORMAT.get(ext)
    if fmt is None:
        raise UploadError(
            415,
            "unsupported_extension",
            "unsupported file type; only .csv and .json are accepted",
        )
    return fmt


async def read_bounded(upload: UploadFile, max_bytes: int) -> bytes:
    """Read the upload in chunks, rejecting it if it exceeds ``max_bytes``."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise UploadError(
                413,
                "payload_too_large",
                f"upload exceeds the maximum allowed size of {max_bytes} bytes",
            )
        chunks.append(chunk)

    if total == 0:
        raise UploadError(400, "empty_upload", "uploaded file is empty")

    return b"".join(chunks)


async def receive_upload(upload: UploadFile, max_bytes: int) -> tuple[bytes, str, str]:
    """Validate type/size and return ``(content, format, safe_filename)``."""
    fmt = determine_format(upload.filename, upload.content_type)
    content = await read_bounded(upload, max_bytes)
    return content, fmt, safe_filename(upload.filename)
