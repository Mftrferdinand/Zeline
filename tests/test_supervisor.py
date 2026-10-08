"""Tests for zeline.supervisor (background worker orchestration).

The task_runner is ALWAYS injected — no test here may call a real provider.
HOME isolation follows tests/test_goals.py: ZELINE_HOME points at a temp dir
and zeline.* modules are reloaded so config.DATA_DIR is test-local.
"""
import contextlib
import importlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


def fresh_supervisor(home: Path):
    os.environ["ZELINE_HOME"] = str(home)
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    return importlib.import_module("zeline.supervisor")


class FakeZeline:
    """Test double for ``zeline.agent.Zeline``: records constructor kwargs.

    ``send`` returns "ok" without touching any provider. Instances are
    collected thread-safely because workers run on their own threads.
    """

    instances: list["FakeZeline"] = []
    _guard = threading.Lock()

    def __init__(self, **kwargs):
        self.kwargs = dict(kwargs)
        with FakeZeline._guard:
            FakeZeline.instances.append(self)

    def send(self, task, approval_policy=None):
        return "ok"

    @classmethod
    def reset(cls):
        with cls._guard:
            cls.instances = []


class SupervisorBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._saved = os.environ.get("ZELINE_HOME")
        self.supervisor = fresh_supervisor(self.home)
        self._pools = []
        self._events_to_release: list[threading.Event] = []

    def tearDown(self) -> None:
        for event in self._events_to_release:
            event.set()
        for pool in self._pools:
            with contextlib.suppress(Exception):
                pool.shutdown(timeout=5)
        self._tmp.cleanup()
        if self._saved is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)

    def make(self, identity: str, **kwargs):
        pool = self.supervisor.Supervisor(identity, **kwargs)
        self._pools.append(pool)
        return pool

    def patch_zeline_class(self):
        """Swap ``zeline.agent.Zeline`` for FakeZeline; restored on teardown."""
        agent_module = importlib.import_module("zeline.agent")
        saved = agent_module.Zeline
        agent_module.Zeline = FakeZeline
        self.addCleanup(setattr, agent_module, "Zeline", saved)
        FakeZeline.reset()

    def blocking_runner(self):
        """A runner that blocks until the test releases it."""
        event = threading.Event()
        self._events_to_release.append(event)
        calls: list[tuple] = []

        def run(task, grants, wid):
            calls.append((task, grants, wid))
            event.wait(timeout=30)
            return f"done: {task}"

        run.calls = calls  # type: ignore[attr-defined]
        run.event = event  # type: ignore[attr-defined]
        return run

    def wait_for(self, fn, timeout: float = 10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if fn():
                return True
            time.sleep(0.02)
        return bool(fn())

    def registry_path(self, identity: str) -> Path:
        key = self.supervisor._key(identity)
        return self.home / "supervisor" / key / "workers.json"


class TestSpawn(SupervisorBase):
    def test_spawn_returns_immediately_while_worker_runs(self):
        pool = self.make("test:fast-spawn", task_runner=self.blocking_runner())
        wid = pool.spawn("tugas lama")
        # BUKTI perilaku, bukan batas waktu: blocking_runner TIDAK PERNAH
        # kembali sebelum tearDown (event-nya dilepas di sana). Fakta bahwa
        # spawn() sudah kembali dan worker berstatus running membuktikan
        # pemanggil tidak menunggu worker selesai — tanpa assert < N detik
        # yang rapuh di mesin lambat/CI padat.
        self.assertTrue(wid.startswith("w_"))
        self.assertTrue(
            self.wait_for(lambda: pool.get_status(wid)["status"] == "running"),
            "worker never reached running state",
        )

    def test_empty_task_raises_value_error(self):
        pool = self.make("test:empty", task_runner=lambda t, g, w: "x")
        for bad in ("", "   ", None):
            with self.assertRaises(ValueError):
                pool.spawn(bad)

    def test_worker_id_format_and_uniqueness(self):
        pool = self.make("test:ids", task_runner=lambda t, g, w: "ok")
        ids = {pool.spawn(f"task {i}") for i in range(20)}
        self.assertEqual(len(ids), 20)
        for wid in ids:
            self.assertTrue(wid.startswith("w_"), wid)
            self.assertEqual(len(wid), 10, wid)  # "w_" + 8 hex chars

    def test_spawn_after_shutdown_raises(self):
        pool = self.make("test:shutdown-spawn", task_runner=lambda t, g, w: "ok")
        pool.shutdown(timeout=5)
        with self.assertRaises(ValueError):
            pool.spawn("too late")


class TestCompletionAndEvents(SupervisorBase):
    def test_completion_event_exactly_once(self):
        pool = self.make("test:events", task_runner=lambda t, g, w: "hasil riset 42")
        wid = pool.spawn("riset cepat")
        self.assertTrue(
            self.wait_for(lambda: pool.get_status(wid)["status"] == "done"),
            "worker did not finish",
        )
        events = pool.poll_events()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.worker_id, wid)
        self.assertEqual(event.status, "done")
        self.assertIn("42", event.summary)
        # Second poll is empty: events are read-once.
        self.assertEqual(pool.poll_events(), [])

    def test_get_result_carries_full_text(self):
        pool = self.make("test:result", task_runner=lambda t, g, w: "laporan lengkap " * 10)
        wid = pool.spawn("tulis laporan")
        self.assertTrue(self.wait_for(lambda: pool.get_status(wid)["status"] == "done"))
        record = pool.get_result(wid)
        self.assertIn("laporan lengkap", record["result"])
        self.assertEqual(record["status"], "done")
        # list_workers stays compact: no full result text.
        listed = pool.list_workers()
        self.assertEqual(len(listed), 1)
        self.assertNotIn("result", listed[0])

    def test_unknown_worker_returns_none(self):
        pool = self.make("test:unknown", task_runner=lambda t, g, w: "ok")
        self.assertIsNone(pool.get_status("w_nope1234"))
        self.assertIsNone(pool.get_result("w_nope1234"))


