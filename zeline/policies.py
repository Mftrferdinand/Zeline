"""Opt-in required policies for tool dispatch, separate from observer plugins.

An absent registry means unconfigured. Once a registry is observed by an engine,
its disappearance is an error. A declared policy never fails open.
"""
from pathlib import Path
from copy import deepcopy
import json
import inspect
import re
import sys
import types
import uuid

from zeline import config


def _unique_fields(pairs):
    fields = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError("duplicate registry field")
        fields[key] = value
    return fields


class PolicyBlocked(str):
    """A denial that best-effort output observers cannot turn into success."""


class PolicyEngine:
    def __init__(self, identity: str, profile: str, workspace: Path):
        self.identity = identity
        self.profile = profile
        self.workspace = workspace
        self.root = config.DATA_DIR
        self.seen_registry = False

    def check(self, name: str, args: dict) -> str | None:
        registry = self.root / "policies.json"
        try:
            if registry.is_symlink():
                return "registry symlinks are not allowed"
            with registry.open("rb") as stream:
                raw = stream.read(16385)
        except FileNotFoundError:
            return "required registry disappeared" if self.seen_registry else None
        except OSError:
            return "required registry is unreadable"
        self.seen_registry = True
        try:
            if len(raw) > 16384:
                return "required registry exceeds its limit"
            data = json.loads(raw, object_pairs_hook=_unique_fields)
            if not isinstance(data, dict) or set(data) != {"required"}:
                return "invalid required registry"
            names = data["required"]
            if not isinstance(names, list) or len(names) > 16 or any(
                not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.py", item)
                for item in names
            ) or len(set(names)) != len(names):
                return "invalid required policy list"
            root = self.root.resolve() / "policies"
            for item in names:
                source = root / item
                if source.is_symlink() or source.resolve().parent != root or not source.is_file():
                    return f"required policy {item} is unavailable"
                module_name = "_zeline_required_policy_" + uuid.uuid4().hex
                module = types.ModuleType(module_name)
                module.__file__ = str(source)
                sys.modules[module_name] = module
                try:
                    with source.open("rb") as stream:
                        code = stream.read(65537)
                    if len(code) > 65536:
                        return f"required policy {item} exceeds its limit"
                    # Explicit, owner-installed Python; compile current bytes so
                    # timestamp/size-based .pyc reuse cannot retain an old rule.
                    exec(compile(code, str(source), "exec"), module.__dict__)
                    authorize = getattr(module, "authorize", None)
                    if not callable(authorize) or inspect.iscoroutinefunction(authorize) or inspect.isasyncgenfunction(authorize):
                        return f"required policy {item} needs synchronous authorize(context)"
                    context = {"tool": name, "args": deepcopy(args),
                               "profile": self.profile, "identity": self.identity,
                               "workspace": str(self.workspace) if self.workspace is not None else None}
                    decision = authorize(context)
                    if inspect.iscoroutine(decision):
                        decision.close()
                    if decision is not True:
                        return f"required policy {item} did not explicitly allow dispatch"
                except BaseException as exc:
                    # Never echo exception text or raw arguments: local policy
                    # failures can contain credentials or private file content.
                    return f"required policy {item} failed ({type(exc).__name__})"
                finally:
                    sys.modules.pop(module_name, None)
            return None
        except Exception:
            return "required policy registry validation failed"
