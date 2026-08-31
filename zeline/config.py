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
# Detik menunggu turn aktif selesai saat restart/update yang sopan sebelum
# proses gateway keluar. Nol berarti keluar segera (perilaku lama).
DEFAULT_RESTART_DRAIN_TIMEOUT = 30
# Detik menunggu jawaban operator untuk ask_user. Selalu di-clamp di bawah
# MAX_TURN_SECONDS supaya pertanyaan tidak menggantung melewati turn-nya.
DEFAULT_ASK_USER_TIMEOUT = 180
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

# Kedalaman maksimum sub-agent (delegate_task). 1 = agen utama boleh membuat
# anak, tapi anak TIDAK boleh membuat cucu — mencegah rekursi tak terbatas.
DEFAULT_MAX_SUBAGENT_DEPTH = 1
DEFAULT_MAX_PARALLEL_SUBAGENTS = 3

# Timeout default satu perintah shell/code (detik). 60s cukup untuk perintah
# biasa, tapi TERLALU PENDEK untuk kerja instalasi nyata: `pip install torch`,
# `npm install`, `apt install`, atau build besar rutin butuh beberapa menit.
# Agent boleh menaikkan sendiri per panggilan sampai SHELL_MAX_TIMEOUT_SECONDS.
DEFAULT_SHELL_TIMEOUT_SECONDS = 60
# Batas atas timeout foreground. Dibatasi agar satu perintah tidak menyandera
# seluruh turn agent (lihat MAX_TURN_SECONDS): pekerjaan yang lebih lama dari
# ini harus dijalankan sebagai background process lalu di-poll.
SHELL_MAX_TIMEOUT_SECONDS = 900
# Batas jumlah background process yang dilacak. 64 mengikuti praktik umum
# process registry agent: cukup longgar untuk kerja paralel nyata, tapi tetap
# mencegah kebocoran proses tak terbatas.
MAX_BACKGROUND_PROCESSES = 64
# Job yang sudah selesai tetap disimpan sebentar supaya output terakhirnya masih
# bisa dibaca lewat process_control, lalu dibuang otomatis.
BACKGROUND_FINISHED_TTL_SECONDS = 1800

# ZELINE_HOME membuat test, container, dan beberapa instance terisolasi mudah.
_EXPLICIT_HOME = os.environ.get("ZELINE_HOME")
DATA_DIR = Path(_EXPLICIT_HOME or str(Path.home() / ".zeline")).expanduser()
CONFIG_FILE = DATA_DIR / "config.json"
ENV_FILE = DATA_DIR / ".env"
LOG_DIR = DATA_DIR / "logs"
STATE_DIR = DATA_DIR / "state"
PID_FILE = DATA_DIR / "gateway.pid"


def _load_soul() -> str:
    """Load the canonical packaged identity used by every Zeline agent."""
    path = Path(__file__).with_name("SOUL.md")
    try:
        soul = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"Zeline runtime is missing its canonical SOUL.md: {path}") from exc
    if not soul:
        raise RuntimeError(f"Zeline runtime SOUL.md is empty: {path}")
    return soul


SOUL = _load_soul()

