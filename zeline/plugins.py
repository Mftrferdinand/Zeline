"""Plugin hooks: observe, rewrite, or block a tool call from a file on disk.

Custom tools (``custom_tools.py``) let an operator *add* capabilities. Hooks let
them *govern* the capabilities that already exist — which is a different job and
needs a different mechanism. A file in ``~/.zeline/plugins/`` like::

    def on_tool_before(name, args):
        if name == "run_shell" and "rm -rf" in args.get("command", ""):
            return deny("blocked by local policy")

    def on_tool_after(name, args, result):
        return result.replace(os.environ.get("COMPANY_TOKEN", "\\0"), "[redacted]")

gives three things nothing else in Zeline provides: an audit trail of every tool
call, argument rewriting (inject a default, clamp a limit), and redaction of tool
output *before* it enters the model's context.

The rules exist because a hook sits on the path of every tool call, so a careless
one is far more damaging than a careless custom tool:

- **A broken hook never breaks the tool call.** Any exception is captured, the
  hook is skipped, and the call proceeds. A governance layer that takes the agent
  down when it has a typo would be abandoned immediately.
- **Blocking must be explicit.** Only a ``deny(...)`` sentinel stops a call.
  Returning ``None``, a wrong type, or nothing at all means "no opinion" — so a
  hook can never block by accident, which would be a silent, baffling failure.
- **Rewrites must be type-correct or ignored.** ``on_tool_before`` must return a
  dict to change arguments and ``on_tool_after`` a string to change output;
  anything else is discarded rather than corrupting the call.
- **Operator profiles only.** Same reasoning as custom tools and MCP stdio: this
  is arbitrary local Python in the agent's process.
- **Deterministic order.** Files run in sorted name order, so ``10-audit.py``
  runs before ``20-redact.py`` and the operator controls the pipeline.
"""
from __future__ import annotations

import importlib.util
import inspect
import os
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zeline import config

# Only the operator's own profiles run local plugin files.
ALLOWED_PROFILES = frozenset({"workspace", "full"})

BEFORE_HOOK = "on_tool_before"
AFTER_HOOK = "on_tool_after"
HOOK_NAMES = (BEFORE_HOOK, AFTER_HOOK)


@dataclass(frozen=True)
class Deny:
    """Returned by a before-hook to stop a tool call.

    A dedicated type rather than a magic string or False, so refusal can never
    be confused with a hook that simply returned a value.
    """

    reason: str = "blocked by a local plugin"


def deny(reason: str = "blocked by a local plugin") -> Deny:
    return Deny(reason)


def enabled() -> bool:
    return bool(getattr(config, "PLUGINS", True))


def plugins_dir() -> Path:
    return config.DATA_DIR / "plugins"


def ensure_dir() -> Path:
    directory = plugins_dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    return directory


@dataclass
class LoadedPlugin:
    name: str
    source: Path
    before: Any | None = None
    after: Any | None = None


@dataclass
class HookOutcome:
    """What the before-hooks decided about one tool call."""

    args: dict[str, Any]
    denied_by: str | None = None
    reason: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.denied_by is not None


def _load_module(path: Path):
    module_name = f"_zeline_plugin_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def _usable_hook(candidate: Any, arity: int, path: Path, hook: str, errors: list[str]):
    """Accept a hook only if it can actually be called with the arguments we pass."""
    if candidate is None:
        return None
    if not callable(candidate):
        errors.append(f"{path.name}: {hook} is not callable")
        return None
    try:
        signature = inspect.signature(candidate)
        # bind() with placeholders proves the call will not raise TypeError later,
        # which is better than discovering it on the first tool call.
        signature.bind(*(None,) * arity)
    except TypeError:
        errors.append(
            f"{path.name}: {hook} must accept {arity} argument(s) "
            f"({'name, args' if arity == 2 else 'name, args, result'})"
        )
        return None
    except ValueError:
        # Builtins without an introspectable signature; let it try.
        return candidate
    return candidate


