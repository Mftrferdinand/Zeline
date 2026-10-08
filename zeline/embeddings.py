"""Provider embedding lokal ringan untuk Zeline (ONNX, tanpa torch).

Model default: ``sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2``
via ``fastembed``.

Kenapa model ini (dievaluasi 2026-10-07 di VM kerja, dengan model real):

- **Multilingual + paraphrase-tuned.** Model sebelumnya
  (``BAAI/bge-small-en-v1.5``, EN-centric) punya noise floor ~0.59–0.69
  untuk pasangan Indonesia TAK TERKAIT — lebih tinggi dari parafrasa benar
  ("jadwal servis AC minggu depan" vs "kapan hari lahir mama?" → 0.61,
  mengalahkan target benar "ulang tahun ibu tanggal 17 Agustus" → 0.55).
  Noise semantik itu mengisi top-K dan MENYEMBUNYIKAN fakta yang benar dari
  prompt — fitur justru memperburuk kelas query yang jadi alasannya
  (parafrasa tanpa overlap keyword). Model ini memisahkan dengan bersih:

  ============================  ============  ==============
  pasangan teks                  bge-small     paraphrase-ml
  ============================  ============  ==============
  tak terkait, beda domain        0.59–0.69     0.02–0.28
  parafrasa benar (kanonis)       0.55–0.76     0.54–0.81
  ============================  ============  ==============

  Rentang tak-terkait vs parafrasa TIDAK tumpang tindih pada model ini
  (diukur 8+6 pasangan Indonesia nyata + 8 pasangan "tricky" se-domain),
  sementara pada bge-small tumpang tindih total. Pasangan se-domain tapi
  fakta beda ("ulang tahun ayah..." vs "kapan hari lahir mama?") memang
  tetap tinggi (~0.61) — itu benar secara semantik (topiknya memang mirip),
  bukan noise.
- **Kecil & CPU-only.** Bobot ONNX terkuantisasi (~220 MB terunduh,
  ``Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q``), jalan murni di
  CPU via onnxruntime — tanpa torch, tanpa GPU.
- **Deterministik.** Bobot tetap + runtime ONNX deterministik untuk input
  yang sama → vektor identik setiap run (tidak ada sampling/temperatur).
- **Dimensi sama (384).** Sidecar cache ``embeddings/`` tetap kompatibel
  secara format; walau begitu cache DISEGREGASI per model (nama file
  memuat slug model) supaya vektor model lama tidak tercampur diam-diam
  dengan query model baru.

``intfloat/multilingual-e5-small`` sempat dipertimbangkan tapi TIDAK
didukung fastembed 0.8.1 (``TextEmbedding`` me-raise ``ValueError`` untuk
nama itu) — dan keluarga e5 butuh prefix "query:"/"passage:" untuk hasil
terbaik, yang menambah kerumitan. Override tetap tersedia lewat
``ZELINE_EMBEDDING_MODEL`` bila kelak ada model kecil yang lebih baik.

Kontrak modul (dipakai worker hybrid retrieval — JANGAN ubah signature):

- ``embeddings_available() -> bool`` — True bila dependensi + model bisa
  dipakai. Boleh memicu pemuatan model satu kali (lazy singleton).
- ``get_embedder() -> Embedder | None`` — singleton lazy; None bila
  dependensi tak ada / model gagal dimuat / dinonaktifkan. TIDAK PERNAH raise.
- ``Embedder.embed(texts) -> list[list[float] | None] | None`` — satu slot
  per teks input, posisi SEJAJAR dengan input. Teks kosong / whitespace-only /
  bukan string → slot ``None`` (tidak di-embed: menghemat compute dan
  menghindari vektor sampah dari string kosong). String tunggal ("halo")
  diterima dan dibungkus menjadi satu slot. ``[]`` → ``[]``. ``None`` hanya
  bila embed gagal total. TIDAK PERNAH raise.
- ``cosine_sim(a, b) -> float`` — hasil dijepit ke [-1, 1]; 0.0 bila input
  invalid (None, kosong, panjang beda, non-numerik, NaN/inf). TIDAK PERNAH
  raise.

Lazy & fail-safe:

- Import modul ini TIDAK memuat model dan TIDAK melakukan I/O jaringan.
  ``fastembed`` di-import di dalam ``get_embedder()`` (bukan top-level),
  dan model baru diunduh/dimuat saat ``get_embedder()`` pertama dipanggil.
- Setiap kegagalan (paket tak terinstal, model gagal diunduh, error saat
  embed, env menonaktifkan) ditangkap → ``None``/``0.0``, bukan exception.
  Kegagalan di-cache per kombinasi env; bila env berubah, pemuatan dicoba
  ulang (jadi ``ZELINE_EMBEDDINGS_ENABLED=0`` lalu dihapus kembali mencoba
  memuat tanpa perlu restart proses).
- Pemuatan model berjalan di thread daemon dengan batas waktu
  (``ZELINE_EMBEDDING_LOAD_TIMEOUT``, default 180s): unduhan yang hang di
  jaringan bermasalah → None (fail-safe ke keyword-only), bukan blokir
  selamanya dan bukan exception.

Variabel lingkungan:

- ``ZELINE_EMBEDDINGS_ENABLED`` — "0"/"false"/"no"/"off" (case-insensitive)
  = nonaktif paksa → ``get_embedder()`` selalu None. Default aktif.
- ``ZELINE_EMBEDDING_MODEL`` — override nama model fastembed.
- ``ZELINE_EMBEDDING_CACHE_DIR`` — direktori cache unduhan model. Bila
  tidak diisi, fastembed memakai cache HuggingFace bawaan
  (``~/.cache/huggingface``, atau ``HF_HUB_CACHE`` bila di-set);
  direktori kerja fastembed sendiri default di ``/tmp/fastembed_cache``
  (bisa di-override via ``FASTEMBED_CACHE_PATH``).
- ``ZELINE_EMBEDDING_LOAD_TIMEOUT`` — batas waktu pemuatan model dalam
  detik (default 180). Nilai tidak valid, non-finite (NaN/inf), atau
  non-positif → default.

Thread-safety: singleton dijaga ``threading.Lock`` (double-checked); dua
thread yang memanggil ``get_embedder()`` bersamaan mendapat objek yang sama
dan model hanya dimuat sekali. Lock dipegang SELAMA pemuatan pertama
(sampai selesai atau timeout) — pemanggil lain antre; lihat docstring
``get_embedder``. ``embed()`` aman dipanggil dari banyak thread
(``onnxruntime.InferenceSession.run`` thread-safe).

Catatan troubleshooting: bila environment punya ``NO_PROXY``/``no_proxy``
berisi literal IPv6 dalam kurung (mis. ``[::1]``), httpx — dipakai
huggingface-hub saat mengunduh model — gagal parse dengan ``InvalidURL``
→ modul fail-safe mengembalikan None. Rapikan ``NO_PROXY`` (mis.
``localhost,127.0.0.1``) bila ini terjadi; itu bug parse httpx, bukan bug
modul ini.
"""
from __future__ import annotations

