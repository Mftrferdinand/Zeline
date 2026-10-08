"""Hybrid retrieval: semantik + keyword di ``MemoryStore``.

Mengunci kontrak implementasi hybrid di ``zeline.memory``:

- **Parafrasa menang:** query parafrasa ("kapan terakhir gue ke dokter gigi?")
  me-ranking fakta yang semantiknya cocok ("appointment dokter gigi 3 Okt
  jam 10") LEBIH TINGGI daripada jalur keyword-only — walau fakta distraktor
  punya overlap token mentah yang lebih besar.
- **Fallback identik:** dengan embedding dimatikan
  (``ZELINE_EMBEDDINGS_ENABLED=0``) atau modul tak tersedia, urutan
  ``retrieve()`` SAMA PERSIS dengan perilaku sebelum hybrid ada (ekspektasi
  ranking di-hardcode dari rumus lama).
- **Invarian untrusted:** fakta ``source="gmail-sync"`` yang ter-retrieve via
  skor semantik TETAP dirender di blok ``<untrusted_external_data>`` oleh
  ``prompt_block(query)`` — skor tidak mengangkatnya ke blok tepercaya.
- **Cache:** teks fakta yang sama hanya di-embed sekali walau ``retrieve()``
  dipanggil berulang (memo memori + sidecar file).
- **Bobot env:** ``ZELINE_HYBRID_SEMANTIC_WEIGHT`` /
  ``ZELINE_HYBRID_KEYWORD_WEIGHT`` mengubah ranking sesuai harapan
  (semantik murni = distraktor keyword hilang dari hasil).
- **Tidak pernah raise:** ``get_embedder()`` raise, ``embed()`` raise /
  return None, atau ``cosine_sim()`` raise → ``retrieve()`` tetap
  mengembalikan hasil keyword, bukan exception.

Embedder di sini PALSU dan deterministik (modul ``zeline.embeddings`` palsu
disuntik ke ``sys.modules``): vektor 2 dimensi konsep — [dokter-gigi,
dokter-umum]. Ini mengisolasi test dari implementasi ``zeline.embeddings``
yang sebenarnya, sekaligus membuat arti "semantik" terdefinisi pasti untuk
assertion.

Pengecualian: ``RealModelParaphraseTests`` memakai MODEL REAL (default) —
kriteria terima MAJOR-1 yang wajib dibuktikan dengan model sebenarnya, bukan
palsu. Test-test itu skip bila model tak bisa dimuat (offline).

Identity palsu ``telegram:999888777`` di seluruh file — tidak ada chat ID asli.
"""
from __future__ import annotations

import importlib
import importlib.abc
import json
import math
import os
import stat
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

FAKE_IDENTITY = "telegram:999888777"

ENV_VARS = (
    "ZELINE_EMBEDDINGS_ENABLED",
    "ZELINE_HYBRID_SEMANTIC_WEIGHT",
    "ZELINE_HYBRID_KEYWORD_WEIGHT",
)


def _fresh(home: Path):
    """Reimport zeline.memory bound ke ZELINE_HOME temporer (pola repo ini)."""
    os.environ["ZELINE_HOME"] = str(home)
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    return importlib.import_module("zeline.memory")


# ---------------------------------------------------------------------------
# Embedder palsu: kontrak API yang sama dengan zeline.embeddings asli.
# ---------------------------------------------------------------------------
def _concept_vector(text: str) -> list[float]:
    """Vektor 2 dimensi konsep: [dokter-gigi, dokter-umum].

    "dokter gigi" / "dental" -> dimensi 0; "dokter umum" -> dimensi 1.
    Query "kapan terakhir gue ke dokter gigi?" -> [1, 0]: cosine 1.0 dengan
    fakta se-topik walau token persisnya berbeda (parafrasa).
    """
    lowered = text.lower()
    gigi = 1.0 if ("dokter gigi" in lowered or "dental" in lowered) else 0.0
    umum = 1.0 if "dokter umum" in lowered else 0.0
    return [gigi, umum]


def _fake_cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (norm_a * norm_b)))


