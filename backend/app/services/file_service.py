from __future__ import annotations

import asyncio
import os
import shutil
import stat
import uuid
from pathlib import Path
from typing import Any

import aiofiles
from fastapi import UploadFile

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov"}
ALLOWED_VIDEO_MIME_TYPES = {
    "video/mp4",
    "video/x-msvideo",
    "video/x-matroska",
    "video/quicktime",
}
CHUNK_SIZE_BYTES = 1024 * 1024
MAX_UPLOAD_SIZE_BYTES = 500 * 1024 * 1024
MAX_FILE_SIZE = MAX_UPLOAD_SIZE_BYTES
_MIME_TO_ALLOWED_EXTENSIONS = {
    "video/mp4": {".mp4"},
    "video/x-msvideo": {".avi"},
    "video/x-matroska": {".mkv"},
    "video/quicktime": {".mov"},
}


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _safe_suffix(filename: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_VIDEO_EXTENSIONS:
        return ""
    return suffix


def validate_video_file(upload_file: UploadFile) -> str:
    suffix = _safe_suffix(upload_file.filename)
    if suffix not in ALLOWED_VIDEO_EXTENSIONS:
        raise ValueError("Unsupported file extension. Allowed: MP4, AVI, MKV, MOV")

    content_type = (upload_file.content_type or "").lower()
    if not content_type:
        raise ValueError("Missing MIME type for uploaded file")
    if content_type == "application/octet-stream":
        raise ValueError("application/octet-stream uploads are not allowed")
    if content_type not in ALLOWED_VIDEO_MIME_TYPES:
        raise ValueError("Unsupported MIME type for video upload")
    allowed_suffixes = _MIME_TO_ALLOWED_EXTENSIONS.get(content_type, set())
    if allowed_suffixes and suffix not in allowed_suffixes:
        raise ValueError("File extension does not match MIME type")

    return suffix


def build_lecture_dir(media_root: str, lecture_id: uuid.UUID) -> Path:
    return Path(media_root) / str(lecture_id)


def generate_storage_name(suffix: str) -> str:
    return f"{uuid.uuid4().hex}{suffix}"


def _parse_clamav_result(result: Any, scanned_path: Path) -> tuple[bool, str | None]:
    if not result:
        return False, None

    # pyclamd usually returns: {"<path>": ("FOUND", "<signature>")}
    if isinstance(result, dict):
        entry = result.get(str(scanned_path))
        if entry is None:
            return False, None
        if not isinstance(entry, tuple) or len(entry) < 1:
            return True, "clamav-error:invalid-result-shape"

        status = str(entry[0]).upper()
        if status == "FOUND":
            signature = str(entry[1]) if len(entry) > 1 and entry[1] else "unknown-signature"
            return True, signature
        if status == "ERROR":
            reason = str(entry[1]).strip() if len(entry) > 1 and entry[1] else "unknown"
            return True, f"clamav-error:{reason}"

        return True, f"clamav-error:unexpected-status:{status.lower() or 'unknown'}"

    return True, "clamav-error:invalid-result-type"


def _scan_file_with_clamav(path: Path) -> None:
    if not _env_flag("CLAMAV_SCAN_ENABLED", default=False):
        return

    try:
        import pyclamd  # type: ignore[import-not-found]
    except Exception as exc:
        raise RuntimeError(
            "CLAMAV_SCAN_ENABLED is set, but pyclamd is not installed"
        ) from exc

    socket_path = os.getenv("CLAMAV_SOCKET_PATH", "").strip()
    host = os.getenv("CLAMAV_HOST", "localhost").strip() or "localhost"
    port_raw = os.getenv("CLAMAV_PORT", "3310").strip() or "3310"
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise RuntimeError("CLAMAV_PORT must be an integer") from exc

    try:
        client = (
            pyclamd.ClamdUnixSocket(filename=socket_path)
            if socket_path
            else pyclamd.ClamdNetworkSocket(host, port)
        )
        client.ping()
        result = client.scan_file(str(path))
    except Exception as exc:
        raise RuntimeError("Unable to scan uploaded file with ClamAV") from exc

    infected, signature = _parse_clamav_result(result, path)
    if infected:
        raise ValueError(f"Uploaded file failed malware scan ({signature})")


async def save_uploaded_file(
    upload_file: UploadFile,
    media_root: str,
    lecture_id: uuid.UUID,
    max_upload_size: int = MAX_UPLOAD_SIZE_BYTES,
) -> str:
    suffix = validate_video_file(upload_file)
    lecture_dir = build_lecture_dir(media_root, lecture_id)
    lecture_dir.mkdir(parents=True, exist_ok=True)

    stored_name = generate_storage_name(suffix)
    destination = lecture_dir / stored_name
    temp_destination = destination.with_suffix(f"{destination.suffix}.part")
    bytes_written = 0

    try:
        async with aiofiles.open(temp_destination, "wb") as file_obj:
            while True:
                chunk = await upload_file.read(CHUNK_SIZE_BYTES)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > max_upload_size:
                    raise ValueError(f"File is too large. Maximum size is {max_upload_size} bytes")
                await file_obj.write(chunk)

        await asyncio.to_thread(_scan_file_with_clamav, temp_destination)
        temp_destination.replace(destination)
        return str(Path(str(lecture_id)) / stored_name)
    except Exception:
        if temp_destination.exists():
            try:
                temp_destination.unlink()
            except OSError:
                pass
        raise
    finally:
        await upload_file.close()


def delete_lecture_media(media_root: str, lecture_id: uuid.UUID) -> None:
    lecture_dir = build_lecture_dir(media_root, lecture_id)
    if lecture_dir.exists():
        shutil.rmtree(lecture_dir, onerror=_retry_remove_readonly)


def _retry_remove_readonly(function, path: str, _exc_info) -> None:
    path_obj = Path(path)
    if path_obj.is_dir():
        os.chmod(path, stat.S_IRWXU)
    else:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    function(path)
