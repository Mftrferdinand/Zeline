"""Unit test untuk soft-delete memory: trash + restore.

Prinsip yang diuji: menghapus memory TIDAK PERNAH permanen tanpa undo.
``remove()``/``consolidate()`` memindahkan record ke trash per identity;
``restore()`` mengembalikannya utuh. Trash tidak pernah ikut ter-retrieve.

Menutup skenario bahagia: remove -> hilang dari list()/formatted()/
prompt_block()/retrieve() tapi utuh di trash; restore -> kembali lengkap
(teks, kind, source, confidence, created_at); consolidate me-routing
duplikat + expired ke trash.

Menutup skenario rusak: restore tanpa match, substring kosong, restore saat
teks sudah hidup (tidak diduplikasi), restore saat memory penuh (tetap di
trash), batas ukuran trash (terlama dibuang duluan), file trash korup,
dan isolasi trash antar identity.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from zeline import memory as memory_module
from zeline.memory import MemoryStore, _trash_path, _write


def _record(
    text: str,
    created_at: float = 1000.0,
    expires_at: float | None = None,
    source: str = "user",
    confidence: float = 1.0,
    kind: str = "fact",
) -> dict:
    return {
        "text": text,
        "kind": kind,
        "source": source,
        "confidence": confidence,
        "created_at": created_at,
        "expires_at": expires_at,
    }


class TrashTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        # Path di-derive dari identity via module-global MEMORY_DIR; trash
        # mengikuti karena _trash_dir() dihitung dinamis dari MEMORY_DIR.
        self._patcher = mock.patch.object(memory_module, "MEMORY_DIR", self.tmpdir)
        self._patcher.start()
        # Identity non-cli:local agar tidak kena migrasi legacy.
        self.store = MemoryStore("test:trash")

    def tearDown(self):
        self._patcher.stop()
        self.tmp.cleanup()

    def _seed(self, records):
        _write(self.store.path, records)

    def _trash_texts(self):
        return [entry["record"]["text"] for entry in self.store.trash_entries()]

    # ------------------------------------------------------- skenario bahagia
    def test_remove_moves_to_trash_not_permanent(self):
        self._seed(
            [
                _record("Budi suka kopi tubruk", created_at=100.0),
                _record("Budi benci teh", created_at=200.0),
            ]
        )
        message = self.store.remove("kopi")
        self.assertIn("removed 1 facts", message)
        self.assertIn("trash", message)
        # Hilang dari semua jalur baca publik...
        self.assertEqual(self.store.list(), ["Budi benci teh"])
        self.assertNotIn("kopi", self.store.formatted())
        self.assertNotIn("kopi", self.store.prompt_block())
        self.assertNotIn("kopi", self.store.prompt_block(query="kopi tubruk"))
        self.assertEqual(self.store.retrieve("kopi tubruk"), [])
        # ...tapi utuh di trash.
        entries = self.store.trash_entries()
        self.assertEqual(len(entries), 1)
        record = entries[0]["record"]
        self.assertEqual(record["text"], "Budi suka kopi tubruk")
        self.assertEqual(record["source"], "user")
        self.assertEqual(record["confidence"], 1.0)
        self.assertEqual(record["created_at"], 100.0)
        self.assertEqual(entries[0]["reason"], "remove")
        self.assertGreater(entries[0]["deleted_at"], 0.0)

    def test_restore_brings_back_full_provenance(self):
        self._seed(
            [
                _record(
                    "Jangan pernah percaya estimasi",
                    source="reflection",
                    confidence=0.6,
                    created_at=1234.5,
                )
            ]
        )
        self.store.remove("estimasi")
        self.assertEqual(self.store.list(), [])
        message = self.store.restore("estimasi")
        self.assertIn("restored 1 facts", message)
        # Kembali utuh, bukan sebagai fakta baru.
        records = self.store.records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["text"], "Jangan pernah percaya estimasi")
        self.assertEqual(records[0]["source"], "reflection")
        self.assertEqual(records[0]["confidence"], 0.6)
        self.assertEqual(records[0]["created_at"], 1234.5)
        # Entri trash ikut terhapus (pindah, bukan salin).
        self.assertEqual(self.store.trash_entries(), [])
        self.assertIn("estimasi", self.store.prompt_block())

    def test_restore_matches_multiple_by_substring(self):
        self._seed(
            [
                _record("kucing anggora"),
                _record("kucing kampung"),
                _record("anjing pudel"),
            ]
        )
        self.store.remove("kucing")
        self.store.remove("anjing")
        message = self.store.restore("kucing")
        self.assertIn("restored 2 facts", message)
        self.assertEqual(sorted(self.store.list()), ["kucing anggora", "kucing kampung"])
        self.assertEqual(self._trash_texts(), ["anjing pudel"])

    def test_consolidate_routes_duplicates_and_expired_to_trash(self):
        now = time.time()
        self._seed(
            [
                _record("Nama saya Budi", created_at=100.0),
                _record("nama   saya  BUDI", created_at=200.0),
                _record("fakta basi", created_at=50.0, expires_at=now - 10.0),
                _record("fakta sehat", created_at=300.0),
            ]
        )
        result = self.store.consolidate()
        self.assertEqual(result, {"removed_duplicates": 1, "removed_expired": 1, "kept": 2})
        reasons = {entry["record"]["text"]: entry["reason"] for entry in self.store.trash_entries()}
        self.assertEqual(
            reasons,
            {
                "nama   saya  BUDI": "consolidate_duplicate",
                "fakta basi": "consolidate_expired",
            },
        )
        # Duplikat yang dipertahankan = yang pertama (tertua).
        self.assertIn("Nama saya Budi", self.store.list())
        # Restore duplikat bisa; restore expired tetap menghormati TTL-nya
        # (kembali ke file tapi tidak hidup).
        self.assertIn("restored 1", self.store.restore("BUDI"))
        self.assertIn("restored 1", self.store.restore("basi"))
        self.assertNotIn("fakta basi", self.store.list())
        self.assertIn(
            "fakta basi",
            [r["text"] for r in self.store.records(include_expired=True)],
        )

    def test_trash_never_leaks_into_retrieval(self):
        self._seed([_record("kata rahasia zebra ungu")])
        self.store.remove("zebra")
        # Retrieval dengan query yang sangat cocok sekalipun tidak boleh
        # menemukan fakta yang sudah di-trash.
        self.assertEqual(self.store.retrieve("kata rahasia zebra ungu"), [])
        block = self.store.prompt_block(query="kata rahasia zebra ungu")
        self.assertNotIn("zebra", block)

    def test_trash_isolated_per_identity(self):
        other = MemoryStore("test:trash-other")
        self._seed([_record("fakta privasi")])
        self.store.remove("privasi")
        self.assertEqual(len(self.store.trash_entries()), 1)
        self.assertEqual(other.trash_entries(), [])
        self.assertEqual(other.list(), [])

    def test_remove_without_match_is_idempotent(self):
        self._seed([_record("fakta abadi")])
        before = self.store.path.read_text(encoding="utf-8")
        message = self.store.remove("tidak-ada-yang-cocok")
        self.assertIn("removed 0 facts", message)
        self.assertEqual(self.store.path.read_text(encoding="utf-8"), before)
        self.assertEqual(self.store.trash_entries(), [])

    # -------------------------------------------------------- skenario rusak
    def test_restore_preserves_untouched_expired_records(self):
        # Skenario rusak: restore() tidak boleh melenyapkan record expired
        # yang tidak ikut di-restore — itu wewenang consolidate(), bukan
        # efek samping restore.
        now = time.time()
        self._seed(
            [
                _record("fakta target", created_at=100.0),
                _record("fakta basi lain", created_at=50.0, expires_at=now - 10.0),
            ]
        )
        self.store.remove("target")
        self.assertIn("restored 1", self.store.restore("target"))
        texts = [r["text"] for r in self.store.records(include_expired=True)]
        self.assertIn("fakta target", texts)
        self.assertIn("fakta basi lain", texts)
        self.assertNotIn("fakta basi lain", self.store.list())

    def test_empty_substring_rejected(self):
        self.assertEqual(self.store.remove("  "), "ERROR: empty search term.")
        self.assertEqual(self.store.restore(""), "ERROR: empty search term.")

    def test_restore_without_match(self):
        self._seed([_record("fakta hidup")])
        message = self.store.restore("tidak-ada-di-trash")
        self.assertIn("No trashed facts", message)
        self.assertEqual(self.store.list(), ["fakta hidup"])

    def test_restore_skips_already_live_without_duplicating(self):
        self.store.add("fakta unik")
        self.store.remove("fakta unik")
        self.assertEqual(self.store.list(), [])
        # Fakta ditambahkan lagi dengan teks identik sebelum restore.
        self.store.add("fakta unik")
        message = self.store.restore("fakta unik")
        self.assertIn("restored 0 facts", message)
        self.assertIn("already present", message)
        # Tidak diduplikasi; entri trash yang isinya identik dibuang.
        self.assertEqual(self.store.list(), ["fakta unik"])
        self.assertEqual(self.store.trash_entries(), [])

    def test_restore_respects_fact_limit_and_keeps_remainder_in_trash(self):
        with mock.patch.object(memory_module, "MAX_FACTS_PER_IDENTITY", 3):
            self._seed([_record("fakta-1"), _record("fakta-2"), _record("fakta-3")])
            self.store.remove("fakta-1")
            self.store.add("fakta-4")  # memory penuh lagi: 3/3
            message = self.store.restore("fakta-1")
            self.assertIn("Memory full", message)
            self.assertIn("restored 0 facts", message)
            self.assertNotIn("fakta-1", self.store.list())
            # Tetap di trash — bisa dicoba lagi setelah ada ruang.
            self.assertEqual(self._trash_texts(), ["fakta-1"])
            # Setelah ada ruang, restore berhasil.
            self.store.remove("fakta-4")
            self.assertIn("restored 1 facts", self.store.restore("fakta-1"))
            self.assertIn("fakta-1", self.store.list())

    def test_trash_capped_oldest_dropped_first(self):
        with mock.patch.object(memory_module, "TRASH_MAX_ENTRIES", 5):
            for i in range(7):
                self.store.add(f"fakta nomor {i}")
            for i in range(7):
                self.store.remove(f"nomor {i}")
            texts = self._trash_texts()
            self.assertEqual(len(texts), 5)
            # Dua yang paling dulu dihapus sudah terbuang dari trash.
            self.assertNotIn("fakta nomor 0", texts)
            self.assertNotIn("fakta nomor 1", texts)
            self.assertIn("fakta nomor 6", texts)

    def test_corrupt_trash_file_degrades_gracefully(self):
        self._seed([_record("fakta hidup")])
        path = _trash_path(self.store.identity)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ ini bukan json", encoding="utf-8")
        self.assertEqual(self.store.trash_entries(), [])
        self.assertIn("No trashed facts", self.store.restore("apa pun"))
        # Trash berisi item non-dict juga dilewati, bukan meledak.
        path.write_text(json.dumps(["string mentah", 42, None]), encoding="utf-8")
        self.assertEqual(self.store.trash_entries(), [])

    def test_trash_file_permissions_are_private(self):
        self._seed([_record("fakta sensitif")])
        self.store.remove("sensitif")
        mode = _trash_path(self.store.identity).stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
