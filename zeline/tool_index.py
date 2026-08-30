"""Lazy tool schemas: send a small core, let the model fetch the rest on demand.

Every request carries the JSON schema of every tool. Measured on this codebase
that is ~1.4k tokens on the ``safe`` profile and ~3.5k on ``full``, re-sent on
every tool round of every turn -- and #186 (custom tools) and #187 (plugins)
only make the list grow.

The insight this is built on: **names are cheap, schemas are expensive.** The
full catalogue of 28 tools costs a few hundred characters as a list of names
with one-line summaries, versus ~14k characters as full schemas. So the model is
always told what exists; it only pays for the parameter detail it asks for.

That removes the usual objection to lazy tool loading. The model is not guessing
in the dark about hidden capabilities -- it can see every name, and:

- calling ``tool_search`` returns the full schemas for whatever matches;
- calling a catalogued tool *directly*, without looking it up first, still
  works and reveals it. There is deliberately no dead end where the model knows
  a tool exists but cannot reach it.

Revelation is **sticky** for the life of the executor: once a schema has been
shown it stays in the list. A tool that vanished again mid-task would make the
model's earlier plan silently unexecutable.

Off by default. Turning it on trades one extra round trip for tokens, which only
pays off on a large tool set, and enabling it silently for existing installs
would change how every agent behaves without being asked.
"""
from __future__ import annotations

import json
from typing import Any

from zeline import config

TOOL_NAME = "tool_search"

# Always visible, whatever else is hidden. These are the tools a turn cannot
# reasonably start without: knowing what the runtime is, reading and writing
# files, searching, running a command, and the discovery tool itself. Hiding any
# of these would just guarantee an extra round trip on almost every turn.
CORE_TOOLS = frozenset({
    "runtime_info",
    "read_file",
    "write_file",
    "edit_file",
    "search_files",
    "run_shell",
    "execute_code",
    "update_task",
    "ask_user",
})

# Below this many tools the extra round trip costs more than the tokens saved.
MIN_TOOLS_TO_BOTHER = 12

_SUMMARY_CHARS = 70


def enabled() -> bool:
    return bool(getattr(config, "TOOL_SEARCH", False))


def _summarize(description: str) -> str:
    """First sentence, trimmed. Enough to choose a tool, not enough to call it."""
    text = " ".join((description or "").split())
    for stop in (". ", " — ", " -- "):
        head = text.split(stop)[0]
        if head != text:
            text = head
            break
    if len(text) > _SUMMARY_CHARS:
        text = text[: _SUMMARY_CHARS - 1].rstrip() + "…"
    return text


def catalog_lines(schemas: list[dict[str, Any]]) -> list[str]:
    lines = []
    for schema in schemas:
        function = schema.get("function", {})
        name = str(function.get("name", ""))
        if not name:
            continue
        lines.append(f"{name}: {_summarize(str(function.get('description', '')))}")
    return lines


def search_schema(hidden: list[dict[str, Any]]) -> dict[str, Any]:
    """Schema for tool_search itself, with the catalogue of hidden tools inline.

    The catalogue lives in the description rather than in an enum so the model
    can also just call a listed tool directly, instead of being forced through a
    lookup step it does not need.
    """
    catalog = "\n".join(f"- {line}" for line in catalog_lines(hidden))
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": (
                "Get the full parameter schema for tools that are not loaded yet. "
                "Their names and summaries are listed below; call this with a query "
                "to receive the exact parameters for the ones you need. You may also "
                "call any listed tool directly if its arguments are obvious.\n"
                f"Available on request:\n{catalog}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Tool name, or words describing the capability "
                            "(e.g. 'send http request', 'search the web')."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    }


def _match(query: str, schema: dict[str, Any]) -> int:
    """Score a schema against a query. 0 means no match."""
    function = schema.get("function", {})
    name = str(function.get("name", "")).casefold()
    description = str(function.get("description", "")).casefold()
    folded = query.casefold().strip()
    if not folded:
        return 0
    if folded == name:
        return 100
    score = 0
    if folded in name:
        score += 50
    for word in {w for w in folded.replace("_", " ").split() if len(w) > 2}:
        if word in name:
            score += 10
        elif word in description:
            score += 3
    return score


class LazySchemaIndex:
    """Tracks which schemas have been revealed to one agent."""

    def __init__(self, all_schemas: list[dict[str, Any]]):
        self.all: list[dict[str, Any]] = list(all_schemas)
        self.revealed: set[str] = set()

    @property
    def applicable(self) -> bool:
        """Only worth doing when it is switched on and there is enough to hide."""
        return enabled() and len(self.all) >= MIN_TOOLS_TO_BOTHER

    def _name(self, schema: dict[str, Any]) -> str:
        return str(schema.get("function", {}).get("name", ""))

    def visible(self) -> list[dict[str, Any]]:
        if not self.applicable:
            return list(self.all)
        shown: list[dict[str, Any]] = []
        hidden: list[dict[str, Any]] = []
        for schema in self.all:
            name = self._name(schema)
            if name in CORE_TOOLS or name in self.revealed:
                shown.append(schema)
            else:
                hidden.append(schema)
        if hidden:
            shown.append(search_schema(hidden))
        return shown

    def reveal(self, name: str) -> None:
        self.revealed.add(name)

    def knows(self, name: str) -> bool:
        return any(self._name(schema) == name for schema in self.all)

    def search(self, query: str, limit: int = 4) -> str:
        """Return matching schemas as text, and remember they were revealed."""
        candidates = [
            schema for schema in self.all
            if self._name(schema) not in CORE_TOOLS
        ]
        scored = sorted(
            ((_match(query, schema), schema) for schema in candidates),
            key=lambda pair: pair[0],
            reverse=True,
        )
        # Prefer name matches when there are any. Otherwise a query like
        # 'run_shell' also drags in whatever merely mentions "running" in its
        # description, and each stray match costs the tokens this feature exists
        # to save.
        name_hits = [schema for score, schema in scored if score >= 10]
        matches = (name_hits or [schema for score, schema in scored if score > 0])[:limit]
        if not matches:
            names = ", ".join(sorted(self._name(s) for s in candidates))
            return (
                f"No tool matched '{query}'. Available on request: {names}. "
                "Search again with one of those names."
            )
        for schema in matches:
            self.reveal(self._name(schema))
        return (
            f"{len(matches)} tool(s) matched '{query}'. They are now available to "
            "call directly with these parameters:\n"
            + json.dumps(matches, ensure_ascii=False, indent=2)
        )