class _FakeEmbedder:
    """Embedder deterministik yang menghitung pemanggilan embed()."""

    # Nama model palsu — dipakai untuk slug segregasi cache (memo key +
    # nama file sidecar), seperti model_name pada Embedder asli.
    model_name = "fake-concept-2d"

    def __init__(self) -> None:
        self.calls = 0
        self.seen_texts: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        self.seen_texts.extend(texts)
        return [_concept_vector(text) for text in texts]


def _install_fake_embeddings() -> tuple[types.ModuleType, _FakeEmbedder]:
    """Suntik modul 'zeline.embeddings' palsu ke sys.modules."""
    module = types.ModuleType("zeline.embeddings")
    embedder = _FakeEmbedder()
    module.embeddings_available = lambda: True  # noqa: E731
    module.get_embedder = lambda: embedder  # noqa: E731
    module.cosine_sim = _fake_cosine_sim
    sys.modules["zeline.embeddings"] = module
    return module, embedder


class _HybridTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.old_home = os.environ.get("ZELINE_HOME")
        self.memory = _fresh(self.home)
        # Pasang embedder palsu SETELAH _fresh (yang me-pop zeline.*).
        self._saved_emb_module = sys.modules.get("zeline.embeddings")
        self._fake_module, self.embedder = _install_fake_embeddings()
        self._saved_env = {}
        for name in ENV_VARS:
            self._saved_env[name] = os.environ.get(name)
            os.environ.pop(name, None)

    def tearDown(self):
        if self._saved_emb_module is None:
            sys.modules.pop("zeline.embeddings", None)
        else:
            sys.modules["zeline.embeddings"] = self._saved_emb_module
        for name, value in self._saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if self.old_home is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self.old_home
        self.temp.cleanup()

    def _store(self, identity: str = FAKE_IDENTITY):
        return self.memory.MemoryStore(identity)


class ParaphraseTests(_HybridTestCase):
    """Hybrid harus mengalahkan keyword-only pada query parafrasa."""

    TARGET = "appointment dokter gigi 3 Okt jam 10"
    DISTRACTOR = "terakhir gue ke dokter umum tanggal 1 Okt, bawa hasil lab"
    NEUTRAL = "beli susu dan roti di minimarket"
    QUERY = "kapan terakhir gue ke dokter gigi?"

    def _seed(self):
        store = self._store()
        store.add(self.TARGET)
        store.add(self.DISTRACTOR)
        store.add(self.NEUTRAL)
        return store

    def test_hybrid_ranking_lebih_baik_dari_keyword_only(self):
        store = self._seed()
        hybrid_texts = [r["text"] for r in store.retrieve(self.QUERY, k=10)]

        os.environ["ZELINE_EMBEDDINGS_ENABLED"] = "0"
        keyword_texts = [r["text"] for r in store.retrieve(self.QUERY, k=10)]

        # Keyword-only: distraktor menang — overlap token mentahnya 3/4
        # ("terakhir", "gue", "dokter") vs target 2/4 ("dokter", "gigi").
        self.assertEqual(keyword_texts[0], self.DISTRACTOR)
        self.assertEqual(keyword_texts[1], self.TARGET)
        # Hybrid: kemiripan semantik (cosine 1.0) mengangkat target ke posisi 0
        # (0.6*1 + 0.4*0.5 = 0.8) di atas distraktor (0.6*0 + 0.4*0.75 = 0.3).
        self.assertEqual(hybrid_texts[0], self.TARGET)
        self.assertEqual(hybrid_texts[1], self.DISTRACTOR)
        # Posisi target strictly lebih baik di hybrid.
        self.assertLess(
            hybrid_texts.index(self.TARGET), keyword_texts.index(self.TARGET)
        )
        # Fakta netral yang tak relevan tidak ikut di kedua jalur.
        self.assertNotIn(self.NEUTRAL, hybrid_texts)
        self.assertNotIn(self.NEUTRAL, keyword_texts)


