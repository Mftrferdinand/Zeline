"""Tool router — fast keyword pre-filter for the native tool list.

Narrows the ~279 native tools down to the top-K most relevant for a user
query, so the coordinator only sends relevant schemas to the model. This is
pure CPU work: no network, no file I/O, no model calls. ``route()`` over all
tools runs in a few milliseconds (budget: <50ms).

Fail-open by design: ``filter_schemas()`` never raises into the agent loop.
Any internal error returns the original schema list untouched, so a router
bug can never break the agent.
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterable, Sequence


class ToolRouterError(RuntimeError):
    """Raised for invalid router input (e.g. malformed tool entries).

    Never raised by ``filter_schemas`` — that entry point is fail-open and
    returns the original schemas instead.
    """


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MIN_TOP_K = 5
MAX_TOP_K = 50
DEFAULT_TOP_K = 15

#: Tools that are always included, even when the query matches nothing.
#: ``ask_user`` is the approval/confirmation channel — the agent must always
#: be able to ask the operator. ``run_shell`` is deliberately NOT essential:
#: it is DESTRUCTIVE-risk and only relevant to shell/code queries, which the
#: keyword scorer already handles.
ESSENTIAL_TOOLS = frozenset({"ask_user"})

#: Module-level enabled flag. Flipped by :func:`configure`.
ROUTER_ENABLED = True

_top_k = DEFAULT_TOP_K


def _env_disabled() -> bool:
    """True when the operator opted out via the ZELINE_ROUTER_OFF env var."""
    return os.environ.get("ZELINE_ROUTER_OFF", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def is_enabled() -> bool:
    """Router active? Env opt-out always wins over :func:`configure`."""
    return ROUTER_ENABLED and not _env_disabled()


def configure(
    enabled: bool | None = None,
    top_k: int | None = None,
) -> None:
    """Tune the router at runtime.

    ``enabled=False`` turns routing off — ``route()``/``filter_schemas()``
    then pass everything through unchanged. ``top_k`` is clamped to 5..50.
    """
    global ROUTER_ENABLED, _top_k
    if enabled is not None:
        ROUTER_ENABLED = bool(enabled)
    if top_k is not None:
        _top_k = max(MIN_TOP_K, min(MAX_TOP_K, int(top_k)))


def _effective_top_k() -> int:
    return max(MIN_TOP_K, min(MAX_TOP_K, _top_k))


# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------

# Negated character class only — linear-time split, no backtracking risk.
_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")

_STOPWORDS = frozenset(
    """
    the a an and or to of in on for with is are was were be been do does did
    done please my your me you it its this that these those from at by as so
    if then than can will would should just now new into over under again
    yang dan atau di ke dari untuk dengan ini itu saya aku kamu dia kita kami
    mereka nya adalah ada tidak tak nggak sudah belum akan bisa tolong dong
    saja aja dalam pada oleh sebagai juga agar supaya kalau jika saat ketika
    """.split()
)


def _tokens(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, drop stopwords and 1-char bits."""
    if not text:
        return []
    return [
        t for t in _TOKEN_SPLIT_RE.split(text.lower()) if t and len(t) > 1 and t not in _STOPWORDS
    ]


# ---------------------------------------------------------------------------
# Synonym / alias map: query token -> tool-name patterns.
# A pattern ending in "*" is a name-prefix match (category boost).
# ---------------------------------------------------------------------------

_SEND_TOOLS = (
    "email_send",
    "gmail_send",
    "resend_send_email",
    "sendgrid_send_email",
    "mailgun_send_email",
    "slack_send_message",
    "telegram_bot_send_message",
    "whatsapp_send",
    "twilio_send_sms",
    "vonage_send_sms",
    "teams_send_message",
    "discord_send_message",
    "send_file",
    "pushover_send_notification",
    "onesignal_send_push",
    "x_api_post_tweet",
)

_TASK_TOOLS = (
    "kanban_*",
    "todoist_add_task",
    "todoist_list_tasks",
    "asana_create_task",
    "asana_list_tasks",
    "clickup_create_task",
    "clickup_list_tasks",
    "wrike_create_task",
    "wrike_list_tasks",
    "shortcut_create_story",
    "shortcut_list_stories",
    "height_list_tasks",
    "update_task",
    "delegate_task",
)