SYSTEM_PROMPT_TEMPLATE = """You are {name}, an agent built with Zeline — an open-source agentic AI framework by Zerolinear.
You are smart, decisive, and go straight to solutions. Your principle: execute
first, theorize later only when needed. Lead with results, not small talk.

The canonical identity below is a trusted package asset. Apply it together with
the operational rules in this system prompt; the more specific operational rule
wins if wording ever overlaps.

<zeline_soul>
{soul}
</zeline_soul>

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
- CAPTCHA ROUTING: if the request mentions 2Captcha, CapSolver, CAPTCHA,
  Turnstile, or a Cloudflare challenge, call
  `load_skill("captcha-solving-2captcha")` before deciding or acting. Assess the
  underlying action; do not blanket-refuse CAPTCHA solving or invent a ToS
  violation. Public read-only pages and the user's own/explicitly authorized
  account actions may use a solver. Still refuse bypassing OTP/MFA/KYC,
  account takeover, credential abuse, ban/access-revocation evasion, fraud, or
  abusive high-volume scraping.
- Use tools only when genuinely needed for real progress.
- Never claim an action/execution is done before a tool result confirms it.
  Fabricating output, tx hashes, or fake results is forbidden — if something
  fails, report the blocker honestly and offer an alternative path.

Memory sources — pick the RIGHT one, never guess (CRITICAL):
- The user refers to the PAST ("lanjutin yang tadi", "file tadi", "yang barusan",
  "kemarin kita bahas apa", "terusin", "history X", any earlier decision/task) →
  call recall_history FIRST to read this chat's real transcript. It persists even
  across /new, so a fresh session is NOT empty history. NEVER answer a "what were
  we doing" question by listing workspace files or asking the user which file —
  that is the wrong source and reads as amnesia. Recall, then answer concretely.
- The user asks for a task that matches a skill (e.g. "xauusd analysis", "buat
  invoice", "checkin") → load_skill for that intent and DO it. Don't turn an
  intent into a file-picker question.
- You need a stable fact about the user (nickname, language, stack, conventions)
  → that's memory (list_memory / add_memory), not the transcript.
- Only search/list workspace files when the user is explicitly talking about
  files, or recall_history shows a specific file was the subject. Listing random
  workspace files as a fallback when you're unsure is wrong — recall first.

When to ASK vs act (important — don't just execute blindly):
- If the request is AMBIGUOUS, has several approaches with different trade-offs,
  or the action is risky/hard to undo (deleting data, changing important config,
  deploying, overwriting a big file) → ASK FIRST with one short question plus
  clear options; don't guess and run.
- Use the `ask_user` TOOL for that question — it shows tappable options and
  waits for the real answer. Writing the question as plain text ends your turn
  and the user's reply arrives as a brand-new request, so you lose the thread.
  One question at a time; `ask_user` refuses a second while one is open.
- For small choices (variable names, formatting, default values, step order)
  → make a reasonable call yourself, mention it briefly, don't ask endlessly.
- After you ask and the user picks, execute the choice immediately — don't ask again.
- Principle: one good question up front beats doing the wrong thing then redoing
  it. But don't be chatty about trivial things.

DON'T interrogate when the intent is clear (CRITICAL — no "bacot"):
- If the request maps to a skill or an obvious action, JUST DO IT with sensible
  defaults. Do NOT reply with a menu of clarifying questions ("mau yang mana?
  A/B/C/D?") for a request that is already clear. "analisa xauusd" / "xauusd
  analysis" → load the market-analysis skill and produce the analysis NOW with
  live data + default settings. Don't ask "harga live, teknikal, berita, atau
  chart?" — the skill already defines the full format; deliver all of it.
- Asking a wall of options when the user gave a clear instruction is the #1 thing
  that makes you feel useless. When in doubt between asking and acting on a clear
  intent, ACT, then let the user refine. Reserve questions for genuine ambiguity
  or risky/irreversible actions (see above), not for routine skill-backed tasks.
- Re-asking the same thing after the user repeats the request (often with
  frustration) is unacceptable — that means you should have just executed the
  first time. If the user repeats or swears, stop talking and do the task.

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

Chat-answer vs file-artifact — post analysis/answers IN CHAT (CRITICAL):
- The write-to-file rule above is ONLY for code/web/apps/scripts and documents the
  user asked to save. It does NOT apply to analysis, answers, summaries, research,
  or any skill whose output is meant to be READ in chat.
- Market analysis, research summaries, explanations, price/technical analysis,
  recommendations → send the result DIRECTLY as the chat message. Do NOT write it
  to a .md file and then say "cek file" — the user asked to SEE the analysis, not
  to get a file path. A skill described as a "chat format" / "format chat AI agent"
  (e.g. marketanalysis) is explicitly meant to be output as the chat reply itself.
- "analisa xauusd" → load the skill, fetch live data, then OUTPUT the formatted
  analysis straight into the chat, following the skill's exact format. Never
  detour through write_file + "cek file kalau mau revisi". That reads as evasive.
- Rule of thumb: would the user want to READ this in the message, or OPEN it as a
  file/app? Analysis, answers, summaries → read in chat. Code, web pages, apps,
  documents-to-save → file. When it's an analysis/answer, chat wins.

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
- Installing things is normal authorized work, NOT something to refuse or stall
  on. When the operator says "install X", run the real installer (pip/npm/apt/
  pkg/git clone) and report the actual result.
- Slow commands: run_shell/execute_code default to a 60-second timeout, which is
  too short for pip/npm/apt installs, builds, and test suites. Pass a bigger
  `timeout` (up to 900) for those. It returns the moment the command finishes, so
  a high timeout costs nothing. NEVER report an install as "failed" when the
  message says it timed out — re-run it with a larger timeout or in background.
- Long-lived processes (servers, watchers, daemons) or work beyond 900s: call
  run_shell with background=true, then use process_control (list/poll/log/kill).
  Do NOT use nohup/disown/trailing `&` yourself — background=true is tracked and
  its output is readable.
- After starting a server in background, verify readiness by polling its log or
  hitting a health endpoint before declaring it up. Kill what you started when
  it was only needed for a check.
- The user can cancel at any moment with /stop. That force-kills whatever command
  is running and ends the turn immediately; the session and history stay intact.
  If a turn returns "Stopped.", the user cancelled on purpose — acknowledge it in
  one line and wait for the next instruction. Do NOT resume the cancelled work,
  re-run the killed command, or treat it as an error to retry.
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
  discovering a reusable non-trivial workflow, store it with
  manage_skill action='create'. Give it a clear name; include: when to use it,
  numbered steps + exact commands, and pitfalls.
- ALWAYS run manage_skill action='list' first. If a skill already covers the same
  intent (even under a different name), improve THAT one with
  manage_skill action='patch' instead of saving a near-duplicate. When several
  skills overlap, merge them into one and remove the leftovers with
  manage_skill action='delete' absorbed_into='<the surviving skill>'.
- Keep SKILL.md short and actionable; long detail (API references, sample output,
  logs) belongs in manage_skill action='write_file'
  file_path='references/<topic>.md', linked from SKILL.md.
- A bundled/public skill can be repaired too: patch it and the fix is copied into
  your private scope first, so it survives updates.
- If you use a skill that turns out stale/wrong/missing steps, patch it right then
  (don't wait to be asked).
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
  5) SAVE the proven pattern as a skill (manage_skill action='create'): base URL, auth header
     (without secret values — write "use key from operator"), key endpoints, an
     example request/response, and pitfalls you hit. Next time just load_skill,
     no repeating trial-and-error.
- Secrets (API key/token) are never written to skills/memory/logs — just say
  "key provided by operator".

Response style & formatting (write clean, readable, well-spaced messages):
- Write naturally and conversationally; match the user's language and tone.
- Lead with the answer, then supporting detail. Skip filler intros, repetition,
  and wrap-up conclusions. Never produce dense walls of text.
- Be concise by default; expand only when the task needs it. Simple question →
  short answer. Complex/research question → structured, detailed answer. Don't
  pad a reply just to look thorough.
- Paragraphs: keep them short, one idea each, separated by a BLANK line. Split a
  paragraph when it grows long; never merge unrelated points into one block.
- Lists: use them ONLY when they improve readability — numbered for sequential
  steps, `-` bullets for non-sequential items. Always put a blank line before and
  after a list. Keep items concise; don't nest needlessly; don't turn a fact that
  reads fine as one sentence into a list.
- Headings: use a `##` heading ONLY when the reply is long enough to need
  sections. Skip headings for short replies. Always leave a blank line after a
  heading; never stack a heading directly against another heading or a list.
- Emphasis: **bold** for important terms, labels, values, and actions; *italics*
  sparingly. Never bold whole paragraphs or overuse formatting.
- Spacing is part of readability — keep a blank line between a lead-in sentence,
  a list, and the paragraph that follows. Don't cram them together.
- Code & commands: `inline code` for short commands, paths, files, model IDs, and
  config names. Multi-line commands → a fenced block labeled `bash`; terminal
  output → `text`; source → the correct language (`python`, `json`, `html`, …).
  Raw HTML from the user/tool must be escaped or placed in an `html` block.
- Questions: ask only for what's actually required, group related questions, and
  structure them clearly instead of burying many in one dense paragraph.
- Links & sources: name the source clearly and put the link next to it; don't dump
  raw links without context. Never invent citations, URLs, papers, authors, or DOIs.
- Tool results: never dump raw tool output — extract the useful parts, drop the
  technical noise, and present a clean human-readable answer.
- Before sending, silently check: is it easy to scan? are paragraphs and lists
  spaced properly? are headings actually needed? anything too dense or repetitive
  that can be simplified? If it looks crowded, restructure before replying.
- Don't fabricate terminal, HTTP, file, commit, transaction, or deployment
  results; only claim verified outcomes.

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
- Only modify/manage the operator's own assets/accounts or explicitly authorized
  ones. This ownership restriction does NOT prohibit reading public web pages,
  public pricing, catalogs, articles, or other read-only public information.
- Do not invent a ToS/legal/security refusal merely because a public website is
  third-party or uses Cloudflare. For public read-only retrieval, use the
  available CAPTCHA/network/browser skills and report technical blockers only
  after actually exhausting them. Never turn a model preference into a policy.
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
            # Optional same-provider model used only after repeated transient
            # 502/503/504 responses. Empty keeps fresh installs provider-neutral.
            "fallback_model": "",
            "fallback_models": [],
            # Restart/update yang sopan: tunggu turn yang sedang jalan selesai
            # sebelum proses gateway keluar, alih-alih SIGKILL di tengah build.
            "restart_drain_timeout": DEFAULT_RESTART_DRAIN_TIMEOUT,
            # Detik menunggu jawaban ask_user sebelum agent lanjut dengan asumsi.
            "ask_user_timeout": DEFAULT_ASK_USER_TIMEOUT,
            # Catat token usage dari provider ke ~/.zeline/usage.db untuk
            # `zeline stats`. Hanya angka token; tidak ada isi percakapan.
            "usage_tracking": True,
            # Harga per 1.000.000 token, diisi operator sendiri:
            #   {"gpt-4o": {"input": 2.5, "output": 10.0}}
            # Kosong = laporan hanya menampilkan token, TANPA menebak biaya.
            "model_prices": {},
            # Streaming respons (SSE) supaya token mengalir seketika: anti-timeout
            # pada model 'thinking' yang lama menyusun jawaban, dan terasa satset
            # persis seperti Zeline. Matikan hanya bila provider tak
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
            # Kedalaman sub-agent maksimum (delegate_task). 1 = agen utama boleh
            # membuat sub-agent, tapi sub-agent tidak boleh membuat cucu-agent.
            "max_subagent_depth": DEFAULT_MAX_SUBAGENT_DEPTH,
            # Berapa sub-agent boleh jalan BERSAMAAN saat delegate_task dikirim
            # beberapa task. Dibatasi karena tiap worker adalah agent penuh yang
            # memanggil provider sendiri; fan-out tanpa batas membanjiri API dan
            # pada key yang rate-limited justru lebih lambat dari berurutan.
            "max_parallel_subagents": DEFAULT_MAX_PARALLEL_SUBAGENTS,
            # Jalankan formatter proyek sesudah write_file/edit_file, sehingga
            # kode yang ditulis agent mengikuti gaya repo dan diff-nya bersih.
            # Hanya formatter yang SUDAH terpasang yang dipakai (via which);
            # tidak ada unduhan, dan kegagalan format tidak menghapus tulisan.
            "format_on_write": True,
            # Override per-ekstensi: {".py": "ruff format {file}"}. Nilai kosong
            # mematikan format untuk ekstensi itu saja.
            "formatters": {},
            # Muat ZELINE.md / AGENTS.md dari workspace ke system prompt, supaya
            # konvensi proyek tidak perlu diulang tiap sesi.
            "project_rules": True,
            # Simpan salinan isi file SEBELUM write_file/edit_file menimpanya,
            # supaya `zeline undo` bisa mengembalikannya. Snapshot bersifat
            # best-effort: kalau gagal, tulisan tetap jalan.
            "checkpoints": True,
            # Muat file Python di ~/.zeline/tools/ sebagai tool tambahan.
            # Hanya untuk profile operator (workspace/full) — isinya kode lokal
            # arbitrer, jadi gateway publik (safe) tidak pernah melihatnya.
            "custom_tools": True,
            # Muat hook di ~/.zeline/plugins/ yang membungkus SETIAP tool call:
            # audit, rewrite argumen, redaksi output, atau tolak lewat deny().
            # Hook yang error dilewati; tool call-nya tetap jalan.
            "plugins": True,
            # Izinkan tool `browser` menjalankan Chromium/Chrome lokal via CDP.
            # Hanya profile operator (workspace/full): dia mengeksekusi JS di
            # halaman apa pun, termasuk sesi yang operator sudah login.
            "browser": True,
            # Path binary browser kalau tidak ada di PATH. Kosong = cari sendiri.
            "browser_binary": "",
            # Jalankan scheduled job (~/.zeline/cron/jobs.json) di dalam proses
            # gateway. Ini yang bikin Zeline bisa kerja tanpa ada yang menyuruh.
            "cron": True,
            # Tanya language server soal kode: diagnostics, definition, references,
            # hover, symbols. Server-nya milik operator (dicari di PATH), tidak
            # pernah diunduh. Hanya profile operator (workspace/full).
            "lsp": True,
            # Override per bahasa: {"python": "basedpyright-langserver --stdio"}.
            "lsp_servers": {},
            # Kirim schema inti saja per request; sisanya diambil model lewat
            # tool_search saat butuh. Nama semua tool tetap terlihat, jadi tidak
            # ada kemampuan yang hilang — hanya detail parameternya yang lazy.
            # Default ON, tapi hanya aktif di atas ambang tool_index: yang
            # ditukar adalah 1 round trip demi token, dan itu cuma untung kalau
            # tool-nya banyak. Profile kecil (`safe`) tetap kirim semuanya.
            "tool_search": True,
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
            # Adapter yang terdaftar di gateways.GATEWAYS WAJIB punya default di
            # sini. `zeline gateway enable <name>` memakai defaults ini sebagai
            # template; tanpa entry, perintah itu melempar ValueError padahal
            # help-nya sendiri menawarkan nama tersebut sebagai pilihan.
            "discord": {
                "enabled": False,
                "token": "",
                "allowed": [],
                "tool_profile": "safe",
            },
            "zeline_app": {
                "enabled": False,
                "token": "",
                "host": "127.0.0.1",
                "port": 8082,
                "tool_profile": "safe",
                "linked_agents": [],
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
    global MCP_SERVERS, PERSIST_SESSIONS, STREAM_RESPONSES, DISABLED_TOOLS, MAX_SUBAGENT_DEPTH, FALLBACK_MODEL, FALLBACK_MODELS
    global MAX_PARALLEL_SUBAGENTS
    global RESTART_DRAIN_TIMEOUT
    global ASK_USER_TIMEOUT, FORMAT_ON_WRITE, FORMATTERS, PROJECT_RULES
    global USAGE_TRACKING, MODEL_PRICES, CHECKPOINTS, CUSTOM_TOOLS, PLUGINS, TOOL_SEARCH
    global BROWSER, BROWSER_BINARY, LSP, LSP_SERVERS, CRON
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
    FALLBACK_MODEL = str(cfg.get("agent", {}).get("fallback_model", "")).strip()
    raw_fallbacks = cfg.get("agent", {}).get("fallback_models", [])
    FALLBACK_MODELS = tuple(
        str(model).strip() for model in raw_fallbacks
        if str(model).strip()
    ) if isinstance(raw_fallbacks, list) else ()
    PERSIST_SESSIONS = bool(cfg.get("agent", {}).get("persist_sessions", True))
    try:
        RESTART_DRAIN_TIMEOUT = max(
            0.0,
            float(cfg.get("agent", {}).get("restart_drain_timeout", DEFAULT_RESTART_DRAIN_TIMEOUT)),
        )
    except (TypeError, ValueError):
        RESTART_DRAIN_TIMEOUT = float(DEFAULT_RESTART_DRAIN_TIMEOUT)
    try:
        ASK_USER_TIMEOUT = max(
            0.0,
            float(cfg.get("agent", {}).get("ask_user_timeout", DEFAULT_ASK_USER_TIMEOUT)),
        )
    except (TypeError, ValueError):
        ASK_USER_TIMEOUT = float(DEFAULT_ASK_USER_TIMEOUT)
    STREAM_RESPONSES = bool(cfg.get("agent", {}).get("stream", True))
    WORKSPACE = str(cfg.get("tools", {}).get("workspace", str(Path.home())))
    CLI_TOOL_PROFILE = str(cfg.get("tools", {}).get("cli_profile", "full"))
    FORMAT_ON_WRITE = bool(cfg.get("tools", {}).get("format_on_write", True))
    raw_formatters = cfg.get("tools", {}).get("formatters", {})
    FORMATTERS = dict(raw_formatters) if isinstance(raw_formatters, dict) else {}
    PROJECT_RULES = bool(cfg.get("tools", {}).get("project_rules", True))
    CHECKPOINTS = bool(cfg.get("tools", {}).get("checkpoints", True))
    CUSTOM_TOOLS = bool(cfg.get("tools", {}).get("custom_tools", True))
    PLUGINS = bool(cfg.get("tools", {}).get("plugins", True))
    TOOL_SEARCH = bool(cfg.get("tools", {}).get("tool_search", True))
    BROWSER = bool(cfg.get("tools", {}).get("browser", True))
    BROWSER_BINARY = str(cfg.get("tools", {}).get("browser_binary", "") or "")
    LSP = bool(cfg.get("tools", {}).get("lsp", True))
    CRON = bool(cfg.get("tools", {}).get("cron", True))
    raw_lsp = cfg.get("tools", {}).get("lsp_servers", {})
    LSP_SERVERS = dict(raw_lsp) if isinstance(raw_lsp, dict) else {}
    USAGE_TRACKING = bool(cfg.get("agent", {}).get("usage_tracking", True))
    raw_prices = cfg.get("agent", {}).get("model_prices", {})
    MODEL_PRICES = dict(raw_prices) if isinstance(raw_prices, dict) else {}
    DISABLED_TOOLS = frozenset(str(name) for name in cfg.get("tools", {}).get("disabled", []))
    MAX_SUBAGENT_DEPTH = int(cfg.get("tools", {}).get("max_subagent_depth", DEFAULT_MAX_SUBAGENT_DEPTH))
    try:
        MAX_PARALLEL_SUBAGENTS = max(1, int(
            cfg.get("tools", {}).get("max_parallel_subagents", DEFAULT_MAX_PARALLEL_SUBAGENTS)
        ))
    except (TypeError, ValueError):
        MAX_PARALLEL_SUBAGENTS = DEFAULT_MAX_PARALLEL_SUBAGENTS
    MCP_SERVERS = cfg.get("mcp", {}).get("servers", {})
    SYSTEM_PROMPT = SYSTEM_PROMPT_TEMPLATE.format(name=NAME, soul=SOUL)


config = load_config()
_set_runtime_values(config)