class RealModelParaphraseTests(unittest.TestCase):
    """Uji dengan MODEL REAL (bukan palsu): hybrid harus mengalahkan keyword-only.

    Kriteria terima MAJOR-1, dijalankan dengan model default yang sebenarnya:

    a. query parafrasa kanonis ("kapan terakhir gue ke dokter gigi?" →
       "appointment dokter gigi 3 Okt jam 10") diranking hybrid LEBIH BAIK
       dari keyword-only;
    b. skenario probe-2d (10 noise tanpa overlap keyword + 1 target):
       target HARUS ada di ``prompt_block`` — via retrieval atau fallback
       injeksi penuh; yang dilarang hanya hilang diam-diam;
    c. exact match ("nomor telepon Andi") tetap rank 0.

    Skip bila model real tak bisa dimuat (fastembed tak terinstal / offline /
    unduhan gagal) — ``get_embedder()`` fail-safe mengembalikan None.
    """

    TARGET = "appointment dokter gigi 3 Okt jam 10"
    DISTRACTOR = "terakhir gue ke dokter umum tanggal 1 Okt, bawa hasil lab"
    NEUTRAL = "beli susu dan roti di minimarket"
    QUERY = "kapan terakhir gue ke dokter gigi?"

    REAL_ENV_VARS = (
        "ZELINE_HYBRID_SEMANTIC_WEIGHT",
        "ZELINE_HYBRID_KEYWORD_WEIGHT",
        "ZELINE_EMBEDDING_MODEL",
    )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.old_home = os.environ.get("ZELINE_HOME")
        # Bersihkan env berbau embedding KECUALI ZELINE_EMBEDDINGS_ENABLED:
        # bila operator mematikan embedding secara eksplisit, test-test ini
        # SKIP (model memang tak bisa dipakai) — bukan memaksa nyala.
        # ZELINE_EMBEDDING_MODEL juga dibersihkan: yang diuji model DEFAULT.
        self._saved_env = {}
        for name in self.REAL_ENV_VARS:
            self._saved_env[name] = os.environ.get(name)
            os.environ.pop(name, None)
        mod = sys.modules.get("zeline.embeddings")
        self._popped_fake = (
            sys.modules.pop("zeline.embeddings", None)
            if mod is not None and not hasattr(mod, "DEFAULT_MODEL")
            else None
        )
        self.memory = _fresh(self.home)
        # Rapikan NO_PROXY (quirk parse httpx, bukan bug modul) supaya hasil
        # skip/gagal mencerminkan ketersediaan model, bukan environment.
        self._saved_proxy = {
            key: os.environ.get(key) for key in ("no_proxy", "NO_PROXY")
        }
        os.environ["no_proxy"] = "localhost,127.0.0.1"
        os.environ["NO_PROXY"] = "localhost,127.0.0.1"
        try:
            from zeline import embeddings as emb

            self._embeddings = emb
            available = emb.embeddings_available()
        finally:
            for key, value in self._saved_proxy.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        if not available:
            raise unittest.SkipTest(
                "model embedding real tak tersedia di environment ini"
            )

    def tearDown(self):
        if self._popped_fake is not None:
            sys.modules["zeline.embeddings"] = self._popped_fake
        for name, value in self._saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if self.old_home is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self.old_home
        self.temp.cleanup()

    def _store(self, identity: str = FAKE_IDENTITY):
        return self.memory.MemoryStore(identity)

    def _keyword_texts(self, store, query, k=10):
        """Ranking jalur keyword-only (embedding dipaksa mati sementara)."""
        os.environ["ZELINE_EMBEDDINGS_ENABLED"] = "0"
        try:
            return [r["text"] for r in store.retrieve(query, k=k)]
        finally:
            os.environ.pop("ZELINE_EMBEDDINGS_ENABLED", None)

    def test_hybrid_ranking_lebih_baik_dari_keyword_only_model_real(self):
        # Kriteria (a).
        store = self._store()
        store.add(self.TARGET)
        store.add(self.DISTRACTOR)
        store.add(self.NEUTRAL)
        hybrid_texts = [r["text"] for r in store.retrieve(self.QUERY, k=10)]
        keyword_texts = self._keyword_texts(store, self.QUERY, k=10)
        self.assertIn(self.TARGET, hybrid_texts)
        self.assertIn(self.TARGET, keyword_texts)
        self.assertLess(
            hybrid_texts.index(self.TARGET),
            keyword_texts.index(self.TARGET),
            "hybrid dengan model real harus me-ranking target parafrasa "
            "LEBIH BAIK dari keyword-only",
        )

    def test_target_tidak_hilang_diam_diam_dari_prompt_block(self):
        # Kriteria (b): skenario probe-2d.
        store = self.memory.MemoryStore("telegram:999888777-real-hide")
        target = "ulang tahun ibu tanggal 17 Agustus"
        noise = [
            "jadwal servis AC pekan depan",
            "beli susu dan roti di minimarket",
            "bayar listrik kosan tiap tanggal lima",
            "jadwal meeting dengan Budi Senin pagi",
            "catatan tentang telepon genggam Andini yang hilang",
            "kopi pahit favorit dari Toraja",
            "libur tujuh belas Agustus ada upacara kantor",
            "tagihan kartu kredit jatuh tempo tanggal dua lima",
            "daftar belanja: telur, minyak, beras",
            "Andi suka makan bakso pedas tiap Jumat",
        ]
        for fact in noise:
            store.add(fact)
        store.add(target)
        block = store.prompt_block("kapan hari lahir mama?")
        self.assertIn(
            target,
            block,
            "target tidak boleh hilang diam-diam dari prompt_block "
            "(boleh via retrieval, boleh via fallback injeksi penuh)",
        )

    def test_exact_match_tetap_rank_nol_model_real(self):
        # Kriteria (c).
        store = self.memory.MemoryStore("telegram:999888777-real-exact")
        store.add("nomor telepon Andi 0812-3456-7890")
        store.add("catatan tentang telepon genggam Andini yang hilang")
        store.add("Andi menelepon kemarin sore")
        texts = [r["text"] for r in store.retrieve("nomor telepon Andi", k=10)]
        self.assertTrue(texts, "exact match harus ter-retrieve")
        self.assertTrue(
            texts[0].startswith("nomor telepon Andi"),
            "exact match harus tetap rank 0 dengan model real",
        )


