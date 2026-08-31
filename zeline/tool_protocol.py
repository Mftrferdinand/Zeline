"""Repair the tool-call protocol instead of deleting the work that broke it.

Every OpenAI-compatible provider enforces one rule: an ``assistant`` message
carrying ``tool_calls`` must be followed by a ``tool`` message for *each* of
those call ids. A history that violates it is rejected with a 400, so the whole
session becomes unusable until something fixes it.

Zeline used to fix it by amputation — ``_drop_incomplete_tail`` popped the
trailing ``tool`` results and then the ``assistant`` message that asked for
them. That satisfies the provider, but it throws away real work. When ``/stop``
lands after three of five tool calls have already finished, those three results
are gone, and so is the plan the model wrote alongside them. The next turn
starts blind and usually re-runs the same commands.

This module repairs instead. Unanswered call ids get a synthetic ``tool``
message that states plainly that the call did not complete, so:

* answered calls keep their real results,
* the assistant's narration survives,
* the model can see which call was interrupted and decide whether to retry.

The placeholder never invents an outcome. "This call was cancelled" is useful;
a fabricated result would be a lie the model would then reason from.

Amputation also only ever looked at the *tail*. A dangling call in the middle of
the history — a parallel batch where one worker raised, a transcript restored
from an older Zeline, a crash between appending the assistant message and the
results — was left in place and kept returning 400 on every subsequent turn with
no way out except wiping the session. ``repair`` scans the entire history, so
that class of dead session is fixed rather than dropped.
"""

from __future__ import annotations

from typing import Any

#: Reason text for a call the runtime never finished (``/stop``, crash, timeout).
CANCELLED_NOTE = (
    "This tool call did not complete: the turn was interrupted before a result "
    "was produced. No output is available. Re-run the call if you still need it."
)

#: Reason text for a call whose arguments the provider sent malformed.
MALFORMED_NOTE = (
    "This tool call could not be executed: the arguments were malformed or "
    "truncated. Re-issue it with valid arguments."
)


def _tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    calls = message.get("tool_calls")
    return [call for call in calls if isinstance(call, dict)] if isinstance(calls, list) else []


def _call_id(call: dict[str, Any]) -> str:
    return str(call.get("id") or "")


def _call_name(call: dict[str, Any]) -> str:
    function = call.get("function")
    if isinstance(function, dict):
        name = str(function.get("name") or "")
        if name:
            return name
    return "unknown"


def _has_arguments(call: dict[str, Any]) -> bool:
    """False when the provider sent a call with no usable argument payload."""
    function = call.get("function")
    if not isinstance(function, dict):
        return False
    return function.get("arguments") is not None


def _placeholder(call: dict[str, Any]) -> dict[str, Any]:
    note = CANCELLED_NOTE if _has_arguments(call) else MALFORMED_NOTE
    return {
        "role": "tool",
        "tool_call_id": _call_id(call),
        "name": _call_name(call),
        "content": f"[zeline] {note}",
    }


def unanswered_call_ids(messages: list[dict[str, Any]]) -> list[str]:
    """Call ids that no ``tool`` message in ``messages`` answers.

    Exposed for diagnostics and tests; ``repair`` uses the same rule.
    """
    answered = {
        str(message.get("tool_call_id") or "")
        for message in messages
        if message.get("role") == "tool"
    }
    missing: list[str] = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for call in _tool_calls(message):
            call_id = _call_id(call)
            if call_id and call_id not in answered and call_id not in missing:
                missing.append(call_id)
    return missing


def repair(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return ``messages`` with a placeholder result for every dangling call.

    The input is not mutated. When nothing dangles the original list is returned
    unchanged, so the common path costs one scan and no allocation.

    Placeholders are inserted directly after the run of real ``tool`` messages
    that follows their ``assistant`` message. Keeping every result adjacent to
    the call that asked for it is what the providers actually validate, and it
    keeps the transcript readable in the archive.
    """
    if not unanswered_call_ids(messages):
        return messages

    answered = {
        str(message.get("tool_call_id") or "")
        for message in messages
        if message.get("role") == "tool"
    }
    repaired: list[dict[str, Any]] = []
    index = 0
    total = len(messages)
    while index < total:
        message = messages[index]
        repaired.append(message)
        index += 1
        calls = _tool_calls(message) if message.get("role") == "assistant" else []
        if not calls:
            continue
        # Carry over the real results that already follow this call batch, then
        # top up the ids nothing answered.
        while index < total and messages[index].get("role") == "tool":
            repaired.append(messages[index])
            index += 1
        for call in calls:
            call_id = _call_id(call)
            if not call_id or call_id in answered:
                continue
            repaired.append(_placeholder(call))
            answered.add(call_id)
    return repaired