import math
import os
import threading
from typing import Any, Sequence

__all__ = ["Embedder", "cosine_sim", "embeddings_available", "get_embedder"]

#: Model default — lihat docstring modul untuk hasil evaluasinya.
#: (2026-10-07: diganti dari BAAI/bge-small-en-v1.5 — noise floor model lama
#: ~0.6 untuk teks Indonesia tak-terkait mengalahkan parafrasa benar dan
#: menyembunyikan fakta dari prompt; model multilingual ini memisahkan
#: bersih: tak-terkait 0.02–0.28 vs parafrasa 0.54–0.81.)
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

#: Nilai ZELINE_EMBEDDINGS_ENABLED yang berarti "nonaktif paksa".
_DISABLED_VALUES = frozenset({"0", "false", "no", "off"})


def _env_enabled() -> bool:
    return (
        os.environ.get("ZELINE_EMBEDDINGS_ENABLED", "").strip().lower()
        not in _DISABLED_VALUES
    )


def _env_model() -> str:
    return os.environ.get("ZELINE_EMBEDDING_MODEL", "").strip() or DEFAULT_MODEL


#: Batas waktu pemuatan model (detik). Tanpa ini, unduhan yang hang di
#: jaringan bermasalah memblokir pemanggil selamanya — hang lebih buruk
#: dari raise dan melanggar kontrak fail-safe modul ini.
_DEFAULT_LOAD_TIMEOUT_S = 180.0