class FallbackIdenticalTests(_HybridTestCase):
    """Embedding mati -> retrieve() SAMA PERSIS seperti sebelum hybrid ada."""

    FACTS = [
        "jadwal kontrol dokter gigi",
        "dokter gigi langganan di klinik senyum",
        "beli pasta gigi baru",
        "jadwal service mobil",
    ]
    QUERY = "kapan kontrol dokter gigi berikutnya?"
    # Dihitung manual dari score_text lama:
    # keyword_match x recency(~1.0) x confidence(1.0), ambang 0.05.
    # query sig = {kontrol, dokter, gigi, berikutnya}:
    #   f1 sig {jadwal, kontrol, dokter, gigi}              -> 3/4 = 0.75
    #   f2 sig {dokter, gigi, langganan, klinik, senyum}    -> 2/4 = 0.50
    #   f3 sig {beli, pasta, gigi, baru}                    -> 1/4 = 0.25
    #   f4 tidak berbagi token -> 0.0 -> di bawah ambang, tidak ikut.
    EXPECTED = [
        "jadwal kontrol dokter gigi",
        "dokter gigi langganan di klinik senyum",
        "beli pasta gigi baru",
    ]

    def _seed(self):
        store = self._store()
        for fact in self.FACTS:
            store.add(fact)
        return store

    def test_env_dimatikan_hasil_sama_dengan_ekspektasi_lama(self):
        store = self._seed()
        os.environ["ZELINE_EMBEDDINGS_ENABLED"] = "0"
        texts = [r["text"] for r in store.retrieve(self.QUERY, k=10)]
        self.assertEqual(texts, self.EXPECTED)

    def test_env_nilai_mati_lain_juga_fallback(self):
        store = self._seed()
        for off in ("false", "OFF", "no"):
            os.environ["ZELINE_EMBEDDINGS_ENABLED"] = off
            texts = [r["text"] for r in store.retrieve(self.QUERY, k=10)]
            self.assertEqual(texts, self.EXPECTED, f"env={off!r}")

    def test_modul_tak_tersedia_juga_fallback(self):
        """Simulasi instalasi tanpa zeline.embeddings: import-nya GAGAL.

        Meta-path finder yang me-raise ``ImportError`` khusus untuk nama
        ``zeline.embeddings`` — jadi cabang ``except`` di
        ``_embeddings_module()`` benar-benar TEREKSEKUSI (bukan self-skip),
        walau modul nyatanya ada di tree ini.
        """

        class _BlockEmbeddings(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path=None, target=None):
                if name == "zeline.embeddings":
                    raise ImportError("diblokir untuk test")
                return None

        saved = sys.modules.pop("zeline.embeddings", None)
        # 'from zeline import embeddings' juga bisa resolve via atribut paket
        # induk bila modulnya pernah diimpor — bersihkan juga.
        parent = sys.modules.get("zeline")
        saved_attr = getattr(parent, "embeddings", None) if parent else None
        if parent is not None and hasattr(parent, "embeddings"):
            delattr(parent, "embeddings")
        blocker = _BlockEmbeddings()
        sys.meta_path.insert(0, blocker)
        try:
            os.environ.pop("ZELINE_EMBEDDINGS_ENABLED", None)
            store = self._seed()
            texts = [r["text"] for r in store.retrieve(self.QUERY, k=10)]
            self.assertEqual(texts, self.EXPECTED)
        finally:
            sys.meta_path.remove(blocker)
            if parent is not None and saved_attr is not None:
                parent.embeddings = saved_attr
            if saved is not None:
                sys.modules["zeline.embeddings"] = saved