class TestVerificationAndRetry(SupervisorBase):
    def test_empty_result_rejected_then_retried_to_success(self):
        calls: list[str] = []

        def flaky(task, grants, wid):
            calls.append(task)
            return "" if len(calls) == 1 else "jawaban final"

        pool = self.make("test:retry-ok", task_runner=flaky)
        wid = pool.spawn("coba lagi")
        self.assertTrue(self.wait_for(lambda: pool.get_status(wid)["status"] == "done"))
        status = pool.get_status(wid)
        self.assertEqual(status["attempts"], 2)
        self.assertEqual(pool.get_result(wid)["result"], "jawaban final")
        events = pool.poll_events()
        self.assertEqual(len(events), 1, "exactly one event after retry-success")

    def test_permanent_empty_failure_after_one_retry(self):
        pool = self.make("test:retry-fail", task_runner=lambda t, g, w: "   ")
        wid = pool.spawn("gagal terus")
        self.assertTrue(self.wait_for(lambda: pool.get_status(wid)["status"] == "failed"))
        status = pool.get_status(wid)
        self.assertEqual(status["attempts"], 2)
        self.assertIn("empty", status["error"].lower())
        events = pool.poll_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].status, "failed")

    def test_accept_if_mismatch_rejects_and_retries(self):
        calls: list[str] = []

        def runner(task, grants, wid):
            calls.append(task)
            return "belum ketemu frasa yang diminta" if len(calls) == 1 else "ada KATA KUNCI di sini"

        pool = self.make("test:accept", task_runner=runner)
        wid = pool.spawn("cari frasa", accept_if="kata kunci")
        self.assertTrue(self.wait_for(lambda: pool.get_status(wid)["status"] == "done"))
        self.assertEqual(pool.get_status(wid)["attempts"], 2)

    def test_accept_if_never_matched_fails_loudly(self):
        pool = self.make(
            "test:accept-fail", task_runner=lambda t, g, w: "jawaban tanpa frasa"
        )
        wid = pool.spawn("cari frasa", accept_if="frasa ajaib")
        self.assertTrue(self.wait_for(lambda: pool.get_status(wid)["status"] == "failed"))
        status = pool.get_status(wid)
        self.assertEqual(status["attempts"], 2)
        self.assertIn("frasa ajaib", status["error"])


class TestCrashIsolation(SupervisorBase):
    def test_worker_crash_fails_loudly_supervisor_survives(self):
        def boom(task, grants, wid):
            raise RuntimeError("provider meledak")

        pool = self.make("test:crash", task_runner=boom)
        wid = pool.spawn("tugas berbahaya")
        self.assertTrue(self.wait_for(lambda: pool.get_status(wid)["status"] == "failed"))
        status = pool.get_status(wid)
        self.assertIn("RuntimeError", status["error"])
        self.assertIn("provider meledak", status["error"])
        # The supervisor is still alive: the next spawn runs fine.
        pool._task_runner = lambda t, g, w: "sehat"
        wid2 = pool.spawn("tugas sehat")
        self.assertTrue(self.wait_for(lambda: pool.get_status(wid2)["status"] == "done"))
        self.assertEqual(pool.get_result(wid2)["result"], "sehat")


