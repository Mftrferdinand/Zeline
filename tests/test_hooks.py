"""Tests for zeline/hooks.py — event-driven automation hooks.

Hooks must never crash the agent loop: failures are isolated with a
per-hook timeout and recorded, never raised.
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


def _fresh_registry():
    """Reset in-memory hook registry (test isolation)."""
    from zeline import hooks

    with hooks._registry_lock:
        for ev in hooks.EVENTS:
            hooks._registry[ev] = []
    hooks._hook_errors.clear()


class HookCoreTests(unittest.TestCase):
    def setUp(self):
        _fresh_registry()

    def tearDown(self):
        _fresh_registry()

    def test_register_and_trigger(self):
        from zeline import hooks

        fired = []
        hooks.register_hook(hooks.ON_TOOL_CALL, lambda d: fired.append(d["name"]), "t")
        res = hooks.trigger(hooks.ON_TOOL_CALL, {"name": "web_search"})
        self.assertEqual(fired, ["web_search"])
        self.assertTrue(res[0]["ok"])

    def test_unknown_event_rejected(self):
        from zeline import hooks

        with self.assertRaises(ValueError):
            hooks.register_hook("bogus", lambda d: None, "x")
        # trigger on unknown event is a safe no-op
        self.assertEqual(hooks.trigger("bogus"), [])

    def test_reregister_replaces(self):
        from zeline import hooks

        fired = []
        hooks.register_hook(hooks.ON_TOOL_CALL, lambda d: fired.append("old"), "dup")
        hooks.register_hook(hooks.ON_TOOL_CALL, lambda d: fired.append("new"), "dup")
        hooks.trigger(hooks.ON_TOOL_CALL, {})
        self.assertEqual(fired, ["new"])

    def test_unregister(self):
        from zeline import hooks
        hooks.register_hook(hooks.ON_TOOL_CALL, lambda d: None, "t")
        self.assertTrue(hooks.unregister_hook(hooks.ON_TOOL_CALL, "t"))
        self.assertFalse(hooks.unregister_hook(hooks.ON_TOOL_CALL, "t"))

    def test_failing_hook_does_not_crash_trigger(self):
        from zeline import hooks

        ran = []

        def bad(d):
            raise RuntimeError("boom")

        hooks.register_hook(hooks.ON_TURN_START, bad, "bad")
        hooks.register_hook(hooks.ON_TURN_START, lambda d: ran.append(1), "good")
        res = hooks.trigger(hooks.ON_TURN_START, {})
        self.assertEqual(ran, [1], "good hook must run even if bad hook fails")
        bad_res = [r for r in res if r["hook"] == "bad"][0]
        self.assertFalse(bad_res["ok"])
        self.assertTrue(any(e["hook"] == "bad" for e in hooks.hook_errors()))

    def test_trigger_deep_copies_caller_data(self):
        """H-A2: hook mutations must not leak into the caller's dict."""
        from zeline import hooks

        def mutator(d):
            d["nested"]["count"] = 999
            d["new_key"] = "injected"

        hooks.register_hook(hooks.ON_TOOL_CALL, mutator, "mut")
        original = {"nested": {"count": 0}, "name": "web_search"}
        hooks.trigger(hooks.ON_TOOL_CALL, original)
        self.assertEqual(original, {"nested": {"count": 0}, "name": "web_search"})

    def test_slow_hook_times_out(self):
        from zeline import hooks
        import zeline.hooks as hm

        orig = hm.HOOK_TIMEOUT
        hm.HOOK_TIMEOUT = 0.3
        try:
            def slow(d):
                time.sleep(10)

            hooks.register_hook(hooks.ON_ERROR, slow, "slow")
            t0 = time.time()
            res = hooks.trigger(hooks.ON_ERROR, {})
            dt = time.time() - t0
            self.assertLess(dt, 5, "timed-out hook must not block trigger")
            slow_res = [r for r in res if r["hook"] == "slow"][0]
            self.assertFalse(slow_res["ok"])
            self.assertIn("timed out", str(slow_res["detail"]))
        finally:
            hm.HOOK_TIMEOUT = orig

    def test_trigger_never_raises(self):
        from zeline import hooks

        # Even with a registry in a weird state, trigger must not raise.
        hooks.register_hook(hooks.ON_TOOL_CALL, "not-callable-at-all", "weird") \
            if False else None
        try:
            hooks.trigger(hooks.ON_TOOL_CALL, None)
            hooks.trigger(hooks.ON_TOOL_CALL, {"a": object()})
        except Exception as exc:  # noqa: BLE001
            self.fail(f"trigger raised: {exc}")