class UntrustedInvariantTests(_HybridTestCase):
    """Skor semantik tidak boleh mengangkat fakta sync ke blok tepercaya."""

    QUERY = "kapan terakhir gue ke dokter gigi?"
    USER_FACT = "appointment dokter gigi 3 Okt jam 10"
    SYNC_FACT = "promo dental checkup diskon dari klinik"

    def test_fakta_sync_tetap_di_blok_untrusted_walau_skor_semantik_tinggi(self):
        store = self._store()
        store.add(self.USER_FACT, source="user")
        store.add(self.SYNC_FACT, source="gmail-sync")

        # Pastikan fakta sync benar-benar ter-retrieve via jalur hybrid:
        # semantik 1.0 ("dental") -> skor 0.6*1 + 0.4*0 = 0.6, di atas ambang.
        hits = [r["text"] for r in store.retrieve(self.QUERY, k=10)]
        self.assertIn(self.SYNC_FACT, hits)
        self.assertIn(self.USER_FACT, hits)

        block = store.prompt_block(query=self.QUERY)
        self.assertIn("<untrusted_external_data>", block)
        self.assertIn("<user_memory>", block)

        # Fakta sync ada di DALAM blok untrusted...
        untrusted_start = block.index("<untrusted_external_data>")
        untrusted_end = block.index("</untrusted_external_data>")
        self.assertLess(untrusted_start, block.index(self.SYNC_FACT))
        self.assertLess(block.index(self.SYNC_FACT), untrusted_end)

        # ...dan TIDAK di dalam blok user_memory (yang berisi fakta user).
        user_start = block.index("<user_memory>")
        user_end = block.index("</user_memory>")
        user_section = block[user_start:user_end]
        self.assertIn(self.USER_FACT, user_section)
        self.assertNotIn(self.SYNC_FACT, user_section)