class TestConcurrencyCap(SupervisorBase):
    def test_max_workers_queues_overflow(self):
        runner = self.blocking_runner()
        pool = self.make("test:cap", task_runner=runner, max_workers=4)
        ids = [pool.spawn(f"pekerjaan {i}") for i in range(6)]
        statuses = [pool.get_status(wid)["status"] for wid in ids]
        self.assertEqual(statuses.count("running"), 4)
        self.assertEqual(statuses.count("queued"), 2)
        runner.event.set()
        self.assertTrue(
            self.wait_for(
                lambda: all(pool.get_status(wid)["status"] == "done" for wid in ids),
                timeout=15.0,
            ),
            "queued workers did not drain after slots freed",
        )
        self.assertEqual(len(runner.calls), 6)

    def test_thread_safety_20_concurrent_spawns(self):
        pool = self.make("test:race", task_runner=lambda t, g, w: "ok")
        ids: list[str] = []
        errors: list[Exception] = []

        def do_spawn(i: int):
            try:
                ids.append(pool.spawn(f"race task {i}"))
            except Exception as exc:  # noqa: BLE001 — collected, then asserted
                errors.append(exc)

        threads = [threading.Thread(target=do_spawn, args=(i,)) for i in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertEqual(len(set(ids)), 20, "worker ids must be unique under concurrency")
        # Registry on disk is valid JSON with all 20 records.
        raw = self.registry_path("test:race").read_text(encoding="utf-8")
        data = json.loads(raw)
        self.assertEqual(len(data["records"]), 20)


class TestPersistence(SupervisorBase):
    def test_registry_persists_and_reloads(self):
        pool = self.make("test:persist", task_runner=lambda t, g, w: "hasil A")
        wid = pool.spawn("tugas A", grants={"risk": ["read", "write"]}, accept_if="A")
        self.assertTrue(self.wait_for(lambda: pool.get_status(wid)["status"] == "done"))
        path = self.registry_path("test:persist")
        self.assertTrue(path.exists())
        # File is 0600.
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        # A fresh Supervisor on the same identity reads the state back.
        pool2 = self.make("test:persist", task_runner=lambda t, g, w: "hasil A")
        status = pool2.get_status(wid)
        self.assertIsNotNone(status)
        self.assertEqual(status["status"], "done")
        self.assertEqual(pool2.get_result(wid)["result"], "hasil A")

    def test_running_record_becomes_interrupted_on_restart(self):
        pool = self.make("test:restart", task_runner=self.blocking_runner())
        wid = pool.spawn("pekerjaan jalan")
        self.assertTrue(self.wait_for(lambda: pool.get_status(wid)["status"] == "running"))
        # Simulate a process restart: a new Supervisor reads the same registry.
        pool2 = self.make("test:restart", task_runner=lambda t, g, w: "ok")
        status = pool2.get_status(wid)
        self.assertEqual(status["status"], "interrupted")
        self.assertIn("restart", status["error"].lower())
        events = pool2.poll_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].status, "interrupted")

    def test_queued_workers_resume_after_restart(self):
        runner = self.blocking_runner()
        pool = self.make("test:resume", task_runner=runner, max_workers=1)
        first = pool.spawn("pertama")
        second = pool.spawn("kedua")
        self.assertTrue(self.wait_for(lambda: pool.get_status(first)["status"] == "running"))
        self.assertEqual(pool.get_status(second)["status"], "queued")
        # Restart: the running one is interrupted, the queued one resumes.
        pool.shutdown(timeout=0)
        runner.event.set()  # release the old (now orphaned) thread, if any
        pool2 = self.make("test:resume", task_runner=lambda t, g, w: "kedua selesai")
        self.assertEqual(pool2.get_status(first)["status"], "interrupted")
        self.assertTrue(
            self.wait_for(lambda: pool2.get_status(second)["status"] == "done"),
            "queued worker did not resume after restart",
        )

    def test_queued_workers_wait_for_bind_with_default_runner(self):
        # A queued worker must NOT fail loudly at construction when the
        # default runner has no bound context yet: it waits, then resumes on
        # the first bind() — approved work is deferred, never destroyed.
        runner = self.blocking_runner()
        pool = self.make("test:defer", task_runner=runner, max_workers=1)
        first = pool.spawn("pertama")
        second = pool.spawn("kedua")
        self.assertTrue(self.wait_for(lambda: pool.get_status(first)["status"] == "running"))
        self.assertEqual(pool.get_status(second)["status"], "queued")
        pool.shutdown(timeout=0)
        runner.event.set()  # release the old (now orphaned) thread, if any
        # "Restart" with the DEFAULT runner and no bind yet.
        pool2 = self.make("test:defer")
        self.assertEqual(pool2.get_status(first)["status"], "interrupted")
        self.assertEqual(pool2.get_status(second)["status"], "queued")
        time.sleep(0.5)
        status = pool2.get_status(second)
        self.assertEqual(status["status"], "queued")
        self.assertEqual(status["attempts"], 0)
        self.assertEqual(status["error"], "")
        # Bind (then supply a runner): the queued worker resumes and finishes.
        pool2.set_task_runner(lambda t, g, w: "kedua selesai")
        pool2.bind(profile="test", workspace="/tmp", depth=0)
        self.assertTrue(
            self.wait_for(lambda: pool2.get_status(second)["status"] == "done"),
            "queued worker did not resume after bind",
        )
        self.assertEqual(pool2.get_result(second)["result"], "kedua selesai")

    def test_corrupt_registry_starts_empty(self):
        path = self.registry_path("test:corrupt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json", encoding="utf-8")
        pool = self.make("test:corrupt", task_runner=lambda t, g, w: "ok")
        self.assertEqual(pool.list_workers(), [])

    def test_shutdown_marks_running_interrupted(self):
        pool = self.make("test:shutdown", task_runner=self.blocking_runner())
        wid = pool.spawn("tidak selesai")
        self.assertTrue(self.wait_for(lambda: pool.get_status(wid)["status"] == "running"))
        pool.shutdown(timeout=0.2)
        status = pool.get_status(wid)
        self.assertEqual(status["status"], "interrupted")
        events = pool.poll_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].status, "interrupted")


class TestGrants(SupervisorBase):
    def test_default_grants_are_read_only(self):
        seen: list[dict] = []

        def capture(task, grants, wid):
            seen.append(grants)
            return "ok"

        pool = self.make("test:grants-default", task_runner=capture)
        wid = pool.spawn("baca saja")
        self.assertTrue(self.wait_for(lambda: pool.get_status(wid)["status"] == "done"))
        self.assertEqual(seen[0], {"tools": [], "risk": ["read"]})

    def test_grants_normalized_fail_closed(self):
        seen: list[dict] = []

        def capture(task, grants, wid):
            seen.append(grants)
            return "ok"

        pool = self.make("test:grants-norm", task_runner=capture)
        wid = pool.spawn(
            "tulis sesuatu",
            grants={"risk": ["READ", "write", "nonsense"], "tools": ["run_shell", ""]},
        )
        self.assertTrue(self.wait_for(lambda: pool.get_status(wid)["status"] == "done"))
        self.assertEqual(seen[0], {"tools": ["run_shell"], "risk": ["read", "write"]})

    def test_explicit_empty_grants_stay_empty(self):
        seen: list[dict] = []

        def capture(task, grants, wid):
            seen.append(grants)
            return "ok"

        pool = self.make("test:grants-empty", task_runner=capture)
        wid = pool.spawn("tanpa izin", grants={"risk": [], "tools": []})
        self.assertTrue(self.wait_for(lambda: pool.get_status(wid)["status"] == "done"))
        self.assertEqual(seen[0], {"tools": [], "risk": []})

    def test_worker_identity_is_distinct_per_worker(self):
        seen: list[str] = []

        def capture(task, grants, wid):
            seen.append(wid)
            return "ok"

        pool = self.make("test:wid", task_runner=capture)
        first = pool.spawn("satu")
        second = pool.spawn("dua")
        self.assertTrue(
            self.wait_for(
                lambda: pool.get_status(first)["status"] == "done"
                and pool.get_status(second)["status"] == "done"
            )
        )
        self.assertEqual(len(set(seen)), 2)
        self.assertTrue(all(wid.startswith("test:wid::wkr") for wid in seen))