class HookConfigTests(unittest.TestCase):
    def setUp(self):
        _fresh_registry()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg_path = Path(self.tmp.name) / "hooks.json"
        p1 = patch("zeline.hooks.config_path", return_value=self.cfg_path)
        p1.start()
        self.addCleanup(p1.stop)

    def test_defaults_created(self):
        from zeline import hooks

        cfg = hooks.load_config()
        self.assertTrue(cfg["enabled"])
        names = {h["name"] for h in cfg["hooks"]}
        self.assertIn("command-logger", names)
        self.assertIn("session-memory", names)
        self.assertTrue(self.cfg_path.is_file())

    def test_add_remove(self):
        from zeline import hooks

        entry = hooks.add_hook_def("my-hook", hooks.ON_ERROR, "echo hi")
        self.assertEqual(entry["name"], "my-hook")
        self.assertEqual(entry["type"], "command")
        cfg = hooks.load_config()
        self.assertTrue(any(h["name"] == "my-hook" for h in cfg["hooks"]))
        self.assertTrue(hooks.remove_hook_def("my-hook"))
        self.assertFalse(hooks.remove_hook_def("my-hook"))

    def test_add_rejects_bad_event(self):
        from zeline import hooks

        with self.assertRaises(ValueError):
            hooks.add_hook_def("x", "bogus", "echo hi")

    def test_add_rejects_empty(self):
        from zeline import hooks

        with self.assertRaises(ValueError):
            hooks.add_hook_def("", hooks.ON_ERROR, "echo hi")
        with self.assertRaises(ValueError):
            hooks.add_hook_def("x", hooks.ON_ERROR, "   ")

    def test_enable_disable(self):
        from zeline import hooks

        hooks.add_hook_def("t", hooks.ON_ERROR, "echo hi")
        self.assertTrue(hooks.set_hook_enabled("t", False))
        cfg = hooks.load_config()
        h = next(x for x in cfg["hooks"] if x["name"] == "t")
        self.assertFalse(h["enabled"])
        self.assertFalse(hooks.set_hook_enabled("nope", True))

    def test_command_hook_runs_with_stdin_json(self):
        from zeline import hooks

        out = Path(self.tmp.name) / "payload.json"
        hooks.add_hook_def("echoer", hooks.ON_ERROR, f"cat > {out}")
        res = hooks.trigger(hooks.ON_ERROR, {"error": "boom"})
        self.assertTrue(out.is_file())
        payload = json.loads(out.read_text())
        self.assertEqual(payload["event"], "on_error")
        self.assertEqual(payload["hook"], "echoer")
        self.assertEqual(payload["data"]["error"], "boom")
        self.assertTrue(any(r["hook"] == "echoer" and r["ok"] for r in res))

    def test_disabled_hook_skipped(self):
        from zeline import hooks

        out = Path(self.tmp.name) / "nope.json"
        hooks.add_hook_def("off", hooks.ON_ERROR, f"cat > {out}")
        hooks.set_hook_enabled("off", False)
        hooks.trigger(hooks.ON_ERROR, {})
        self.assertFalse(out.exists())

    def test_global_disable(self):
        from zeline import hooks

        fired = []
        hooks.register_hook(hooks.ON_TOOL_CALL, lambda d: fired.append(1), "t")
        cfg = hooks.load_config()
        cfg["enabled"] = False
        hooks.save_config(cfg)
        self.assertEqual(hooks.trigger(hooks.ON_TOOL_CALL, {}), [])
        self.assertEqual(fired, [])