class CacheTests(_HybridTestCase):
    """Fakta yang sama hanya di-embed sekali; cache di memo + sidecar."""

    QUERY = "kapan terakhir gue ke dokter gigi?"
    FACTS = [
        "appointment dokter gigi 3 Okt jam 10",
        "terakhir gue ke dokter umum tanggal 1 Okt",
    ]

    def _seed(self):
        store = self._store()
        for fact in self.FACTS:
            store.add(fact)
        return store

    def test_embed_fakta_sekali_untuk_retrieve_berurutan(self):
        store = self._seed()
        store.retrieve(self.QUERY, k=10)
        first_fact_embeds = [t for t in self.embedder.seen_texts if t in self.FACTS]
        self.assertEqual(sorted(first_fact_embeds), sorted(self.FACTS))

        store.retrieve(self.QUERY, k=10)
        all_fact_embeds = [t for t in self.embedder.seen_texts if t in self.FACTS]
        # Tidak ada embed ulang fakta: tiap teks fakta muncul tepat sekali.
        self.assertEqual(sorted(all_fact_embeds), sorted(self.FACTS))
        # Query wajar di-embed ulang tiap retrieve (query selalu baru)...
        self.assertEqual(self.embedder.seen_texts.count(self.QUERY), 2)

        # ...dan sidecar sudah tertulis sejak retrieve pertama.
        sidecar = self.memory._embeddings_sidecar_path(
            FAKE_IDENTITY, self.memory._model_slug(self.embedder.model_name)
        )
        self.assertTrue(sidecar.exists(), "sidecar embedding harus tertulis")
        self.assertEqual(stat.S_IMODE(sidecar.stat().st_mode), 0o600)
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(len(payload), len(self.FACTS))
        for vector in payload.values():
            self.assertEqual(len(vector), 2)

    def test_instance_baru_tidak_embed_ulang_fakta(self):
        self._seed().retrieve(self.QUERY, k=10)
        before = len(self.embedder.seen_texts)
        # Instance baru, identity sama: memo modul-level + sidecar mencegah
        # embed ulang fakta — yang di-embed hanya query baru.
        self._store().retrieve(self.QUERY, k=10)
        self.assertEqual(self.embedder.seen_texts[before:], [self.QUERY])

    def test_fakta_lebih_dari_1000_char_tidak_diembed(self):
        store = self._store()
        long_fact = "x" * 1001
        store.add("appointment dokter gigi 3 Okt jam 10")
        # add() menolak >1000 char, jadi tulis langsung via jalur internal
        # untuk mensimulasikan file yang ditulis manual.
        self.assertTrue(store.add(long_fact).startswith("ERROR"))
        records = store.records()
        self.assertTrue(all(len(r["text"]) <= 1000 for r in records))
        store.retrieve(self.QUERY, k=10)
        for text in self.embedder.seen_texts:
            self.assertLessEqual(len(text), 1000)

    def test_embed_berjalan_di_luar_lock_identitas(self):
        """Panggilan embed() batch TIDAK BOLEH memegang self._lock.

        embed() bisa makan waktu detik; lock identitas hanya untuk tulis
        sidecar. threading.Lock bukan reentrant — acquire(False) dari thread
        yang sama gagal bila lock sedang dipegang.
        """
        store = self._store()
        store.add("appointment dokter gigi 3 Okt jam 10")
        lock_was_free: list[bool] = []

        real_embed = self.embedder.embed

        def spying_embed(texts):
            acquired = store._lock.acquire(blocking=False)
            lock_was_free.append(acquired)
            if acquired:
                store._lock.release()
            return real_embed(texts)

        self.embedder.embed = spying_embed
        try:
            store.retrieve(self.QUERY, k=10)
        finally:
            self.embedder.embed = real_embed
        self.assertTrue(lock_was_free, "embed() tidak pernah dipanggil")
        self.assertTrue(
            all(lock_was_free),
            "embed() berjalan sambil lock identitas dipegang thread ini",
        )

    def test_ganti_model_tidak_memakai_cache_lama(self):
        """Segregasi cache per model: vektor model lama tidak dipakai diam-diam.

        Dimensi sama (384) tidak berarti arti sama — cosine lintas model
        adalah sampah yang lolos cek dimensi. Setelah ganti model, sidecar +
        memo yang dipakai harus yang baru (fakta di-embed ulang).
        """
        store = self._seed()
        store.retrieve(self.QUERY, k=10)
        old_sidecar = self.memory._embeddings_sidecar_path(
            FAKE_IDENTITY, self.memory._model_slug(self.embedder.model_name)
        )
        self.assertTrue(old_sidecar.exists())
        embeds_before = list(self.embedder.seen_texts)

        self.embedder.model_name = "model-lain-xyz"
        try:
            store.retrieve(self.QUERY, k=10)
        finally:
            self.embedder.model_name = "fake-concept-2d"

        new_sidecar = self.memory._embeddings_sidecar_path(
            FAKE_IDENTITY, self.memory._model_slug("model-lain-xyz")
        )
        self.assertNotEqual(old_sidecar, new_sidecar)
        self.assertTrue(new_sidecar.exists(), "sidecar model baru harus tertulis")
        # Fakta di-embed ulang untuk model baru (memo lama tidak dipakai).
        for fact in self.FACTS:
            self.assertEqual(
                self.embedder.seen_texts.count(fact),
                embeds_before.count(fact) + 1,
                f"fakta {fact!r} tidak di-embed ulang untuk model baru",
            )