ALIASES: dict[str, tuple[str, ...]] = {
    # --- communication ---------------------------------------------------
    "email": (
        "email_send",
        "gmail_send",
        "gmail_read",
        "gmail_search",
        "resend_send_email",
        "sendgrid_send_email",
        "mailgun_send_email",
        "mailgun_list_messages",
    ),
    "mail": ("email_send", "gmail_send", "gmail_read", "gmail_search"),
    "gmail": ("gmail_send", "gmail_read", "gmail_search"),
    "kirim": _SEND_TOOLS,
    "send": _SEND_TOOLS,
    "kirimkan": _SEND_TOOLS,
    "sms": ("twilio_send_sms", "vonage_send_sms", "twilio_list_messages"),
    "pesan": (
        "slack_send_message",
        "telegram_bot_send_message",
        "whatsapp_send",
        "teams_send_message",
        "discord_send_message",
        "twilio_send_sms",
    ),
    "message": (
        "slack_send_message",
        "telegram_bot_send_message",
        "whatsapp_send",
        "teams_send_message",
        "discord_send_message",
    ),
    "chat": (
        "slack_send_message",
        "telegram_bot_send_message",
        "whatsapp_send",
        "teams_send_message",
        "discord_send_message",
    ),
    "tweet": ("x_api_post_tweet",),
    "twitter": ("x_api_post_tweet", "x_api_read_timeline"),
    "posting": (
        "x_api_post_tweet",
        "bluesky_post",
        "mastodon_post_toot",
        "linkedin_share_post",
        "buffer_create_post",
        "devto_create_article",
        "ghost_create_post",
    ),
    "post": (
        "x_api_post_tweet",
        "bluesky_post",
        "linkedin_share_post",
        "devto_create_article",
        "ghost_create_post",
    ),
    "slack": ("slack_*",),
    "telegram": ("telegram_bot_send_message", "telegram_bot_get_me"),
    "whatsapp": ("whatsapp_send", "whatsapp_template"),
    "discord": ("discord_send_message", "discord_list_channels"),
    "notifikasi": ("pushover_send_notification", "onesignal_send_push"),
    "notification": ("pushover_send_notification", "onesignal_send_push"),
    # --- meetings --------------------------------------------------------
    "meeting": ("meeting_*",),
    "rapat": ("meeting_*",),
    "zoom": ("meeting_join",),
    "meet": ("meeting_join",),
    "teams": ("meeting_join", "teams_send_message"),
    "notulen": ("meeting_notes",),
    "notes": ("meeting_notes",),
    # --- tasks / kanban --------------------------------------------------
    "tugas": _TASK_TOOLS,
    "task": _TASK_TOOLS,
    "todo": ("todoist_add_task", "todoist_list_tasks", "kanban_*"),
    "kanban": ("kanban_*",),
    "kartu": ("kanban_create", "trello_create_card"),
    "card": ("kanban_create", "trello_create_card"),
    "trello": ("trello_*",),
    "asana": ("asana_*",),
    "jadwalkan": ("schedule_task", "google_calendar"),
    # --- search / web ----------------------------------------------------
    "cari": ("web_search", "deep_research", "search_files", "search_sessions", "clawhub_search"),
    "search": ("web_search", "deep_research", "search_files", "search_sessions"),
    "mencari": ("web_search", "deep_research", "search_files"),
    "web": ("web_search", "web_fetch"),
    "internet": ("web_search", "web_fetch"),
    "online": ("web_search", "web_fetch"),
    "berita": ("web_search", "deep_research", "hackernews_top_stories"),
    "news": ("web_search", "deep_research", "hackernews_top_stories"),
    "riset": ("deep_research", "web_search"),
    "research": ("deep_research", "web_search"),
    # --- files -----------------------------------------------------------
    "file": (
        "read_file",
        "write_file",
        "edit_file",
        "search_files",
        "download_file",
        "send_file",
        "pdf_tool",
    ),
    "baca": (
        "read_file",
        "gmail_read",
        "slack_read_history",
        "mastodon_read_timeline",
        "x_api_read_timeline",
        "bluesky_read_timeline",
        "sheets_read",
    ),
    "read": ("read_file", "gmail_read", "slack_read_history", "sheets_read"),
    "tulis": ("write_file",),
    "write": ("write_file", "edit_file"),
    "edit": ("edit_file", "patch_file", "undo_file"),
    "ubah": ("edit_file", "patch_file"),
    "download": ("download_file",),
    "unduh": ("download_file",),
    # --- shell / code ----------------------------------------------------
    "shell": ("run_shell",),
    "terminal": ("run_shell",),
    "jalankan": ("run_shell", "execute_code"),
    "run": ("run_shell", "execute_code"),
    "execute": ("run_shell", "execute_code"),
    "kode": ("execute_code", "code_intel", "lsp_*"),
    "code": ("execute_code", "code_intel", "lsp_*"),
    "program": ("run_shell", "execute_code"),
    # --- scheduling / reminders ------------------------------------------
    "pengingat": ("schedule_task",),
    "ingatkan": ("schedule_task",),
    "ingat": ("schedule_task",),
    "reminder": ("schedule_task", "pushover_send_notification"),
    "jadwal": ("schedule_task", "google_calendar"),
    "schedule": ("schedule_task",),
    "set": ("schedule_task", "user_model_set"),
    "cron": ("schedule_task",),
    "kalender": ("google_calendar",),
    "calendar": ("google_calendar",),
    # --- media -----------------------------------------------------------
    "gambar": ("generate_image", "edit_image", "analyze_media"),
    "image": ("generate_image", "edit_image", "analyze_media"),
    "foto": ("analyze_media", "generate_image"),
    "video": ("generate_video", "edit_video"),
    "suara": ("text_to_speech", "voice_speak", "transcribe_audio"),
    "audio": ("transcribe_audio", "voice_transcribe", "text_to_speech"),
    "transcribe": ("transcribe_audio", "voice_transcribe"),
    "transkrip": ("transcribe_audio", "voice_transcribe"),
    # --- misc ------------------------------------------------------------
    "goal": ("goal_*",),
    "target": ("goal_*",),
    "skill": (
        "learn_skill",
        "load_skill",
        "list_learned_skills",
        "apply_skill_proposal",
        "improve_skill",
    ),
    "cuaca": ("openweathermap_current_weather", "openweathermap_forecast"),
    "weather": ("openweathermap_current_weather", "openweathermap_forecast"),
    "github": ("github_*",),
    "issue": (
        "github_create_issue",
        "jira_create_issue",
        "linear_create_issue",
        "sentry_list_issues",
    ),
    "biaya": ("runtime_info",),  # no dedicated cost tool; closest match
    "cost": ("runtime_info",),
    "tanya": ("ask_user",),
    "ask": ("ask_user",),
    "konfirmasi": ("ask_user",),
    "memori": ("list_memory", "add_memory", "search_sessions", "recall_history"),
    "memory": ("list_memory", "add_memory", "recall_history"),
}

