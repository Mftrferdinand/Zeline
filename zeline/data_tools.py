"""Workspace data-file conversion and validation: CSV, JSON, YAML, TOML.

An agent that produces or inspects data files previously needed ``run_shell``
(python one-liners, jq, yq) for anything beyond reading — and ``run_shell`` is
owner-only by design. ``data_convert`` covers the small, safe core:

- **Convert** between JSON, CSV, YAML and TOML (stdlib json/csv/tomllib/tomli_w
  not required: TOML output uses a tiny writer for flat/nested dicts and lists).
- **Validate** a file's syntax and report where it breaks, so the model fixes
  the actual line instead of guessing.

Deliberate scope:

- **Workspace-confined, read-only inputs.** Output goes to a workspace path
  when given, or is returned inline (bounded) when not.
- **stdlib only.** YAML support uses a conservative subset parser/writer
  (mappings, lists, scalars) — enough for config files, without adding a
  dependency. Anything the subset cannot parse is reported honestly.
- **Bounded.** Input files larger than 2 MB are refused; inline output is
  truncated by ``offload`` like every other tool result.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

ALGORITHMS = ("json", "csv", "yaml", "toml")
MAX_INPUT_BYTES = 2_000_000
INLINE_LIMIT = 12_000


class _DataError(ValueError):
    pass


# --------------------------------------------------------------------- parsing
def _parse_json(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise _DataError(f"JSON error at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc


def _parse_csv(text: str):
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error as exc:
        raise _DataError(f"CSV error: {exc}") from exc
    if not rows:
        return []
    header = rows[0]
    return [dict(zip(header, row)) for row in rows[1:] if row]


def _yaml_scalar(token: str):
    token = token.strip()
    if not token or token in ("~", "null", "Null", "NULL"):
        return None
    if token in ("true", "True", "TRUE"):
        return True
    if token in ("false", "False", "FALSE"):
        return False
    if (token.startswith('"') and token.endswith('"')) or (
        token.startswith("'") and token.endswith("'")
    ):
        return token[1:-1]
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    return token


def _parse_yaml(text: str):
    """Conservative YAML subset: nested mappings, lists, scalars."""
    lines = [line for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    if not lines:
        return {}

    def block(index: int, indent: int):
        # Decide mapping vs list by the first significant line at this indent.
        if index >= len(lines):
            return {}, index
        if lines[index].strip().startswith("- "):
            items = []
            while index < len(lines):
                raw = lines[index]
                current = len(raw) - len(raw.lstrip(" "))
                if current < indent or not raw.strip().startswith("-"):
                    break
                if current > indent:
                    raise _DataError(f"YAML error at line {index + 1}: unexpected indent")
                item_text = raw.strip()[2:].strip()
                if ":" in item_text and not item_text.startswith(("'", '"')):
                    # Inline start of a mapping item; merge continuation lines.
                    mapping = {}
                    key, _, value = item_text.partition(":")
                    if value.strip():
                        mapping[key.strip()] = _yaml_scalar(value)
                    else:
                        nested, index = block(index + 1, _indent(lines, index + 1))
                        mapping[key.strip()] = nested
                    index += 1
                    while index < len(lines):
                        raw = lines[index]
                        current = len(raw) - len(raw.lstrip(" "))
                        if current <= indent or raw.strip().startswith("- "):
                            break
                        key2, _, value2 = raw.strip().partition(":")
                        if value2.strip():
                            mapping[key2.strip()] = _yaml_scalar(value2)
                        else:
                            nested, index = block(index + 1, _indent(lines, index + 1))
                            mapping[key2.strip()] = nested
                            continue
                        index += 1
                    items.append(mapping)
                    continue
                items.append(_yaml_scalar(item_text))
                index += 1
            return items, index
        mapping = {}
        while index < len(lines):
            raw = lines[index]
            current = len(raw) - len(raw.lstrip(" "))
            if current < indent:
                break
            if current > indent:
                raise _DataError(f"YAML error at line {index + 1}: unexpected indent")
            stripped = raw.strip()
            if ":" not in stripped:
                raise _DataError(f"YAML error at line {index + 1}: expected 'key: value'")
            key, _, value = stripped.partition(":")
            if value.strip():
                mapping[key.strip()] = _yaml_scalar(value)
                index += 1
            else:
                nested, index = block(index + 1, _indent(lines, index + 1))
                mapping[key.strip()] = nested
        return mapping, index

    def _indent(lines: list[str], index: int) -> int:
        if index >= len(lines):
            return 0
        raw = lines[index]
        return len(raw) - len(raw.lstrip(" "))

    value, _ = block(0, _indent(lines, 0))
    return value


def _parse_toml(text: str):
    try:
        import tomllib
    except ModuleNotFoundError as exc:  # Python < 3.11
        raise _DataError("TOML input requires Python 3.11+ (tomllib)") from exc
    try:
        return tomllib.loads(text)
    except Exception as exc:  # tomllib.TOMLDecodeError
        raise _DataError(f"TOML error: {exc}") from exc


def _parse(fmt: str, text: str):
    return {
        "json": lambda: _parse_json(text),
        "csv": lambda: _parse_csv(text),
        "yaml": lambda: _parse_yaml(text),
        "toml": lambda: _parse_toml(text),
    }[fmt]()


# -------------------------------------------------------------------- writing
def _dump_json(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _dump_csv(data) -> str:
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise _DataError("CSV output needs a list of flat objects (or an object)")
    buffer = io.StringIO()
    keys: list[str] = []
    for item in data:
        for key in item:
            if key not in keys:
                keys.append(key)
    writer = csv.DictWriter(buffer, fieldnames=keys, extrasaction="ignore")
    writer.writeheader()
    for item in data:
        writer.writerow({key: item.get(key, "") for key in keys})
    return buffer.getvalue()


def _yaml_dump_value(value, indent: int = 0) -> list[str]:
    pad = "  " * indent
    lines: list[str] = []
    if isinstance(value, dict):
        if not value:
            return [pad + "{}"]
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}{key}:")
                lines.extend(_yaml_dump_value(item, indent + 1))
            else:
                lines.append(f"{pad}{key}: {_yaml_scalar_out(item)}")
    elif isinstance(value, list):
        if not value:
            return [pad + "[]"]
        for item in value:
            if isinstance(item, (dict, list)):
                nested = _yaml_dump_value(item, indent + 1)
                lines.append(f"{pad}- " + nested[0].strip())
                lines.extend(nested[1:])
            else:
                lines.append(f"{pad}- {_yaml_scalar_out(item)}")
    else:
        lines.append(pad + _yaml_scalar_out(value))
    return lines


def _yaml_scalar_out(value) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    text = str(value)
    if not text or any(char in text for char in ":#{}[],&*?|-<>=!%@\\\"'") or text != text.strip():
        return json.dumps(text, ensure_ascii=False)
    return text


def _dump_yaml(data) -> str:
    return "\n".join(_yaml_dump_value(data)) + "\n"


def _toml_scalar_out(value) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _dump_toml(data) -> str:
    if not isinstance(data, dict):
        raise _DataError("TOML output needs an object at the top level")
    lines: list[str] = []

    def emit(table: dict, prefix: str) -> None:
        scalars = {k: v for k, v in table.items() if not isinstance(v, (dict, list))}
        for key, value in scalars.items():
            full = f"{prefix}.{key}" if prefix else key
            if isinstance(value, list) and all(not isinstance(i, (dict, list)) for i in value):
                lines.append(f"{full} = [{', '.join(_toml_scalar_out(i) for i in value)}]")
            elif isinstance(value, list):
                raise _DataError(f"TOML output: array-of-tables is not supported ({full})")
            else:
                lines.append(f"{full} = {_toml_scalar_out(value)}")
        for key, value in table.items():
            if isinstance(value, dict):
                full = f"{prefix}.{key}" if prefix else key
                if lines:
                    lines.append("")
                lines.append(f"[{full}]")
                emit(value, full)

    emit(data, "")
    return "\n".join(lines) + "\n"


def _dump(fmt: str, data) -> str:
    return {
        "json": _dump_json,
        "csv": _dump_csv,
        "yaml": _dump_yaml,
        "toml": _dump_toml,
    }[fmt](data)


# -------------------------------------------------------------------- the tool
def _data_convert(
    path: str,
    workspace: Path,
    to_format: str = "json",
    output_path: str = "",
    validate_only: bool = False,
) -> str:
    """Convert or validate a workspace data file between JSON/CSV/YAML/TOML."""
    from zeline.offload import maybe_offload
    from zeline.tools import _resolve_workspace_path

    source_fmt = (path.rsplit(".", 1)[-1] if "." in path else "").lower()
    target_fmt = (to_format or "json").strip().lower()
    for fmt, label in ((source_fmt, "source"), (target_fmt, "target")):
        if label == "source" and validate_only and not fmt:
            fmt = target_fmt  # validation without a recognizable suffix: try target
            source_fmt = fmt
        if fmt not in ALGORITHMS:
            return (f"ERROR data_convert: unknown {label} format '{fmt}'. "
                    f"Supported: {', '.join(ALGORITHMS)} (from the file extension).")
    try:
        target = _resolve_workspace_path(path, workspace)
    except ValueError as exc:
        return f"ERROR data_convert: {exc}"
    if not target.is_file():
        return f"ERROR data_convert: not a file or not found: {target}"
    try:
        if target.stat().st_size > MAX_INPUT_BYTES:
            return f"ERROR data_convert: file exceeds the {MAX_INPUT_BYTES // 1_000_000} MB limit."
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return f"ERROR data_convert: {exc.__class__.__name__}: {exc}"
    try:
        data = _parse(source_fmt, text)
    except _DataError as exc:
        return f"INVALID {source_fmt.upper()}: {exc}"
    if validate_only:
        count = len(data) if isinstance(data, (list, dict)) else 1
        return f"VALID {source_fmt.upper()}: parses cleanly ({count} top-level element(s))."
    try:
        rendered = _dump(target_fmt, data)
    except _DataError as exc:
        return f"ERROR data_convert: {exc}"
    if output_path:
        try:
            destination = _resolve_workspace_path(output_path, workspace)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(rendered, encoding="utf-8")
        except (ValueError, OSError) as exc:
            return f"ERROR data_convert: cannot write output ({exc})"
        return f"OK, converted {source_fmt} -> {target_fmt} ({len(data) if isinstance(data, list) else 'object'}) to {destination}"
    return maybe_offload(rendered, INLINE_LIMIT)
