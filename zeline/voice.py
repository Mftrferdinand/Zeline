"""Text-to-speech inti: teks menjadi voice note (ogg/opus) via edge-tts + ffmpeg.

Dulu logika ini tinggal di ``zeline/skills/voice-reply/scripts/voice_reply.py``
sebagai alat bantu agent; kini dipromosikan ke core supaya gateway bisa
membalas dengan voice note secara otomatis (mode per-chat, lihat
``zeline.voice_prefs``). Script lama kini menjadi thin wrapper di atas modul
ini — tidak ada logika ganda.

Keputusan desain yang didokumentasikan:

1. **edge-tts, bukan endpoint provider.** Provider OpenAI-compatible memang
   punya ``/audio/speech``, tapi suara-suara yang diminta di sini adalah
   preset "cewe anime Indonesia" hasil tuning edge-tts (voice neural Microsoft
   + pitch/rate) — bukan suara generik provider. edge-tts juga gratis dan
   tidak memakan kuota model chat.

2. **ogg/opus sebagai format utama.** Telegram merender ogg/opus yang dikirim
   via ``sendVoice`` sebagai bubble voice note asli (dengan waveform), bukan
   attachment. Bila konversi opus gagal (ffmpeg aneh), mp3 dikembalikan
   sebagai fallback yang masih bisa diputar — lebih baik terdengar sebagai
   audio biasa daripada tidak terkirim sama sekali.

3. **Batas panjang keras, bukan potong diam-diam.** ``MAX_TTS_CHARS`` menolak
   teks kepanjangan dengan ``VoiceError`` yang jelas. VN 5 menit = file besar,
   generate lama, dan tidak ada yang mendengarkan sampai habis; gateway
   memotong alur lebih awal (balasan panjang tetap dikirim sebagai teks).

4. **Semua kegagalan adalah ``VoiceError`` dengan pesan yang bisa dipakai.**
   Pemanggil (gateway) tidak perlu menebak: edge-tts hilang, ffmpeg hilang,
   teks kosong, teks kepanjangan, atau edge-tts gagal — semuanya dijelaskan
   dalam pesannya sehingga fallback ke teks bisa menyertakan alasannya.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

#: preset: style -> (voice edge-tts, rate, pitch)
#: Dimigrasikan 1:1 dari skill voice-reply; sweet spot anime Indo +30 s/d +40Hz.
PRESETS: dict[str, tuple[str, str, str]] = {
    "emma-anime": ("en-US-EmmaMultilingualNeural", "+10%", "+35Hz"),
    "ava-anime": ("en-US-AvaMultilingualNeural", "+8%", "+30Hz"),
    "gadis": ("id-ID-GadisNeural", "+0%", "+0Hz"),
    "gadis-anime": ("id-ID-GadisNeural", "+15%", "+40Hz"),
    "ana": ("en-US-AnaNeural", "+0%", "+0Hz"),
    "nanami": ("ja-JP-NanamiNeural", "+0%", "+0Hz"),
}
DEFAULT_STYLE = "emma-anime"

#: Batas karakter teks untuk satu voice note. VN raksasa = file besar + lama
#: generate; gateway mengirim balasan yang lebih panjang sebagai teks biasa.
MAX_TTS_CHARS = 600

#: Timeout satu panggilan edge-tts / ffmpeg.
TTS_TIMEOUT_SECONDS = 120


class VoiceError(RuntimeError):
    """Sintesis suara gagal; pesannya siap ditampilkan ke operator."""


# ---------------------------------------------------------------------------
# Offline speech-to-text (faster-whisper) dan text-to-speech (piper/espeak).
#
# Semua backend di sini bersifat OPSIONAL dan dideteksi saat runtime — tidak
# ada yang di-install otomatis, dan tidak ada model yang diunduh otomatis.
# faster-whisper dimuat dengan ``local_files_only=True`` supaya gagal jujur
# (dengan instruksi unduh) bila model belum ada di cache, bukan mengunduh
# diam-diam ratusan MB saat pertama dipanggil. Unduhan eksplisit tersedia
# lewat ``download_stt_model()`` / ``zeline voice download-model``.
# ---------------------------------------------------------------------------

#: Ukuran maksimum file audio untuk transkripsi (100MB). Mencegah memory DoS.
MAX_AUDIO_BYTES = 100 * 1024 * 1024

#: Batas karakter untuk speak() lokal (file output, bukan voice note).
MAX_SPEAK_CHARS = 5000

#: Timeout sintesis TTS lokal per panggilan.
LOCAL_TTS_TIMEOUT_SECONDS = 60

#: Ukuran model faster-whisper yang didukung.
STT_MODELS = ("tiny", "base", "small", "medium", "large-v3", "turbo")


def _which(names: tuple[str, ...]) -> str | None:
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None


def stt_backends() -> list[str]:
    """Backend STT lokal yang tersedia saat ini (nama saja)."""
    found: list[str] = []
    try:
        import faster_whisper  # noqa: F401
        found.append("faster-whisper")
    except ImportError:
        pass
    return found


def local_tts_backends() -> list[str]:
    """Backend TTS lokal yang tersedia saat ini, urut prioritas.

    piper butuh file model suara (.onnx); bila biner ada tapi model tidak
    ditemukan, piper dilewati dan espeak dipakai (tidak butuh model).
    """
    found: list[str] = []
    if _which(("piper",)) and _find_piper_voice() is not None:
        found.append("piper")
    if _which(("espeak-ng",)):
        found.append("espeak-ng")
    elif _which(("espeak",)):
        found.append("espeak")
    return found


def _find_piper_voice() -> Path | None:
    """Cari file model suara piper (.onnx). Urutan: env, ~/.zeline/voices, ~/.local/share/piper."""
    env_voice = os.environ.get("PIPER_VOICE", "").strip()
    candidates: list[Path] = []
    if env_voice:
        candidates.append(Path(env_voice).expanduser())
    home = Path.home()
    for pattern_dir in (home / ".zeline" / "voices", home / ".local" / "share" / "piper"):
        try:
            candidates.extend(sorted(pattern_dir.glob("*.onnx")))
        except OSError:
            pass
    for cand in candidates:
        try:
            if cand.is_file() and not cand.is_symlink():
                return cand
        except OSError:
            continue
    return None


def stt_status() -> dict[str, object]:
    """Status STT lokal untuk CLI/diagnostik."""
    backends = stt_backends()
    if not backends:
        return {
            "available": False,
            "backend": None,
            "detail": (
                "faster-whisper belum terpasang. Pasang dengan: "
                "pip install faster-whisper — lalu unduh model sekali via "
                "`zeline voice download-model`."
            ),
        }
    return {"available": True, "backend": backends[0], "detail": "siap (model harus sudah di-cache)."}


def local_tts_status() -> dict[str, object]:
    """Status TTS lokal untuk CLI/diagnostik."""
    backends = local_tts_backends()
    if not backends:
        return {
            "available": False,
            "backend": None,
            "detail": (
                "Tidak ada TTS lokal. Pilihan: `pip install piper-tts` + model suara "
                "(.onnx di ~/.zeline/voices/, atau set PIPER_VOICE), atau "
                "`apt install espeak-ng` (tanpa model, suara robotik)."
            ),
        }
    return {"available": True, "backend": backends[0], "detail": f"siap via {backends[0]}."}


def voice_status() -> dict[str, dict[str, object]]:
    """Ringkasan status semua kemampuan suara (STT lokal, TTS lokal, TTS edge)."""
    edge_ok = _which(("edge-tts",)) is not None and _which(("ffmpeg",)) is not None
    return {
        "stt": stt_status(),
        "tts_local": local_tts_status(),
        "tts_edge": {
            "available": edge_ok,
            "backend": "edge-tts" if edge_ok else None,
            "detail": "siap (butuh internet)." if edge_ok else "edge-tts/ffmpeg belum terpasang.",
        },
    }


def download_stt_model(model: str = "tiny") -> str:
    """Unduh model faster-whisper secara eksplisit (opt-in, tidak otomatis).

    Dipanggil manual via ``zeline voice download-model [model]``. Mengembalikan
    pesan status untuk ditampilkan ke operator.
    """
    model = (model or "tiny").strip().lower()
    if model not in STT_MODELS:
        raise VoiceError(
            f"model '{model}' tidak dikenal. Pilihan: {', '.join(STT_MODELS)}."
        )
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise VoiceError(
            "faster-whisper belum terpasang. Pasang dulu dengan: "
            "pip install faster-whisper"
        ) from exc
    try:
        WhisperModel(model, device="cpu", compute_type="int8")
    except Exception as exc:
        raise VoiceError(f"gagal mengunduh model '{model}': {exc}") from exc
    return f"model faster-whisper '{model}' siap di cache lokal."


def transcribe(
    audio_path: str | Path,
    *,
    model: str = "tiny",
    language: str | None = None,
) -> str:
    """Transkripsikan file audio menjadi teks via faster-whisper (lokal, offline).

    ``model`` salah satu dari :data:`STT_MODELS` (default "tiny": tercepat,
    akurasi secukupnya). Model harus sudah di-cache — lihat
    :func:`download_stt_model`; tidak ada unduhan otomatis.

    Catatan keamanan: fungsi ini menerima path apa pun yang bisa dibaca
    proses (trusted-caller). Pembatasan ke workspace dilakukan di wrapper
    tool ``voice_transcribe`` — jangan panggil langsung dengan path dari
    input yang tidak tepercaya.

    Raise ``VoiceError`` bila: file tidak ada/bukan file/kegedean, model tak
    dikenal, faster-whisper hilang, atau model belum di-cache.
    """
    model = (model or "tiny").strip().lower()
    if model not in STT_MODELS:
        raise VoiceError(
            f"model '{model}' tidak dikenal. Pilihan: {', '.join(STT_MODELS)}."
        )
    path = Path(audio_path).expanduser()
    if not path.is_file():
        raise VoiceError(f"file audio tidak ditemukan: {path}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise VoiceError(f"tidak bisa membaca file audio: {exc}") from exc
    if size > MAX_AUDIO_BYTES:
        raise VoiceError(
            f"file audio {size // (1024 * 1024)}MB melebihi batas "
            f"{MAX_AUDIO_BYTES // (1024 * 1024)}MB."
        )
    if size == 0:
        raise VoiceError("file audio kosong.")

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise VoiceError(
            "faster-whisper belum terpasang (dibutuhkan untuk speech-to-text "
            "lokal). Pasang dengan: pip install faster-whisper"
        ) from exc

    try:
        # local_files_only=True: gagal jujur bila model belum di-cache,
        # jangan unduh ratusan MB diam-diam.
        whisper = WhisperModel(model, device="cpu", compute_type="int8", local_files_only=True)
    except Exception as exc:
        raise VoiceError(
            f"model faster-whisper '{model}' belum ada di cache lokal "
            f"(unduhan otomatis dimatikan). Unduh sekali secara eksplisit: "
            f"`zeline voice download-model {model}` — butuh internet sekali saja."
        ) from exc

    try:
        segments, _info = whisper.transcribe(str(path), language=language)
        text = " ".join(seg.text.strip() for seg in segments).strip()
    except Exception as exc:
        raise VoiceError(f"transkripsi gagal: {exc}") from exc
    return text


def speak(
    text: str,
    output_path: str | Path,
    *,
    voice: str = "id",
    backend: str | None = None,
) -> Path:
    """Ubah teks menjadi file audio WAV via TTS lokal (offline).

    Backend dicoba berurutan: piper (bila ada model suara) → espeak-ng →
    espeak. ``backend`` memaksa salah satu ("piper"/"espeak-ng"/"espeak").
    ``voice`` adalah kode suara espeak (default "id" = Bahasa Indonesia);
    untuk piper, suara ditentukan oleh file modelnya.

    Catatan keamanan: ``output_path`` ditulis apa adanya (trusted-caller);
    pembatasan ke workspace dilakukan di wrapper tool ``voice_speak``.
    Direktori output dibuat hanya setelah backend dipastikan tersedia.

    Mengembalikan Path file hasil. Raise ``VoiceError`` bila: teks kosong /
    kepanjangan, backend tak dikenal / tak tersedia, atau sintesis gagal.
    """
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        raise VoiceError("tidak ada teks untuk diubah menjadi suara.")
    if len(cleaned) > MAX_SPEAK_CHARS:
        raise VoiceError(
            f"teks {len(cleaned)} karakter melebihi batas TTS lokal "
            f"({MAX_SPEAK_CHARS} karakter). Bagi menjadi beberapa bagian."
        )

    out = Path(output_path).expanduser()
    if out.suffix.lower() != ".wav":
        raise VoiceError("output TTS lokal harus berekstensi .wav")

    available = local_tts_backends()
    if backend is not None:
        backend = backend.strip().lower()
        if backend not in ("piper", "espeak-ng", "espeak"):
            raise VoiceError(
                f"backend '{backend}' tidak dikenal. Pilihan: piper, espeak-ng, espeak."
            )
        if backend not in available:
            detail = str(local_tts_status().get("detail", ""))
            raise VoiceError(f"backend '{backend}' tidak tersedia. {detail}")
        order = [backend]
    else:
        if not available:
            raise VoiceError(str(local_tts_status().get("detail", "TTS lokal tidak tersedia.")))
        order = available

    # Direktori output dibuat HANYA setelah backend dipastikan tersedia —
    # jangan tinggalkan direktori yatim di path gagal.
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise VoiceError(f"tidak bisa membuat direktori output: {exc}") from exc

    last_error: Exception | None = None
    for name in order:
        try:
            if name == "piper":
                _speak_piper(cleaned, out)
            else:
                _speak_espeak(cleaned, out, voice=voice, binary=name)
            if out.is_file() and out.stat().st_size > 0:
                return out
            raise VoiceError(f"backend {name} tidak menghasilkan file audio.")
        except VoiceError as exc:
            last_error = exc
            continue
    raise VoiceError(f"semua backend TTS lokal gagal. Terakhir: {last_error}")


def _speak_piper(text: str, out: Path) -> None:
    """Sintesis via piper (stdin teks → file wav). Raise VoiceError bila gagal."""
    model_path = _find_piper_voice()
    if model_path is None:
        raise VoiceError("piper terpasang tapi tidak ada model suara (.onnx).")
    binary = _which(("piper",))
    if binary is None:
        raise VoiceError("piper tidak ditemukan.")
    try:
        completed = subprocess.run(
            [binary, "--model", str(model_path), "--output_file", str(out)],
            input=text,
            capture_output=True,
            text=True,
            timeout=LOCAL_TTS_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VoiceError(
            f"piper tidak merespons dalam {LOCAL_TTS_TIMEOUT_SECONDS} detik."
        ) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip().splitlines()
        raise VoiceError(
            "piper gagal" + (f": {detail[-1][:160]}" if detail else ".")
        )


def _speak_espeak(text: str, out: Path, *, voice: str, binary: str) -> None:
    """Sintesis via espeak-ng/espeak (stdin teks → file wav). Raise VoiceError bila gagal."""
    # Argv-flag injection guard dulu (validasi input sebelum cek environment):
    # "-v" mengambil argumen berikutnya sebagai data; nilai voice yang
    # diawali "-" akan diparse sebagai flag CLI.
    if (voice or "").startswith("-"):
        raise VoiceError(
            f"parameter voice tidak valid: tidak boleh diawali '-' "
            f"({voice!r}) — ditolak untuk mencegah injeksi flag CLI."
        )
    exe = _which((binary,))
    if exe is None:
        raise VoiceError(f"{binary} tidak ditemukan.")
    # Teks lewat stdin (bukan argv) agar aman dari injeksi shell/argv.
    try:
        completed = subprocess.run(
            [exe, "-v", voice or "id", "-w", str(out), "--stdin"],
            input=text,
            capture_output=True,
            text=True,
            timeout=LOCAL_TTS_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VoiceError(
            f"{binary} tidak merespons dalam {LOCAL_TTS_TIMEOUT_SECONDS} detik."
        ) from exc
    if completed.returncode != 0 or not out.is_file():
        detail = (completed.stderr or "").strip().splitlines()
        raise VoiceError(
            f"{binary} gagal" + (f": {detail[-1][:160]}" if detail else ".")
        )


class _SelfCleaningPath(type(Path())):
    """Path hasil ``synthesize(out_dir=None)``: memiliki TemporaryDirectory-nya.

    Direktori sementara dihapus otomatis saat Path HASIL ini tidak lagi
    direferensikan siapa pun — pola membersihkan diri. Pemanggil yang memakai
    ``out_dir=None`` tidak perlu, dan tidak bisa, membersihkannya secara
    manual. Path turunan (``parent``, ``/``, ``with_suffix``) TIDAK
    memperpanjang umur direktori: pegang Path hasil selama file dipakai.
    """

    __slots__ = ("_tmpdir",)

    def __new__(cls, *args, **kwargs):
        # Path.__new__ di 3.12 hanya alokasi; parsing path terjadi di __init__.
        return super().__new__(cls)

    def __init__(self, *args, tmpdir: tempfile.TemporaryDirectory | None = None) -> None:
        # tmpdir keyword-only: with_segments memanggil type(self)(*segmen)
        # secara posisional untuk path turunan — ia tidak boleh bertabrakan
        # dengan arg path, dan turunan tidak ikut memiliki temp dir.
        super().__init__(*args)
        self._tmpdir = tmpdir

    def __del__(self) -> None:
        tmpdir = getattr(self, "_tmpdir", None)
        if tmpdir is None:
            return
        try:
            tmpdir.cleanup()
        except Exception:
            pass


def styles() -> list[str]:
    """Daftar nama preset suara yang valid."""
    return sorted(PRESETS)


def describe_style(style: str) -> tuple[str, str, str]:
    """Kembalikan (voice, rate, pitch) untuk preset; raise VoiceError bila tak dikenal."""
    try:
        return PRESETS[style]
    except KeyError:
        raise VoiceError(
            f"style suara '{style}' tidak dikenal. Pilihan: {', '.join(styles())}."
        ) from None


def _require_tools() -> None:
    if not shutil.which("edge-tts"):
        raise VoiceError(
            "edge-tts belum terpasang (dibutuhkan untuk text-to-speech). "
            "Pasang dengan: pip install edge-tts"
        )
    if not shutil.which("ffmpeg"):
        raise VoiceError(
            "ffmpeg belum terpasang (dibutuhkan untuk mengubah mp3 menjadi "
            "ogg/opus voice note). Pasang ffmpeg dulu, atau kirim teks biasa."
        )


def synthesize(
    text: str,
    *,
    style: str = DEFAULT_STYLE,
    voice: str = "",
    rate: str = "",
    pitch: str = "",
    out_dir: Path | None = None,
) -> Path:
    """Ubah teks menjadi voice note ogg/opus. Kembalikan path file hasil.

    ``style`` memilih preset (lihat :data:`PRESETS`); ``voice``/``rate``/``pitch``
    meng-override tuning preset bila diisi. ``out_dir`` menentukan direktori
    output (dibuat bila belum ada); bila None, dipakai direktori sementara
    yang membersihkan diri sendiri: direktori terhapus otomatis saat Path
    hasil tidak lagi direferensikan — jangan simpan Path-nya lebih lama
    dari file yang dipakai.

    Raise ``VoiceError`` dengan pesan usable untuk: teks kosong, teks lebih
    dari ``MAX_TTS_CHARS``, style tak dikenal, edge-tts/ffmpeg hilang, atau
    kegagalan sintesis/konversi.
    """
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        raise VoiceError("tidak ada teks untuk diubah menjadi suara.")
    if len(cleaned) > MAX_TTS_CHARS:
        raise VoiceError(
            f"teks {len(cleaned)} karakter melebihi batas voice note "
            f"({MAX_TTS_CHARS} karakter). Kirim sebagai teks biasa."
        )
    preset_voice, preset_rate, preset_pitch = describe_style(style)
    voice = voice.strip() or preset_voice
    rate = rate.strip() or preset_rate
    pitch = pitch.strip() or preset_pitch

    # Argv-flag injection guard: nilai-nilai ini diinterpolasi ke command
    # line edge-tts; awalan "-" akan diparse sebagai flag CLI (mis.
    # "--proxy"), bukan data. Preset bawaan ("+10%", "+35Hz") tidak kena.
    for _label, _value in (("voice", voice), ("rate", rate), ("pitch", pitch)):
        if _value.startswith("-"):
            raise VoiceError(
                f"parameter {_label} tidak valid: tidak boleh diawali '-' "
                f"({_value!r}) — ditolak untuk mencegah injeksi flag CLI."
            )

    _require_tools()

    if out_dir is None:
        tmp: tempfile.TemporaryDirectory | None = tempfile.TemporaryDirectory(prefix="zl-tts-")
        target_dir = Path(tmp.name)
    else:
        tmp = None
        target_dir = Path(out_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
    stem = target_dir / "reply"
    mp3 = stem.with_suffix(".mp3")
    ogg = stem.with_suffix(".ogg")

    def _own(path: Path) -> Path:
        # out_dir=None: serahkan Path yang memiliki temp dir-nya — direktori
        # terhapus otomatis saat Path hasil tidak lagi direferensikan.
        return _SelfCleaningPath(str(path), tmpdir=tmp) if tmp is not None else path

    try:
        try:
            completed = subprocess.run(
                [
                    "edge-tts",
                    "--voice", voice,
                    "--rate", rate,
                    "--pitch", pitch,
                    "--text", cleaned,
                    "--write-media", str(mp3),
                ],
                capture_output=True,
                text=True,
                timeout=TTS_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise VoiceError(
                f"edge-tts tidak merespons dalam {TTS_TIMEOUT_SECONDS} detik "
                "(jaringan ke server suara Microsoft mungkin bermasalah)."
            ) from exc
        if completed.returncode != 0 or not mp3.is_file():
            detail = (completed.stderr or completed.stdout or "").strip().splitlines()
            raise VoiceError(
                "edge-tts gagal membuat audio"
                + (f": {detail[-1][:160]}" if detail else ".")
                + " (butuh koneksi internet ke server suara Microsoft)."
            )

        converted = subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-i", str(mp3), "-c:a", "libopus", "-b:a", "48k", str(ogg)],
            capture_output=True,
            text=True,
            timeout=TTS_TIMEOUT_SECONDS,
            check=False,
        )
        if converted.returncode == 0 and ogg.is_file():
            try:
                mp3.unlink()
            except OSError:
                pass
            return _own(ogg)
        # Fallback jujur: opus gagal, tapi mp3-nya valid dan tetap bisa diputar.
        return _own(mp3)
    except Exception:
        # Jalur gagal: tidak ada Path hasil yang diserahkan ke pemanggil,
        # jadi temp dir milik kita harus dibersihkan di sini (bukan bocor).
        if tmp is not None:
            try:
                tmp.cleanup()
            except Exception:
                pass
        raise
