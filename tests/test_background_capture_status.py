"""A promoted collector failure must remain visible through process status."""
import importlib
from types import SimpleNamespace


def test_background_status_reports_capture_error_without_exception_payload():
    tools = importlib.import_module('zeline.tools')
    job = SimpleNamespace(process=SimpleNamespace(poll=lambda: None),
                          capture=SimpleNamespace(error=OSError('private error detail')))
    status = tools._bg_status(job)
    assert 'output capture failed' in status
    assert 'private error detail' not in status


def test_legacy_background_status_without_collector_is_unchanged():
    tools = importlib.import_module('zeline.tools')
    job = SimpleNamespace(process=SimpleNamespace(poll=lambda: 7))
    assert tools._bg_status(job) == 'exited (exit=7)'
