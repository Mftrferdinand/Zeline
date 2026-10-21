"""Bounded worker ownership persists after an interrupted batch returns."""
import threading
import time


def test_cancel_does_not_join_and_late_worker_keeps_its_slot():
    from zeline.parallel_runtime import ParallelRuntime
    runtime = ParallelRuntime(1)
    stop = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    calls = []

    def invoke(value, cancelled):
        calls.append(value)
        stop.set()
        try:
            release.wait(2)
            assert cancelled()
            return value
        finally:
            finished.set()

    try:
        began = time.monotonic()
        first = runtime.run([1, 2], invoke, stop.is_set, time.monotonic() + 1)
        assert first.reason == 'cancelled'
        assert time.monotonic() - began < .5
        second = runtime.run([3], invoke, lambda: False, time.monotonic() + .05)
        assert second.reason == 'deadline'
        assert calls == [1]
    finally:
        release.set()
        assert finished.wait(3)


def test_results_are_indexed_and_failure_preserves_completed_evidence():
    from zeline.parallel_runtime import ParallelRuntime
    runtime = ParallelRuntime(1)
    def invoke(value, _cancelled):
        if value == 2:
            raise ValueError('fixture')
        return str(value)
    result = runtime.run([1, 2, 3], invoke, lambda: False, time.monotonic() + 1)
    assert result.results == {0: '1'}
    assert isinstance(result.error, ValueError)


def test_already_cancelled_batch_starts_no_workers():
    from zeline.parallel_runtime import ParallelRuntime
    calls = []
    result = ParallelRuntime(2).run([1, 2], lambda *_: calls.append(1), lambda: True, time.monotonic()+1)
    assert result.reason == 'cancelled'
    assert calls == []