def _env_load_timeout() -> float:
    raw = os.environ.get("ZELINE_EMBEDDING_LOAD_TIMEOUT", "").strip()
    try:
        value = float(raw) if raw else _DEFAULT_LOAD_TIMEOUT_S
    except ValueError:
        value = _DEFAULT_LOAD_TIMEOUT_S
    # Timeout harus bilangan FINITE positif: NaN/inf/non-positif ditolak ke
    # default. Tanpa cek isfinite, "inf" lolos uji `> 0` dan thread.join(inf)
    # menunggu selamanya — persis hang yang ingin dicegah oleh timeout ini.
    if not math.isfinite(value) or value <= 0:
        return _DEFAULT_LOAD_TIMEOUT_S
    return value


def _env_cache_dir() -> str | None:
    value = os.environ.get("ZELINE_EMBEDDING_CACHE_DIR", "").strip()
    return value or None


def _config() -> tuple[bool, str, str | None, float]:
    """Fingerprint konfigurasi dari env — dasar cache singleton.

    Singleton di-cache per kombinasi (enabled, model, cache_dir, timeout):
    bila env berubah antar panggilan, pemuatan dicoba ulang alih-alih
    mengembalikan hasil basi dari konfigurasi lama.
    """
    return (_env_enabled(), _env_model(), _env_cache_dir(), _env_load_timeout())


class Embedder:
    """Pembungkus model embedding fastembed. Dibuat via ``get_embedder()``."""

    def __init__(self, model_name: str, dim: int, _backend: Any) -> None:
        self.model_name = model_name
        self.dim = dim
        self._backend = _backend

    def embed(self, texts: list[str]) -> list[list[float] | None] | None:
        """Embed tiap teks menjadi vektor; slot sejajar dengan input.

        - Teks kosong / whitespace-only / bukan string → slot ``None``
          (tidak di-embed — menghemat compute, menghindari vektor sampah).
        - String tunggal ("halo") dibungkus menjadi satu slot.
        - ``[]`` → ``[]``. ``None`` hanya bila embed gagal total.
        - TIDAK PERNAH raise: semua error → ``None``.
        """
        try:
            if texts is None:
                return None
            if isinstance(texts, str):
                texts = [texts]
            items = list(texts)
        except Exception:
            # Bukan iterable (mis. embed(123)), atau iterable yang raise
            # saat dikonsumsi — gagal total, bukan parsial, bukan raise.
            return None
        try:
            result: list[list[float] | None] = [None] * len(items)
            pending: list[tuple[int, str]] = []
            for index, item in enumerate(items):
                if isinstance(item, str):
                    cleaned = item.strip()
                    if cleaned:
                        pending.append((index, cleaned))
                # else: slot None — kosong / whitespace-only / bukan string.
            if not pending:
                return result
            vectors = self._backend.embed([text for _, text in pending])
            for (index, _), vector in zip(pending, vectors):
                dense = [float(x) for x in vector]
                # Defensif: backend yang mengembalikan dimensi tak konsisten
                # menandakan model korup — slot itu None, bukan vektor aneh.
                result[index] = dense if len(dense) == self.dim else None
            return result
        except Exception:
            return None


#: Lock + state singleton. Double-checked: baca cepat tanpa lock dulu,
#: lalu verifikasi ulang di dalam lock sebelum memuat model.
_lock = threading.Lock()
_singleton: Embedder | None = None
_singleton_failed = False
_singleton_config: tuple[bool, str, str | None, float] | None = None


def _load_backend(
    model_name: str, cache_dir: str | None, timeout_s: float
) -> tuple[Any, int] | None:
    """Muat TextEmbedding di thread daemon dengan batas waktu.

    Mengembalikan ``(backend, dim)`` atau None bila: timeout (unduhan
    hang di jaringan bermasalah), atau pemuatan gagal. Thread daemon
    tidak memblokir interpreter exit bila unduhan tak kunjung selesai.
    TIDAK PERNAH raise.
    """
    outcome: dict[str, Any] = {}

    def _do_load() -> None:
        try:
            # Import lazy: import modul zeline.embeddings TIDAK BOLEH
            # memuat fastembed/onnxruntime (berat) atau I/O jaringan.
            from fastembed import TextEmbedding

            kwargs: dict[str, Any] = {"model_name": model_name}
            if cache_dir:
                kwargs["cache_dir"] = cache_dir
            backend = TextEmbedding(**kwargs)
            # Probe satu inferensi: memverifikasi model benar-benar jalan
            # (bukan cuma terdaftar) sekaligus mengukur dimensi.
            probe = next(iter(backend.embed(["Zeline"])))
            dim = len(probe)
            if dim <= 0:
                raise ValueError(f"dimensi embedding tidak valid: {dim}")
            outcome["ok"] = (backend, dim)
        except Exception as exc:  # noqa: BLE001 — kontrak fail-safe
            outcome["err"] = exc

    worker = threading.Thread(target=_do_load, daemon=True, name="zeline-embed-load")
    worker.start()
    worker.join(timeout=timeout_s)
    if worker.is_alive():
        # Unduhan hang — tinggalkan thread daemon, laporkan gagal.
        # Singleton meng-cache kegagalan; coba lagi hanya bila env berubah.
        return None
    return outcome.get("ok")


