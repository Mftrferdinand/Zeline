"""Test provider embedding lokal Zeline (zeline/embeddings.py).

Kontrak yang dikunci di sini:

- import modul TIDAK melakukan I/O (jaringan/model load) — diverifikasi di
  proses terisolasi dengan socket diblokir;
- singleton lazy + thread-safe; ``get_embedder()`` TIDAK PERNAH raise;
- dimensi vektor benar & konsisten; ``embed([])`` aman;
- teks identik → cosine ~1.0; pasangan terkait > pasangan tak-terkait;
- teks kosong/whitespace → slot None (posisi sejajar input);
- ``ZELINE_EMBEDDINGS_ENABLED=0`` → None; model rusak → None, bukan raise.

Test yang butuh model asli memakai model default
(``sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2``, ~220 MB,
diunduh sekali ke cache fastembed). ``NO_PROXY``/``no_proxy`` dirapikan di
fixture karena httpx gagal parse literal IPv6 dalam kurung (``[::1]``) —
quirk environment, bukan modul.
"""
from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import zeline.embeddings as emb


@contextlib.contextmanager
def _env(**overrides: str | None):
    """Set/unset env sementara; kembalikan seperti semula (None = hapus)."""
    old = {key: os.environ.get(key) for key in overrides}
    try:
        for key, value in overrides.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _clean_proxy_env():
    """NO_PROXY tanpa literal IPv6 — penetral quirk parse httpx di test."""
    return _env(no_proxy="localhost,127.0.0.1", NO_PROXY="localhost,127.0.0.1")


class ImportPurityTests(unittest.TestCase):
    def test_import_tidak_melakukan_io(self):
        """Import di proses terisolasi: socket diblokir total.

        Membuktikan import modul tidak memuat model / menyentuh jaringan —
        bila ada, socket yang diblokir akan me-raise dan test gagal.
        """
        code = (
            "import socket, sys\n"
            f"sys.path.insert(0, {str(SOURCE_ROOT)!r})\n"
            "def _blocked(*a, **k):\n"
            "    raise RuntimeError('network saat import')\n"
            "socket.socket = _blocked\n"
            "import zeline.embeddings as e\n"
            "assert 'fastembed' not in sys.modules, 'fastembed ter-import saat import'\n"
            "assert 'onnxruntime' not in sys.modules, 'onnxruntime ter-import saat import'\n"
            "print('IMPORT_OK')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"import gagal/IO saat import:\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr[-2000:]}",
        )
        self.assertIn("IMPORT_OK", proc.stdout)


class DisabledTests(unittest.TestCase):
    def test_enabled_nol_mematikan_total(self):
        with _env(ZELINE_EMBEDDINGS_ENABLED="0"):
            self.assertIsNone(emb.get_embedder())
            self.assertFalse(emb.embeddings_available())

    def test_enabled_false_juga_mematikan(self):
        with _env(ZELINE_EMBEDDINGS_ENABLED="false"):
            self.assertIsNone(emb.get_embedder())

    def test_env_kembali_aktif_setelah_dihapus(self):
        # Skip bila model embedding tak tersedia di environment ini
        # (mis. fastembed belum diinstal): mengaktifkan ulang tetap
        # menghasilkan None, bukan embedder. Pola sama seperti
        # EmbedderBehaviorTests.setUpClass.
        with _env(ZELINE_EMBEDDINGS_ENABLED=None), _clean_proxy_env():
            if emb.get_embedder() is None:
                raise unittest.SkipTest(
                    "model embedding tak tersedia di environment ini")
        # Singleton di-cache per konfigurasi env: menonaktifkan lalu
        # mengembalikan env harus mencoba memuat ulang, bukan hasil basi.
        with _env(ZELINE_EMBEDDINGS_ENABLED="0"):
            self.assertIsNone(emb.get_embedder())
        with _env(ZELINE_EMBEDDINGS_ENABLED=None), _clean_proxy_env():
            self.assertIsNotNone(emb.get_embedder())


