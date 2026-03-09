from __future__ import annotations

import os
import shutil
import stat
import uuid
from pathlib import Path

import aiofiles
from fastapi import UploadFile

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov"}
ALLOWED_VIDEO_MIME_TYPES = {
    "video/mp4",
    "video/x-msvideo",
    "video/x-matroska",
    "video/quicktime",
}


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
    if content_type and content_type != "application/octet-stream":
        if content_type not in ALLOWED_VIDEO_MIME_TYPES:
            raise ValueError("Unsupported MIME type for video upload")

    return suffix


def build_lecture_dir(media_root: str, lecture_id: uuid.UUID) -> Path:
    return Path(media_root) / str(lecture_id)


def generate_storage_name(suffix: str) -> str:
    return f"{uuid.uuid4().hex}{suffix}"


async def save_uploaded_file(upload_file: UploadFile, media_root: str, lecture_id: uuid.UUID) -> str:
    suffix = validate_video_file(upload_file)
    lecture_dir = build_lecture_dir(media_root, lecture_id)
    lecture_dir.mkdir(parents=True, exist_ok=True)

    stored_name = generate_storage_name(suffix)
    destination = lecture_dir / stored_name

    async with aiofiles.open(destination, "wb") as file_obj:
        while True:
            chunk = await upload_file.read(1024 * 1024)
            if not chunk:
                break
            await file_obj.write(chunk)

    await upload_file.close()
    return str(Path(str(lecture_id)) / stored_name)


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
