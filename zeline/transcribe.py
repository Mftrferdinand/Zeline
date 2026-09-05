"""Turn inbound audio into text, so a voice note is a message and not a dead end.

The gateway already downloaded voice notes to the media inbox. From there the trail
stopped: ``analyze_media`` replied *"transcribe first (e.g. an STT/Whisper tool)"* and
no such tool existed anywhere in Zeline — grep for whisper/stt/speech returned
nothing. So on the platform Zeline is used from, the most natural way to send a long
instruction produced an apology.

Design notes worth keeping:

**The provider's own ``/audio/transcriptions`` endpoint, not a bundled model.** Zeline
runs on a phone; shipping or downloading a Whisper build is not a reasonable install
step, and every provider that speaks the OpenAI API already exposes this route. It is
the same shape ``generate_image`` uses for ``/images/generations``, including the
"owner must configure a model" gate — a transcription model is a separate, often
separately-billed model, so guessing one produces a confusing 404 rather than a
transcript.

**Opus goes up as-is.** Telegram voice notes are Ogg/Opus, which the OpenAI
transcription API accepts directly (flac, m4a, mp3, mp4, mpeg, mpga, oga, ogg, wav,
webm). Converting with ffmpeg first would add a dependency, a temp file and a failure
mode to buy nothing. Conversion is only attempted for formats the API does *not*
accept, and only when ffmpeg exists.

**Long audio is refused, not silently truncated.** A 40-minute recording is a real
cost and a real latency; saying so lets the operator decide instead of waiting three
minutes for a bill.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import requests

from zeline import config

#: Formats the OpenAI-compatible transcription API accepts directly.
NATIVE_FORMATS = frozenset(
    {".flac", ".m4a", ".mp3", ".mp4", ".mpeg", ".mpga", ".oga", ".ogg", ".wav", ".webm"}
)
#: Anything else is converted to wav first, when ffmpeg is available.
CONVERTIBLE_FORMATS = frozenset({".opus", ".aac", ".amr", ".wma", ".aiff", ".caf", ".3gp"})

#: The API's own hard limit is 25 MB. Refusing a hair under it means the failure is
#: ours and specific, rather than an opaque 413 from the provider.
MAX_UPLOAD_BYTES = 24 * 1024 * 1024

#: Generous: a phone recording of a few minutes is normal, a lecture is not.
TIMEOUT_SECONDS = 300

CONVERT_TIMEOUT_SECONDS = 120


class TranscribeError(RuntimeError):
    """Something the caller should be told plainly."""


def model() -> str:
    return str(getattr(config, "AUDIO_MODEL", "") or "").strip()


def configured() -> bool:
    return bool(model() and config.API_KEY and config.BASE_URL)


def unconfigured_reason() -> str:
    if not config.API_KEY or not config.BASE_URL:
        return "the provider is not configured (no base URL or API key)"
    return (
        "no transcription model is set. The owner can set one with `zeline setup` "
        "or the ZELINE_AUDIO_MODEL environment variable, e.g. whisper-1 or "
        "gpt-4o-mini-transcribe"
    )


def _prepare(path: Path) -> tuple[Path, Path | None]:
    """Return (file_to_upload, temp_to_clean_up)."""
    suffix = path.suffix.lower()
    if suffix in NATIVE_FORMATS:
        return path, None
    if suffix not in CONVERTIBLE_FORMATS:
        raise TranscribeError(
            f"'{suffix or path.name}' is not an audio format this can read. "
            f"Supported directly: {', '.join(sorted(NATIVE_FORMATS))}."
        )
    if not shutil.which("ffmpeg"):
        raise TranscribeError(
            f"'{suffix}' needs converting first and ffmpeg is not installed. "
            "Install ffmpeg, or send the audio as ogg/mp3/m4a/wav."
        )
    target = Path(tempfile.mkdtemp(prefix="zl-stt-")) / (path.stem + ".wav")
    try:
        completed = subprocess.run(
            # 16 kHz mono: what speech models want, and a fraction of the upload.
            ["ffmpeg", "-nostdin", "-y", "-i", str(path), "-ar", "16000", "-ac", "1", str(target)],
            capture_output=True,
            text=True,
            timeout=CONVERT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TranscribeError("converting the audio timed out.") from exc
    if completed.returncode != 0 or not target.is_file():
        detail = (completed.stderr or "").strip().splitlines()
        raise TranscribeError(f"ffmpeg could not convert the audio: {detail[-1][:160] if detail else 'unknown error'}")
    return target, target.parent


def transcribe(path: Path, *, language: str = "", prompt: str = "") -> str:
    """Transcribe one audio file. Raises TranscribeError with a usable message."""
    if not path.is_file():
        raise TranscribeError(f"not a file or not found: {path}")
    size = path.stat().st_size
    if size == 0:
        raise TranscribeError(f"{path.name} is empty.")
    if size > MAX_UPLOAD_BYTES:
        raise TranscribeError(
            f"{path.name} is {size / 1_048_576:.1f} MB, over the "
            f"{MAX_UPLOAD_BYTES // 1_048_576} MB transcription limit. Split it or send a shorter clip."
        )
    if not configured():
        raise TranscribeError(unconfigured_reason() + ".")

    upload, cleanup = _prepare(path)
    data: dict[str, str] = {"model": model()}
    if language.strip():
        data["language"] = language.strip()[:8]
    if prompt.strip():
        # The API calls this a "prompt": vocabulary/spelling hints, not an instruction.
        data["prompt"] = prompt.strip()[:900]
    try:
        with upload.open("rb") as handle:
            response = requests.post(
                f"{config.BASE_URL}/audio/transcriptions",
                headers={"Authorization": f"Bearer {config.API_KEY}"},
                files={"file": (upload.name, handle)},
                data=data,
                timeout=TIMEOUT_SECONDS,
            )
    except requests.exceptions.Timeout as exc:
        raise TranscribeError(
            f"the transcription model did not respond within {TIMEOUT_SECONDS}s."
        ) from exc
    except requests.exceptions.ConnectionError as exc:
        raise TranscribeError(f"could not reach the provider at {config.BASE_URL}.") from exc
    except requests.RequestException as exc:
        raise TranscribeError(f"network error ({exc.__class__.__name__}).") from exc
    finally:
        if cleanup is not None:
            shutil.rmtree(cleanup, ignore_errors=True)

    if not response.ok:
        detail = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                inner = payload.get("error")
                if isinstance(inner, dict):
                    detail = str(inner.get("message") or "")
                elif inner:
                    detail = str(inner)
        except ValueError:
            detail = (response.text or "")[:200]
        if response.status_code == 404:
            detail = detail or (
                f"the model '{model()}' or the /audio/transcriptions route is not "
                "available on this provider"
            )
        raise TranscribeError(f"provider HTTP {response.status_code}{f' — {detail}' if detail else ''}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise TranscribeError("the provider returned a non-JSON response.") from exc
    text = ""
    if isinstance(payload, dict):
        text = str(payload.get("text") or "").strip()
    if not text:
        raise TranscribeError("the provider returned no transcript text.")
    return text