class TestModuleRegistry(SupervisorBase):
    def test_get_supervisor_caches_per_identity(self):
        first = self.supervisor.get_supervisor("test:cache-a")
        second = self.supervisor.get_supervisor("test:cache-a")
        self.assertIs(first, second)
        other = self.supervisor.get_supervisor("test:cache-b")
        self.assertIsNot(first, other)
        self._pools.extend([first, other])

    def test_get_supervisor_takes_no_kwargs(self):
        # Regression guard for the cache-poisoning defect: context kwargs on
        # a cached lookup were silently ignored after the first call. Now
        # they are rejected loudly — context goes through bind(), never
        # through the cache.
        with self.assertRaises(TypeError):
            self.supervisor.get_supervisor("test:cache-kw", task_runner=lambda t, g, w: "ok")  # type: ignore[call-arg]

    def test_format_completions_indonesian(self):
        done = self.supervisor.WorkerEvent(
            worker_id="w_abc12345",
            task="riset harga emas",
            status="done",
            summary="harga naik 2%",
            finished_at=1.0,
        )
        failed = self.supervisor.WorkerEvent(
            worker_id="w_def67890",
            task="cek stok",
            status="failed",
            error="timeout",
            finished_at=2.0,
        )
        text = self.supervisor.format_completions([done, failed])
        self.assertIn("w_abc12345", text)
        self.assertIn("selesai", text)
        self.assertIn("harga naik 2%", text)
        self.assertIn("w_def67890", text)
        self.assertIn("gagal", text)
        self.assertIn("timeout", text)
        self.assertEqual(self.supervisor.format_completions([]), "")


class TestToolWiring(SupervisorBase):
    """The three tools exist with the right risk classes, profiles, and behavior."""

    def _tools(self):
        tools = importlib.import_module("zeline.tools")
        return tools

    def test_risk_classes(self):
        tools = self._tools()
        risks = {d.name: d.risk for d in tools.TOOL_DEFS}
        # Install-class: spawning a persistent unattended worker always asks,
        # and the declared worker grants are shown in the approval question.
        self.assertEqual(risks["spawn_worker"], tools.ToolRisk.INSTALL)
        self.assertEqual(risks["worker_status"], tools.ToolRisk.READ)
        self.assertEqual(risks["worker_result"], tools.ToolRisk.READ)

    def test_profiles_workspace_and_full_only(self):
        tools = self._tools()
        defs = {d.name: d for d in tools.TOOL_DEFS}
        for name in ("spawn_worker", "worker_status", "worker_result"):
            self.assertEqual(defs[name].profiles, frozenset({"workspace", "full"}))

    def test_spawn_worker_disabled_at_max_depth(self):
        tools = self._tools()
        leaf = tools.ToolExecutor("cli:leaf", profile="full", depth=99)
        names = {d.name for d in leaf._enabled_native_defs()}
        self.assertNotIn("spawn_worker", names)
        root = tools.ToolExecutor("cli:root", profile="full", depth=0)
        self.assertIn("spawn_worker", {d.name for d in root._enabled_native_defs()})

    def test_wrapper_rejects_empty_task_with_error_string(self):
        tools = self._tools()
        executor = tools.ToolExecutor(
            "test:wrapper", profile="full", workspace=str(self.home), depth=0
        )
        out = executor._spawn_worker("")
        self.assertTrue(out.startswith("ERROR:"), out)

    def test_wrapper_spawns_and_reports(self):
        tools = self._tools()
        executor = tools.ToolExecutor(
            "test:wrapper-run", profile="full", workspace=str(self.home), depth=0
        )
        # The wrapper enforces the worker-grant cap against the installed
        # policy: a direct call needs the production-like interactive context.
        executor.approval_policy = tools.InteractiveApprovalPolicy()
        # Route the module-level supervisor registry at this identity through
        # an injected runner so no real provider is touched.
        runner = lambda t, g, w: "hasil worker"  # noqa: E731 — throwaway test double
        pool = self.supervisor.get_supervisor("test:wrapper-run")
        pool.set_task_runner(runner)
        self._pools.append(pool)
        out = executor._spawn_worker("kerjakan sesuatu")
        self.assertIn("w_", out)
        self.assertIn("do NOT poll", out)
        wid = out.split("Worker ")[1].split(" ")[0]
        self.assertTrue(
            self.wait_for(lambda: pool.get_status(wid)["status"] == "done")
        )
        status_out = executor._worker_status(wid)
        self.assertIn(wid, status_out)
        self.assertIn("done", status_out)
        result_out = executor._worker_result(wid)
        self.assertIn("hasil worker", result_out)
        # Unknown id and empty id are loud errors, not tracebacks.
        self.assertTrue(executor._worker_status("w_tidakada").startswith("ERROR:"))
        self.assertTrue(executor._worker_result("w_tidakada").startswith("ERROR:"))
        self.assertTrue(executor._worker_result("").startswith("ERROR:"))
        # worker_status with no workers at a fresh identity:
        fresh_executor = tools.ToolExecutor(
            "test:wrapper-fresh", profile="full", workspace=str(self.home), depth=0
        )
        self.assertEqual(fresh_executor._worker_status(""), "No background workers.")

    def test_worker_result_while_running(self):
        tools = self._tools()
        executor = tools.ToolExecutor(
            "test:wrapper-busy", profile="full", workspace=str(self.home), depth=0
        )
        gate = threading.Event()
        self._events_to_release.append(gate)

        def slow(task, grants, wid):
            gate.wait(timeout=30)
            return "akhirnya"

        pool = self.supervisor.get_supervisor("test:wrapper-busy")
        pool.set_task_runner(slow)
        self._pools.append(pool)
        wid = pool.spawn("lambat")
        self.assertTrue(self.wait_for(lambda: pool.get_status(wid)["status"] == "running"))
        out = executor._worker_result(wid)
        self.assertIn("still running", out)


