"""Contract tests for `/version` and `/update` on chat surfaces.

The hazard this feature exists around: **an update cannot run inside the process
it is updating.** ``zeline update`` drains the gateway and escalates to SIGKILL on
its *process group*, so an updater sharing that group would kill its own
installer mid-write. These tests pin the properties that keep that from
happening, plus the ones that keep the report honest:

- the updater is spawned **detached** (own session / process group), never inline;
- only one update at a time, enforced by an O_EXCL lock that self-heals when its
  owner dies;
- the success message reports the version read back from a **fresh** interpreter,
  never the pre-update value this process still holds in memory;
- ``/update`` is owner-gated and refuses to silently install a working tree;
- ``/version`` says the check failed rather than implying the build is current.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def fresh(home: Path):
    os.environ["ZELINE_HOME"] = str(home)
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    config = importlib.import_module("zeline.config")
    self_update = importlib.import_module("zeline.self_update")
    return config, self_update


class SelfUpdateBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "zhome"
        self._saved = os.environ.get("ZELINE_HOME")
        self.config, self.su = fresh(self.home)
        self.config.ensure_data_dirs()

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved
        self._tmp.cleanup()


class LockTests(SelfUpdateBase):
    def test_nothing_running_by_default(self):
        self.assertIsNone(self.su.active_update())

    def test_a_second_update_cannot_start_while_one_runs(self):
        """Two installers mutating the same venv is unrecoverable from chat."""
        self.assertTrue(self.su.acquire_lock(os.getpid()))
        self.assertIsNotNone(self.su.active_update())
        self.assertFalse(self.su.acquire_lock(os.getpid()))

    def test_release_frees_the_slot(self):
        self.su.acquire_lock(os.getpid())
        self.su.release_lock()
        self.assertIsNone(self.su.active_update())
        self.assertTrue(self.su.acquire_lock(os.getpid()))

    def test_a_lock_whose_owner_died_does_not_wedge_updates_forever(self):
        """A crashed updater must not require manual file deletion."""
        self.su.lock_path().write_text(
            json.dumps({"pid": 2 ** 31 - 1, "started_at": 9e9}), encoding="utf-8"
        )
        with mock.patch.object(self.su, "_pid_alive", return_value=False):
            self.assertIsNone(self.su.active_update())
        self.assertFalse(self.su.lock_path().exists())

    def test_a_lock_older_than_the_stale_window_is_reclaimed(self):
        """Even a live PID cannot hold the slot indefinitely."""
        self.su.lock_path().write_text(
            json.dumps({"pid": os.getpid(), "started_at": 0.0}), encoding="utf-8"
        )
        self.assertIsNone(self.su.active_update())

    def test_a_corrupt_lock_file_is_not_fatal(self):
        self.su.lock_path().write_text("{not json", encoding="utf-8")
        self.assertIsNone(self.su.active_update())


class SpawnTests(SelfUpdateBase):
    def test_the_updater_is_spawned_detached_not_run_inline(self):
        """The load-bearing property.

        `zeline update` signals the gateway's process GROUP. An updater in that
        group would be killed by the stop it just requested, leaving a
        half-installed venv. So it must get its own session.
        """
        captured: dict[str, Any] = {}

        class FakeProcess:
            pid = 4242

        def fake_popen(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            return FakeProcess()

        with mock.patch.object(self.su.subprocess, "Popen", fake_popen):
            ok, message = self.su.start_background_update("telegram:5")
        self.assertTrue(ok, message)
        self.assertIn("4242", message)
        if os.name == "nt":
            self.assertIn("creationflags", captured["kwargs"])
        else:
            self.assertTrue(captured["kwargs"]["start_new_session"])
        self.assertIn("_self-update", captured["command"])
        self.assertIn("--notify", captured["command"])
        self.assertIn("telegram:5", captured["command"])

    def test_spawn_is_refused_while_an_update_is_already_running(self):
        self.su.acquire_lock(os.getpid())
        ok, message = self.su.start_background_update("telegram:5")
        self.assertFalse(ok)
        self.assertIn("already running", message)

    def test_a_failed_spawn_is_reported_not_raised(self):
        with mock.patch.object(self.su.subprocess, "Popen", side_effect=OSError("no fork")):
            ok, message = self.su.start_background_update("")
        self.assertFalse(ok)
        self.assertIn("no fork", message)

    def test_the_self_update_subcommand_exists(self):
        """The child is a plain `python -m zeline.cli` call, so it must parse."""
        cli = importlib.import_module("zeline.cli")
        namespace = cli.build_parser().parse_args(["_self-update", "--notify", "telegram:9"])
        self.assertEqual(namespace.command, "_self-update")
        self.assertEqual(namespace.notify, "telegram:9")


class VersionReportTests(SelfUpdateBase):
    def test_up_to_date_when_the_tag_matches(self):
        with mock.patch("zeline.updater._latest_tag", return_value=f"v{self.su.__version__}"):
            report = self.su.version_report()
        self.assertTrue(report["up_to_date"])
        self.assertEqual(report["error"], "")

    def test_update_available_when_the_tag_is_ahead(self):
        with mock.patch("zeline.updater._latest_tag", return_value="v99.0.0"):
            report = self.su.version_report()
        self.assertFalse(report["up_to_date"])
        self.assertEqual(report["latest"], "v99.0.0")

    def test_offline_reports_unknown_rather_than_claiming_current(self):
        """Silently saying 'up to date' when the check failed is a lie."""
        with mock.patch("zeline.updater._latest_tag", side_effect=OSError("dns")):
            report = self.su.version_report()
        self.assertIsNone(report["up_to_date"])
        self.assertEqual(report["error"], "OSError")
        self.assertEqual(report["current"], self.su.__version__)


class ChildRunTests(SelfUpdateBase):
    def _run(self, *, latest: str, update_code: int, installed: str) -> list[str]:
        """Run the detached child's body with the real update stubbed out."""
        sent: list[str] = []

        class Ticker:
            enabled = False
            chat_id = 0

            def __init__(self, notify):
                pass

            def stage(self, line):
                sent.append(f"stage:{line}")

            def final(self, line):
                sent.append(f"final:{line}")

        with mock.patch.object(self.su, "_Ticker", Ticker), \
             mock.patch("zeline.updater._latest_tag", return_value=latest), \
             mock.patch("zeline.updater.update", return_value=update_code), \
             mock.patch.object(self.su, "_installed_version", return_value=installed):
            self.su.run_background_update("telegram:1")
        return sent

    def test_a_successful_update_reports_the_version_read_back_afterwards(self):
        """Not this process's __version__ -- that is the pre-update value."""
        sent = self._run(latest="v99.0.0", update_code=0, installed="99.0.0")
        final = [line for line in sent if line.startswith("final:")]
        self.assertEqual(len(final), 1)
        self.assertIn("99.0.0", final[0])
        self.assertIn(self.su.__version__, final[0])

    def test_nothing_is_installed_when_already_current(self):
        with mock.patch("zeline.updater.update") as never:
            with mock.patch.object(self.su, "_Ticker") as ticker:
                ticker.return_value.stage = lambda line: None
                ticker.return_value.final = lambda line: None
                with mock.patch("zeline.updater._latest_tag", return_value=f"v{self.su.__version__}"):
                    code = self.su.run_background_update("")
        self.assertEqual(code, 0)
        never.assert_not_called()

    def test_a_failed_install_says_so_and_names_the_log(self):
        sent = self._run(latest="v99.0.0", update_code=1, installed=self.su.__version__)
        final = [line for line in sent if line.startswith("final:")][0]
        self.assertIn("failed", final.lower())
        self.assertIn("self-update.log", final)

    def test_an_installer_that_did_not_change_the_version_is_not_called_success(self):
        """Exit 0 with an unchanged version is not an update; do not claim one."""
        sent = self._run(latest="v99.0.0", update_code=0, installed=self.su.__version__)
        final = [line for line in sent if line.startswith("final:")][0]
        self.assertNotIn("Updated", final)
        self.assertIn("still on", final.lower())

    def test_an_unreadable_version_after_install_is_flagged_not_assumed(self):
        sent = self._run(latest="v99.0.0", update_code=0, installed="")
        final = [line for line in sent if line.startswith("final:")][0]
        self.assertIn("could not be read back", final)

    def test_the_lock_is_released_even_when_the_update_raises(self):
        class Ticker:
            def __init__(self, notify):
                pass

            def stage(self, line):
                pass

            def final(self, line):
                pass

        with mock.patch.object(self.su, "_Ticker", Ticker), \
             mock.patch("zeline.updater._latest_tag", return_value="v99.0.0"), \
             mock.patch("zeline.updater.update", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.su.run_background_update("")
        self.assertIsNone(self.su.active_update())


class TelegramSurfaceTests(SelfUpdateBase):
    def setUp(self):
        super().setUp()
        self.telegram = importlib.import_module("zeline.gateways.telegram")

    def test_both_commands_are_in_the_bot_menu(self):
        names = {item["command"] for item in self.telegram._telegram_commands()}
        self.assertIn("version", names)
        self.assertIn("update", names)

    def test_version_card_shows_installed_and_latest(self):
        with mock.patch("zeline.updater._latest_tag", return_value="v99.0.0"):
            card = self.telegram._version_reply()
        self.assertIn(self.su.__version__, card)
        self.assertIn("v99.0.0", card)
        self.assertIn("/update", card)

    def test_version_card_admits_a_failed_check(self):
        with mock.patch("zeline.updater._latest_tag", side_effect=OSError("dns")):
            card = self.telegram._version_reply()
        self.assertIn("unknown", card)
        self.assertNotIn("Up to date", card)

    def test_update_is_refused_when_the_bot_has_no_owner(self):
        """A public bot has nobody authorised to reinstall it."""
        reply = self.telegram._start_update_reply(5, [])
        self.assertIn("disabled", reply)

    def test_update_is_refused_for_a_non_owner_chat(self):
        reply = self.telegram._start_update_reply(5, ["7"])
        self.assertIn("owner", reply.lower())

    def test_update_from_a_checkout_tells_the_owner_to_do_it_deliberately(self):
        """Installing an uncommitted working tree from chat is a surprise."""
        with mock.patch("zeline.updater._checkout_root", return_value=Path("/tmp/co")):
            reply = self.telegram._start_update_reply(7, ["7"])
        self.assertIn("checkout", reply)
        self.assertIn("git pull", reply)

    def test_update_does_nothing_when_already_current(self):
        with mock.patch("zeline.updater._checkout_root", return_value=None), \
             mock.patch("zeline.updater._latest_tag", return_value=f"v{self.su.__version__}"):
            reply = self.telegram._start_update_reply(7, ["7"])
        self.assertIn("Nothing to do", reply)

    def test_update_starts_the_detached_updater_and_warns_about_downtime(self):
        with mock.patch("zeline.updater._checkout_root", return_value=None), \
             mock.patch("zeline.updater._latest_tag", return_value="v99.0.0"), \
             mock.patch.object(
                 importlib.import_module("zeline.self_update"),
                 "start_background_update",
                 return_value=(True, "Update started (PID 1)."),
             ) as spawn:
            reply = self.telegram._start_update_reply(7, ["7"])
        spawn.assert_called_once_with("telegram:7")
        self.assertIn("v99.0.0", reply)
        self.assertIn("unreachable", reply)

    def test_the_dispatcher_passes_the_allowlist_to_the_command_handler(self):
        """Without this, /update could not tell the owner from a guest."""
        source = (SOURCE_ROOT / "zeline" / "gateways" / "telegram.py").read_text(encoding="utf-8")
        self.assertIn("tool_profile=tool_profile, allowed=allowed", source)


class TickerTests(SelfUpdateBase):
    def test_the_ticker_is_inert_without_a_telegram_token(self):
        """No token means no notifier -- and definitely no crash."""
        ticker = self.su._Ticker("telegram:5")
        self.assertFalse(ticker.enabled)
        ticker.stage("nothing happens")
        ticker.final("nothing happens")

    def test_a_non_telegram_destination_is_inert(self):
        self.assertFalse(self.su._Ticker("").enabled)
        self.assertFalse(self.su._Ticker("discord:1").enabled)
        self.assertFalse(self.su._Ticker("telegram:notanumber").enabled)

    def test_it_reports_through_the_bot_api_not_the_gateway(self):
        """The gateway is deliberately stopped for most of an update."""
        saved = self.config.config_copy()
        saved["gateways"]["telegram"]["token"] = "123:abc"
        self.config.save_config(saved)
        _config, su = fresh(self.home)
        ticker = su._Ticker("telegram:5")
        self.assertTrue(ticker.enabled)
        self.assertIn("api.telegram.org", ticker._api)

    def test_a_failed_notification_does_not_abort_the_update(self):
        saved = self.config.config_copy()
        saved["gateways"]["telegram"]["token"] = "123:abc"
        self.config.save_config(saved)
        _config, su = fresh(self.home)
        ticker = su._Ticker("telegram:5")
        import requests

        with mock.patch.object(requests, "post", side_effect=OSError("offline")):
            ticker.stage("still fine")
            ticker.final("still fine")


if __name__ == "__main__":
    unittest.main()
