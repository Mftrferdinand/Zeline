"""llm.py — provider-agnostic chat+tools adapter (Anthropic / OpenAI / Gemini).

The CANONICAL conversation format is the Anthropic Messages shape:
  - {"role": "user"|"assistant", "content": str | [blocks]}
  - blocks: {"type":"text",...} | {"type":"tool_use",...} | {"type":"tool_result",...}
The solver builds history in this format via `format_assistant` /
`format_tool_result`; each adapter converts canonical -> its wire format on
every step, so the same history can be replayed against any provider.

The adapter exposes one method:
    step(system, messages, tools) -> AssistantTurn
where AssistantTurn carries text, optional tool calls, and a NORMALIZED stop
reason ("tool_use" whenever the model requested a tool, else the provider's
native reason). Zero-dep on provider SDKs: plain `requests` REST calls.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import requests


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class AssistantTurn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
    raw: dict = field(default_factory=dict)


# --- canonical history helpers (Anthropic block shape) ----------------------

def format_tool_result(tool_call_id: str, content: str) -> dict:
    return {
        "role": "user",
        "content": [{
            "type": "tool_result",
            "tool_use_id": tool_call_id,
            "content": content,
        }],
    }


def format_assistant(turn: AssistantTurn) -> dict:
    content = []
    if turn.text:
        content.append({"type": "text", "text": turn.text})
    for tc in turn.tool_calls:
        content.append({"type": "tool_use", "id": tc.id,
                        "name": tc.name, "input": tc.input})
    return {"role": "assistant", "content": content}


def _iter_blocks(msg: dict):
    """Yield canonical content blocks; bare-string content becomes one text block."""
    content = msg.get("content", "")
    if isinstance(content, str):
        if content:
            yield {"type": "text", "text": content}
        return
    for block in content:
        yield block


def make_adapter(spec: str, api_key: str):
    provider, _, model = spec.partition(":")
    provider = provider.strip()
    model = model.strip()
    if provider == "anthropic":
        return AnthropicAdapter(model, api_key)
    if provider == "openai":
        return OpenAIAdapter(model, api_key)
    if provider == "gemini":
        return GeminiAdapter(model, api_key)
    raise ValueError(f"unknown provider '{provider}'")


class AnthropicAdapter:
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, model: str, api_key: str):
        self.model = model
        self.api_key = api_key

    def step(self, system: str, messages: list[dict], tools: list[dict]) -> AssistantTurn:
        body = {
            "model": self.model,
            "max_tokens": 4096,
            "system": system,
            "messages": messages,
            "tools": tools,
        }
        r = requests.post(
            self.URL,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            data=json.dumps(body),
            timeout=120,
        )
        r.raise_for_status()
        data = r.json()
        turn = AssistantTurn(stop_reason=data.get("stop_reason", ""), raw=data)
        for block in data.get("content", []):
            if block["type"] == "text":
                turn.text += block["text"]
            elif block["type"] == "tool_use":
                turn.tool_calls.append(
                    ToolCall(id=block["id"], name=block["name"], input=block["input"])
                )
        return turn

    # kept as aliases — canonical helpers live at module level
    format_tool_result = staticmethod(format_tool_result)
    format_assistant = staticmethod(format_assistant)


# --- OpenAI (Chat Completions, function tools) -------------------------------

def to_openai(system: str, messages: list[dict], tools: list[dict]):
    """Canonical history + Anthropic-style tool defs -> OpenAI wire format."""
    oai_tools = [{
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
        },
    } for t in tools]

    oai_msgs: list[dict] = [{"role": "system", "content": system}]
    for msg in messages:
        if msg["role"] == "assistant":
            out = {"role": "assistant", "content": "", "tool_calls": []}
            for block in _iter_blocks(msg):
                if block["type"] == "text":
                    out["content"] += block["text"]
                elif block["type"] == "tool_use":
                    out["tool_calls"].append({
                        "id": block["id"],
                        "type": "function",
                        "function": {"name": block["name"],
                                     "arguments": json.dumps(block["input"])},
                    })
            if not out["tool_calls"]:
                del out["tool_calls"]
            if not out["content"]:
                out["content"] = None
            oai_msgs.append(out)
        else:  # user turn: plain text and/or tool results
            text = ""
            for block in _iter_blocks(msg):
                if block["type"] == "text":
                    text += block["text"]
                elif block["type"] == "tool_result":
                    oai_msgs.append({"role": "tool",
                                     "tool_call_id": block["tool_use_id"],
                                     "content": block["content"]})
            if text:
                oai_msgs.append({"role": "user", "content": text})
    return oai_msgs, oai_tools


def from_openai(data: dict) -> AssistantTurn:
    """OpenAI chat.completions response -> AssistantTurn (normalized stop)."""
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message", {}) or {}
    turn = AssistantTurn(text=msg.get("content") or "", raw=data)
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        turn.tool_calls.append(ToolCall(id=tc.get("id", ""),
                                        name=fn.get("name", ""), input=args))
    finish = choice.get("finish_reason", "")
    turn.stop_reason = "tool_use" if (finish == "tool_calls" or turn.tool_calls) else finish
    return turn


class OpenAIAdapter:
    URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, model: str, api_key: str):
        self.model, self.api_key = model, api_key

    def step(self, system, messages, tools) -> AssistantTurn:
        oai_msgs, oai_tools = to_openai(system, messages, tools)
        body = {"model": self.model, "messages": oai_msgs,
                "tools": oai_tools, "max_completion_tokens": 4096}
        r = requests.post(
            self.URL,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "content-type": "application/json"},
            data=json.dumps(body),
            timeout=120,
        )
        r.raise_for_status()
        return from_openai(r.json())


# --- Gemini (generateContent, function calling) -------------------------------

def to_gemini(system: str, messages: list[dict], tools: list[dict]) -> dict:
    """Canonical history -> Gemini generateContent body.

    Gemini functionResponse parts are keyed by function NAME (no call ids), so
    we map tool_use_id -> name from earlier assistant turns while converting.
    """
    decls = [{
        "name": t["name"],
        "description": t.get("description", ""),
        "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
    } for t in tools]

    id_to_name: dict[str, str] = {}
    contents: list[dict] = []
    for msg in messages:
        role = "model" if msg["role"] == "assistant" else "user"
        parts: list[dict] = []
        for block in _iter_blocks(msg):
            if block["type"] == "text":
                parts.append({"text": block["text"]})
            elif block["type"] == "tool_use":
                id_to_name[block["id"]] = block["name"]
                parts.append({"functionCall": {"name": block["name"],
                                               "args": block["input"]}})
            elif block["type"] == "tool_result":
                name = id_to_name.get(block["tool_use_id"], "run")
                parts.append({"functionResponse": {
                    "name": name,
                    "response": {"result": block["content"]},
                }})
        if parts:
            contents.append({"role": role, "parts": parts})

    return {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "tools": [{"functionDeclarations": decls}],
        "generationConfig": {"maxOutputTokens": 4096},
    }


def from_gemini(data: dict) -> AssistantTurn:
    """Gemini generateContent response -> AssistantTurn (normalized stop).

    Gemini emits no call ids; we synthesize stable ones (gemini-call-N) — they
    only need to be consistent inside the canonical history.
    """
    cand = (data.get("candidates") or [{}])[0]
    parts = (cand.get("content") or {}).get("parts") or []
    turn = AssistantTurn(raw=data)
    for i, part in enumerate(parts):
        if "text" in part:
            turn.text += part["text"]
        elif "functionCall" in part:
            fc = part["functionCall"]
            turn.tool_calls.append(ToolCall(id=f"gemini-call-{i}",
                                            name=fc.get("name", ""),
                                            input=fc.get("args") or {}))
    finish = cand.get("finishReason", "")
    turn.stop_reason = "tool_use" if turn.tool_calls else finish.lower()
    return turn


class GeminiAdapter:
    URL_TMPL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, model: str, api_key: str):
        self.model, self.api_key = model, api_key

    def step(self, system, messages, tools) -> AssistantTurn:
        body = to_gemini(system, messages, tools)
        r = requests.post(
            self.URL_TMPL.format(model=self.model),
            headers={"x-goog-api-key": self.api_key,
                     "content-type": "application/json"},
            data=json.dumps(body),
            timeout=120,
        )
        r.raise_for_status()
        return from_gemini(r.json())