class TestAgentDrain(SupervisorBase):
    """Mirror the agent.py turn-entry drain: bind fresh context, then drain."""

    def test_drain_injects_completion_block(self):
        identity = "test:drain"
        runner = lambda t, g, w: "ringkasan penting"  # noqa: E731 — throwaway test double
        pool = self.supervisor.get_supervisor(identity)
        pool.set_task_runner(runner)
        self._pools.append(pool)
        wid = pool.spawn("tugas latar")
        self.assertTrue(self.wait_for(lambda: pool.get_status(wid)["status"] == "done"))
        # This mirrors zeline/agent.py send(): refresh the runner context on
        # every turn, then drain events into the turn's ephemeral skill
        # context (never persisted to history).
        pool.bind(profile="full", workspace=str(self.home), depth=0)
        block = self.supervisor.drain_completion_block(identity)
        turn_skill_context = ""
        if block:
            turn_skill_context = "\n\n".join(
                part for part in (turn_skill_context, block) if part
            )
        self.assertIn(wid, turn_skill_context)
        self.assertIn("selesai", turn_skill_context)
        self.assertIn("ringkasan penting", turn_skill_context)
        # Drained: a second turn sees nothing.
        self.assertEqual(self.supervisor.drain_completion_block(identity), "")


class TestBindContext(SupervisorBase):
    """Defect 1 regression: the runner context must come from the latest
    bind(), never from a silently-ignored cache lookup."""

    def test_spawn_uses_latest_bound_context(self):
        identity = "test:bind-context"
        # Production order: the drain happens first (get_supervisor, no bind).
        sup = self.supervisor.get_supervisor(identity)
        self._pools.append(sup)
        self.assertEqual(sup.poll_events(), [])
        self.patch_zeline_class()
        # Bind, then spawn with the DEFAULT runner (task_runner=None) and a
        # mocked Zeline that records its constructor kwargs.
        sup.bind(profile="workspace", workspace="/ws/asli", depth=2)
        wid = sup.spawn("tugas satu")
        self.assertTrue(
            self.wait_for(lambda: sup.get_status(wid)["status"] == "done"),
            "worker did not finish with the default runner",
        )
        self.assertEqual(len(FakeZeline.instances), 1)
        first = FakeZeline.instances[0].kwargs
        self.assertEqual(first["workspace"], "/ws/asli")
        self.assertEqual(first["tool_profile"], "workspace")
        self.assertEqual(first["depth"], 3)  # bound depth 2 + 1
        # Re-bind with different values: the next spawn uses the NEWEST
        # context — the second bind is not silently ignored.
        sup.bind(profile="full", workspace="/ws/baru", depth=5)
        wid2 = sup.spawn("tugas dua")
        self.assertTrue(
            self.wait_for(lambda: sup.get_status(wid2)["status"] == "done"),
            "second worker did not finish",
        )
        self.assertEqual(len(FakeZeline.instances), 2)
        second = FakeZeline.instances[1].kwargs
        self.assertEqual(second["workspace"], "/ws/baru")
        self.assertEqual(second["tool_profile"], "full")
        self.assertEqual(second["depth"], 6)

    def test_unbound_default_runner_fails_loudly(self):
        self.patch_zeline_class()
        pool = self.make("test:unbound")  # task_runner=None, bind() never called
        wid = pool.spawn("tugas tanpa konteks")
        self.assertTrue(
            self.wait_for(lambda: pool.get_status(wid)["status"] == "failed"),
            "unbound default runner must fail, not run in a default workspace",
        )
        status = pool.get_status(wid)
        self.assertIn("runner context not bound", status["error"])
        # Nothing was ever built: the worker never ran anywhere.
        self.assertEqual(FakeZeline.instances, [])


class TestGrantsFailClosed(SupervisorBase):
    """Defect 2 regression: malformed grants fail closed, never widen."""

    def test_non_dict_grants_become_strict_read_only(self):
        seen: list[dict] = []

        def capture(task, grants, wid):
            seen.append(grants)
            return "ok"

        pool = self.make("test:grants-malformed", task_runner=capture)
        for malformed in ("read", ["read"], 42, object()):
            seen.clear()
            wid = pool.spawn("tugas", grants=malformed)
            self.assertTrue(
                self.wait_for(lambda: pool.get_status(wid)["status"] == "done"),
                f"worker with grants={malformed!r} did not finish",
            )
            self.assertEqual(
                seen[0],
                {"tools": [], "risk": ["read"]},
                f"malformed grants {malformed!r} must fail closed to read-only",
            )


class TestConcurrentPoll(SupervisorBase):
    """T1: concurrent poll_events must deliver each event exactly once."""

    def test_concurrent_poll_no_loss_no_dup(self):
        total = 30
        pool = self.make("test:poll-race", task_runner=lambda t, g, w: "ok")
        ids = [pool.spawn(f"job {i}") for i in range(total)]
        self.assertTrue(
            self.wait_for(
                lambda: all(pool.get_status(i)["status"] == "done" for i in ids),
                timeout=20.0,
            ),
            "workers did not finish",
        )
        collected: list = []
        guard = threading.Lock()
        deadline = time.monotonic() + 10.0

        def grab():
            while time.monotonic() < deadline:
                events = pool.poll_events()
                with guard:
                    collected.extend(events)
                    if len(collected) >= total:
                        return
                if not events:
                    time.sleep(0.001)

        threads = [threading.Thread(target=grab) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=12)
        with guard:
            got = list(collected)
        self.assertEqual(len(got), total, "events lost or duplicated under concurrency")
        self.assertEqual(len({event.worker_id for event in got}), total)


class TestPartialRegistry(SupervisorBase):
    """T5: one corrupt record must not nuke the registry; the good one loads."""

    def test_corrupt_record_skipped_good_one_loads(self):
        path = self.registry_path("test:partial")
        path.parent.mkdir(parents=True, exist_ok=True)
        good = {
            "id": "w_good0001",
            "task": "tugas bagus",
            "status": "done",
            "created_at": 1.0,
            "finished_at": 2.0,
            "result": "hasil",
            "attempts": 1,
            # grants deliberately missing -> strict read-only default
        }
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "records": {
                        "w_good0001": good,
                        "w_bad00002": "bukan dict",
                        "w_noid0003": {"task": "tanpa id"},
                    },
                }
            ),
            encoding="utf-8",
        )
        pool = self.make("test:partial", task_runner=lambda t, g, w: "ok")
        self.assertIsNotNone(pool.get_status("w_good0001"))
        self.assertEqual(pool.get_result("w_good0001")["result"], "hasil")
        self.assertIsNone(pool.get_status("w_bad00002"))
        self.assertIsNone(pool.get_status("w_noid0003"))
        # Missing grants normalized to the strict read-only default.
        self.assertEqual(
            pool._records["w_good0001"].grants, {"tools": [], "risk": ["read"]}
        )


