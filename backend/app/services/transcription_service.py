from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any


class TranscriptionServiceError(RuntimeError):
    """Base exception for transcription failures."""


class TranscriptionDependencyError(TranscriptionServiceError):
    """Raised when faster-whisper dependency is unavailable."""


class AudioDecodingError(TranscriptionServiceError):
    """Raised when audio file is corrupted or cannot be decoded."""


ALLOWED_AUDIO_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".m4a",
    ".ogg",
    ".flac",
    ".aac",
    ".webm",
}

WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "tiny")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_DOWNLOAD_ROOT = os.getenv("WHISPER_DOWNLOAD_ROOT", "/tmp/faster-whisper-cache")


def _validate_audio_path(audio_path: str) -> Path:
    source = Path(audio_path)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"Audio file not found: {source}")

    if source.suffix.lower() not in ALLOWED_AUDIO_EXTENSIONS:
        raise ValueError(
            f"Unsupported audio format: {source.suffix or '<none>'}. "
            "Supported: wav, mp3, m4a, ogg, flac, aac, webm"
        )

    try:
        with source.open("rb"):
            pass
    except OSError as exc:
        raise AudioDecodingError(f"Audio file is unreadable: {source}. Details: {exc}") from exc
    return source


def _is_decode_error(exc: Exception) -> bool:
    message = str(exc).lower()
    markers = (
        "invalid data found",
        "error opening input",
        "unsupported codec",
        "could not open input",
        "failed to decode",
        "ffmpeg",
    )
    return any(marker in message for marker in markers)


@lru_cache(maxsize=1)
def _get_whisper_model() -> Any:
    try:
        from faster_whisper import WhisperModel
    except ModuleNotFoundError as exc:
        raise TranscriptionDependencyError(
            "faster-whisper is not installed in this environment. "
            "Use the backend Docker container or Linux environment with dependencies."
        ) from exc

    try:
        Path(WHISPER_DOWNLOAD_ROOT).mkdir(parents=True, exist_ok=True)
        return WhisperModel(
            WHISPER_MODEL_SIZE,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
            download_root=WHISPER_DOWNLOAD_ROOT,
        )
    except OSError as exc:
        raise TranscriptionServiceError(
            "Failed to prepare Whisper cache directory. "
            f"download_root={WHISPER_DOWNLOAD_ROOT}, size={WHISPER_MODEL_SIZE}, "
            f"device={WHISPER_DEVICE}, compute_type={WHISPER_COMPUTE_TYPE}. "
            f"Details: {exc}"
        ) from exc
    except Exception as exc:  # pragma: no cover - depends on runtime/hardware
        raise TranscriptionServiceError(
            "Failed to initialize Whisper model. "
            f"Check model/device settings: download_root={WHISPER_DOWNLOAD_ROOT}, "
            f"size={WHISPER_MODEL_SIZE}, device={WHISPER_DEVICE}, "
            f"compute_type={WHISPER_COMPUTE_TYPE}"
        ) from exc


def transcribe_audio(audio_path: str, language: str = "ru") -> dict[str, Any]:
    source = _validate_audio_path(audio_path)
    model = _get_whisper_model()
    stripped_language = language.strip() if language is not None else ""
    normalized_language = stripped_language.lower() if stripped_language else "ru"

    try:
        segments_iter, _info = model.transcribe(
            str(source),
            language=normalized_language,
            vad_filter=True,
            beam_size=5,
        )

        segments: list[dict[str, Any]] = []
        texts: list[str] = []
        for segment in segments_iter:
            text = (segment.text or "").strip()
            if not text:
                continue

            start = max(float(segment.start), 0.0)
            end = max(float(segment.end), start)
            item = {
                "start": round(start, 3),
                "end": round(end, 3),
                "text": text,
            }
            segments.append(item)
            texts.append(text)

        return {
            "segments": segments,
            "full_text": " ".join(texts).strip(),
        }
    except Exception as exc:
        if _is_decode_error(exc):
            raise AudioDecodingError(
                "Unable to decode audio file. File may be corrupted or format is unsupported."
            ) from exc
        raise TranscriptionServiceError("Failed to transcribe audio") from exc
