"""Lazy tool schemas: send a small core, let the model fetch the rest on demand.

Every request carries the JSON schema of every tool. Measured on a real install:
17,089 characters (~4.3k tokens) on a stock ``full`` profile, 25,898 (~6.5k) once
MCP servers are connected -- re-sent on every tool round of every turn, and #186
(custom tools) and #187 (plugins) only make the list grow.

The insight this is built on: **names are cheap, schemas are expensive.** The same
tools cost 6,793 and 7,715 characters respectively when the non-core ones become a
list of names with one-line summaries -- 60% and 69% less. So the model is always
told what exists; it only pays for the parameter detail it asks for.

That removes the usual objection to lazy tool loading. The model is not guessing
in the dark about hidden capabilities -- it can see every name, and:

- calling ``tool_search`` returns the full schemas for whatever matches;
- calling a catalogued tool *directly*, without looking it up first, still
  works and reveals it. There is deliberately no dead end where the model knows
  a tool exists but cannot reach it.

Revelation is **sticky** for the life of the executor: once a schema has been
shown it stays in the list. A tool that vanished again mid-task would make the
model's earlier plan silently unexecutable.

On by default, but engaged only where it pays: both ``MIN_TOOLS_TO_BOTHER`` and
``MIN_CHARS_SAVED`` must be cleared, measured on the executor's own tool set. The
``safe`` profile does not clear them, so public gateways keep sending everything.
``zeline toolsearch off`` restores byte-identical behaviour anywhere.

One consequence worth stating: as tools are revealed, the remaining saving falls,
and the index can drop back below the character floor and simply send everything.
That is safe because the visible set only ever grows -- a revealed tool never
disappears, so no earlier plan becomes unexecutable.
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

# ...and below this much saved schema, likewise. The tool count alone is a bad
# proxy: the `safe` profile has exactly 12 small tools and hiding them saves only
# ~2.7k characters, while the extra round trip it buys re-sends the *entire*
# prompt and conversation. So the saving has to be large enough to outweigh one
# more full turn, not merely positive. 8k characters (~2k tokens) is a deliberate
# floor: it keeps public gateway profiles eager and lets the big operator
# profiles (34 and 41 tools here, 16k+ characters hidden) engage.
MIN_CHARS_SAVED = 8000

_SUMMARY_CHARS = 70


def enabled() -> bool:
    return bool(getattr(config, "TOOL_SEARCH", True))


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
        """Only worth doing when it is switched on and there is enough to hide.

        Two floors, both measured on this executor's own tool set: a tool count,
        and the characters actually saved. The second one is what keeps small
        profiles honest -- twelve tiny tools clear the count while saving too
        little to justify the round trip.
        """
        if not enabled() or len(self.all) < MIN_TOOLS_TO_BOTHER:
            return False
        return self._chars_saved() >= MIN_CHARS_SAVED

    def _chars_saved(self) -> int:
        """Schema characters removed, net of the catalogue that replaces them."""
        hidden = [
            schema for schema in self.all
            if self._name(schema) not in CORE_TOOLS and self._name(schema) not in self.revealed
        ]
        if not hidden:
            return 0
        cost = len(json.dumps(hidden, ensure_ascii=False))
        catalog = len(json.dumps(search_schema(hidden), ensure_ascii=False))
        return cost - catalog

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
