"""Custom tools: drop a Python file in ~/.zeline/tools/ and the agent can call it.

This is what lets an operator extend Zeline without forking it. A file like::

    def jira_issue(key: str, verbose: bool = False) -> str:
        \"\"\"Fetch a Jira issue by key.

        key: issue key such as PROJ-123
        verbose: include the full description
        \"\"\"
        return ...

becomes a tool named ``custom_jira_issue`` whose JSON schema is derived from the
signature: annotations give types, defaults decide what is required, and the
docstring supplies both the tool description and the per-argument descriptions.
There is no separate manifest to keep in sync with the code, because a manifest
that can drift from the signature is a bug waiting to happen.

Four rules, each of which exists because the alternative is worse:

- **Operator profiles only.** These files are arbitrary local Python running in
  the agent's own process. A public gateway on the ``safe`` profile must never
  reach them, exactly like MCP stdio servers.
- **One bad file cannot take down the agent.** Import errors, syntax errors and
  bad signatures are collected per file and reported; every other file still
  loads. An extension mechanism that breaks the whole agent when one file has a
  typo would not be usable.
- **Names are prefixed and never shadow a native tool.** ``custom_`` makes the
  origin obvious in transcripts and guarantees a custom file cannot silently
  replace ``write_file``.
- **A tool that returns nothing useful still returns a string.** The provider
  protocol requires a string result, so returns are coerced and exceptions are
  turned into ``ERROR ...`` text rather than escaping into the turn.
"""
from __future__ import annotations

import importlib.util
import inspect
import os
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zeline import config

TOOL_PREFIX = "custom_"

# Only the operator's own profiles. A public gateway must not run local files.
ALLOWED_PROFILES = frozenset({"workspace", "full"})

# JSON-schema type for each annotation we accept. Anything else is rejected with
# a clear message instead of being guessed at, because a wrong schema makes the
# model send arguments the function cannot accept.
_TYPE_MAP: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
    list: "array",
}


def enabled() -> bool:
    return bool(getattr(config, "CUSTOM_TOOLS", True))


def tools_dir() -> Path:
    return config.DATA_DIR / "tools"


def ensure_dir() -> Path:
    directory = tools_dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    return directory


@dataclass(frozen=True)
class CustomTool:
    name: str
    description: str
    parameters: dict[str, Any]
    function: Callable[..., Any]
    source: Path

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _split_docstring(doc: str) -> tuple[str, dict[str, str]]:
    """Return (summary, {arg: description}) from a docstring.

    Argument lines are plain ``name: description``, which is the shape people
    already write. A heavier convention (Google/Numpy sections) would be more
    to remember for no gain here.
    """
    summary_lines: list[str] = []
    args: dict[str, str] = {}
    for raw in (doc or "").strip().splitlines():
        line = raw.strip()
        if not line:
            if summary_lines:
                # Blank line ends the summary; later prose is not the summary.
                summary_lines.append("")
            continue
        head, separator, tail = line.partition(":")
        candidate = head.strip()
        if (
            separator
            and candidate
            and " " not in candidate
            and candidate.isidentifier()
            and tail.strip()
        ):
            args[candidate] = tail.strip()
            continue
        if not args:
            summary_lines.append(line)
    summary = " ".join(part for part in summary_lines if part).strip()
    return summary, args