class LoadTimeoutTests(unittest.TestCase):
    """Temuan audit MINOR: ``ZELINE_EMBEDDING_LOAD_TIMEOUT`` non-finite."""

    def test_nonfinite_ditolak_ke_default(self):
        # NaN/inf (dan non-positif) harus jatuh ke default 180 — "inf"
        # sebelumnya lolos uji `> 0` dan membuat thread.join(inf).
        for raw in ("nan", "NaN", "inf", "-inf", "Infinity", "", "0", "-5", "abc"):
            with _env(ZELINE_EMBEDDING_LOAD_TIMEOUT=raw):
                self.assertEqual(
                    emb._env_load_timeout(),
                    emb._DEFAULT_LOAD_TIMEOUT_S,
                    f"raw={raw!r}",
                )

    def test_nilai_positif_dipakai(self):
        with _env(ZELINE_EMBEDDING_LOAD_TIMEOUT="42.5"):
            self.assertEqual(emb._env_load_timeout(), 42.5)
        with _env(ZELINE_EMBEDDING_LOAD_TIMEOUT=None):
            self.assertEqual(
                emb._env_load_timeout(), emb._DEFAULT_LOAD_TIMEOUT_S
            )


class BrokenModelTests(unittest.TestCase):
    def test_model_tidak_ada_jadi_none_bukan_raise(self):
        with _env(ZELINE_EMBEDDING_MODEL="model-yang-jelas-tidak-ada-xyz-123"), _clean_proxy_env():
            try:
                result = emb.get_embedder()
            except Exception as exc:  # noqa: BLE001 — test TIDAK BOLEH raise
                self.fail(f"get_embedder() me-raise {type(exc).__name__}: {exc}")
            self.assertIsNone(result)
            self.assertFalse(emb.embeddings_available())

    def test_timeout_kecil_tidak_hang(self):
        # Unduhan yang hang (jaringan bermasalah, mis. egress proxy
        # macet) tidak boleh memblokir pemanggil selamanya: timeout kecil
        # → None dengan cepat, bukan hang puluhan menit.
        with _env(
            ZELINE_EMBEDDING_MODEL="model-yang-jelas-tidak-ada-xyz-123",
            ZELINE_EMBEDDING_LOAD_TIMEOUT="0.5",
        ), _clean_proxy_env():
            start = time.monotonic()
            try:
                result = emb.get_embedder()
            except Exception as exc:  # noqa: BLE001 — test TIDAK BOLEH raise
                self.fail(f"get_embedder() me-raise {type(exc).__name__}: {exc}")
            elapsed = time.monotonic() - start
            self.assertIsNone(result)
            self.assertLess(elapsed, 30, "pemuatan model hang melebihi batas wajar")


class EmbedderBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._proxy = _clean_proxy_env()
        cls._proxy.__enter__()
        cls.embedder = emb.get_embedder()
        if cls.embedder is None:
            raise unittest.SkipTest("model embedding tak tersedia di environment ini")

    @classmethod
    def tearDownClass(cls):
        cls._proxy.__exit__(None, None, None)

    def test_singleton_dim_benar(self):
        self.assertEqual(self.embedder.model_name, emb.DEFAULT_MODEL)
        self.assertEqual(self.embedder.dim, 384)

    def test_singleton_objek_sama(self):
        self.assertIs(emb.get_embedder(), self.embedder)

    def test_embed_dim_konsisten(self):
        vectors = self.embedder.embed(["satu", "dua tiga", "empat lima enam"])
        self.assertEqual(len(vectors), 3)
        for vector in vectors:
            self.assertIsNotNone(vector)
            self.assertEqual(len(vector), 384)
            self.assertTrue(all(isinstance(x, float) for x in vector))

    def test_embed_list_kosong(self):
        self.assertEqual(self.embedder.embed([]), [])

    def test_teks_kosong_jadi_slot_none(self):
        vectors = self.embedder.embed(["", "   ", "\n\t", "halo dunia"])
        self.assertEqual(len(vectors), 4)
        self.assertIsNone(vectors[0])
        self.assertIsNone(vectors[1])
        self.assertIsNone(vectors[2])
        self.assertIsNotNone(vectors[3])
        self.assertEqual(len(vectors[3]), 384)

    def test_item_bukan_string_jadi_slot_none(self):
        vectors = self.embedder.embed(["halo", None, 123])
        self.assertEqual(len(vectors), 3)
        self.assertIsNotNone(vectors[0])
        self.assertIsNone(vectors[1])
        self.assertIsNone(vectors[2])

    def test_string_tunggal_dibungkus(self):
        vectors = self.embedder.embed("halo")
        self.assertEqual(len(vectors), 1)
        self.assertEqual(len(vectors[0]), 384)

    def test_embed_none_dan_noniterable_tidak_raise(self):
        self.assertIsNone(self.embedder.embed(None))
        self.assertIsNone(self.embedder.embed(123))

    def test_teks_identik_cosine_satu(self):
        vectors = self.embedder.embed(["saya suka kopi", "saya suka kopi"])
        sim = emb.cosine_sim(vectors[0], vectors[1])
        self.assertGreaterEqual(sim, 0.999)

    def test_terkait_lebih_tinggi_dari_tak_terkait(self):
        vectors = self.embedder.embed(
            [
                "saya suka kopi",
                "saya minum kopi setiap pagi",
                "resep rendang daging sapi pedas",
            ]
        )
        terkait = emb.cosine_sim(vectors[0], vectors[1])
        tak_terkait = emb.cosine_sim(vectors[0], vectors[2])
        # Margin 0.1: terukur 0.78 vs 0.54 pada model default.
        self.assertGreater(terkait, tak_terkait + 0.1)
        self.assertLess(tak_terkait, 0.7)

    def test_thread_safety_singleton(self):
        """N thread memanggil get_embedder() bersamaan: satu objek, tanpa raise."""
        results: list = []
        errors: list = []

        def worker():
            try:
                results.append(emb.get_embedder())
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 16)
        self.assertTrue(all(result is self.embedder for result in results))


class CosineSimTests(unittest.TestCase):
    def test_identik(self):
        self.assertAlmostEqual(emb.cosine_sim([1.0, 0.0], [1.0, 0.0]), 1.0)

    def test_berlawanan(self):
        self.assertAlmostEqual(emb.cosine_sim([1.0, 0.0], [-1.0, 0.0]), -1.0)

    def test_tegak_lurus(self):
        self.assertAlmostEqual(emb.cosine_sim([1.0, 0.0], [0.0, 1.0]), 0.0)

    def test_dijepit_ke_rentang(self):
        # n=3: dot/(na*nb) mentah = 1.0000000000000002 (error floating-point)
        # → harus dijepit tepat ke 1.0, bukan bocor keluar [-1, 1].
        self.assertEqual(emb.cosine_sim([1.0, 1.0, 1.0], [1.0, 1.0, 1.0]), 1.0)
        self.assertEqual(emb.cosine_sim([-1.0, -1.0, -1.0], [1.0, 1.0, 1.0]), -1.0)

    def test_input_invalid_nol(self):
        self.assertEqual(emb.cosine_sim(None, [1.0]), 0.0)
        self.assertEqual(emb.cosine_sim([1.0], None), 0.0)
        self.assertEqual(emb.cosine_sim([], []), 0.0)
        self.assertEqual(emb.cosine_sim([1.0, 2.0], [1.0]), 0.0)
        self.assertEqual(emb.cosine_sim([0.0, 0.0], [0.0, 0.0]), 0.0)
        self.assertEqual(emb.cosine_sim(["a"], [1.0]), 0.0)

    def test_nan_inf_nol(self):
        self.assertEqual(emb.cosine_sim([float("nan")], [1.0]), 0.0)
        self.assertEqual(emb.cosine_sim([float("inf")], [1.0]), 0.0)


if __name__ == "__main__":
    unittest.main()