# Exact-name alias hits get the strongest bonus; prefix hits slightly less.
_ALIAS_EXACT_BONUS = 6
_ALIAS_PREFIX_BONUS = 4

# Token in tool name (e.g. "send" in "email_send").
_NAME_TOKEN_WEIGHT = 3
# Token equals the tool's category prefix (e.g. "meeting" in "meeting_join").
_CATEGORY_WEIGHT = 2
# Token in tool description.
_DESC_WEIGHT = 1

# Generic fallback tools for an empty/meaningless query (filtered to names
# that actually exist in the tool list at call time).
_GENERIC_TOOLS = (
    "web_search",
    "run_shell",
    "read_file",
    "write_file",
    "search_files",
    "list_memory",
    "deep_research",
    "recall_history",
)


# ---------------------------------------------------------------------------
# Scoring internals
# ---------------------------------------------------------------------------


def _name_desc(tool: Any) -> tuple[str, str]:
    """Extract (name, description) from a ToolDef or a plain dict."""
    name = getattr(tool, "name", None)
    desc = getattr(tool, "description", None)
    if isinstance(name, str) and isinstance(desc, str):
        return name, desc
    if isinstance(tool, dict):
        name = tool.get("name")
        desc = tool.get("description", "")
        if isinstance(name, str) and isinstance(desc, str):
            return name, desc
    raise ToolRouterError(
        f"tool entry is not a ToolDef or name/description dict: {type(tool).__name__}"
    )


def _score_tool(
    query_tokens: set[str],
    name: str,
    desc_tokens: set[str],
) -> int:
    """Keyword score for one tool. Pure function, no I/O."""
    score = 0
    name_parts = name.split("_")
    category = name_parts[0] if name_parts else ""
    name_set = set(name_parts)
    for tok in query_tokens:
        if tok in name_set:
            score += _NAME_TOKEN_WEIGHT
            if tok == category:
                score += _CATEGORY_WEIGHT
        if tok in desc_tokens:
            score += _DESC_WEIGHT
        for pattern in ALIASES.get(tok, ()):
            if pattern.endswith("*"):
                if name.startswith(pattern[:-1]):
                    score += _ALIAS_PREFIX_BONUS
            elif name == pattern:
                score += _ALIAS_EXACT_BONUS
    return score