def _schema_for(function: Callable[..., Any], arg_docs: dict[str, str]) -> dict[str, Any]:
    """Derive a JSON schema from the signature. Raises ValueError if impossible."""
    signature = inspect.signature(function)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for parameter in signature.parameters.values():
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            # *args / **kwargs cannot be described to a provider, and silently
            # dropping them would make the tool look callable when it is not.
            raise ValueError(
                f"parameter '{parameter.name}' uses *args/**kwargs, which cannot be "
                "described in a tool schema — use explicit named parameters"
            )
        annotation = parameter.annotation
        if annotation is inspect.Parameter.empty:
            # Untyped defaults to string: the provider must be told something,
            # and string is the only type every value can be expressed as.
            json_type = "string"
        elif annotation in _TYPE_MAP:
            json_type = _TYPE_MAP[annotation]
        elif isinstance(annotation, str) and annotation in {t.__name__ for t in _TYPE_MAP}:
            # `from __future__ import annotations` turns these into strings.
            json_type = next(v for k, v in _TYPE_MAP.items() if k.__name__ == annotation)
        else:
            raise ValueError(
                f"parameter '{parameter.name}' has unsupported type {annotation!r} — "
                "use str, int, float, bool, dict or list"
            )
        entry: dict[str, Any] = {"type": json_type}
        description = arg_docs.get(parameter.name)
        if description:
            entry["description"] = description
        properties[parameter.name] = entry
        if parameter.default is inspect.Parameter.empty:
            required.append(parameter.name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _load_module(path: Path):
    """Import a file by path under a private module name."""
    module_name = f"_zeline_custom_tool_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path.name}")
    module = importlib.util.module_from_spec(spec)
    # Registered before exec so dataclasses and pickling inside the file work.
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def discover(directory: Path | None = None) -> tuple[list[CustomTool], list[str]]:
    """Load every tool file. Returns (tools, errors); one bad file never stops the rest."""
    if not enabled():
        return [], []
    root = Path(directory) if directory is not None else tools_dir()
    if not root.is_dir():
        return [], []

    tools: list[CustomTool] = []
    errors: list[str] = []
    seen: dict[str, Path] = {}

    for path in sorted(root.glob("*.py")):
        if path.name.startswith("_"):
            # Leading underscore marks a helper the operator imports themselves.
            continue
        try:
            module = _load_module(path)
        except BaseException as exc:  # noqa: BLE001 — arbitrary operator code
            # SystemExit and KeyboardInterrupt are deliberately included: a file
            # calling sys.exit() at import time must not kill the agent.
            detail = traceback.format_exception_only(type(exc), exc)[-1].strip()
            errors.append(f"{path.name}: {detail}")
            continue

        exported = getattr(module, "ZELINE_TOOLS", None)
        for attribute, value in vars(module).items():
            if attribute.startswith("_") or not callable(value):
                continue
            if not (inspect.isfunction(value) or inspect.isbuiltin(value)):
                continue
            if getattr(value, "__module__", None) != module.__name__:
                # Skip imported helpers (`from json import dumps`); only
                # functions defined in this file are tools.
                continue
            if exported is not None and attribute not in exported:
                continue

            summary, arg_docs = _split_docstring(inspect.getdoc(value) or "")
            try:
                parameters = _schema_for(value, arg_docs)
            except (ValueError, TypeError) as exc:
                errors.append(f"{path.name}:{attribute}: {exc}")
                continue

            name = f"{TOOL_PREFIX}{attribute}"
            if name in seen:
                errors.append(
                    f"{path.name}:{attribute}: name '{name}' already defined in {seen[name].name}"
                )
                continue
            seen[name] = path
            tools.append(CustomTool(
                name=name,
                description=summary or f"Custom tool {attribute} from {path.name}",
                parameters=parameters,
                function=value,
                source=path,
            ))

    return tools, errors


class CustomToolRegistry:
    """Holds the loaded tools for one agent, and dispatches calls to them."""

    def __init__(self, profile: str, directory: Path | None = None):
        self.profile = profile
        self.tools: dict[str, CustomTool] = {}
        self.errors: list[str] = []
        if profile not in ALLOWED_PROFILES:
            # Not an error: a public gateway is simply not offered local files.
            return
        found, errors = discover(directory)
        self.errors = errors
        self.tools = {tool.name: tool for tool in found}

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self.tools.values()]

    def has_tool(self, name: str) -> bool:
        return name in self.tools

    def call(self, name: str, arguments: dict[str, Any]) -> str:
        tool = self.tools.get(name)
        if tool is None:
            return f"ERROR: custom tool '{name}' is not registered."
        try:
            result = tool.function(**(arguments or {}))
        except TypeError as exc:
            return f"ERROR argument {name}: {exc}"
        except BaseException as exc:  # noqa: BLE001 — arbitrary operator code
            detail = traceback.format_exception_only(type(exc), exc)[-1].strip()
            return f"ERROR running {name}: {detail}"
        if result is None:
            return f"OK, {name} finished (no output)."
        return str(result)


TEMPLATE = '''"""Custom Zeline tools.

Every public function here becomes a tool named custom_<function>. The schema is
derived from the signature: annotations give types, defaults decide what is
optional, and the docstring provides the description — the first line for the
tool, and `name: text` lines for each argument.

Supported annotations: str, int, float, bool, dict, list.

Optional: set ZELINE_TOOLS = ["only", "these"] to export a subset.
"""


def word_count(text: str, unique: bool = False) -> str:
    """Count the words in a piece of text.

    text: the text to count
    unique: count distinct words instead of all words
    """
    words = text.split()
    if unique:
        return f"{len(set(words))} unique word(s)"
    return f"{len(words)} word(s)"
'''
