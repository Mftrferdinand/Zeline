"""Digest ``recall_history`` + tool ``consolidate_memory``.

Recall harus dikelompokkan per thread ``### <title> — <YYYY-MM-DD>`` dengan
budget karakter (digest berkelompok dari FTS5 tanpa LLM call), TANPA
mengubah logika pemilihan (continuation vs keyword search).
``consolidate_memory`` adalah nudge deterministik ke ``MemoryStore``.

Semua test offline: archive di-seed langsung dengan timestamp terkontrol dan
``ZELINE_HOME`` diarahkan ke direktori sementara.
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import time
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

DAY = 86_400.0


def _fresh_zeline(home: Path):
    os.environ["ZELINE_HOME"] = str(home)
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    config = importlib.import_module("zeline.config")
    assert config.DATA_DIR == home, f"isolasi gagal: {config.DATA_DIR}"
    return config


class _DigestFixture(unittest.TestCase):
    identity = "telegram:digest-probe"

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._old_home = os.environ.get("ZELINE_HOME")
        self.addCleanup(self._restore_home)
        _fresh_zeline(Path(self._tmp.name))
        self.session_store = importlib.import_module("zeline.session_store")
        self.store = self.session_store.SessionPersistence()
        self.tools_mod = importlib.import_module("zeline.tools")
        self.now = time.time()

    def _restore_home(self) -> None:
        if self._old_home is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._old_home
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)

    def seed(self, role: str, content: str, title: str, ts: float) -> None:
        with closing(sqlite3.connect(str(self.store.path))) as conn, conn:
            conn.execute(
                "INSERT INTO archive (key, role, content, title, ts) VALUES (?,?,?,?,?)",
                (self.session_store._key(self.identity), role, content, title, ts),
            )

    def _executor(self):
        return self.tools_mod.ToolExecutor(
            identity=self.identity, profile="safe", workspace=self._tmp.name
        )


class RecallDigestTests(_DigestFixture):
    def test_keyword_search_groups_turns_by_title_and_date(self):
        self.seed("user", "invoice bulan maret tolong diformat", "Invoice generator", self.now - DAY)
        self.seed("assistant", "invoice maret sudah diformat rapi", "Invoice generator", self.now - DAY + 60)
        self.seed("user", "gateway telegram mati pas kirim foto", "Gateway telegram foto", self.now - 600)
        out = self._executor()._recall_history("invoice telegram")
        day = time.strftime("%Y-%m-%d", time.localtime(self.now - DAY))
        today = time.strftime("%Y-%m-%d", time.localtime(self.now - 600))
        self.assertIn(f"### Invoice generator — {day}", out)
        self.assertIn(f"### Gateway telegram foto — {today}", out)
        # Isi turn tetap terbaca di bawah header grupnya.
        self.assertIn("invoice bulan maret", out)
        self.assertIn("gateway telegram mati", out)

    def test_thread_budget_truncates_a_huge_thread(self):
        # 5 turn x ~400 char > budget 1500/thread. last_thread limit=12 muat.
        for i in range(5):
            self.seed("user", f"kerjaan {i} " + "x" * 390, "Thread raksasa", self.now - 300 + i * 10)
        out = self._executor()._recall_history("lanjut")
        self.assertIn("### Thread raksasa — ", out)
        self.assertIn("…(truncated)", out)
        # Bagian thread ini tidak boleh jauh melewati budget.
        section = out.split("### Thread raksasa — ", 1)[1]
        self.assertLessEqual(len(section), 1500 + 500)

    def test_total_budget_caps_output_across_threads(self):
        # search_archive dibatasi 8 baris, jadi total budget diuji via mock:
        # 40 turn x 400 char tersebar di 8 thread — tidak semuanya boleh lolos.
        rows = []
        for t in range(8):
            for i in range(5):
                rows.append({
                    "role": "user",
                    "content": f"topik{t} turn{i} " + "y" * 390,
                    "when": "2026-09-30 10:00",
                    "title": f"Topik {t}",
                })
        with mock.patch.object(
            self.session_store.SessionPersistence, "search_archive", return_value=rows
        ):
            out = self._executor()._recall_history("topik")
        self.assertIn("…(truncated)", out)
        self.assertLessEqual(len(out), 6000 + 800)
        # Thread awal lolos, thread akhir kepotong total budget.
        self.assertIn("### Topik 0 — 2026-09-30", out)
        self.assertNotIn("### Topik 7 — 2026-09-30", out)

    def test_untitled_thread_gets_a_placeholder_header(self):
        rows = [{
            "role": "user", "content": "halo tanpa judul",
            "when": "2026-09-30 10:00", "title": "",
        }]
        with mock.patch.object(
            self.session_store.SessionPersistence, "search_archive", return_value=rows
        ):
            out = self._executor()._recall_history("halo")
        self.assertIn("### (untitled) — 2026-09-30", out)

    def test_continuation_still_selects_the_newest_thread(self):
        # Logika pemilihan TIDAK boleh berubah: "lanjut" = thread terbaru.
        self.seed("user", "kerjaan kemarin soal invoice", "Invoice generator", self.now - DAY)
        self.seed("user", "kerjaan sekarang soal gateway", "Gateway telegram foto", self.now - 300)
        out = self._executor()._recall_history("lanjut")
        self.assertIn("MOST RECENT thread", out)
        self.assertIn("kerjaan sekarang soal gateway", out)
        self.assertNotIn("kerjaan kemarin", out)

    def test_empty_results_unchanged(self):
        out = self._executor()._recall_history("lanjut")
        self.assertIn("No recent work to continue", out)
        out = self._executor()._recall_history("topik yang tidak ada")
        self.assertIn("No past conversation found matching", out)


class ConsolidateMemoryToolTests(_DigestFixture):
    def test_tool_is_registered_in_safe_profiles(self):
        for profile in ("safe", "workspace", "full"):
            ex = self.tools_mod.ToolExecutor(
                identity=self.identity, profile=profile, workspace=self._tmp.name
            )
            self.assertIn("consolidate_memory", {d.name for d in ex._enabled_native_defs()})

    def test_binding_formats_the_contract_dict(self):
        ex = self._executor()
        ex.memory = mock.Mock()
        ex.memory.consolidate.return_value = {
            "removed_duplicates": 2, "removed_expired": 1, "kept": 15,
        }
        out = ex._handlers["consolidate_memory"]()
        self.assertEqual(
            out,
            "Consolidated memory: 2 duplicates removed, 1 expired removed, "
            "15 kept. 3 moved to trash (restorable via restore_memory).",
        )

    def test_binding_reports_errors_without_crashing(self):
        ex = self._executor()
        ex.memory = mock.Mock()
        ex.memory.consolidate.side_effect = RuntimeError("disk penuh")
        out = ex._handlers["consolidate_memory"]()
        self.assertIn("ERROR: consolidate_memory failed", out)


if __name__ == "__main__":
    unittest.main()