def _rank(names_descs: Sequence[tuple[str, str]], query: str) -> list[str]:
    """Ranked tool names, highest score first (ties broken by name)."""
    query_tokens = set(_tokens(query))
    scored: list[tuple[int, str]] = []
    for name, desc in names_descs:
        scored.append((_score_tool(query_tokens, name, set(_tokens(desc))), name))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [name for _, name in scored]


def _apply_essentials(ranked: list[str], top_k: int, available: set[str]) -> list[str]:
    """Essentials first (forced in even if unranked), then the rest, top_k."""
    deduped: list[str] = []
    seen: set[str] = set()
    for name in ranked:
        if name not in seen:
            seen.add(name)
            deduped.append(name)
    essentials = sorted(n for n in ESSENTIAL_TOOLS if n in available)
    rest = [n for n in deduped if n not in ESSENTIAL_TOOLS]
    return (essentials + rest)[:top_k]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def route_names(
    query: str,
    names_descriptions: Iterable[Any],
) -> list[str]:
    """Rank tool names for a query. Test-friendly entry point.

    Accepts ``(name, description)`` tuples, ``{"name": ..., "description":
    ...}`` dicts, or bare name strings. Returns ranked names (all of them —
    the caller applies top-K).
    """
    pairs: list[tuple[str, str]] = []
    for item in names_descriptions:
        if isinstance(item, str):
            pairs.append((item, ""))
        elif isinstance(item, dict):
            name = item.get("name")
            desc = item.get("description", "")
            if not isinstance(name, str):
                raise ToolRouterError("route_names dict entry missing string 'name'")
            pairs.append((name, desc if isinstance(desc, str) else ""))
        else:
            try:
                name, desc = item  # type: ignore[misc]
            except (TypeError, ValueError) as exc:
                raise ToolRouterError(
                    "route_names entry must be str, dict, or (name, desc)"
                ) from exc
            if not isinstance(name, str):
                raise ToolRouterError("route_names entry name must be a str")
            pairs.append((name, desc if isinstance(desc, str) else ""))
    return _rank(pairs, query or "")


def route(query: str, tools: Sequence[Any]) -> list[Any]:
    """Return the top-K tools most relevant to ``query``, best first.

    ``tools`` is a list of ``ToolDef`` (or dicts with name/description);
    the returned list holds the *same objects* in ranked order. Always
    includes :data:`ESSENTIAL_TOOLS`. With routing disabled, or an empty
    query, behavior degrades gracefully (see below).

    - Router disabled (``configure(enabled=False)`` or ``ZELINE_ROUTER_OFF``):
      returns all tools unchanged.
    - Empty ``tools``: returns ``[]``.
    - Empty query: essentials + generic everyday tools.
    """
    tools = list(tools)
    if not tools:
        return []
    if not is_enabled():
        return tools

    top_k = _effective_top_k()
    pairs = [_name_desc(t) for t in tools]
    by_name: dict[str, Any] = {}
    for t, (name, _desc) in zip(tools, pairs):
        by_name.setdefault(name, t)  # first occurrence wins on duplicates

    if not (query or "").strip():
        ranked = [n for n in _GENERIC_TOOLS if n in by_name]
        # Fill to top_k deterministically with the remaining tools, A→Z.
        ranked += sorted(n for n in by_name if n not in set(ranked))
    else:
        ranked = _rank(pairs, query)
    ordered = _apply_essentials(ranked, top_k, set(by_name))
    return [by_name[n] for n in ordered]


def filter_schemas(query: str, schemas: Sequence[dict]) -> list[dict]:
    """Filter OpenAI-format tool schemas down to the top-K for ``query``.

    Schemas look like ``{"type": "function", "function": {"name": ...,
    "description": ..., "parameters": ...}}`` (same as
    ``ToolExecutor.schemas``). Fail-open: any error — malformed entries,
    bad input, internal bug — returns the *original* schemas untouched so
    the agent loop keeps working.
    """
    try:
        original = list(schemas)
        if not original or not is_enabled():
            return original
        by_name = {s["function"]["name"]: s for s in original}
        top_k = _effective_top_k()
        if not (query or "").strip():
            ranked = [n for n in _GENERIC_TOOLS if n in by_name]
            ranked += sorted(n for n in by_name if n not in set(ranked))
        else:
            ranked = route_names(
                query,
                [
                    (s["function"]["name"], s["function"].get("description", "") or "")
                    for s in original
                ],
            )
        ordered = _apply_essentials(ranked, top_k, set(by_name))
        return [by_name[n] for n in ordered]
    except Exception:
        # Fail-open: never break the agent loop because of the router.
        try:
            return list(schemas)
        except Exception:
            return []
