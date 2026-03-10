from __future__ import annotations

import ipaddress
import os
import socket
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
DEFAULT_ALLOWED_VIDEO_DOMAINS = ("youtube.com", "youtu.be", "vk.com", "vkvideo.ru")
BLOCKED_METADATA_NETWORKS = (
    ipaddress.ip_network("169.254.169.254/32"),
    ipaddress.ip_network("fd00:ec2::254/128"),
)


def _decode_ffmpeg_error(exc: ffmpeg.Error) -> str:
    if isinstance(exc.stderr, (bytes, bytearray)):
        return exc.stderr.decode("utf-8", errors="ignore").strip()
    return str(exc)


def _validate_url(url: str) -> str:
    normalized = url.strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise VideoDownloadError("Invalid URL. Use a full http(s) URL")
    hostname = parsed.hostname
    if not hostname:
        raise VideoDownloadError("Invalid URL. Hostname is required")
    if not _is_allowed_source_host(hostname):
        raise VideoDownloadError("URL host is not allowed")

    # Defense-in-depth only: DNS can change between validation and fetch.
    # Production must additionally enforce egress firewall/proxy restrictions.
    for ip in _resolve_host_ips(hostname):
        if _is_blocked_target_ip(ip):
            raise VideoDownloadError("URL points to a restricted network address")
    return normalized


def _normalized_domain(value: str) -> str:
    return value.strip().lower().rstrip(".")


def _allowed_video_domains() -> tuple[str, ...]:
    raw = os.getenv("VIDEO_SOURCE_DOMAIN_ALLOWLIST", "")
    if not raw.strip():
        return DEFAULT_ALLOWED_VIDEO_DOMAINS

    domains = tuple(
        domain
        for domain in (_normalized_domain(item) for item in raw.split(","))
        if domain
    )
    return domains or DEFAULT_ALLOWED_VIDEO_DOMAINS


def _is_allowed_source_host(hostname: str) -> bool:
    host = _normalized_domain(hostname)
    if not host:
        return False
    for domain in _allowed_video_domains():
        if host == domain or host.endswith(f".{domain}"):
            return True
    return False


def _resolve_host_ips(hostname: str) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    normalized_host = hostname.strip().strip("[]")
    if not normalized_host:
        raise VideoDownloadError("Invalid URL host")

    direct_host = normalized_host.split("%", 1)[0]
    try:
        return {ipaddress.ip_address(direct_host)}
    except ValueError:
        pass

    try:
        resolved = socket.getaddrinfo(
            normalized_host,
            None,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise VideoDownloadError(f"Failed to resolve URL host: {normalized_host}") from exc

    ips: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for entry in resolved:
        raw_ip = str(entry[4][0]).split("%", 1)[0]
        try:
            ips.add(ipaddress.ip_address(raw_ip))
        except ValueError:
            continue
    if not ips:
        raise VideoDownloadError(f"Could not resolve IP addresses for host: {normalized_host}")
    return ips


def _is_blocked_target_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_loopback or ip.is_private or ip.is_link_local:
        return True
    return any(ip in network for network in BLOCKED_METADATA_NETWORKS)


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
        "allowed_extractors": ["default", "-generic"],
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
