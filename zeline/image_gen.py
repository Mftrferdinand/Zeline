"""Local image generation: text prompt -> image file.

This module covers image generation OUTSIDE the chat provider: a direct
OpenAI Images API call (DALL-E) when the owner has an OpenAI key, or a fully
local Stable Diffusion install (``diffusers`` library, or an AUTOMATIC1111 /
ComfyUI-style web UI listening on localhost). The provider-routed tools
(``generate_image`` / ``generate_video`` in ``zeline/tools.py``) are separate —
this module is the offline/local counterpart, mirroring how ``voice_speak``
relates to ``text_to_speech``.

Design decisions (documented, mirroring ``zeline/voice.py``):

1. **All backends optional, detected at runtime.** No hard dependency on
   ``openai``, ``diffusers`` or ``torch``. Importing this module never pulls
   heavy ML libraries.

2. **Never auto-download models.** Stable Diffusion checkpoints are gigabytes;
   ``diffusers`` is always loaded with ``local_files_only=True`` so a missing
   model fails with a clear message instead of silently downloading. The owner
   points ``SD_MODEL_DIR`` at an already-downloaded checkpoint.

3. **No silent fallback.** If the requested backend is unavailable, or no
   backend is available at all, ``generate_image`` raises ``ImageGenError``
   with a message that names the missing piece and how to fix it.

4. **Video is an honest placeholder.** ``generate_video`` raises
   ``ImageGenError`` explaining that local text-to-video is not implemented
   and pointing at the provider ``generate_video`` tool (Veo) instead of
   faking output.
"""
from __future__ import annotations

import base64
import http.client
import json
import os
import re
import shutil
import ssl
import threading
import time
import urllib.request
from pathlib import Path

# Cache pipeline Stable Diffusion per model_dir — load sekali, reuse antar
# panggilan (load model bisa makan menit dan GB RAM/VRAM).
_SD_PIPES: dict[str, object] = {}
_SD_PIPES_LOCK = threading.Lock()

#: Batas karakter prompt. DALL-E menolak prompt raksasa; potong lebih awal
#: dengan pesan yang jelas alih-alih error samar dari API.
MAX_PROMPT_CHARS = 2000

#: Ukuran yang didukung backend OpenAI langsung.
_OPENAI_SIZES = {"256x256", "512x512", "1024x1024", "1792x1024", "1024x1792"}

#: Ekstensi output yang didukung.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

#: Timeout HTTP untuk API eksternal (detik).
_HTTP_TIMEOUT = 120

#: Cap respons body API (50MB) — cegah OOM.
_MAX_BODY_BYTES = 50 * 1024 * 1024

#: Timeout untuk generate lokal (detik) — SD di CPU bisa lambat.
_SD_TIMEOUT_HINT = 600


class ImageGenError(RuntimeError):
    """Kegagalan generate gambar/video yang bisa dijelaskan ke user."""


def _which(names: tuple[str, ...]) -> str | None:
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None


def _openai_key() -> str:
    return (os.environ.get("OPENAI_API_KEY", "") or "").strip()


