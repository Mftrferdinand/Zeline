#!/usr/bin/env python3
"""Muse poller: standalone reasoning-engine loop for the Muse bridge.

Polls <bridge_dir>/pending/ every MUSE_POLLER_INTERVAL seconds. For each
request it:
  1. drops it silently if older than MUSE_POLLER_STALE_SECS,
  2. mechanically trims the message history if the request is large
     (prevents reasoning timeouts / context blowups),
  3. asks the configured OpenAI-compatible LLM (system prompt from
     MUSE_POLLER_SYSTEM_PROMPT) for the next step,
  4. writes {"content": ..., "tool_calls": [...]} to <bridge_dir>/done/<id>.json
     and removes the pending file.

The LLM is the "brain"; this script is only the loop around it. Any
OpenAI-compatible endpoint works (your own 9Router, OpenAI, etc.).

Config (env vars):
  MUSE_BRIDGE_DIR          queue root (default ~/.muse-bridge/queue)
  MUSE_POLLER_BASE_URL     LLM base URL, e.g. http://localhost:20128/v1
  MUSE_POLLER_API_KEY      LLM API key (keep secret, never commit)
  MUSE_POLLER_MODEL        model name to request (default "muse")
  MUSE_POLLER_SYSTEM_PROMPT path to system prompt (default ./system-prompt.md)
  MUSE_POLLER_INTERVAL     poll seconds (default 10)
  MUSE_POLLER_STALE_SECS   drop requests older than this (default 220)
  MUSE_POLLER_MAX_PER_CYCLE max requests per cycle (default 5)
  MUSE_POLLER_TRIM_BYTES   trim histories above this size (default 200000)
  MUSE_POLLER_JSON_MODE    "1" to send response_format json_object (default 0)
  MUSE_POLLER_TEMPERATURE  default temperature when request omits it (0.7)

Stdlib only — no third-party dependencies.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def env(name, default):
    return os.environ.get(name, default)


BRIDGE_DIR = env("MUSE_BRIDGE_DIR", os.path.join(os.path.expanduser("~"), ".muse-bridge", "queue"))
PENDING = os.path.join(BRIDGE_DIR, "pending")
DONE = os.path.join(BRIDGE_DIR, "done")
LLM_BASE_URL = env("MUSE_POLLER_BASE_URL", "http://localhost:20128/v1").rstrip("/")
LLM_API_KEY = env("MUSE_POLLER_API_KEY", "")
LLM_MODEL = env("MUSE_POLLER_MODEL", "muse")
SYSTEM_PROMPT_PATH = env("MUSE_POLLER_SYSTEM_PROMPT", os.path.join(HERE, "system-prompt.md"))
INTERVAL = float(env("MUSE_POLLER_INTERVAL", "10"))
STALE_SECS = float(env("MUSE_POLLER_STALE_SECS", "220"))
MAX_PER_CYCLE = int(env("MUSE_POLLER_MAX_PER_CYCLE", "5"))
TRIM_BYTES = int(env("MUSE_POLLER_TRIM_BYTES", "200000"))
JSON_MODE = env("MUSE_POLLER_JSON_MODE", "0") == "1"
DEFAULT_TEMPERATURE = float(env("MUSE_POLLER_TEMPERATURE", "0.7"))
HTTP_TIMEOUT = float(env("MUSE_POLLER_HTTP_TIMEOUT", "150"))


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} [poller] {msg}", flush=True)


def _msg_len(m):
    c = m.get("content", "")
    if isinstance(c, str):
        return len(c)
    return len(json.dumps(c, ensure_ascii=False))


def trim_messages(messages):
    """Mechanical history trim (mirrors the v4 poller rules).

    - system message: cut to 15000 chars
    - ALL user messages: kept intact
    - last 12 messages: kept, each capped at 8000 chars
    - middle assistant/tool messages: dropped, replaced by one summary note
    The original pending file is never modified; we reason on the trimmed copy.
    """
    if not messages:
        return messages
    out = []
    # system (first message) trimmed
    first = dict(messages[0])
    if isinstance(first.get("content"), str) and len(first["content"]) > 15000:
        first["content"] = first["content"][:15000] + "...[trimmed]"
    out.append(first)

    rest = messages[1:]
    if len(rest) <= 12:
        tail = rest
        middle = []
    else:
        tail, middle = rest[-12:], rest[:-12]

    dropped_tools = set()
    for m in middle:
        if m.get("role") == "user":
            out.append(m)  # user instructions are never dropped
        else:
            for tc in (m.get("tool_calls") or []):
                try:
                    dropped_tools.add(tc["function"]["name"])
                except Exception:
                    pass
    if middle:
        out.append({"role": "user", "content": (
            f"[history trimmed: {len(middle)} middle messages omitted; "
            f"tools used there: {sorted(dropped_tools) or 'none'}]")})

    for m in tail:
        m = dict(m)
        if isinstance(m.get("content"), str) and len(m["content"]) > 8000:
            m["content"] = m["content"][:8000] + f"...[trimmed {len(m['content']) - 8000} chars]"
        out.append(m)
    return out


def call_llm(messages, max_tokens=None, temperature=None):
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": DEFAULT_TEMPERATURE if temperature is None else temperature,
    }
    if max_tokens:
        try:
            payload["max_tokens"] = int(max_tokens)
        except Exception:
            pass
    if JSON_MODE:
        payload["response_format"] = {"type": "json_object"}
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{LLM_BASE_URL}/chat/completions", data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {LLM_API_KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"LLM HTTP {e.code}: {detail}")
    try:
        return body["choices"][0]["message"]["content"] or ""
    except Exception:
        raise RuntimeError(f"unexpected LLM response shape: {str(body)[:300]}")


def _strip_fences(text):
    t = text.strip()
    if t.startswith("```"):
        # drop first fence line and trailing fence
        lines = t.splitlines()
        if len(lines) >= 2:
            lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            t = "\n".join(lines).strip()
            # also drop a leading "json" marker line if fenced as ```json
    return t


def parse_answer(text):
    """Parse the LLM answer into {"content": str, "tool_calls": [...]}.

    Contract: the model returns one JSON object. Be lenient: strip fences,
    fall back to treating the whole text as content.
    """
    t = _strip_fences(text)
    try:
        obj = json.loads(t)
    except Exception:
        # try to find the outermost {...} span
        start, end = t.find("{"), t.rfind("}")
        if start != -1 and end > start:
            try:
                obj = json.loads(t[start:end + 1])
            except Exception:
                obj = None
        else:
            obj = None
    if not isinstance(obj, dict):
        return {"content": text.strip(), "tool_calls": []}
    content = obj.get("content", "")
    if not isinstance(content, str):
        content = str(content)
    calls = obj.get("tool_calls") or []
    if not isinstance(calls, list):
        calls = []
    norm = []
    for c in calls:
        if not isinstance(c, dict):
            continue
        fn = c.get("function") or {}
        name = str(fn.get("name", "") or c.get("name", "")).strip()
        if not name:
            continue
        args = fn.get("arguments", "")
        if not isinstance(args, str):
            try:
                args = json.dumps(args, ensure_ascii=False)
            except Exception:
                args = "{}"
        cid = str(c.get("id") or "")
        if not cid.startswith("call_"):
            import random
            cid = "call_" + "".join(random.choice("0123456789abcdef") for _ in range(6))
        norm.append({"id": cid, "type": "function",
                     "function": {"name": name, "arguments": args}})
    return {"content": content, "tool_calls": norm}


def process_one(path, system_prompt):
    rid = os.path.basename(path)[:-len(".json")]
    try:
        with open(path) as f:
            item = json.load(f)
    except Exception as e:
        log(f"{rid[:8]}: unreadable, dropping ({e})")
        _rm(path)
        return
    req = item.get("request", {})
    age = time.time() - float(item.get("received_at", 0))
    if age > STALE_SECS:
        log(f"{rid[:8]}: stale ({age:.0f}s), dropping")
        _rm(path)
        return

    size = os.path.getsize(path)
    messages = req.get("messages", [])
    if size > TRIM_BYTES:
        log(f"{rid[:8]}: {size} bytes > trim threshold, trimming")
        messages = trim_messages(messages)

    full_messages = [{"role": "system", "content": system_prompt}] + messages
    try:
        raw = call_llm(full_messages,
                       max_tokens=req.get("max_tokens"),
                       temperature=req.get("temperature"))
    except Exception as e:
        log(f"{rid[:8]}: LLM error: {e} — leaving for next cycle")
        return  # keep pending; retry next cycle (until stale)

    answer = parse_answer(raw)
    try:
        with open(os.path.join(DONE, f"{rid}.json"), "w") as f:
            json.dump(answer, f, ensure_ascii=False)
    except Exception as e:
        log(f"{rid[:8]}: cannot write done file: {e}")
        return
    _rm(path)
    n_calls = len(answer["tool_calls"])
    log(f"{rid[:8]}: answered (content {len(answer['content'])} chars, {n_calls} tool_calls)")


def _rm(path):
    try:
        os.remove(path)
    except Exception:
        pass


def main():
    if not LLM_API_KEY:
        print("ERROR: MUSE_POLLER_API_KEY is not set.", file=sys.stderr)
        sys.exit(2)
    try:
        with open(SYSTEM_PROMPT_PATH) as f:
            system_prompt = f.read()
    except Exception as e:
        print(f"ERROR: cannot read system prompt {SYSTEM_PROMPT_PATH}: {e}", file=sys.stderr)
        sys.exit(2)
    os.makedirs(PENDING, exist_ok=True)
    os.makedirs(DONE, exist_ok=True)
    log(f"started: bridge={BRIDGE_DIR} llm={LLM_BASE_URL} model={LLM_MODEL} "
        f"interval={INTERVAL}s (prompt {len(system_prompt)} chars)")
    while True:
        try:
            files = sorted(
                (os.path.join(PENDING, f) for f in os.listdir(PENDING) if f.endswith(".json")),
                key=lambda p: os.path.getmtime(p))
        except Exception as e:
            log(f"list error: {e}")
            files = []
        for path in files[:MAX_PER_CYCLE]:
            try:
                process_one(path, system_prompt)
            except Exception as e:
                log(f"process error on {path}: {e}")
        if len(files) > MAX_PER_CYCLE:
            log(f"backlog: {len(files)} pending (> {MAX_PER_CYCLE} per cycle)")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