def get_embedder() -> Embedder | None:
    """Kembalikan singleton Embedder (lazy), atau None bila tak tersedia.

    Thread-safe: dua thread yang memanggil bersamaan mendapat objek yang
    sama; model hanya dimuat sekali. TIDAK PERNAH raise — dependensi hilang,
    model gagal diunduh/dimuat, unduhan hang melebihi
    ``ZELINE_EMBEDDING_LOAD_TIMEOUT``, atau ``ZELINE_EMBEDDINGS_ENABLED=0``
    → None. Kegagalan di-cache per konfigurasi env; ubah env untuk mencoba
    ulang.

    CATATAN OPERATOR — lock dipegang selama first load: pemuatan model
    pertama berjalan di dalam lock singleton, dan lock itu TIDAK dilepas
    sampai pemuatan selesai ATAU timeout tercapai. Artinya thread lain yang
    memanggil ``get_embedder()`` (atau ``embeddings_available()``) selagi
    model sedang dimuat akan ANTRE di lock sampai ``_load_backend``
    kembali — hingga ``ZELINE_EMBEDDING_LOAD_TIMEOUT`` detik (default 180)
    bila unduhan hang. Ini disengaja (mencegah unduhan ganda model ~220 MB),
    tapi operator perlu tahu: hang jaringan = SEMUA pemanggil antre, bukan
    cuma satu. Setelah singleton terbentuk (atau gagal dan di-cache),
    panggilan berikutnya memakai jalur cepat tanpa lock dan kembali
    seketika.
    """
    global _singleton, _singleton_failed, _singleton_config
    config = _config()
    # Jalur cepat tanpa lock: konfigurasi sama seperti pemuatan terakhir.
    if _singleton_config == config:
        return None if _singleton_failed else _singleton
    with _lock:
        # Cek ulang di dalam lock — thread lain mungkin sudah memuat
        # selagi kita menunggu giliran.
        if _singleton_config == config:
            return None if _singleton_failed else _singleton
        _singleton_config = config
        enabled, model_name, cache_dir, timeout_s = config
        if not enabled:
            _singleton = None
            _singleton_failed = True
            return None
        try:
            loaded = _load_backend(model_name, cache_dir, timeout_s)
            if loaded is None:
                raise RuntimeError(
                    "pemuatan model embedding gagal atau timeout "
                    f"({timeout_s:g}s) — fallback ke keyword-only"
                )
            backend, dim = loaded
            _singleton = Embedder(model_name=model_name, dim=dim, _backend=backend)
            _singleton_failed = False
            return _singleton
        except Exception:
            _singleton = None
            _singleton_failed = True
            return None


def embeddings_available() -> bool:
    """True bila dependensi + model embedding bisa dipakai.

    Boleh memicu pemuatan model satu kali (lazy singleton yang di-cache);
    panggilan berikutnya murah. TIDAK PERNAH raise.
    """
    try:
        return get_embedder() is not None
    except Exception:
        return False


def cosine_sim(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity, dijepit ke [-1, 1].

    0.0 bila input invalid: None, kosong, panjang berbeda, elemen
    non-numerik, atau hasil NaN/inf (mis. vektor nol). TIDAK PERNAH raise.
    """
    try:
        if a is None or b is None:
            return 0.0
        va = [float(x) for x in a]
        vb = [float(x) for x in b]
        if not va or len(va) != len(vb):
            return 0.0
        dot = sum(x * y for x, y in zip(va, vb))
        denom = math.sqrt(sum(x * x for x in va)) * math.sqrt(
            sum(y * y for y in vb)
        )
        if denom == 0.0:
            return 0.0
        value = dot / denom
        if not math.isfinite(value):
            return 0.0
        return max(-1.0, min(1.0, value))
    except Exception:
        # TypeError/ValueError dari float(x), OverflowError dari int raksasa,
        # atau error lain dari input patologis — selalu 0.0, bukan raise.
        return 0.0