class TestQueueCap(SupervisorBase):
    """Queue cap: a full queue fails loudly instead of growing unbounded."""

    def test_queue_full_raises_and_wrapper_reports_error(self):
        identity = "test:queue-full"
        runner = self.blocking_runner()
        sup = self.supervisor.get_supervisor(identity)
        sup.set_task_runner(runner)
        # Force everything after the first spawn into the queue.
        sup.max_workers = 1
        self._pools.append(sup)
        first = sup.spawn("berjalan")
        self.assertTrue(
            self.wait_for(lambda: sup.get_status(first)["status"] == "running")
        )
        for i in range(self.supervisor.MAX_QUEUED):
            sup.spawn(f"antre {i}")
        with self.assertRaises(ValueError) as ctx:
            sup.spawn("satu lagi")
        self.assertIn("queue full", str(ctx.exception))
        # The tool wrapper surfaces the same failure as a clear ERROR string.
        tools = importlib.import_module("zeline.tools")
        executor = tools.ToolExecutor(
            identity, profile="full", workspace=str(self.home), depth=0
        )
        executor.approval_policy = tools.InteractiveApprovalPolicy()
        out = executor._spawn_worker("satu lagi")
        self.assertTrue(out.startswith("ERROR:"), out)
        self.assertIn("queue full", out)


class TestWrapperBindOrder(SupervisorBase):
    """T3: the wrapper binds the executor's CURRENT context on every call —
    the defect-1 production sequence must keep working."""

    def test_wrapper_binds_before_spawn(self):
        identity = "test:wrapper-bind"
        # Production order: a drain happens first — get_supervisor alone,
        # no bind — then the model calls spawn_worker through the executor.
        drained = self.supervisor.get_supervisor(identity)
        self._pools.append(drained)
        self.assertEqual(drained.poll_events(), [])
        self.patch_zeline_class()
        tools = importlib.import_module("zeline.tools")
        executor = tools.ToolExecutor(
            identity, profile="workspace", workspace="/ws/produksi", depth=2
        )
        # Direct wrapper call: install the production-like interactive policy
        # so the worker-grant cap check sees an auditable caller context.
        executor.approval_policy = tools.InteractiveApprovalPolicy()
        out = executor._spawn_worker("tugas penting")
        self.assertIn("Worker w_", out)
        wid = out.split("Worker ")[1].split(" ")[0]
        self.assertTrue(
            self.wait_for(lambda: drained.get_status(wid)["status"] == "done"),
            "worker did not finish",
        )
        self.assertEqual(len(FakeZeline.instances), 1)
        kwargs = FakeZeline.instances[0].kwargs
        self.assertEqual(kwargs["workspace"], "/ws/produksi")
        self.assertEqual(kwargs["tool_profile"], "workspace")
        self.assertEqual(kwargs["depth"], 3)  # executor depth 2 + 1


class TestTerminalRetention(SupervisorBase):
    """B3: terminal records are retained up to MAX_TERMINAL_RECORDS; live
    records and undelivered events are never pruned."""

    def _spawn_many(self, pool, count, prefix):
        """Spawn in small batches so the queue cap never trips."""
        ids = []
        for i in range(0, count, 20):
            batch = [pool.spawn(f"{prefix} {i + j}") for j in range(min(20, count - i))]
            ids.extend(batch)
            self.assertTrue(
                self.wait_for(
                    lambda: all(pool.get_status(w)["status"] in ("done", "failed") for w in batch),
                    timeout=30.0,
                ),
                "workers did not finish",
            )
        return ids

    def test_terminal_records_pruned_to_cap(self):
        cap = self.supervisor.MAX_TERMINAL_RECORDS
        pool = self.make("test:retention", task_runner=lambda t, g, w: "ok", max_workers=16)
        ids = self._spawn_many(pool, cap + 5, "job")
        self.assertEqual(
            len([r for r in pool._records.values() if r.status == "done"]),
            cap + 5,
            "undelivered events pin their records: nothing pruned before the poll",
        )
        pool.poll_events()
        terminal = [
            r
            for r in pool._records.values()
            if r.status in ("done", "failed", "interrupted")
        ]
        self.assertEqual(len(terminal), cap)
        # The survivors are delivered (eligible for pruning); the pruned
        # ones were the oldest finished.
        self.assertTrue(all(r.event_delivered for r in terminal))

    def test_running_and_queued_never_pruned(self):
        cap = self.supervisor.MAX_TERMINAL_RECORDS
        pool = self.make(
            "test:retention-live", task_runner=lambda t, g, w: "ok", max_workers=16
        )
        self._spawn_many(pool, cap, "job")
        pool.poll_events()  # deliver -> the cap is now exactly full
        gate = self.blocking_runner()
        pool.set_task_runner(gate)
        pool.max_workers = 2
        running_ids = [pool.spawn(f"live {i}") for i in range(2)]
        queued_id = pool.spawn("waiting")
        self.assertTrue(
            self.wait_for(
                lambda: all(
                    pool.get_status(w)["status"] == "running" for w in running_ids
                )
            )
        )
        self.assertEqual(pool.get_status(queued_id)["status"], "queued")
        # Another persist (via this poll) must not touch live records, even
        # though the terminal count sits exactly at the cap.
        pool.poll_events()
        for w in running_ids:
            self.assertEqual(pool.get_status(w)["status"], "running")
        self.assertEqual(pool.get_status(queued_id)["status"], "queued")
        gate.event.set()

    def test_failed_records_count_toward_retention(self):
        cap = self.supervisor.MAX_TERMINAL_RECORDS
        pool = self.make("test:retention-fail", task_runner=lambda t, g, w: "", max_workers=16)
        self._spawn_many(pool, cap + 3, "job")
        pool.poll_events()
        terminal = [
            r
            for r in pool._records.values()
            if r.status in ("done", "failed", "interrupted")
        ]
        self.assertEqual(len(terminal), cap)


