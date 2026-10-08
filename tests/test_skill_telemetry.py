"""Tests telemetri skill: counter, outcome, normalisasi identitas, privasi, scope."""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from zeline import config, skill_telemetry


class SkillTelemetryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.object(config, "DATA_DIR", Path(self._tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _storage_path(self, identity: str) -> Path:
        return skill_telemetry._path(identity)

    def _raw_json(self, identity: str) -> str:
        path = self._storage_path(identity)
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    # -- counter & outcome -------------------------------------------------

    def test_record_load_increments_counter(self):
        skill_telemetry.record_load("MySkill", "alice")
        skill_telemetry.record_load("  myskill ", "alice")
        record = skill_telemetry.stats("myskill", "alice")
        # Normalisasi: beda kapital/spasi tetap satu record.
        self.assertEqual(record["loads"], 2)
        self.assertGreater(record["last_used_ts"], 0)

    def test_success_rate_none_before_any_outcome(self):
        skill_telemetry.record_load("fresh", "alice")
        record = skill_telemetry.stats("fresh", "alice")
        self.assertIsNone(record["success_rate"])
        self.assertEqual(record["successes"], 0)
        self.assertEqual(record["failures"], 0)

    def test_outcome_success_resets_consecutive_failures(self):
        skill_telemetry.record_outcome("s", "alice", False, error_kind="Timeout")
        skill_telemetry.record_outcome("s", "alice", False, error_kind="Timeout")
        self.assertEqual(skill_telemetry.stats("s", "alice")["consecutive_failures"], 2)
        skill_telemetry.record_outcome("s", "alice", True, duration_s=1.5)
        record = skill_telemetry.stats("s", "alice")
        self.assertEqual(record["consecutive_failures"], 0)
        self.assertEqual(record["successes"], 1)
        self.assertEqual(record["failures"], 2)
        self.assertEqual(record["total_duration_s"], 1.5)
        self.assertAlmostEqual(record["success_rate"], 1 / 3)

    def test_outcome_failure_increments(self):
        skill_telemetry.record_outcome("s", "alice", False, 0.5, "ValueError")
        skill_telemetry.record_outcome("s", "alice", False, 0.5, "KeyError")
        record = skill_telemetry.stats("s", "alice")
        self.assertEqual(record["failures"], 2)
        self.assertEqual(record["consecutive_failures"], 2)
        # Kategori error terakhir yang menang.
        self.assertEqual(record["last_error_kind"], "KeyError")
        self.assertEqual(record["total_duration_s"], 1.0)

    def test_negative_duration_treated_as_zero(self):
        skill_telemetry.record_outcome("s", "alice", True, duration_s=-5.0)
        self.assertEqual(
            skill_telemetry.stats("s", "alice")["total_duration_s"], 0.0
        )

    def test_invalid_skill_name_is_silent_noop(self):
        for bad in ("", "   ", None, 123, ["x"]):
            skill_telemetry.record_load(bad, "alice")
            skill_telemetry.record_outcome(bad, "alice", False)
            self.assertEqual(skill_telemetry.stats(bad, "alice"), {})
        # Tidak ada file yang terbentuk dari nama invalid.
        self.assertFalse(self._storage_path("alice").exists())

    # -- identitas ----------------------------------------------------------

    def test_owner_identity_strips_worker_suffix(self):
        cases = {
            "alice::wkr3f8a2b1c": "alice",
            "alice::subxyz": "alice",
            "alice::wkrABC::sub123": "alice",
            "tg:12345::wkr9": "tg:12345",
            "alice": "alice",
            "": "cli:local",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(skill_telemetry.owner_identity(raw), expected)

    def test_worker_telemetry_lands_on_owner_identity(self):
        skill_telemetry.record_load("s", "alice::wkr3f8a2b1c")
        skill_telemetry.record_outcome("s", "alice::subxyz", True)
        via_owner = skill_telemetry.stats("s", "alice")
        via_worker = skill_telemetry.stats("s", "alice::wkr3f8a2b1c")
        self.assertEqual(via_owner["loads"], 1)
        self.assertEqual(via_owner["successes"], 1)
        self.assertEqual(via_owner, via_worker)

    # -- error_kind ----------------------------------------------------------

    def test_error_kind_sanitized(self):
        skill_telemetry.record_outcome(
            "s",
            "alice",
            False,
            error_kind="ValueError: kunci 'rahasia123' tidak ditemukan di chat",
        )
        kind = skill_telemetry.stats("s", "alice")["last_error_kind"]
        self.assertEqual(kind, "ValueError:kuncirahasia123tidakditemukandichat")
        # Teks chat (dengan spasi/kutip) hilang; hanya kategori yang lolos.
        self.assertNotIn("tidak ditemukan di chat", kind)
        self.assertNotIn("'", kind)
        self.assertNotIn(" ", kind)

    def test_error_kind_truncated_to_60_chars(self):
        skill_telemetry.record_outcome("s", "alice", False, error_kind="E" * 100)
        kind = skill_telemetry.stats("s", "alice")["last_error_kind"]
        self.assertEqual(kind, "E" * 60)

    # -- storage -------------------------------------------------------------

    def test_file_permissions_0600(self):
        skill_telemetry.record_load("s", "alice")
        mode = stat.S_IMODE(os.stat(self._storage_path("alice")).st_mode)
        self.assertEqual(mode, 0o600)
        dir_mode = stat.S_IMODE(os.stat(skill_telemetry.telemetry_dir()).st_mode)
        self.assertEqual(dir_mode, 0o700)

    def test_corrupt_json_reads_empty_without_raise(self):
        path = self._storage_path("alice")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("bukan json {{{", encoding="utf-8")
        self.assertEqual(skill_telemetry.stats("s", "alice"), {})
        self.assertEqual(skill_telemetry.all_stats("alice"), {})
        # Byte UTF-8 invalid juga dianggap rusak, bukan error.
        path.write_bytes(b"\xff\xfe\x00\x28")
        self.assertEqual(skill_telemetry.all_stats("alice"), {})

    def test_never_raises_when_write_fails(self):
        with mock.patch.object(
            skill_telemetry, "_write", side_effect=OSError("disk penuh")
        ):
            skill_telemetry.record_load("s", "alice")
            skill_telemetry.record_outcome("s", "alice", False, 1.0, "Boom")
        with mock.patch.object(
            skill_telemetry, "_read", side_effect=RuntimeError("rusak")
        ):
            skill_telemetry.record_load("s", "alice")
            skill_telemetry.record_outcome("s", "alice", True)

    def test_never_raises_when_storage_dir_blocked(self):
        # Direktori storage diganti file: mkdir di dalam _write pasti gagal.
        target = skill_telemetry.telemetry_dir()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("saya file, bukan direktori", encoding="utf-8")
        skill_telemetry.record_load("s", "alice")
        skill_telemetry.record_outcome("s", "alice", False, error_kind="E")

    # -- usage_scope -----------------------------------------------------------

    def test_note_used_noop_outside_scope(self):
        skill_telemetry.note_used("s")
        self.assertEqual(skill_telemetry.skills_in_scope(), frozenset())

    def test_usage_scope_collects_and_resets(self):
        with skill_telemetry.usage_scope():
            skill_telemetry.note_used("Alpha")
            skill_telemetry.note_used("beta")
            skill_telemetry.note_used("  ALPHA ")  # duplikat ternormalisasi
            self.assertEqual(
                skill_telemetry.skills_in_scope(), frozenset({"alpha", "beta"})
            )
        # Keluar scope: kosong lagi, note berikutnya no-op.
        self.assertEqual(skill_telemetry.skills_in_scope(), frozenset())
        skill_telemetry.note_used("gamma")
        self.assertEqual(skill_telemetry.skills_in_scope(), frozenset())

    def test_usage_scope_nested(self):
        with skill_telemetry.usage_scope():
            skill_telemetry.note_used("outer")
            with skill_telemetry.usage_scope():
                skill_telemetry.note_used("inner")
                self.assertEqual(
                    skill_telemetry.skills_in_scope(), frozenset({"inner"})
                )
            # Scope dalam tidak mengotori scope luar.
            self.assertEqual(
                skill_telemetry.skills_in_scope(), frozenset({"outer"})
            )

    def test_usage_scope_thread_isolation(self):
        barrier = threading.Barrier(2)
        seen: dict[str, frozenset] = {}

        def worker(skill: str, key: str) -> None:
            with skill_telemetry.usage_scope():
                barrier.wait(timeout=10)
                skill_telemetry.note_used(skill)
                seen[key] = skill_telemetry.skills_in_scope()

        threads = [
            threading.Thread(target=worker, args=("skill-alpha", "a")),
            threading.Thread(target=worker, args=("skill-beta", "b")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(seen["a"], frozenset({"skill-alpha"}))
        self.assertEqual(seen["b"], frozenset({"skill-beta"}))

    # -- privasi -----------------------------------------------------------------

    def test_privacy_raw_chat_text_never_hits_disk(self):
        chat = "PIN saya 77 88 99, jangan disebar ya"
        skill_telemetry.record_load("vault", "alice")
        skill_telemetry.record_outcome(
            "vault",
            "alice",
            False,
            2.0,
            error_kind=f"Exception: user bilang '{chat}' di percakapan",
        )
        raw = self._raw_json("alice")
        self.assertTrue(raw)  # file memang tertulis
        # Teks percakapan mentah tidak pernah mendarat utuh di disk.
        self.assertNotIn(chat, raw)
        self.assertNotIn("jangan disebar", raw)
        self.assertNotIn("PIN saya", raw)
        # Struktur JSON-nya tetap valid dan hanya berisi metadata.
        data = json.loads(raw)
        self.assertEqual(set(data), {"vault"})
        self.assertEqual(
            set(data["vault"]),
            {
                "loads",
                "successes",
                "failures",
                "total_duration_s",
                "last_used_ts",
                "last_error_kind",
                "consecutive_failures",
            },
        )

    def test_all_stats_includes_success_rate(self):
        skill_telemetry.record_load("a", "alice")
        skill_telemetry.record_outcome("a", "alice", True)
        skill_telemetry.record_load("b", "alice")
        result = skill_telemetry.all_stats("alice")
        self.assertEqual(set(result), {"a", "b"})
        self.assertEqual(result["a"]["success_rate"], 1.0)
        self.assertIsNone(result["b"]["success_rate"])

    def test_error_kind_masking_is_idempotent(self):
        # Temuan audit MINOR: teks yang sudah di-masking tidak boleh berubah
        # saat dinormalisasi ulang (mis. baca dari disk) — penanda "#"
        # adalah karakter yang diizinkan.
        once = skill_telemetry._normalize_error_kind(
            "PIN 987654, kode 12, telepon 081234567890"
        )
        twice = skill_telemetry._normalize_error_kind(once)
        self.assertEqual(once, twice)
        self.assertIn("#", once)
        self.assertNotIn("987654", once)
        self.assertNotIn("081234567890", once)
        # Rangkaian < 4 digit bukan PII -> tidak disamarkan.
        self.assertIn("12", once)

    # -- pengerasan error_kind + agregat global --------------------------------

    def test_error_kind_digit_runs_masked(self):
        # Nomor telepon / PIN: rangkaian 4+ digit disamarkan.
        skill_telemetry.record_outcome(
            "s", "alice", False, error_kind="Halo saya Budi, nomor 0812-3456-7890"
        )
        kind = skill_telemetry.stats("s", "alice")["last_error_kind"]
        self.assertNotIn("0812", kind)
        self.assertNotIn("3456", kind)
        self.assertIn("#", kind)
        # Kategori normal tanpa digit panjang tidak berubah.
        skill_telemetry.record_outcome(
            "s2", "alice", False, error_kind="exception:TimeoutError"
        )
        self.assertEqual(
            skill_telemetry.stats("s2", "alice")["last_error_kind"],
            "exception:TimeoutError",
        )

    def test_global_stats_aggregates_across_identities(self):
        for _ in range(4):
            skill_telemetry.record_load("x", "alice")
        skill_telemetry.record_outcome("x", "alice", True)
        for _ in range(6):
            skill_telemetry.record_load("x", "bob")
        skill_telemetry.record_outcome("x", "bob", False, error_kind="boom")
        skill_telemetry.record_outcome("x", "bob", False, error_kind="boom")

        g = skill_telemetry.global_stats("x")
        self.assertEqual(g["loads"], 10)
        self.assertEqual(g["successes"], 1)
        self.assertEqual(g["failures"], 2)
        self.assertAlmostEqual(g["success_rate"], 1 / 3)

    def test_global_stats_skips_corrupt_files(self):
        skill_telemetry.record_load("y", "alice")
        # File identitas lain rusak — agregat tetap jalan (fail-safe).
        bad = skill_telemetry.telemetry_dir() / "deadbeef.json"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("{bukan json", encoding="utf-8")
        g = skill_telemetry.global_stats("y")
        self.assertEqual(g["loads"], 1)

    def test_global_stats_unknown_skill_empty(self):
        self.assertEqual(skill_telemetry.global_stats("tak-ada"), {})
        self.assertEqual(skill_telemetry.global_stats(""), {})

    def test_module_compiles_without_invalid_escape_sequences(self):
        # m1: `\-` di docstring non-raw memicu SyntaxWarning — kompilasi
        # ulang dengan SyntaxWarning sebagai error harus lolos.
        import py_compile
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error", SyntaxWarning)
            py_compile.compile(skill_telemetry.__file__, doraise=True)


if __name__ == "__main__":
    unittest.main()