def _sd_model_dir() -> Path:
    raw = (os.environ.get("SD_MODEL_DIR", "") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".cache" / "huggingface" / "stable-diffusion"


def _sd_webui_url() -> str:
    return (os.environ.get("SD_WEBUI_URL", "") or "").strip() or "http://127.0.0.1:7860"


def _validate_webui_url(url: str) -> str:
    """Tolak URL web UI yang jelas-jelas bukan HTTP(S) atau mengarah ke
    cloud metadata service. Loopback diizinkan — itu default yang sah
    (web UI jalan lokal). URL ini dari env operator sendiri, jadi ini
    defense-in-depth, bukan trust boundary utama."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ImageGenError(
            f"SD_WEBUI_URL tidak valid: scheme harus http/https, dapat {parsed.scheme!r}."
        )
    host = (parsed.hostname or "").lower()
    if host in ("169.254.169.254", "metadata.google.internal"):
        raise ImageGenError(
            "SD_WEBUI_URL menunjuk ke cloud metadata service — ditolak."
        )
    return url


def openai_available() -> bool:
    """True bila OPENAI_API_KEY terisi (backend DALL-E langsung bisa dipakai)."""
    return bool(_openai_key())


def _diffusers_importable() -> bool:
    try:
        import diffusers  # noqa: F401
        return True
    except ImportError:
        return False


#: TTL cache hasil probe web UI (detik). Auto path memanggil
#: _sd_webui_reachable() lalu _generate_sd_webui() mem-probe lagi —
#: tanpa cache, tiap generate membayar 2x round-trip HTTP.
_WEBUI_PROBE_TTL = 30.0
_webui_probe_cache: dict[str, object] = {"at": 0.0, "ok": False}


def _sd_webui_reachable(timeout: float = 3.0) -> bool:
    now = time.monotonic()
    if now - _webui_probe_cache["at"] < _WEBUI_PROBE_TTL:
        return bool(_webui_probe_cache["ok"])
    try:
        base = _validate_webui_url(_sd_webui_url()).rstrip("/")
    except ImageGenError:
        return False
    url = base + "/sdapi/v1/sd-models"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ok = resp.status == 200
    except Exception:
        ok = False
    _webui_probe_cache["at"] = now
    _webui_probe_cache["ok"] = ok
    return ok


def _model_dir_has_model() -> bool:
    """True bila direktori model ada dan berisi file. OSError -> False."""
    try:
        d = _sd_model_dir()
        return d.is_dir() and any(d.iterdir())
    except OSError:
        return False


def sd_available() -> bool:
    """True bila Stable Diffusion lokal bisa dipakai (diffusers+model atau web UI)."""
    if _diffusers_importable() and _model_dir_has_model():
        return True
    return _sd_webui_reachable()


def image_gen_status() -> dict[str, dict[str, object]]:
    """Status deteksi backend, untuk CLI `zeline image --status`."""
    openai_ok = openai_available()
    diffusers_ok = _diffusers_importable()
    model_dir = _sd_model_dir()
    model_ok = _model_dir_has_model()
    webui_ok = _sd_webui_reachable()
    return {
        "openai": {
            "available": openai_ok,
            "backend": "dall-e" if openai_ok else "-",
            "detail": (
                "OPENAI_API_KEY terisi — DALL-E langsung bisa dipakai."
                if openai_ok
                else "OPENAI_API_KEY tidak diset. Set env OPENAI_API_KEY untuk pakai DALL-E langsung."
            ),
        },
        "sd": {
            "available": diffusers_ok and model_ok or webui_ok,
            "backend": (
                "diffusers"
                if (diffusers_ok and model_ok)
                else ("webui" if webui_ok else "-")
            ),
            "detail": (
                f"diffusers + model di {model_dir}."
                if (diffusers_ok and model_ok)
                else (
                    f"Stable Diffusion web UI di {_sd_webui_url()}."
                    if webui_ok
                    else (
                        "Tidak ada Stable Diffusion lokal. "
                        "Install `diffusers`+`torch` dan set SD_MODEL_DIR ke checkpoint, "
                        f"atau jalankan web UI di {_sd_webui_url()}."
                    )
                )
            ),
        },
    }


def _validate_prompt(prompt: str) -> str:
    if not isinstance(prompt, str):
        raise ImageGenError(
            f"prompt harus string, bukan {type(prompt).__name__}."
        )
    text = prompt.strip()
    if not text:
        raise ImageGenError("prompt kosong — tulis deskripsi gambar yang mau dibuat.")
    if len(text) > MAX_PROMPT_CHARS:
        raise ImageGenError(
            f"prompt terlalu panjang ({len(text)} karakter, maks {MAX_PROMPT_CHARS}). "
            "Singkatkan deskripsinya."
        )
    return text


def _validate_output(output_path: str | os.PathLike) -> Path:
    if not isinstance(output_path, (str, os.PathLike)):
        raise ImageGenError(
            f"output_path harus string atau Path, bukan {type(output_path).__name__}."
        )
    out = Path(output_path).expanduser()
    if out.suffix.lower() not in _IMAGE_EXTS:
        raise ImageGenError(
            f"output harus berakhiran {sorted(_IMAGE_EXTS)}, dapat: {out.suffix or '(tanpa ekstensi)'}."
        )
    return out


def _validate_size_openai(size: str) -> str:
    s = (size or "1024x1024").strip() or "1024x1024"
    if s not in _OPENAI_SIZES:
        raise ImageGenError(
            f"ukuran '{s}' tidak didukung backend openai. "
            f"Pilih: {', '.join(sorted(_OPENAI_SIZES))}."
        )
    return s


_SIZE_RE = re.compile(r"^(\d+)x(\d+)$")


def _validate_size_sd(size: str) -> tuple[int, int]:
    s = (size or "512x512").strip() or "512x512"
    m = _SIZE_RE.match(s)
    if not m:
        raise ImageGenError(f"ukuran '{s}' tidak valid — format WxH, misal 512x512.")
    w, h = int(m.group(1)), int(m.group(2))
    if w < 64 or h < 64 or w > 2048 or h > 2048:
        raise ImageGenError("ukuran SD harus 64–2048 piksel per sisi.")
    return w, h


def _post_json(url: str, payload: dict, headers: dict[str, str], timeout: float) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # Baca dengan cap 50MB — jangan OOM kalau server ngaco.
            body = resp.read(_MAX_BODY_BYTES + 1)
            if len(body) > _MAX_BODY_BYTES:
                raise ImageGenError("respons API terlalu besar (>50MB).")
    except urllib.error.HTTPError as exc:
        # Cap body yang dibaca — jangan OOM kalau server error mengirim body raksasa.
        raw = exc.read(_MAX_BODY_BYTES + 1)
        if len(raw) > _MAX_BODY_BYTES:
            detail = "(error body terlalu besar)"
        else:
            detail = raw.decode("utf-8", errors="replace")[:500]
        raise ImageGenError(f"API error {exc.code}: {detail or exc.reason}")
    except urllib.error.URLError as exc:
        raise ImageGenError(f"tidak bisa menghubungi {url}: {exc.reason}")
    except TimeoutError:
        raise ImageGenError(f"timeout ({timeout}s) menghubungi {url} — coba lagi.")
    except (ssl.SSLError, http.client.HTTPException, OSError) as exc:
        raise ImageGenError(f"koneksi ke {url} gagal: {exc}")
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ImageGenError("respons API bukan JSON yang valid.")


def _generate_openai(prompt: str, out: Path, size: str) -> Path:
    """Generate via OpenAI Images API langsung (DALL-E)."""
    key = _openai_key()
    if not key:
        raise ImageGenError(
            "backend openai butuh OPENAI_API_KEY. "
            "Set env OPENAI_API_KEY dulu, atau pakai backend 'sd'."
        )
    size = _validate_size_openai(size)
    model = (os.environ.get("IMAGE_GEN_MODEL", "") or "").strip() or "dall-e-3"
    payload = {"model": model, "prompt": prompt, "size": size, "n": 1, "response_format": "b64_json"}
    result = _post_json(
        "https://api.openai.com/v1/images/generations",
        payload,
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        _HTTP_TIMEOUT,
    )
    try:
        b64 = result["data"][0]["b64_json"]
        raw = base64.b64decode(b64)
    except (KeyError, IndexError, ValueError, TypeError):
        raise ImageGenError("respons OpenAI tidak berisi gambar (format tak dikenal).")
    # Backend sudah pasti ada (kita baru saja memakainya) — aman bikin direktori.
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(raw)
    except OSError as exc:
        raise ImageGenError(f"gagal menyimpan gambar ke {out}: {exc}")
    return out


def _generate_sd_diffusers(prompt: str, out: Path, size: str) -> Path:
    """Generate via diffusers (in-process, tanpa download model)."""
    if not _diffusers_importable():
        raise ImageGenError(
            "backend sd butuh library `diffusers` (+`torch`). "
            "Install: pip install diffusers torch -- atau jalankan Stable Diffusion web UI."
        )
    model_dir = _sd_model_dir()
    if not _model_dir_has_model():
        raise ImageGenError(
            f"model Stable Diffusion tidak ditemukan di {model_dir}. "
            "Download checkpoint dulu dan set SD_MODEL_DIR ke direktorinya. "
            "Tidak ada download otomatis."
        )
    w, h = _validate_size_sd(size)
    try:
        from diffusers import StableDiffusionPipeline
        import torch
    except ImportError as exc:
        raise ImageGenError(f"import diffusers/torch gagal: {exc}")
    cache_key = str(model_dir.resolve(strict=False))
    with _SD_PIPES_LOCK:
        pipe = _SD_PIPES.get(cache_key)
    if pipe is None:
        try:
            new_pipe = StableDiffusionPipeline.from_pretrained(
                str(model_dir), torch_dtype=torch.float16, local_files_only=True
            )
            device = "cuda" if torch.cuda.is_available() else "cpu"
            new_pipe = new_pipe.to(device)
        except Exception as exc:
            raise ImageGenError(f"Stable Diffusion gagal load model: {exc}")
        with _SD_PIPES_LOCK:
            # Double-checked: thread lain mungkin sudah load duluan.
            pipe = _SD_PIPES.setdefault(cache_key, new_pipe)
    try:
        image = pipe(prompt, height=h, width=w).images[0]
    except Exception as exc:
        raise ImageGenError(f"Stable Diffusion gagal generate: {exc}")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        image.save(str(out))
    except OSError as exc:
        raise ImageGenError(f"gagal menyimpan gambar ke {out}: {exc}")
    return out


def _generate_sd_webui(prompt: str, out: Path, size: str) -> Path:
    """Generate via Stable Diffusion web UI API (AUTOMATIC1111) di localhost."""
    base = _validate_webui_url(_sd_webui_url()).rstrip("/")
    if not _sd_webui_reachable():
        raise ImageGenError(
            f"Stable Diffusion web UI tidak reachable di {base}. "
            "Jalankan dengan --api, atau pakai backend 'openai'."
        )
    w, h = _validate_size_sd(size)
    result = _post_json(
        base + "/sdapi/v1/txt2img",
        {"prompt": prompt, "width": w, "height": h, "steps": 25},
        {"Content-Type": "application/json"},
        _SD_TIMEOUT_HINT,
    )
    try:
        raw = base64.b64decode(result["images"][0])
    except (KeyError, IndexError, ValueError, TypeError):
        raise ImageGenError("respons web UI tidak berisi gambar.")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(raw)
    except OSError as exc:
        raise ImageGenError(f"gagal menyimpan gambar ke {out}: {exc}")
    return out


def generate_image(
    prompt: str,
    output_path: str | os.PathLike,
    backend: str | None = None,
    size: str = "1024x1024",
) -> Path:
    """Generate gambar dari teks, simpan ke file. Return path hasil.

    backend: "openai" | "sd" | None (auto: openai bila ada key, lalu sd).
    Semua kegagalan raise ImageGenError dengan pesan yang bisa dipakai user.
    Fungsi ini trusted-caller: path containment ditangani wrapper (tool/CLI).
    """
    if backend is not None and not isinstance(backend, str):
        raise ImageGenError(
            f"backend harus string ('openai'/'sd') atau None, bukan {type(backend).__name__}."
        )
    text = _validate_prompt(prompt)
    out = _validate_output(output_path)
    want = (backend or "").strip().lower() or None

    if want == "openai":
        return _generate_openai(text, out, size)
    if want == "sd":
        if _diffusers_importable() and _model_dir_has_model():
            return _generate_sd_diffusers(text, out, size)
        return _generate_sd_webui(text, out, size)
    if want:
        raise ImageGenError(
            f"backend '{backend}' tidak dikenal. Pilih: openai, sd (atau kosongkan untuk auto)."
        )

    # Auto: openai dulu (paling reliable), lalu SD lokal.
    if openai_available():
        return _generate_openai(text, out, size)
    if _diffusers_importable() and _model_dir_has_model():
        return _generate_sd_diffusers(text, out, size)
    if _sd_webui_reachable():
        return _generate_sd_webui(text, out, size)
    raise ImageGenError(
        "tidak ada backend image generation yang tersedia. "
        "Set OPENAI_API_KEY untuk DALL-E langsung, "
        "atau install diffusers+torch dan set SD_MODEL_DIR, "
        "atau jalankan Stable Diffusion web UI di 127.0.0.1:7860. "
        "Alternatif: tool `generate_image` memakai provider chat yang sudah dikonfigurasi."
    )