def discover(directory: Path | None = None) -> tuple[list[LoadedPlugin], list[str]]:
    """Load plugin files. Returns (plugins, errors); one bad file never stops the rest."""
    if not enabled():
        return [], []
    root = Path(directory) if directory is not None else plugins_dir()
    if not root.is_dir():
        return [], []

    plugins: list[LoadedPlugin] = []
    errors: list[str] = []
    for path in sorted(root.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            module = _load_module(path)
        except BaseException as exc:  # noqa: BLE001 — arbitrary operator code
            # BaseException on purpose: a plugin calling sys.exit() at import
            # time must not terminate the agent.
            detail = traceback.format_exception_only(type(exc), exc)[-1].strip()
            errors.append(f"{path.name}: {detail}")
            continue

        before = _usable_hook(getattr(module, BEFORE_HOOK, None), 2, path, BEFORE_HOOK, errors)
        after = _usable_hook(getattr(module, AFTER_HOOK, None), 3, path, AFTER_HOOK, errors)
        if before is None and after is None:
            if not any(path.name in item for item in errors):
                errors.append(
                    f"{path.name}: defines no hooks — expected {BEFORE_HOOK} or {AFTER_HOOK}"
                )
            continue
        plugins.append(LoadedPlugin(name=path.stem, source=path, before=before, after=after))

    return plugins, errors


class PluginBus:
    """Runs the loaded hooks around one agent's tool calls."""

    def __init__(self, profile: str, directory: Path | None = None):
        self.profile = profile
        self.plugins: list[LoadedPlugin] = []
        self.errors: list[str] = []
        # Runtime hook failures, kept separate from load errors so `zeline
        # plugins` can show which file is misbehaving during actual use.
        self.runtime_errors: list[str] = []
        if profile not in ALLOWED_PROFILES:
            return
        found, errors = discover(directory)
        self.plugins = found
        self.errors = errors

    @property
    def active(self) -> bool:
        return bool(self.plugins)

    def _record(self, plugin: LoadedPlugin, hook: str, exc: BaseException) -> str:
        detail = traceback.format_exception_only(type(exc), exc)[-1].strip()
        message = f"{plugin.source.name}:{hook}: {detail}"
        self.runtime_errors.append(message)
        # Bounded: a hook failing on every call must not grow without limit.
        del self.runtime_errors[:-50]
        return message

    def before(self, name: str, args: dict[str, Any]) -> HookOutcome:
        """Run before-hooks. Arguments may be rewritten; a deny() stops the call."""
        outcome = HookOutcome(args=dict(args or {}))
        for plugin in self.plugins:
            if plugin.before is None:
                continue
            try:
                # A copy per hook, so a hook mutating the dict in place cannot
                # corrupt what the next hook or the tool itself receives.
                result = plugin.before(name, dict(outcome.args))
            except BaseException as exc:  # noqa: BLE001 — arbitrary operator code
                outcome.errors.append(self._record(plugin, BEFORE_HOOK, exc))
                continue
            if isinstance(result, Deny):
                outcome.denied_by = plugin.source.name
                outcome.reason = result.reason
                return outcome
            if isinstance(result, dict):
                outcome.args = result
            # Anything else is "no opinion" and deliberately ignored.
        return outcome

    def after(self, name: str, args: dict[str, Any], result: str) -> str:
        """Run after-hooks. A hook returning a string replaces the tool output."""
        current = result
        for plugin in self.plugins:
            if plugin.after is None:
                continue
            try:
                replacement = plugin.after(name, dict(args or {}), current)
            except BaseException as exc:  # noqa: BLE001 — arbitrary operator code
                self._record(plugin, AFTER_HOOK, exc)
                continue
            if isinstance(replacement, str):
                current = replacement
        return current


def denial_message(name: str, outcome: HookOutcome) -> str:
    """Text the model sees when a plugin blocks a call.

    Phrased so the model stops retrying: it says who refused and why, rather
    than looking like a transient failure worth attempting again.
    """
    return (
        f"ERROR: tool '{name}' was blocked by plugin {outcome.denied_by}: "
        f"{outcome.reason}. This is a local policy decision, not a transient "
        "failure — do not retry the same call."
    )


TEMPLATE = '''"""Zeline plugin hooks.

Define either or both:

    on_tool_before(name, args)         -> None | dict | deny("reason")
    on_tool_after(name, args, result)  -> None | str

Return a dict from on_tool_before to rewrite the arguments, or deny("why") to
block the call. Return a string from on_tool_after to rewrite the tool output
before the model ever sees it.

Files run in sorted filename order, so 10-audit.py runs before 20-redact.py.
A hook that raises is skipped and reported; the tool call still proceeds.
"""
from zeline.plugins import deny

DANGEROUS = ("rm -rf /", "mkfs", ":(){:|:&};:")


def on_tool_before(name, args):
    """Refuse a few shell commands that are never a good idea."""
    if name == "run_shell":
        command = str(args.get("command", ""))
        for pattern in DANGEROUS:
            if pattern in command:
                return deny(f"command contains {pattern!r}")
    return None


def on_tool_after(name, args, result):
    """Keep tool output from growing unboundedly in the context window."""
    limit = 20_000
    if len(result) > limit:
        return result[:limit] + f"\\n... [truncated {len(result) - limit} characters by plugin]"
    return None
'''
