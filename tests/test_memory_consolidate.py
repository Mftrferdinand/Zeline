"""Unit test untuk MemoryStore.consolidate() (nudge deterministik).

Menutup: duplikat-varian (whitespace/case) terhapus dan record PERTAMA
(tertua) yang dipertahankan, record expired terhapus permanen dari file,
record sehat tidak tersentuh, return dict sesuai kontrak tool
``consolidate_memory``, dan idempotensi (jalan 2x -> 0 removals).
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from zeline import memory as memory_module
from zeline.memory import MemoryStore, _write


def _record(
    text: str,
    created_at: float = 1000.0,
    expires_at: float | None = None,
    source: str = "user",
    confidence: float = 1.0,
) -> dict:
    return {
        "text": text,
        "kind": "fact",
        "source": source,
        "confidence": confidence,
        "created_at": created_at,
        "expires_at": expires_at,
    }


class ConsolidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        # Path di-derive dari identity via module-global MEMORY_DIR.
        self._patcher = mock.patch.object(memory_module, "MEMORY_DIR", self.tmpdir)
        self._patcher.start()
        # Identity non-cli:local agar tidak kena migrasi legacy.
        self.store = MemoryStore("test:consolidate")

    def tearDown(self):
        self._patcher.stop()
        self.tmp.cleanup()

    def _seed(self, records):
        _write(self.store.path, records)

    def test_duplicate_variants_removed_keeps_oldest(self):
        self._seed(
            [
                _record("Nama saya Budi", created_at=100.0),
                _record("nama   saya  BUDI", created_at=200.0),
                _record("NAMA SAYA BUDI", created_at=300.0),
                _record("Saya suka kopi", created_at=150.0),
            ]
        )
        result = self.store.consolidate()
        self.assertEqual(
            result,
            {"removed_duplicates": 2, "removed_expired": 0, "kept": 2},
        )
        kept = self.store.records(include_expired=True)
        self.assertEqual(len(kept), 2)
        # Yang dipertahankan adalah record PERTAMA (tertua), bukan varian.
        self.assertEqual(kept[0]["text"], "Nama saya Budi")
        self.assertEqual(kept[0]["created_at"], 100.0)
        self.assertEqual(kept[1]["text"], "Saya suka kopi")

    def test_expired_records_removed_permanently(self):
        past = time.time() - 3600
        future = time.time() + 3600
        self._seed(
            [
                _record("Fakta basi", created_at=100.0, expires_at=past),
                _record("Fakta hidup", created_at=200.0, expires_at=None),
                _record("Fakta sementara", created_at=300.0, expires_at=future),
            ]
        )
        result = self.store.consolidate()
        self.assertEqual(
            result,
            {"removed_duplicates": 0, "removed_expired": 1, "kept": 2},
        )
        # Benar-benar hilang dari file, bukan cuma disaring saat baca.
        raw = self.store.records(include_expired=True)
        self.assertEqual([r["text"] for r in raw], ["Fakta hidup", "Fakta sementara"])
        # list() (kontrak lama) tetap konsisten.
        self.assertEqual(self.store.list(), ["Fakta hidup", "Fakta sementara"])

    def test_mixed_duplicates_and_expired_counts_add_up(self):
        past = time.time() - 3600
        self._seed(
            [
                _record("Halo dunia", created_at=100.0),
                _record("  HALO   dunia ", created_at=200.0),
                _record("Sudah basi", created_at=300.0, expires_at=past),
                _record("Tetap ada", created_at=400.0),
            ]
        )
        result = self.store.consolidate()
        self.assertEqual(result["removed_duplicates"], 1)
        self.assertEqual(result["removed_expired"], 1)
        self.assertEqual(result["kept"], 2)
        # Aritmetika konsisten: dibuang + disimpan = total awal.
        self.assertEqual(
            result["removed_duplicates"] + result["removed_expired"] + result["kept"], 4
        )

    def test_idempotent_second_run_removes_nothing(self):
        self._seed(
            [
                _record("Satu", created_at=100.0),
                _record("  SATU ", created_at=200.0),
            ]
        )
        first = self.store.consolidate()
        self.assertEqual(first["removed_duplicates"], 1)
        before = self.store.path.read_bytes()
        second = self.store.consolidate()
        self.assertEqual(
            second,
            {"removed_duplicates": 0, "removed_expired": 0, "kept": 1},
        )
        # File tidak ditulis ulang bila tidak ada perubahan.
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_empty_store_returns_zeros_without_creating_file(self):
        result = self.store.consolidate()
        self.assertEqual(
            result,
            {"removed_duplicates": 0, "removed_expired": 0, "kept": 0},
        )
        self.assertFalse(self.store.path.exists())

    def test_healthy_records_untouched(self):
        self._seed(
            [
                _record("Fakta A", created_at=100.0),
                _record("Fakta B", created_at=200.0, source="reflection",
                        confidence=0.6),
            ]
        )
        before = self.store.path.read_bytes()
        result = self.store.consolidate()
        self.assertEqual(
            result,
            {"removed_duplicates": 0, "removed_expired": 0, "kept": 2},
        )
        self.assertEqual(self.store.path.read_bytes(), before)
        # Provenance record sehat tidak berubah.
        kept = self.store.records(include_expired=True)
        self.assertEqual(kept[1]["source"], "reflection")
        self.assertEqual(kept[1]["confidence"], 0.6)

    def test_exact_contract_keys(self):
        # Kontrak untuk tool consolidate_memory: key persis ini.
        self._seed([_record("X", created_at=100.0)])
        result = self.store.consolidate()
        self.assertEqual(
            set(result.keys()), {"removed_duplicates", "removed_expired", "kept"}
        )
        self.assertTrue(all(isinstance(v, int) for v in result.values()))


if __name__ == "__main__":
    unittest.main()
