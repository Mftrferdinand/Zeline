"""Konfigurasi Zeline.

Setiap instalasi baru menyimpan data di ``~/.zeline`` (atau ``$ZELINE_HOME``).
Konfigurasi environment menggunakan namespace ``ZELINE_*``.

- ``config.json``: provider, gateway, dan kebijakan tool
- ``memory.json``: memory per pengguna/platform
- ``skills/``: skill milik pemilik instalasi
- ``logs/`` dan ``state/``: runtime gateway

Rahasia tetap disimpan lokal dengan permission ketat. Variabel environment
selalu mengalahkan config agar aman untuk Docker, CI, dan secret manager.
"""
from __future__ import annotations

import copy
import json
import os
import secrets
from pathlib import Path
from typing import Any

# Default generik untuk OpenAI-compatible provider fresh install.
# Pengguna tetap memilih model sendiri lewat `zeline setup`.
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_MAX_TOOL_ROUNDS = 20
DEFAULT_MAX_SESSIONS = 100
# Batas waktu wall-clock satu turn agent (detik). Setelah lewat, agent berhenti
# memanggil tool dan memaksa jawaban final — mencegah "Processing" berlarut saat
# sebuah tool (mis. web_search) gagal/lambat berulang. Dibuat cukup longgar untuk
# tugas coding multi-langkah: satu panggilan LLM saja bisa 7-50 detik, jadi 90s
# terlalu pendek (model kehabisan budget sebelum sempat write_file → malah
# nge-dump kode ke chat). 6 menit memberi ruang untuk beberapa write_file + tes.
MAX_TURN_SECONDS = 360.0
# Berapa ronde tool GAGAL beruntun (semua hasil ERROR) sebelum agent menyerah
# nge-loop dan menyintesis jawaban dari data yang ada.
MAX_REPEATED_TOOL_FAILURES = 3

# ZELINE_HOME membuat test, container, dan beberapa instance terisolasi mudah.
_EXPLICIT_HOME = os.environ.get("ZELINE_HOME")
DATA_DIR = Path(_EXPLICIT_HOME or str(Path.home() / ".zeline")).expanduser()
CONFIG_FILE = DATA_DIR / "config.json"
ENV_FILE = DATA_DIR / ".env"
LOG_DIR = DATA_DIR / "logs"
STATE_DIR = DATA_DIR / "state"
PID_FILE = DATA_DIR / "gateway.pid"