class WeightEnvTests(_HybridTestCase):
    """Bobot env mengubah ranking sesuai harapan."""

    QUERY = "kapan terakhir gue ke dokter gigi?"
    TARGET = "jadwal dental checkup tanggal 3 oktober jam sepuluh pagi"
    DISTRACTOR = "terakhir gue ke dokter umum"

    def _seed(self):
        store = self._store()
        store.add(self.TARGET)
        store.add(self.DISTRACTOR)
        return store

    def test_bobot_default_keduanya_muncul_target_di_atas(self):
        # Target: 0.6*1.0 + 0.4*0 = 0.6. Distraktor: 0.6*0 + 0.4*0.75 = 0.3.
        store = self._seed()
        texts = [r["text"] for r in store.retrieve(self.QUERY, k=10)]
        self.assertEqual(texts, [self.TARGET, self.DISTRACTOR])

    def test_semantik_murni_menghilangkan_distraktor_keyword(self):
        store = self._seed()
        os.environ["ZELINE_HYBRID_SEMANTIC_WEIGHT"] = "1.0"
        os.environ["ZELINE_HYBRID_KEYWORD_WEIGHT"] = "0.0"
        texts = [r["text"] for r in store.retrieve(self.QUERY, k=10)]
        # Target: 1.0*1.0 = 1.0. Distraktor: 1.0*0 + 0.0*0.75 = 0 -> di
        # bawah ambang 0.05, tidak ikut.
        self.assertEqual(texts, [self.TARGET])

    def test_keyword_murni_mengembalikan_urutan_keyword(self):
        store = self._seed()
        os.environ["ZELINE_HYBRID_SEMANTIC_WEIGHT"] = "0.0"
        os.environ["ZELINE_HYBRID_KEYWORD_WEIGHT"] = "1.0"
        texts = [r["text"] for r in store.retrieve(self.QUERY, k=10)]
        # Target kw=0 -> 0 (terfilter); distraktor kw=0.75 -> lolos.
        self.assertEqual(texts, [self.DISTRACTOR])

    def test_bobot_rusak_kembali_ke_default(self):
        store = self._seed()
        os.environ["ZELINE_HYBRID_SEMANTIC_WEIGHT"] = "bukan-angka"
        os.environ["ZELINE_HYBRID_KEYWORD_WEIGHT"] = "nan"
        texts = [r["text"] for r in store.retrieve(self.QUERY, k=10)]
        self.assertEqual(texts, [self.TARGET, self.DISTRACTOR])

    def test_bobot_negatif_dijepit_ke_nol(self):
        # Bobot negatif akan mengurangkan komponen skor (fakta yang mirip
        # malah dihukum) — dijepit ke 0.0, bukan dipakai diam-diam.
        os.environ["ZELINE_HYBRID_SEMANTIC_WEIGHT"] = "-2.0"
        os.environ["ZELINE_HYBRID_KEYWORD_WEIGHT"] = "-0.5"
        sem_w, kw_w = self.memory._hybrid_weights()
        self.assertEqual((sem_w, kw_w), (0.0, 0.0))
        # -0.0 juga negatif-nol yang sah -> tetap 0.0.
        os.environ["ZELINE_HYBRID_SEMANTIC_WEIGHT"] = "-0.0"
        sem_w, _ = self.memory._hybrid_weights()
        self.assertEqual(sem_w, 0.0)

    def test_kw_nol_record_tanpa_vektor_tidak_lolos(self):
        """kw_w=0 (mode semantik murni) + record tanpa vektor -> skor 0.0.

        Record >1000 char (ditulis manual, tidak di-embed) yang punya overlap
        keyword dengan query: dengan kw_w=0 komponen keyword-nya harus ikut
        mati — bila tidak, record tanpa vektor lolos lewat jalur keyword dan
        merusak janji "semantik murni".
        """
        store = self._store()
        long_fact = "catatan panjang tentang dokter gigi " + "x" * 1001
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text(
            json.dumps(
                [
                    {
                        "text": long_fact,
                        "kind": "fact",
                        "source": "user",
                        "confidence": 1.0,
                        "created_at": time.time(),
                        "expires_at": None,
                    }
                ]
            ),
            encoding="utf-8",
        )
        os.environ["ZELINE_HYBRID_SEMANTIC_WEIGHT"] = "1.0"
        os.environ["ZELINE_HYBRID_KEYWORD_WEIGHT"] = "0.0"
        texts = [r["text"] for r in store.retrieve("dokter gigi", k=10)]
        self.assertNotIn(long_fact, texts)


