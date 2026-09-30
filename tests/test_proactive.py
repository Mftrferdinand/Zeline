"""Test proactive briefing: prompt, identitas job, anti-spam deliver, enable/disable."""

import json
import unittest
from pathlib import Path
from unittest import mock

from zeline import proactive, scheduler


def _patch_paths(testcase, tmp: Path):
    """Arahkan jobs.json + cron dir ke direktori sementara (jangan sentuh ~/.zeline)."""
    testcase.addCleanup(mock.patch.stopall)
    mock.patch.object(scheduler, "jobs_path", return_value=tmp / "jobs.json").start()
    mock.patch.object(scheduler, "cron_dir", return_value=tmp).start()
    mock.patch.object(scheduler, "output_dir", return_value=tmp / "output").start()


class BriefingPromptTests(unittest.TestCase):
    def test_prompt_memuat_instruksi_silent(self):
        prompt = proactive.briefing_prompt("telegram:123")
        self.assertIn("__SILENT__", prompt)
        self.assertIn("EXACTLY", prompt)

    def test_prompt_menyebut_tool_yang_ada(self):
        prompt = proactive.briefing_prompt("telegram:123")
        self.assertIn("list_memory", prompt)
        self.assertIn("recall_history", prompt)

    def test_preview_tanpa_membuat_job(self):
        with mock.patch.object(scheduler, "add_job") as add:
            text = proactive.preview("telegram:123")
        add.assert_not_called()
        self.assertIn("__SILENT__", text)


class JobIdentityTests(unittest.TestCase):
    def test_default_terisolasi(self):
        job = scheduler.Job(id="job7", schedule="09:00", prompt="x")
        self.assertEqual(scheduler._job_identity(job), "cron:job7")

    def test_run_as_dipakai_bila_diisi(self):
        job = scheduler.Job(id="job7", schedule="09:00", prompt="x", run_as=" telegram:123 ")
        self.assertEqual(scheduler._job_identity(job), "telegram:123")

    def test_run_as_kosong_kembali_ke_default(self):
        job = scheduler.Job(id="job7", schedule="09:00", prompt="x", run_as="  ")
        self.assertEqual(scheduler._job_identity(job), "cron:job7")

    def test_backward_compat_json_lama_tanpa_run_as(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jobs.json"
            path.write_text(
                json.dumps([{"id": "job1", "schedule": "09:00", "prompt": "halo"}]),
                encoding="utf-8",
            )
            with mock.patch.object(scheduler, "jobs_path", return_value=path):
                jobs = scheduler._read_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].run_as, "")
        self.assertEqual(scheduler._job_identity(jobs[0]), "cron:job1")


class DeliverSilentTests(unittest.TestCase):
    def _job(self, deliver="telegram:123"):
        return scheduler.Job(id="job1", schedule="09:00", prompt="x", deliver=deliver)

    def test_silent_marker_tidak_mengirim(self):
        ok, detail = scheduler.deliver(self._job(), "__SILENT__")
        self.assertTrue(ok)
        self.assertEqual(detail, "silent: nothing worth sending")

    def test_silent_dengan_whitespace_tetap_silent(self):
        ok, detail = scheduler.deliver(self._job(), "  __SILENT__\n")
        self.assertTrue(ok)
        self.assertIn("silent", detail)

    def test_teks_kosong_tidak_mengirim(self):
        ok, detail = scheduler.deliver(self._job(), "   ")
        self.assertTrue(ok)
        self.assertIn("silent", detail)

    def test_teks_normal_tetap_jalan(self):
        # deliver=local tidak menyentuh network; memastikan early-return
        # silent tidak merusak jalur normal.
        ok, detail = scheduler.deliver(self._job(deliver="local"), "halo")
        self.assertTrue(ok)
        self.assertEqual(detail, "saved locally")


class EnableDisableTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        _patch_paths(self, Path(self._tmp.name))

    def test_enable_membuat_job_briefing(self):
        job = proactive.enable("telegram:123", "07:00")
        self.assertEqual(job.id, "briefing-telegram-123")
        self.assertEqual(job.deliver, "telegram:123")
        self.assertEqual(job.run_as, "telegram:123")
        self.assertEqual(job.schedule, "07:00")
        self.assertIn("__SILENT__", job.prompt)

    def test_enable_mengganti_bukan_mendobel(self):
        proactive.enable("telegram:123", "07:00")
        proactive.enable("telegram:123", "08:30")
        jobs = [j for j in scheduler.list_jobs() if j.id.startswith("briefing-")]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].schedule, "08:30")

    def test_enable_chat_kosong_ditolak(self):
        with self.assertRaises(ValueError):
            proactive.enable("  ")

    def test_disable_menghapus_semua_briefing(self):
        proactive.enable("telegram:123")
        proactive.enable("telegram:999")
        self.assertTrue(proactive.disable())
        remaining = [j for j in scheduler.list_jobs() if j.id.startswith("briefing-")]
        self.assertEqual(remaining, [])
        self.assertFalse(proactive.disable())

    def test_status_meringkas_job(self):
        self.assertIn("Tidak ada", proactive.status())
        proactive.enable("telegram:123", "07:00")
        text = proactive.status()
        self.assertIn("briefing-telegram-123", text)
        self.assertIn("telegram:123", text)


if __name__ == "__main__":
    unittest.main()