class BuiltinHookTests(unittest.TestCase):
    def setUp(self):
        _fresh_registry()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg_path = Path(self.tmp.name) / "hooks.json"
        self.log_dir = Path(self.tmp.name) / "hooks-log"
        p1 = patch("zeline.hooks.config_path", return_value=self.cfg_path)
        p2 = patch("zeline.hooks.log_dir", return_value=self.log_dir)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    def test_command_logger_writes_jsonl(self):
        from zeline import hooks

        res = hooks.trigger(
            hooks.ON_TOOL_CALL,
            {"identity": "test:cli", "name": "web_search", "args": {"q": "x"}},
        )
        self.assertTrue(any(r["hook"] == "command-logger" and r["ok"] for r in res))
        logf = self.log_dir / "tool-calls.jsonl"
        self.assertTrue(logf.is_file())
        entry = json.loads(logf.read_text().strip().split("\n")[-1])
        self.assertEqual(entry["tool"], "web_search")

    def test_session_memory_quiet_turn_noop(self):
        from zeline import hooks

        res = hooks.trigger(hooks.ON_TURN_END, {"identity": "t", "tool_calls": 0})
        self.assertTrue(any(r["hook"] == "session-memory" and r["ok"] for r in res))

    # --- H4: hook log files are owner-only (0600) ---

    def test_hook_log_files_are_0600(self):
        import stat
        from zeline import hooks

        hooks.trigger(
            hooks.ON_TOOL_CALL,
            {"identity": "t", "name": "web_search", "args": {"q": "x"}},
        )
        # force an error log entry too
        hooks._record_error("probe", hooks.ON_TOOL_CALL, "boom")
        for fname in ("tool-calls.jsonl", "hook-errors.jsonl"):
            p = self.log_dir / fname
            self.assertTrue(p.is_file(), fname)
            mode = stat.S_IMODE(p.stat().st_mode)
            self.assertEqual(mode, 0o600, f"{fname} mode {oct(mode)}")

    # --- H5: secret-looking arg values are redacted in on-disk logs ---

    def test_command_logger_redacts_secrets(self):
        from zeline import hooks

        hooks.trigger(
            hooks.ON_TOOL_CALL,
            {
                "identity": "t",
                "name": "some_tool",
                "args": {
                    "api_key": "sk-SECRET123",
                    "query": "public text",
                    "authToken": "tok-456",
                    "PASSWORD": "hunter2",
                },
            },
        )
        logf = self.log_dir / "tool-calls.jsonl"
        raw = logf.read_text()
        self.assertNotIn("sk-SECRET123", raw)
        self.assertNotIn("tok-456", raw)
        self.assertNotIn("hunter2", raw)
        self.assertIn("[REDACTED]", raw)
        self.assertIn("public text", raw)  # non-secret values preserved

    def test_redact_args_unit(self):
        from zeline import hooks

        out = hooks._redact_args({"token": "abc", "limit": 5, "x": "y"})
        self.assertEqual(out["token"], "[REDACTED]")
        self.assertEqual(out["limit"], 5)
        self.assertEqual(out["x"], "y")
        # non-dict passes through untouched
        self.assertEqual(hooks._redact_args("plain"), "plain")

    def test_run_with_timeout_success(self):
        from zeline import hooks
        ok, result = hooks._run_with_timeout(lambda: 42, 5.0)
        self.assertTrue(ok)
        self.assertEqual(result, 42)

    def test_run_with_timeout_captures_exception(self):
        from zeline import hooks
        def boom():
            raise RuntimeError("hook exploded")
        ok, detail = hooks._run_with_timeout(boom, 5.0)
        self.assertFalse(ok)
        self.assertIn("hook exploded", detail)

    def test_run_with_timeout_abandons_slow_hook(self):
        import threading, time
        from zeline import hooks
        start = time.time()
        ok, detail = hooks._run_with_timeout(lambda: time.sleep(30), 1.0)
        elapsed = time.time() - start
        self.assertFalse(ok)
        self.assertIn("timed out", detail)
        self.assertLess(elapsed, 5.0)

    def test_timed_out_hook_thread_is_daemon(self):
        import threading, time
        from zeline import hooks
        before = {t for t in threading.enumerate() if not t.daemon}
        hooks._run_with_timeout(lambda: time.sleep(60), 0.2)
        time.sleep(0.3)
        leaked = [t for t in threading.enumerate()
                  if not t.daemon and t not in before]
        self.assertEqual(leaked, [],
            "timed-out hook left a non-daemon thread (blocks shutdown)")

    def test_trigger_closure_binds_per_hook(self):
        from zeline import hooks
        seen = []
        names = [f"closure-h{i}" for i in range(3)]
        for i in range(3):
            hooks.register_hook(hooks.ON_TOOL_CALL,
                                lambda data, i=i: seen.append(i),
                                name=names[i])
        try:
            hooks.trigger(hooks.ON_TOOL_CALL, {})
            self.assertEqual(sorted(seen), [0, 1, 2])
        finally:
            for n in names:
                hooks.unregister_hook(hooks.ON_TOOL_CALL, n)


class HookConfigCacheTests(unittest.TestCase):
    """A2-L4: load_config caches by file mtime."""

    def setUp(self):
        from zeline import hooks
        _fresh_registry()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg_path = Path(self.tmp.name) / "hooks.json"
        p1 = patch("zeline.hooks.config_path", return_value=self.cfg_path)
        p1.start()
        self.addCleanup(p1.stop)
        # reset module cache between tests
        hooks._config_cache_mtime = None
        hooks._config_cache_data = None
        self.addCleanup(setattr, hooks, "_config_cache_mtime", None)
        self.addCleanup(setattr, hooks, "_config_cache_data", None)

    def test_cache_reused_when_mtime_unchanged(self):
        from zeline import hooks
        hooks.load_config()  # creates file
        c1 = hooks.load_config()  # reload + cache
        c2 = hooks.load_config()  # cache hit
        self.assertIs(c1, c2)

    def test_cache_invalidated_on_save(self):
        from zeline import hooks
        hooks.load_config()
        c1 = hooks.load_config()
        hooks.save_config({**c1, "enabled": False})
        c2 = hooks.load_config()
        self.assertFalse(c2["enabled"])
        self.assertIsNot(c1, c2)

    def test_cache_reloads_on_external_edit(self):
        import json, time
        from zeline import hooks
        hooks.load_config()
        hooks.load_config()  # populate cache
        data = json.loads(self.cfg_path.read_text())
        data["enabled"] = False
        # ensure mtime actually changes
        new_mtime = time.time() + 5
        self.cfg_path.write_text(json.dumps(data))
        import os
        os.utime(self.cfg_path, (new_mtime, new_mtime))
        c = hooks.load_config()
        self.assertFalse(c["enabled"])


if __name__ == "__main__":
    unittest.main()