class EmbeddingFailureTests(_HybridTestCase):
    """retrieve() tidak pernah raise saat embedding error."""

    FACTS = FallbackIdenticalTests.FACTS
    QUERY = FallbackIdenticalTests.QUERY
    EXPECTED = FallbackIdenticalTests.EXPECTED

    def _broken_module(self, mode: str) -> types.ModuleType:
        module = types.ModuleType("zeline.embeddings")
        module.embeddings_available = lambda: True  # noqa: E731
        if mode == "get_embedder_raise":

            def _raise():
                raise RuntimeError("backend embedding mati")

            module.get_embedder = _raise
        elif mode == "embed_raise":

            class _Raiser:
                def embed(self, texts):
                    raise RuntimeError("backend embedding mati")

            module.get_embedder = lambda: _Raiser()  # noqa: E731
        elif mode == "embed_none":

            class _Noner:
                def embed(self, texts):
                    return None

            module.get_embedder = lambda: _Noner()  # noqa: E731
        elif mode == "cosine_raise":

            class _OkEmbedder(_FakeEmbedder):
                pass

            module.get_embedder = lambda: _OkEmbedder()  # noqa: E731

            def _boom(a, b):
                raise RuntimeError("cosine rusak")

            module.cosine_sim = _boom
        else:
            raise AssertionError(f"mode tak dikenal: {mode}")
        if mode != "cosine_raise":
            module.cosine_sim = _fake_cosine_sim
        return module

    def _assert_keyword_fallback(self, mode: str):
        store = self._store()
        for fact in self.FACTS:
            store.add(fact)
        sys.modules["zeline.embeddings"] = self._broken_module(mode)
        try:
            texts = [r["text"] for r in store.retrieve(self.QUERY, k=10)]
        finally:
            sys.modules["zeline.embeddings"] = self._fake_module
        # Hasil = jalur keyword lama, bukan exception dan bukan [].
        self.assertEqual(texts, self.EXPECTED)

    def test_get_embedder_raise_fallback_keyword(self):
        self._assert_keyword_fallback("get_embedder_raise")

    def test_embed_raise_fallback_keyword(self):
        self._assert_keyword_fallback("embed_raise")

    def test_embed_return_none_fallback_keyword(self):
        self._assert_keyword_fallback("embed_none")

    def test_cosine_sim_raise_fallback_keyword(self):
        # cosine_sim gagal per record -> record itu diskoring keyword-only.
        self._assert_keyword_fallback("cosine_raise")

    def test_retrieve_tidak_pernah_raise(self):
        store = self._store()
        for fact in self.FACTS:
            store.add(fact)
        for mode in ("get_embedder_raise", "embed_raise", "embed_none", "cosine_raise"):
            sys.modules["zeline.embeddings"] = self._broken_module(mode)
            try:
                result = store.retrieve(self.QUERY, k=10)  # tidak boleh raise
            finally:
                sys.modules["zeline.embeddings"] = self._fake_module
            self.assertIsInstance(result, list)


class LessonsUntouchedTests(_HybridTestCase):
    """LessonsStore.retrieve tetap keyword-only dan tidak rusak."""

    def test_lessons_retrieve_tetap_keyword(self):
        from zeline import lessons as lessons_module

        lessons_store = lessons_module.LessonsStore()
        lessons_store.record_failure(
            FAKE_IDENTITY,
            "kirim_email",
            {"path": "/tmp/laporan.txt"},
            "gagal login karena token kedaluwarsa",
        )
        lessons_store.record_fix(
            FAKE_IDENTITY, "kirim_email", "laporan", "refresh token dulu sebelum kirim"
        )
        query = "token kedaluwarsa login"
        hits = lessons_store.retrieve(FAKE_IDENTITY, query, k=5)
        self.assertTrue(hits, "pelajaran resolved harus ter-retrieve")
        # Dengan embedding AKTIF, LessonsStore tidak memakai jalur hybrid:
        # hasilnya harus sama dengan saat embedding dimatikan.
        os.environ["ZELINE_EMBEDDINGS_ENABLED"] = "0"
        hits_off = lessons_store.retrieve(FAKE_IDENTITY, query, k=5)
        self.assertEqual(
            [h["error"] for h in hits], [h["error"] for h in hits_off]
        )


if __name__ == "__main__":
    unittest.main()