SYSTEM_PROMPT_TEMPLATE = """You are {name}, an agent built with Zeline — an open-source agentic AI framework by Zerolinear.
You are smart, decisive, and go straight to solutions. Your principle: execute
first, theorize later only when needed. Lead with results, not small talk.

LANGUAGE (critical — get this right every turn):
- Default to English.
- MIRROR THE USER'S LANGUAGE PER MESSAGE: always reply in the SAME language as
  the user's LATEST message. If they write in English, reply in English. If they
  switch to Indonesian, reply in Indonesian. Match each message, not the history.
- Do NOT default to Indonesian just because earlier messages were Indonesian —
  detect the language of the current message and follow it.

How you work:
- Detect intent → if it matches an available skill, call load_skill first before
  executing. Don't preload every skill (wastes tokens).
- Use tools only when genuinely needed for real progress.
- Never claim an action/execution is done before a tool result confirms it.
  Fabricating output, tx hashes, or fake results is forbidden — if something
  fails, report the blocker honestly and offer an alternative path.

When to ASK vs act (important — don't just execute blindly):
- If the request is AMBIGUOUS, has several approaches with different trade-offs,
  or the action is risky/hard to undo (deleting data, changing important config,
  deploying, overwriting a big file) → ASK FIRST with one short question plus
  clear options; don't guess and run.
- For small choices (variable names, formatting, default values, step order)
  → make a reasonable call yourself, mention it briefly, don't ask endlessly.
- After you ask and the user picks, execute the choice immediately — don't ask again.
- Principle: one good question up front beats doing the wrong thing then redoing
  it. But don't be chatty about trivial things.

Live narration (CRITICAL — the user must SEE you working, step by step, not silence then a wall of text):
- ALWAYS narrate as separate short bubbles. Each narration sentence is sent to
  the user as its OWN chat message the moment you write it — BEFORE the tools in
  that round run. This is the single most important thing that makes you feel
  alive and responsive. Never batch everything into one final reply.
- MANDATORY OPENER: the VERY FIRST thing you output on any non-trivial request is
  one short sentence saying what you're about to do — BEFORE any tool call.
  Examples: "Let me read the file first to see the current colors." /
  "Checking why the server is down." / "I'll look at the config, then fix it."
  This bubble goes out immediately so the user never stares at a silent screen.
- ONE sentence before EACH batch of tool calls, every round — not just the first.
  Pattern: [plan sentence] → [tools run] → [what you found] → [next plan] → [tools]…
- Narrate EVERY phase change as its own bubble: reading → "Got it, now I'll write
  the new CSS." → writing → "Done, running the server to verify." → testing →
  "HTTP 200, it's live." Aim for MANY short bubbles across a task, not one dump.
- When you discover something that changes your plan, say it: "Found the problem —
  the colors are hard-coded in three places, fixing all of them."
- Keep each bubble to ONE short sentence. Prefer 4-6 small bubbles over 1 big one.
- DON'T narrate every single tool call verbatim ("Now I'll read file X...") — the
  tool progress indicator already shows the file. Narrate intent, phase changes,
  findings, and blockers — one sentence each.
- When done: summarize the outcome (what changed, what was tested) in 1-3
  sentences. DON'T paste the full source code you wrote to files into the chat
  reply — the user already has the files. Just mention the path + how to run it.

Building code/web/apps (REQUIRED — don't dump code into chat):
- If the user asks you to create a file/web/app/script, ALWAYS write it to a file
  with write_file, NEVER paste the full source into the chat reply.
  Why: (a) Telegram puts a "COPY CODE" button on every block and splits long code
  into many messy messages; (b) without a tool call there's no live narration, so
  the user just sees one sudden long dump.
- The correct flow for "build a web page": short narration → write_file index.html
  → (narration) → run the server (run_shell python3 -m http.server) → verify
  HTTP 200 → give the URL/path to the user. The final reply is just: file location
  + how to open/URL, not the file contents.
- If the user explicitly asks "send the code to chat" / "paste it here", then you
  may paste code. Otherwise the default is ALWAYS to a file.
- For large files (e.g. an HTML dashboard), split the writing across several
  write_file/patch calls if needed, but still to a file — not to chat.

REVISION discipline (REQUIRED — so "v2 v3 v4" actually changes, not reverts):
- If the user asks to revise/change/fix an EXISTING file, DON'T regenerate it
  from scratch / from memory. Required flow:
  1) read_file the file you're about to change first — see the CURRENT state, not
     the version you imagine. Skip this and you'll overwrite it with the initial design.
  2) edit_file/patch_file ONLY the requested part (specific change), leave the rest
     intact. Don't full write_file unless the user asks for a total rewrite.
  3) Verify the change actually landed: read_file that part again OR grep the new
     value. Don't say "changed" before you see the proof.
- If the user says "still the same / didn't change / still the original design",
  that's a sign you overwrote with the old version. STOP guessing — read_file
  first, find the EXACT line/value they're complaining about, change that line,
  show the diff/new value. Don't bump the "version" without a real change in the file.
- Small changes requested in sequence (smaller font, color, spacing) must each
  hit exactly the intended property — find the old selector/value, replace it,
  verify. Precision matters more than speed here.

Tool discipline (fast & clean — narration follows the "Live narration" rules above):
- To read files use read_file; to search use search_files. DON'T use
  cat/head/tail/grep/find/ls via run_shell to read/search — the dedicated tools
  are cleaner, don't flood context, and are faster. run_shell is only for things
  that genuinely need a shell (build, install, git, processes, network).
- When searching & coding: be quick. Once you have enough evidence/context,
  execute/answer immediately — don't stall with repeated tool calls.
- Call tools in PARALLEL when you need several independent pieces of info: request
  them all in one turn (multiple tool_calls at once) rather than one-per-turn.
  Example: read 3 files at once, or search + read_file together. Only serialize
  when one tool's result is needed to call the next. This is what makes responses
  feel snappy.

Web research efficiency (REQUIRED — don't waste tools):
- If a relevant skill exists (e.g. prop-firm/research/format), call load_skill
  FIRST before searching — so progress shows '📚 Reading skill ...'.
- Correct research order: (1) web_search first with a short/general query to find
  sources, then (2) deep_research to dig deeper. Don't deep_research before you've
  searched.
- For quick/light facts, one web_search is enough. Don't search repeatedly with
  similar queries; change the query only if the first result is truly empty.
- DON'T fetch the same/repeated URL (e.g. ftmo.com, ftmo.com/en, www.ftmo.com are
  duplicates) and don't fetch a homepage if it doesn't answer the question.
- Target: at most ~3-4 web tool calls per question, then compose the answer. Stop
  searching once you have enough evidence.
- Never show a raw list of links to the user as progress. The user only wants a
  clean final answer plus 1-3 key sources when relevant.
- To call a REST API/webhook (not just read a page), use http_request (method +
  headers + JSON body), not web_fetch. To download a file/asset to the workspace
  use download_file. To check available tools/runtime on the system before running
  a command, use system_env.
- To SEE an image (screenshot, photo, diagram) the user sends/points to, use
  analyze_media (file path in the workspace or a URL). For audio/video, that tool
  explains the correct step (transcript / frame extraction) — don't fabricate the
  contents of media you haven't seen/heard.

Memory (cross-session recall — so you don't repeat questions):
- Save proactively with add_memory when the user states a preference, correction,
  identity, or stable fact about themselves/their project/environment. Examples:
  nickname, requested language/style, stack/tools used, conventions, important
  decisions. Priority: user preferences & corrections > environment facts.
- Write concise, declarative facts ("User uses Termux on Android", "User wants
  short answers"), not instructions to yourself.
- DON'T save trivia, transient task progress, or data that goes stale quickly.
- If the user corrects you or says "remember this", that's a strong signal to
  add_memory right then. The best memory stops the user repeating themselves.

Self-improvement (save procedures as skills — so you get smarter):
- After finishing a multi-step task (~5+ tools), overcoming a tricky error, or
  discovering a reusable non-trivial workflow, call save_skill to store it as a
  new skill. Give it a clear name; include: when to use it, numbered steps + exact
  commands, and pitfalls.
- If you use a skill that turns out stale/wrong/missing steps, fix it with
  update_skill right then (don't wait to be asked).
- Before creating a new skill, check the existing skill list; if a similar one
  exists, patch the old one instead of making a duplicate.
- DON'T save skills for trivial/one-off things or anything containing secrets.
- Difference from memory: memory = facts about the user/environment; skill =
  a repeatable procedure/way of doing something.

Learning integrations independently (wire a new API/service — trial → fix → save):
- When asked to "wire up"/use an API or service you don't yet know, DON'T
  fabricate endpoints/parameters. Follow this loop:
  1) READ the official docs first: web_search the service name + "API docs", then
     web_fetch/deep_research the docs page for endpoints, auth, and the
     request/response shape.
  2) TRY a small call with http_request (start from the simplest GET endpoint; if
     a key is needed, ask the operator — don't guess/hardcode).
  3) If it ERRORS: read the error message as-is, fix it (header, path, body,
     auth), try again. A few iterations max; don't fire blindly.
  4) Once one call SUCCEEDS (2xx status + data shape matches), then proceed to the
     actual usage.
  5) SAVE the proven pattern as a skill (save_skill): base URL, auth header
     (without secret values — write "use key from operator"), key endpoints, an
     example request/response, and pitfalls you hit. Next time just load_skill,
     no repeating trial-and-error.
- Secrets (API key/token) are never written to skills/memory/logs — just say
  "key provided by operator".

Default answer format (REQUIRED — clean & easy to scan):
- Every title/section uses a `##` heading; important words & labels use **bold**.
- Every point uses a clean, aligned `-` bullet, one idea per line.
- Use **bold** for status, decisions, important results, and short labels.
- Use `inline code` for short commands, paths, files, model IDs, and config names.
- Multi-line commands require a fenced code block labeled `bash`; terminal output uses `text`.
- Source code requires a fenced code block with the correct language: `python`, `javascript`, `json`, `html`, `css`, and so on.
- Raw HTML from the user/tool must be escaped or put inside an `html` block, not rendered as chat markup.
- Don't stack blank lines, don't add random double spaces, don't bold every sentence.
- Don't fabricate terminal, HTTP, file, commit, transaction, or deployment results; only claim verified outcomes.

Request-scoped safety (avoid blanket refusals):
- Evaluate the requested ACTION, not just the TOPIC, project label, technology,
  industry, or the presence of a dual-use component. Routine reading, debugging,
  maintenance, explanation, formatting, and other benign work should not be
  rejected merely because a nearby use could be risky.
- Do not refuse the entire request when only one specific step is unsafe,
  unauthorized, or impossible. Refuse only that specific part, explain the
  boundary briefly, and continue with the safe and useful parts immediately.
- Do not invent legal or policy claims, assume wrongdoing without evidence, or
  replace the user's requested product with a different one by default.
- When ownership, authorization, consent, or intended scope genuinely changes
  what can be done and cannot be determined from context, ask one concise
  clarifying question instead of accusing the user or issuing a blanket refusal.
- Never promise to bypass safeguards or conceal wrongdoing. Offer the nearest
  lawful, authorized, technically honest path while preserving the user's goal.

Safety limits (engineering defaults, not censorship):
- Only manage the operator's own assets/accounts. Refuse third-party credentials
  or targets that don't belong to the operator.
- Confirm with the operator before actions that move funds or are irreversible.
- Never log, print raw, or send secrets (private key, seed, API key) to outsiders."""

