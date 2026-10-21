"""A collector startup failure cannot orphan an already spawned command."""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock


def test_capture_initialization_failure_terminates_and_untracks_the_child():
    with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"ZELINE_HOME": home}):
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)
        tools = importlib.import_module("zeline.tools")
        output = importlib.import_module("zeline.process_output")
        process = mock.Mock(pid=1234)
        process.poll.return_value = None
        with mock.patch.object(tools.subprocess, "Popen", return_value=process), \
             mock.patch.object(output, "ProcessOutput", side_effect=OSError("collector unavailable")), \
             mock.patch.object(tools, "_terminate_group") as terminate:
            try:
                tools._run_tracked(["fixture"], shell=False, cwd=home, seconds=1, identity="fixture-owner")
            except OSError:
                pass
            else:
                raise AssertionError("collector failure was not propagated")
        terminate.assert_called_once_with(process)
        assert not tools._FG_PROCS.get("fixture-owner")
        process.stdout.close.assert_called_once()


def test_interrupt_handoff_cannot_bypass_the_background_process_limit():
    with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"ZELINE_HOME": home}):
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)
        tools = importlib.import_module("zeline.tools")
        live = mock.Mock()
        live.poll.return_value = None
        tools._BG_JOBS["existing"] = SimpleNamespace(process=live)
        with mock.patch.object(tools.config, "MAX_BACKGROUND_PROCESSES", 1), \
             mock.patch.object(tools, "_bg_log_dir", side_effect=AssertionError("capacity check must precede new log allocation")):
            with TestCase().assertRaisesRegex(RuntimeError, "background.*limit"):
                tools._promote_foreground(mock.Mock(), "fixture-owner", "fixture-command")
        assert len(tools._BG_JOBS) == 1


def test_concurrent_background_launches_share_one_capacity_guard():
    with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"ZELINE_HOME": home}):
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)
        tools = importlib.import_module("zeline.tools")
        entered = threading.Event()
        release = threading.Event()
        attempted_second = threading.Event()
        process = mock.Mock(pid=1234)
        process.poll.return_value = None

        def spawn(*_args, **_kwargs):
            entered.set()
            release.wait(1)
            return process

        def second():
            attempted_second.set()
            return tools._run_shell("fixture-two", Path(home), background=True)

        try:
            with mock.patch.object(tools.config, "MAX_BACKGROUND_PROCESSES", 1), \
                 mock.patch.object(tools.subprocess, "Popen", side_effect=spawn) as popen, \
                 ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(tools._run_shell, "fixture-one", Path(home), None, True)
                if not entered.wait(1):
                    raise AssertionError("first disposable launch did not enter")
                later = pool.submit(second)
                attempted_second.wait(1)
                threading.Event().wait(.1)
                release.set()
                results = [first.result(timeout=2), later.result(timeout=2)]
                assert popen.call_count == 1
                assert sum("started background" in result for result in results) == 1
        finally:
            release.set()
            for job in tools._BG_JOBS.values():
                job.close_log()
            tools._BG_JOBS.clear()