class TestBindAfterShutdown(SupervisorBase):
    """M2: bind() after shutdown() must not resurrect queued workers."""

    def test_bind_after_shutdown_is_noop(self):
        runner = self.blocking_runner()
        pool = self.make("test:bind-shutdown", task_runner=runner, max_workers=1)
        first = pool.spawn("satu")
        second = pool.spawn("dua")  # queued: no free slot
        self.assertTrue(
            self.wait_for(lambda: pool.get_status(first)["status"] == "running")
        )
        self.assertEqual(pool.get_status(second)["status"], "queued")
        pool.shutdown(timeout=0)
        self.assertEqual(pool.get_status(first)["status"], "interrupted")
        self.assertEqual(pool.get_status(second)["status"], "queued")
        # A bind after shutdown must NOT drain the queue into running.
        pool.bind(profile="full", workspace="/x", depth=0)
        time.sleep(0.5)
        self.assertEqual(pool.get_status(second)["status"], "queued")
        runner.event.set()


class TestRestartEventRedelivery(SupervisorBase):
    """M3: completion events survive a restart, delivered exactly once."""

    def test_undelivered_event_reenqueued_on_restart(self):
        identity = "test:redeliver"
        pool = self.make(identity, task_runner=lambda t, g, w: "hasil penting")
        wid = pool.spawn("tugas penting")
        self.assertTrue(
            self.wait_for(lambda: pool.get_status(wid)["status"] == "done")
        )
        # Deliberately NO poll: the event is still in-memory only.
        # Simulate a process restart: a fresh Supervisor on the same files.
        pool2 = self.make(identity, task_runner=lambda t, g, w: "hasil penting")
        events = pool2.poll_events()
        self.assertEqual(len(events), 1, "the undelivered event must be re-enqueued")
        event = events[0]
        self.assertEqual(event.worker_id, wid)
        self.assertEqual(event.status, "done")
        self.assertIn("hasil penting", event.summary)
        # Exactly once: a second poll — and a third Supervisor — see nothing.
        self.assertEqual(pool2.poll_events(), [])
        pool3 = self.make(identity, task_runner=lambda t, g, w: "hasil penting")
        self.assertEqual(pool3.poll_events(), [])

    def test_delivered_event_not_reemitted_after_restart(self):
        identity = "test:no-redeliver"
        pool = self.make(identity, task_runner=lambda t, g, w: "sudah dibaca")
        wid = pool.spawn("tugas dibaca")
        self.assertTrue(
            self.wait_for(lambda: pool.get_status(wid)["status"] == "done")
        )
        events = pool.poll_events()
        self.assertEqual(len(events), 1)
        # Restart after a poll: nothing comes back.
        pool2 = self.make(identity, task_runner=lambda t, g, w: "sudah dibaca")
        self.assertEqual(pool2.poll_events(), [])

    def test_failed_event_redelivered(self):
        identity = "test:redeliver-fail"
        pool = self.make(identity, task_runner=lambda t, g, w: "")
        wid = pool.spawn("tugas gagal")
        self.assertTrue(
            self.wait_for(lambda: pool.get_status(wid)["status"] == "failed")
        )
        pool2 = self.make(identity, task_runner=lambda t, g, w: "")
        events = pool2.poll_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].worker_id, wid)
        self.assertEqual(events[0].status, "failed")


class TestWorkerTimeout(SupervisorBase):
    """M4: a hung worker is failed with a timeout, evented, slot freed."""

    def test_timeout_fails_worker_and_frees_slot(self):
        def hang_forever(task, grants, wid):
            time.sleep(60)
            return "too late"

        pool = self.make("test:timeout", task_runner=hang_forever, max_workers=1)
        wid = pool.spawn("tugas macet", timeout=2)
        self.assertTrue(
            self.wait_for(lambda: pool.get_status(wid)["status"] == "failed", timeout=10.0),
            "the timeout did not fire",
        )
        status = pool.get_status(wid)
        self.assertIn("timeout", status["error"].lower())
        events = pool.poll_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].worker_id, wid)
        self.assertEqual(events[0].status, "failed")
        self.assertIn("timeout", events[0].error.lower())
        # The slot is free: the next spawn starts running immediately.
        pool.set_task_runner(lambda t, g, w: "cepat")
        wid2 = pool.spawn("tugas cepat")
        self.assertEqual(pool.get_status(wid2)["status"], "running")
        self.assertTrue(
            self.wait_for(lambda: pool.get_status(wid2)["status"] == "done"),
            "the freed slot did not run the next worker",
        )

    def test_timeout_not_triggered_for_fast_worker(self):
        pool = self.make("test:timeout-ok", task_runner=lambda t, g, w: "selesai")
        wid = pool.spawn("tugas cepat", timeout=30)
        self.assertTrue(
            self.wait_for(lambda: pool.get_status(wid)["status"] == "done")
        )
        self.assertEqual(pool.get_status(wid)["status"], "done")

    def test_non_positive_timeout_rejected(self):
        pool = self.make("test:timeout-bad", task_runner=lambda t, g, w: "ok")
        for bad in (0, -5, -0.5):
            with self.assertRaises(ValueError):
                pool.spawn("tugas", timeout=bad)

    def test_default_timeout_is_twelve_minutes(self):
        self.assertEqual(self.supervisor.WORKER_TIMEOUT_DEFAULT, 12 * 60)
        pool = self.make("test:timeout-default", task_runner=lambda t, g, w: "ok")
        wid = pool.spawn("tugas")
        self.assertEqual(pool._records[wid].timeout, 12 * 60)