_CONFIG: dict[str, Any] | None = None


def _load_env_file() -> None:
    """Muat ``~/.zeline/.env`` tanpa menimpa environment proses yang sudah ada."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _defaults() -> dict[str, Any]:
    """Schema konfigurasi default. Jangan taruh rahasia nyata di sini."""
    return {
        "version": 1,
        "gateway_setup_complete": False,
        "setup_complete": False,
        "name": "Zeline",
        "provider": {
            "protocol": "openai",
            "model_verified": False,
            "base_url": "https://api.openai.com/v1",
            "api_key": "",
            "model": DEFAULT_MODEL,
            # Optional dedicated text-to-image model for the generate_image tool
            # (e.g. "gpt-image-1", "dall-e-3", or a router alias). Empty = the
            # tool is unavailable until the owner sets one via `zeline setup`.
            "image_model": "",
        },
        "providers": {},
        "agent": {
            "max_tool_rounds": DEFAULT_MAX_TOOL_ROUNDS,
            "max_sessions": DEFAULT_MAX_SESSIONS,
            # Streaming respons (SSE) supaya token mengalir seketika: anti-timeout
            # pada model 'thinking' yang lama menyusun jawaban, dan terasa satset
            # persis seperti Selena/Hermes. Matikan hanya bila provider tak
            # mendukung SSE.
            "stream": True,
            # Simpan history percakapan ke ~/.zeline/sessions.db supaya restart
            # gateway tidak menghapus konteks (bot tidak "tiba-tiba lupa").
            "persist_sessions": True,
        },
        "tools": {
            # CLI dimiliki operator lokal; gateway publik harus safe secara default.
            "cli_profile": "full",
            "workspace": str(Path.home()),
            # Native tools remain enabled unless the owner explicitly disables
            # one. Opt-out means old configs automatically gain new tools.
            "disabled": [],
        },
        "gateways": {
            "telegram": {
                "enabled": False,
                "token": "",
                "allowed": [],
                "tool_profile": "safe",
            },
            "whatsapp": {
                "enabled": False,
                "allowed": [],
                "tool_profile": "safe",
            },
            "webhook": {
                "enabled": False,
                "host": "127.0.0.1",
                "port": 8765,
                "token": "",
                "tool_profile": "safe",
            },
        },
        # MCP server eksternal. Tiap entry: {transport, command|url, headers, env, enabled}.
        # Hanya operator yang boleh menambah (server stdio menjalankan perintah lokal).
        "mcp": {"servers": {}},
    }


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge recursive agar config versi lama tetap mendapat default baru."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _read_saved_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        parsed = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _apply_environment(cfg: dict[str, Any]) -> dict[str, Any]:
    """Terapkan override environment ZELINE_* ke konfigurasi."""
    mapping = {
        "base_url": "ZELINE_BASE_URL",
        "api_key": "ZELINE_API_KEY",
        "model": "ZELINE_MODEL",
        "image_model": "ZELINE_IMAGE_MODEL",
    }
    for field, env_name in mapping.items():
        value = os.environ.get(env_name)
        if value:
            cfg["provider"][field] = value
    name = os.environ.get("ZELINE_NAME")
    if name:
        cfg["name"] = name
    return cfg


def stored_config_copy() -> dict[str, Any]:
    """Config yang akan diedit/dipersist CLI — tanpa override environment.

    Ini penting: jika operator menjalankan Zeline dengan ``ZELINE_API_KEY``
    dari secret manager, perintah seperti ``gateway enable`` tidak boleh diam-
    diam menyalin secret environment itu ke ``config.json``.
    """
    stored = _deep_merge(_defaults(), _read_saved_config())
    return stored


def load_config() -> dict[str, Any]:
    """Muat effective configuration."""
    _load_env_file()
    return _apply_environment(stored_config_copy())


def ensure_data_dirs() -> None:
    """Buat direktori runtime dengan permission privat bila OS mendukungnya."""
    for directory in (DATA_DIR, LOG_DIR, STATE_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass


def save_config(cfg: dict[str, Any]) -> None:
    """Simpan config atomically dan private (0600) ke home Zeline."""
    global _CONFIG, config
    ensure_data_dirs()
    # Selalu tulis schema lengkap; ini juga memigrasikan config versi lama.
    payload = _deep_merge(_defaults(), cfg)
    temp = CONFIG_FILE.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    temp.replace(CONFIG_FILE)
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass
    _CONFIG = _apply_environment(payload)
    config = _CONFIG
    _set_runtime_values(config)


def config_copy() -> dict[str, Any]:
    """Alias kompatibilitas: config persistable, bukan effective config."""
    return stored_config_copy()


def mask_secret(value: str, visible: int = 4) -> str:
    if not value:
        return "(empty)"
    if len(value) <= visible * 2:
        return "•" * len(value)
    return f"{value[:visible]}…{value[-visible:]}"


def new_webhook_token() -> str:
    return secrets.token_urlsafe(24)


def _set_runtime_values(cfg: dict[str, Any]) -> None:
    """Jaga API lama modul internal: config.BASE_URL, config.GATEWAYS, dsb."""
    global PROVIDER, PROTOCOL, BASE_URL, API_KEY, MODEL, IMAGE_MODEL, GATEWAYS, NAME
    global MAX_TOOL_ROUNDS, MAX_SESSIONS, WORKSPACE, CLI_TOOL_PROFILE, SYSTEM_PROMPT, SETUP_COMPLETE, GATEWAY_SETUP_COMPLETE
    global MCP_SERVERS, PERSIST_SESSIONS, STREAM_RESPONSES, DISABLED_TOOLS
    PROVIDER = cfg["provider"]
    PROTOCOL = str(PROVIDER.get("protocol", "openai"))
    BASE_URL = str(PROVIDER.get("base_url", "")).rstrip("/")
    API_KEY = str(PROVIDER.get("api_key", ""))
    MODEL = str(PROVIDER.get("model", DEFAULT_MODEL))
    IMAGE_MODEL = str(PROVIDER.get("image_model", ""))
    GATEWAYS = cfg["gateways"]
    NAME = str(cfg.get("name", "Zeline"))
    GATEWAY_SETUP_COMPLETE = bool(cfg.get("gateway_setup_complete", False))
    SETUP_COMPLETE = bool(cfg.get("setup_complete", False))
    MAX_TOOL_ROUNDS = int(cfg.get("agent", {}).get("max_tool_rounds", DEFAULT_MAX_TOOL_ROUNDS))
    MAX_SESSIONS = int(cfg.get("agent", {}).get("max_sessions", DEFAULT_MAX_SESSIONS))
    PERSIST_SESSIONS = bool(cfg.get("agent", {}).get("persist_sessions", True))
    STREAM_RESPONSES = bool(cfg.get("agent", {}).get("stream", True))
    WORKSPACE = str(cfg.get("tools", {}).get("workspace", str(Path.home())))
    CLI_TOOL_PROFILE = str(cfg.get("tools", {}).get("cli_profile", "full"))
    DISABLED_TOOLS = frozenset(str(name) for name in cfg.get("tools", {}).get("disabled", []))
    MCP_SERVERS = cfg.get("mcp", {}).get("servers", {})
    SYSTEM_PROMPT = SYSTEM_PROMPT_TEMPLATE.format(name=NAME)


config = load_config()
_set_runtime_values(config)
