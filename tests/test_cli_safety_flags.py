"""Tests untuk 3 sisa open questions hardening (verdict owner).

Item 1 (reflect tanpa policy -> deny-all) diuji di
tests/test_approval_chokepoint.py::GateSemanticsTests. File ini menguji:
- Item 2: ``zeline mcp add --trust-risk-cap <kelas>``
- Item 3: ``zeline cron add --grants <daftar-kelas>``
"""
from __future__ import annotations

import importlib
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def fresh(home: Path):
    """Reimport zeline modules dengan ZELINE_HOME terisolasi (pola test_scheduler)."""
    os.environ["ZELINE_HOME"] = str(home)
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    config = importlib.import_module("zeline.config")
    cli = importlib.import_module("zeline.cli")
    scheduler = importlib.import_module("zeline.scheduler")
    return config, cli, scheduler


class IsolatedCliBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = os.environ.get("ZELINE_HOME")
        self.config, self.cli, self.scheduler = fresh(Path(self._tmp.name) / "zhome")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved
        self._tmp.cleanup()
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)


class McpTrustRiskCapTests(IsolatedCliBase):
    def _servers(self):
        return self.config.stored_config_copy().get("mcp", {}).get("servers", {})

    def test_add_with_valid_cap_saves_it_normalized(self):
        rc = self.cli.cmd_mcp("add", "docs-srv", command="echo hi", trust_risk_cap="READ")
        self.assertEqual(rc, 0)
        spec = self._servers()["docs-srv"]
        self.assertEqual(spec["trust"]["risk_cap"], "read")

    def test_add_with_invalid_cap_is_rejected_and_nothing_saved(self):
        rc = self.cli.cmd_mcp("add", "evil-srv", command="echo hi", trust_risk_cap="superadmin")
        self.assertEqual(rc, 2)
        self.assertNotIn("evil-srv", self._servers())

    def test_add_with_multiple_caps_is_rejected(self):
        # Flag ini hanya menerima tepat satu kelas — tidak ada silent truncation.
        rc = self.cli.cmd_mcp("add", "multi-srv", command="echo hi", trust_risk_cap="read,write")
        self.assertEqual(rc, 2)
        self.assertNotIn("multi-srv", self._servers())

    def test_add_without_cap_keeps_destructive_default(self):
        rc = self.cli.cmd_mcp("add", "plain-srv", command="echo hi")
        self.assertEqual(rc, 0)
        spec = self._servers()["plain-srv"]
        self.assertNotIn("trust", spec)

    def test_list_shows_the_cap(self):
        self.cli.cmd_mcp("add", "docs-srv", command="echo hi", trust_risk_cap="read")
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(self.cli.cmd_mcp("list"), 0)
        self.assertIn("trust:read", buf.getvalue())
        self.cli.cmd_mcp("add", "plain-srv", command="echo hi")
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.cli.cmd_mcp("list")
        self.assertIn("destructive default", buf.getvalue())


class CronGrantsFlagTests(IsolatedCliBase):
    def test_add_with_valid_grants_creates_active_job_without_picker(self):
        # Picker TIDAK boleh muncul: patch ask agar meledak bila dipanggil.
        with mock.patch(
            "zeline.interaction.ask", side_effect=AssertionError("picker must not appear")
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = self.cli.cmd_cron(
                    "add", schedule="30m", prompt="laporan", grants="write,network"
                )
        self.assertEqual(rc, 0)
        job = self.scheduler.find_job("job1")
        self.assertIsNotNone(job)
        self.assertTrue(job.enabled)
        self.assertEqual(job.grants["risk"], ["network", "write"])
        out = buf.getvalue()
        # Pembuatan job me-log apa yang di-grant, jelas tanpa picker.
        self.assertIn("network", out)
        self.assertIn("write", out)
        self.assertIn("no picker", out)

    def test_add_with_invalid_grants_is_rejected_and_no_job_created(self):
        rc = self.cli.cmd_cron(
            "add", schedule="30m", prompt="laporan", grants="write,superadmin"
        )
        self.assertEqual(rc, 2)
        self.assertEqual(self.scheduler.list_jobs(), [])

    def test_add_with_grants_is_case_insensitive_and_deduped(self):
        with mock.patch(
            "zeline.interaction.ask", side_effect=AssertionError("picker must not appear")
        ):
            rc = self.cli.cmd_cron(
                "add", schedule="30m", prompt="x", grants="Write, WRITE,network"
            )
        self.assertEqual(rc, 0)
        job = self.scheduler.find_job("job1")
        self.assertEqual(job.grants["risk"], ["network", "write"])

    def test_add_without_grants_keeps_the_interactive_picker_flow(self):
        # Tanpa flag: perilaku lama — paused dulu, picker yang memutuskan.
        with mock.patch("zeline.interaction.ask", return_value="Allow") as ask:
            rc = self.cli.cmd_cron("add", schedule="30m", prompt="x")
        self.assertEqual(rc, 0)
        ask.assert_called_once()
        job = self.scheduler.find_job("job1")
        self.assertTrue(job.enabled)
        # Default minimal (read + write), bukan perluasan diam-diam.
        self.assertEqual(job.grants["risk"], ["read", "write"])


if __name__ == "__main__":
    unittest.main()