class TestDrainCompletionBlock(SupervisorBase):
    """drain_completion_block: a block for pending events, "" when empty."""

    def test_block_for_events_empty_when_none(self):
        identity = "test:drain-block"
        sup = self.supervisor.get_supervisor(identity)
        sup.set_task_runner(lambda t, g, w: "ringkasan block")
        self._pools.append(sup)
        # Empty first: nothing drained, nothing reported.
        self.assertEqual(self.supervisor.drain_completion_block(identity), "")
        wid = sup.spawn("tugas block")
        self.assertTrue(
            self.wait_for(lambda: sup.get_status(wid)["status"] == "done")
        )
        block = self.supervisor.drain_completion_block(identity)
        self.assertIn(wid, block)
        self.assertIn("selesai", block)
        self.assertIn("ringkasan block", block)
        # Second drain is empty: events are read-once.
        self.assertEqual(self.supervisor.drain_completion_block(identity), "")


class TestLazyStateDir(SupervisorBase):
    """n7: supervisor tidak boleh meninggalkan direktori state kosong."""

    def test_no_state_dir_without_workers(self):
        identity = "test:lazy-dir"
        pool = self.make(identity)  # konstruktor: tidak boleh mkdir
        pool.bind(profile="full", workspace=".", depth=0)  # bind: tidak boleh mkdir
        # drain completion (jalur send()): tetap jalan, tetap tidak mkdir.
        self.assertEqual(self.supervisor.drain_completion_block(identity), "")
        self.assertFalse(self.registry_path(identity).parent.exists())

    def test_state_dir_created_on_spawn(self):
        identity = "test:lazy-dir-spawn"
        pool = self.make(identity, task_runner=lambda t, g, w: "ok")
        pool.bind(profile="full", workspace=".", depth=0)
        wid = pool.spawn("tugas")
        self.assertTrue(
            self.wait_for(lambda: pool.get_status(wid)["status"] == "done")
        )
        self.assertTrue(self.registry_path(identity).exists())


class TestVerifyResultSignature(SupervisorBase):
    """n13: _verify_result tanpa parameter task yang tak terpakai."""

    def test_rejects_empty_result(self):
        ok, reason = self.supervisor._verify_result("", "")
        self.assertFalse(ok)
        self.assertIn("empty", reason)

    def test_rejects_missing_accept_phrase(self):
        ok, reason = self.supervisor._verify_result("hasil kerja", "kata-kunci-wajib")
        self.assertFalse(ok)
        self.assertIn("kata-kunci-wajib", reason)

    def test_accepts_result_mentioning_phrase(self):
        ok, reason = self.supervisor._verify_result(
            "hasil kerja menyebut kata-kunci-wajib di sini", "kata-kunci-wajib"
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "")


class TestActualAttemptsOnCrash(SupervisorBase):
    """n14: except terluar mencatat attempts AKTUAL, bukan MAX_ATTEMPTS."""

    def test_last_resort_records_actual_attempts(self):
        real_complete = self.supervisor.Supervisor._complete_locked
        calls = {"n": 0}

        def flaky_complete(slf, wid, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("simulasi crash di _complete_locked")
            return real_complete(slf, wid, **kwargs)

        pool = self.make("test:attempts-actual", task_runner=lambda t, g, w: "ok")
        with mock.patch.object(
            self.supervisor.Supervisor, "_complete_locked", flaky_complete
        ):
            wid = pool.spawn("tugas kilat")
            settled = self.wait_for(
                lambda: pool.get_status(wid)["status"] in ("done", "failed")
            )
        self.assertTrue(settled, "worker never settled")
        status = pool.get_status(wid)
        # Runner sukses di percobaan pertama (attempts=1); crash terjadi di
        # _complete_locked SETELAH loop — last-resort harus mencatat 1,
        # bukan MAX_ATTEMPTS (=2).
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["attempts"], 1)


class TestPersistFailureLogged(SupervisorBase):
    """n15: gagal persist dicatat via logging, tidak ditelan diam-diam."""

    def test_persist_oserror_logged_and_spawn_survives(self):
        pool = self.make("test:persist-log", task_runner=lambda t, g, w: "ok")
        real_mkdir = Path.mkdir

        def flaky_mkdir(self, *args, **kwargs):
            if "supervisor" in self.parts:
                raise OSError("mkdir gagal (simulasi)")
            return real_mkdir(self, *args, **kwargs)

        with mock.patch.object(Path, "mkdir", flaky_mkdir):
            with self.assertLogs("zeline.supervisor", level="WARNING") as logs:
                wid = pool.spawn("tugas cepat")
        self.assertTrue(
            any("persist failed" in message for message in logs.output),
            f"no persist-failure warning logged: {logs.output}",
        )
        # Spawn tetap jalan: state in-memory benar walau disk gagal.
        self.assertTrue(
            self.wait_for(lambda: pool.get_status(wid)["status"] == "done")
        )


class TestGrantsDeepCopy(SupervisorBase):
    """n18: runner menerima deep copy grants — mutasi runner tidak boleh
    merusak record yang dipersist."""

    def test_runner_grant_mutation_isolated_from_record(self):
        def evil_runner(task, grants, wid):
            grants["tools"].append("write_file")
            grants["risk"].append("destructive")
            return "ok"

        pool = self.make("test:grants-copy", task_runner=evil_runner)
        wid = pool.spawn("tugas", grants={"tools": ["read_file"], "risk": ["read"]})
        self.assertTrue(
            self.wait_for(lambda: pool.get_status(wid)["status"] == "done")
        )
        record = pool._records[wid]
        self.assertEqual(record.grants, {"tools": ["read_file"], "risk": ["read"]})


if __name__ == "__main__":
    unittest.main()
