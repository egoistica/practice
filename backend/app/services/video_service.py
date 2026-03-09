from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import ffmpeg
import yt_dlp


class VideoServiceError(RuntimeError):
    """Base exception for video service failures."""


class UnsupportedVideoFormatError(VideoServiceError):
    """Raised when URL/video format is not supported."""


class VideoDownloadError(VideoServiceError):
    """Raised when the video cannot be downloaded."""


SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov", ".webm", ".m4v"}


def _decode_ffmpeg_error(exc: ffmpeg.Error) -> str:
    if isinstance(exc.stderr, (bytes, bytearray)):
        return exc.stderr.decode("utf-8", errors="ignore").strip()
    return str(exc)


def _validate_url(url: str) -> str:
    normalized = url.strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Invalid URL. Use a full http(s) URL")
    return normalized


def _validate_video_path(video_path: str) -> Path:
    source = Path(video_path)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"Video file not found: {source}")
    if source.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
        raise UnsupportedVideoFormatError(
            f"Unsupported video format: {source.suffix or '<none>'}. "
            "Supported: mp4, avi, mkv, mov, webm, m4v"
        )
    return source


def _resolve_output_file(output_path: str, default_filename: str) -> Path:
    path = Path(output_path)
    if path.exists() and path.is_dir():
        resolved = path / default_filename
    elif not path.suffix:
        resolved = path / default_filename
    else:
        resolved = path
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def download_video(url: str, output_path: str) -> str:
    normalized_url = _validate_url(url)
    output = Path(output_path)
    output_dir = output.parent if output.suffix else output
    output_dir.mkdir(parents=True, exist_ok=True)
    output_template = f"{output.stem}.%(ext)s" if output.suffix else "%(title).180B-%(id)s.%(ext)s"

    ydl_opts = {
        "format": "bestvideo+bestaudio/best",
        "noplaylist": True,
        "outtmpl": str(output_dir / output_template),
        "merge_output_format": "mp4",
        "restrictfilenames": True,
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(normalized_url, download=True)
            if not isinstance(info, dict):
                raise VideoDownloadError("Unable to resolve downloaded file metadata")

            candidate_paths: list[Path] = []
            requested_downloads = info.get("requested_downloads") or []
            for item in requested_downloads:
                if isinstance(item, dict) and item.get("filepath"):
                    candidate_paths.append(Path(item["filepath"]))

            prepared = ydl.prepare_filename(info)
            candidate_paths.extend([Path(prepared), Path(prepared).with_suffix(".mp4")])

            for candidate in candidate_paths:
                if candidate.exists():
                    return str(candidate)
    except yt_dlp.utils.DownloadError as exc:
        message = str(exc).lower()
        if "unsupported url" in message or "unsupported" in message:
            raise UnsupportedVideoFormatError("Unsupported URL or video format") from exc
        raise VideoDownloadError(f"Unable to download video from URL: {normalized_url}") from exc

    raise VideoDownloadError("Video download finished but output file was not found")


def extract_audio(video_path: str, output_path: str) -> str:
    source = _validate_video_path(video_path)
    output_file = _resolve_output_file(output_path, f"{source.stem}.wav")
    if output_file.suffix.lower() != ".wav":
        output_file = output_file.with_suffix(".wav")

    try:
        (
            ffmpeg
            .input(str(source))
            .output(
                str(output_file),
                acodec="pcm_s16le",
                ac=1,
                ar=16000,
                format="wav",
            )
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as exc:
        raise VideoServiceError(f"Failed to extract audio: {_decode_ffmpeg_error(exc)}") from exc

    return str(output_file)


def get_video_duration(video_path: str) -> float:
    source = _validate_video_path(video_path)
    try:
        probe_data = ffmpeg.probe(str(source))
    except ffmpeg.Error as exc:
        raise VideoServiceError(f"Failed to probe video duration: {_decode_ffmpeg_error(exc)}") from exc

    duration_raw = (probe_data.get("format") or {}).get("duration")
    if duration_raw is None:
        raise VideoServiceError("Unable to detect video duration")

    duration = float(duration_raw)
    if duration < 0:
        raise VideoServiceError("Video duration cannot be negative")
    return duration


def get_video_thumbnail(video_path: str, output_path: str) -> str:
    source = _validate_video_path(video_path)
    output_file = _resolve_output_file(output_path, f"{source.stem}.jpg")
    if output_file.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        output_file = output_file.with_suffix(".jpg")

    duration = get_video_duration(str(source))
    timestamp = 0.0 if duration <= 1 else min(duration * 0.1, duration - 0.1)

    try:
        (
            ffmpeg
            .input(str(source), ss=timestamp)
            .output(str(output_file), vframes=1)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as exc:
        raise VideoServiceError(f"Failed to create thumbnail: {_decode_ffmpeg_error(exc)}") from exc

    return str(output_file)

