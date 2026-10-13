"""Pytest isolation for tests that reload :mod:`zeline` modules.

A number of tests reload configuration by evicting ``zeline.*`` from
``sys.modules``. Pytest, however, imports connector test modules during
collection; those modules retain references to the original connector/store
objects. Without restoring the package registry at each test boundary, a later
test can patch a newly imported ``zeline.connectors.store`` while the connector
under test still uses the old one. The resulting failures depend on test order,
not product behavior.

Python packages also cache imported child modules as attributes. Restoring only
``sys.modules`` is not enough: ``from zeline import gateway_service`` can still
return an evicted module cached on the ``zeline`` package. Restore both forms of
module identity after every test.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


# Tests regularly unset ZELINE_HOME to exercise a fresh-install path. Keep HOME
# isolated too, otherwise that fallback resolves to the operator's ~/.zeline.
# ZELINE_TEST_HOME allows CI or a caller to provide a known disposable parent.
_ORIGINAL_ENV = {
    name: os.environ.get(name)
    for name in ("HOME", "ZELINE_HOME", "PIP_CACHE_DIR")
}
_SESSION_HOME: Path | None = None
_SESSION_HOME_CREATED = False


def _restore_env(snapshot: dict[str, str | None]) -> None:
    for name, value in snapshot.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _is_within(path: Path, parent: Path | None) -> bool:
    """True when *path* is *parent* or lives inside it (resolved)."""
    if parent is None:
        return False
    try:
        resolved_parent = parent.expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    path = path.resolve()
    return path == resolved_parent or resolved_parent in path.parents


def _session_home_is_safe(session_home: Path) -> bool:
    """Reject any session home overlapping the operator's real HOME/state.

    A misconfigured ``ZELINE_TEST_HOME`` (e.g. ``$HOME``) would otherwise make
    the sandbox resolve to the operator's own ``~/.zeline`` — the exact gateway
    state the sandbox exists to protect. Overlap in either direction is unsafe.
    """
    for name in ("HOME", "ZELINE_HOME"):
        original = _ORIGINAL_ENV.get(name)
        if not original:
            continue
        real = Path(original)
        if _is_within(session_home, real) or _is_within(real, session_home):
            return False
    return True


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """Route every test's default Zeline state away from the operator account."""
    del config  # The hook exists solely to run before test collection imports Zeline.
    global _SESSION_HOME, _SESSION_HOME_CREATED

    # ZELINE_TEST_HOME is only a disposable PARENT: a fresh subdirectory is
    # always created inside it so the variable can never itself be the sandbox
    # (and therefore can never point the sandbox straight at ~/.zeline).
    requested = os.environ.get("ZELINE_TEST_HOME")
    parent = Path(requested).expanduser().resolve() if requested else None
    if parent is not None:
        parent.mkdir(parents=True, exist_ok=True)
    _SESSION_HOME = Path(tempfile.mkdtemp(prefix="zeline-pytest-home-", dir=parent))
    _SESSION_HOME_CREATED = True

    if not _session_home_is_safe(_SESSION_HOME):
        shutil.rmtree(_SESSION_HOME, ignore_errors=True)
        raise pytest.UsageError(
            "ZELINE_TEST_HOME resolves to the operator's real HOME/ZELINE_HOME; "
            "refusing to run tests against live Zeline state."
        )

    state = _SESSION_HOME / ".zeline"
    state.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(_SESSION_HOME)
    os.environ["ZELINE_HOME"] = str(state)

    # Preserve the operator's wheel cache WITHOUT writing into it: the installer
    # tests build a venv and pip-install real deps (PyYAML etc.), which must not
    # recompile native wheels on Termux or write locks into ~/.cache/pip. Copy
    # the cache into the disposable session HOME; the suite reads/writes there.
    original_pip_cache = _ORIGINAL_ENV.get("PIP_CACHE_DIR")
    source_cache = (
        Path(original_pip_cache).expanduser()
        if original_pip_cache
        else Path(_ORIGINAL_ENV.get("HOME") or Path.home()) / ".cache" / "pip"
    )
    session_cache = _SESSION_HOME / ".cache" / "pip"
    if source_cache.is_dir():
        shutil.copytree(source_cache, session_cache, dirs_exist_ok=True)
    else:
        session_cache.mkdir(parents=True, exist_ok=True)
    os.environ["PIP_CACHE_DIR"] = str(session_cache)

    # ACP instantiates the real agent during its protocol tests and therefore
    # needs a syntactically configured provider. This is a disposable config,
    # never the operator's config, and it is created only when the test state
    # did not already provide one.
    from zeline import config as zeline_config

    if not zeline_config.CONFIG_FILE.exists():
        test_config = zeline_config.config_copy()
        test_config["provider"].update(
            {
                "api_key": "test-key",
                "model": "test-model",
                "base_url": "http://provider.test/v1",
            }
        )
        zeline_config.save_config(test_config)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Restore a host process environment after in-process pytest use."""
    del session, exitstatus
    try:
        _restore_env(_ORIGINAL_ENV)
    finally:
        if _SESSION_HOME_CREATED and _SESSION_HOME is not None:
            shutil.rmtree(_SESSION_HOME, ignore_errors=True)


def _is_zeline_module(name: str) -> bool:
    return name == "zeline" or name.startswith("zeline.")


def _child_module_links(modules: dict[str, ModuleType]) -> dict[str, dict[str, ModuleType]]:
    """Return child-module attributes cached by every captured Zeline module."""
    links: dict[str, dict[str, ModuleType]] = {}
    for name, module in modules.items():
        links[name] = {
            attr: value
            for attr, value in vars(module).items()
            if isinstance(value, ModuleType) and _is_zeline_module(value.__name__)
        }
    return links


def pytest_runtest_setup(item: Any) -> None:
    modules = {
        name: module
        for name, module in sys.modules.items()
        if _is_zeline_module(name) and isinstance(module, ModuleType)
    }
    item._zeline_module_snapshot = (modules, _child_module_links(modules))


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_teardown(item: Any, nextitem: Any) -> Any:
    """Restore module identity after the test's own teardown/mocks complete."""
    yield

    modules, links = item._zeline_module_snapshot
    try:
        for name in list(sys.modules):
            if _is_zeline_module(name) and name not in modules:
                sys.modules.pop(name, None)
        sys.modules.update(modules)

        for name, parent in modules.items():
            before = links[name]
            for attr, value in list(vars(parent).items()):
                if (
                    isinstance(value, ModuleType)
                    and _is_zeline_module(value.__name__)
                    and attr not in before
                ):
                    delattr(parent, attr)
            for attr, child in before.items():
                setattr(parent, attr, child)
    finally:
        del item._zeline_module_snapshot
