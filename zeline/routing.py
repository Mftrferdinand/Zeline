"""Per-turn model routing: send each user turn to the most suitable model.

The router is **enabled by default**. With no configured routes it is a safe
no-op: every turn goes to the configured default model and the agent behaves
exactly as if this module did not exist. Configure per-category models to
actually route turns elsewhere.

When enabled, a cheap deterministic classifier inspects the user's message
(plus recent history) and maps it to one of five categories. Each category
may name its own model; a turn whose category has no configured model falls
back to the default model (fail-closed towards quality, never towards the
cheapest model).

Classification is heuristic and deliberately conservative: only strong,
unambiguous signals trigger a category, and anything doubtful returns
``"default"``. No LLM call is made — routing itself must never cost a
provider round-trip.

Precedence (first match wins):

1. ``long_context`` — estimated context (text + history) above
   ``LONG_CONTEXT_CHARS``. Checked first because sending a huge context to a
   small-context model is the one routing mistake that silently destroys a
   turn.
2. ``code`` — code fence, code keywords / file extensions, or a code-ish
   tool call in recent history.
3. ``research`` — lookup keywords (search, news, price comparison, ...).
4. ``reasoning`` — long prompt *and* analysis keywords.
5. ``quick`` — short message with a simple greeting / factual pattern. This
   is checked last on purpose: a specific signal always beats ``quick``.
6. ``default`` — no strong signal.

Configuration, in increasing order of precedence (each level wins over the
previous one):

1. Config file section (``~/.zeline/config.json``)::

       "routing": {
         "enabled": true,
         "routes": {
           "code": "provider/code-model",
           "quick": "provider/flash-model",
           "research": "provider/search-model"
         }
       }

   Unknown category keys inside ``routes`` are ignored safely.

2. Environment variables (always win over the file):

   - ``ZELINE_ROUTING_ENABLED`` — truthy (``1``/``true``/``yes``/``on``,
     case-insensitive) enables routing; *setting* it to a falsy value
     disables routing even if the file says ``enabled: true``.
   - ``ZELINE_ROUTE_CODE``, ``ZELINE_ROUTE_QUICK``, ``ZELINE_ROUTE_REASONING``,
     ``ZELINE_ROUTE_RESEARCH``, ``ZELINE_ROUTE_LONG_CONTEXT`` — model name per
     category. Setting one to an empty string removes the route (useful to
     unset a route defined in the file).

Example::

    from zeline import routing

    cfg = routing.RouterConfig.from_dict(section_from_config_file,
                                         default_model="provider/main-model")
    cfg.apply_env()  # environment always wins over the file
    decision = routing.resolve("halo", [], cfg, "provider/main-model")
    if decision.routed:
        print(decision.model)  # model configured for the "quick" route
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

log = logging.getLogger(__name__)

#: Categories the classifier can return. ``"default"`` is returned for
#: anything without a strong signal and is intentionally not in this tuple.
CATEGORIES = ("code", "quick", "reasoning", "research", "long_context")

#: Estimated context size (user text + history, in characters) at or above
#: which a turn is classified ``long_context``. Roughly 8k tokens at the
#: usual ~4 characters per token — well above a normal chat turn, so only
#: genuinely large prompts (pasted documents, long transcripts) trip it.
LONG_CONTEXT_CHARS = 32_000

#: Minimum user-text length for the ``reasoning`` category. Analysis keywords
#: alone in a short message are not enough — a two-word "analisis dong" is a
#: quick chat turn, not a planning task.
REASONING_MIN_CHARS = 500

#: Maximum user-text length for the ``quick`` category. Anything longer is a
#: real task even when it starts with "halo".
QUICK_MAX_CHARS = 300

_ENV_ENABLED = "ZELINE_ROUTING_ENABLED"
_ENV_ROUTE_PREFIX = "ZELINE_ROUTE_"

_TRUTHY = {"1", "true", "yes", "on"}


def _truthy(value: Any) -> bool:
    """Truthy convention shared with the rest of the codebase."""
    return str(value).strip().lower() in _TRUTHY


def _env_route_name(category: str) -> str:
    return f"{_ENV_ROUTE_PREFIX}{category.upper()}"


# ---------------------------------------------------------------------------
# Heuristic signals (compiled once; all match case-insensitively)
# ---------------------------------------------------------------------------

# ``` fence — the strongest single code signal.
_CODE_FENCE_RE = re.compile(r"```")

# Words that, on their own, mean "this is about code". Kept deliberately
# narrow: generic words like "error" or "from" appear in ordinary chat too.
# Indonesian words that look code-ish but are usually not ("kode" as in
# "kode promo", "fungsi" as in "fungsi vitamin", "program" as in
# "program pemerintah") are intentionally excluded — a bare keyword must
# be an unambiguous signal, never a guess.
_CODE_KEYWORDS_RE = re.compile(
    r"\b(def|class|import|traceback|refactor|debug|debugging|compile|exception|syntax|"
    r"stack trace|pull request|skrip|script|bug|patch)\b"
)

# File extensions that mark a message as code-adjacent.
_CODE_EXTENSIONS = frozenset({
    "py", "pyi", "js", "mjs", "jsx", "ts", "tsx", "go", "rs", "java", "kt",
    "kts", "c", "h", "cpp", "hpp", "cs", "rb", "php", "swift", "scala", "sql",
    "sh", "bash", "zsh", "ps1", "bat", "r", "lua", "dart", "vue", "svelte",
    "tf", "yaml", "yml", "toml", "ini", "cfg",
})
_CODE_EXTENSION_RE = re.compile(
    r"\.(" + "|".join(sorted(_CODE_EXTENSIONS)) + r")\b"
)

# Known filenames WITHOUT extensions that mark a message as code-adjacent
# (e.g. "edit the Dockerfile"). Kept separate from _CODE_EXTENSIONS because
# that regex requires a leading dot, which bare filenames never have.
_CODE_BARE_FILENAMES = frozenset({"dockerfile", "makefile"})
_CODE_BARE_FILENAME_RE = re.compile(
    r"\b(" + "|".join(sorted(_CODE_BARE_FILENAMES)) + r")\b"
)

# Tool calls whose presence in recent history marks a turn as code work.
_CODE_TOOLS = frozenset({
    "run_shell", "run_command", "write_file", "edit_file", "read_file",
    "apply_patch", "create_file",
})

# Lookup / current-information intent. Indonesian + English.
_RESEARCH_KEYWORDS_RE = re.compile(
    r"\b(cari|search|searching|berita|news|riset|research|terbaru|latest|"
    r"update|bandingkan|compare|perbandingan|harga|price|breaking|"
    r"apa yang terjadi|kabar terbaru|cek harga|info terkini|"
    r"gosip|rumor|skor|jadwal|cuaca|weather|kurs|exchange rate)\b"
)

# Analysis / planning intent. Only counts together with a long prompt
# (see REASONING_MIN_CHARS) — a bare "analisis" is not a signal.
_REASONING_KEYWORDS_RE = re.compile(
    r"\b(analisis|analisa|analysis|evaluasi|evaluation|rencana|plan|"
    r"strategi|strategy|trade-?off|langkah-langkah|pro-kontra|"
    r"pro dan kontra|kelebihan dan kekurangan|pertimbangkan|"
    r"root cause|deep dive|arsitektur)\b"
)

# Simple greetings / acknowledgements / short factual questions.
_QUICK_RE = re.compile(
    r"^(halo|hai|hi|hello|hey|helo|selamat|pagi|siang|sore|malam|"
    r"apa kabar|thanks|terima kasih|makasih|thank you|ok|oke|okay|sip|"
    r"siap|baik|test|tes|ping|yo|gas|lanjut|ya|iya|tidak|nggak)\b"
    r"|^(jam berapa|tanggal berapa|hari apa|cuaca)"
)


def _history_chars(history: Any) -> int:
    """Estimated character size of the recent history (defensive)."""
    total = 0
    if not history:
        return 0
    try:
        iterator = iter(history)
    except TypeError:
        return 0
    for message in iterator:
        if not isinstance(message, Mapping):
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif content is not None:
            total += len(str(content))
    return total


def _recent_tool_names(history: Any, limit: int = 6) -> set[str]:
    """Tool names called in the last ``limit`` history messages."""
    names: set[str] = set()
    if not history:
        return names
    try:
        messages = list(history)
    except TypeError:
        return names
    for message in messages[-limit:]:
        if not isinstance(message, Mapping):
            continue
        calls = message.get("tool_calls") or []
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, Mapping):
                continue
            function = call.get("function")
            if not isinstance(function, Mapping):
                continue
            name = function.get("name")
            if name:
                names.add(str(name))
    return names


def _is_code(text: str, history: Any) -> bool:
    if (
        _CODE_FENCE_RE.search(text)
        or _CODE_EXTENSION_RE.search(text)
        or _CODE_BARE_FILENAME_RE.search(text)
    ):
        return True
    if _CODE_KEYWORDS_RE.search(text):
        return True
    return bool(_recent_tool_names(history) & _CODE_TOOLS)


def _is_quick(text: str) -> bool:
    return bool(_QUICK_RE.search(text.strip().lower()))


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class RouterConfig:
    """Effective routing configuration for one agent turn.

    ``enabled`` is True by default: routing is on, but with no configured
    routes it is a safe no-op (every turn falls back to the default model).
    ``routes`` maps a category name to a model name; categories without an
    entry fall back to the default model.
    """

    enabled: bool = True
    default_model: str = ""
    routes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, default_model: str = "") -> "RouterConfig":
        """Build a config purely from environment variables."""
        cfg = cls(enabled=False, default_model=default_model, routes={})
        cfg.apply_env()
        return cfg

    @classmethod
    def from_dict(
        cls, data: Mapping[str, Any] | None, default_model: str = ""
    ) -> "RouterConfig":
        """Build a config from a config-file ``routing`` section.

        Never raises on malformed input: non-dict input is treated as an
        empty section, and route entries for unknown categories are ignored
        instead of crashing.
        """
        enabled = False
        routes: dict[str, str] = {}
        if isinstance(data, Mapping):
            enabled = _truthy(data.get("enabled", False))
            raw_routes = data.get("routes", {})
            if isinstance(raw_routes, Mapping):
                for category, model in raw_routes.items():
                    if category not in CATEGORIES:
                        # Unknown category — ignore safely, never crash.
                        log.debug("routing: ignoring unknown route category %r", category)
                        continue
                    model_name = str(model).strip() if model is not None else ""
                    if model_name:
                        routes[category] = model_name
        return cls(enabled=enabled, default_model=default_model, routes=routes)

    def apply_env(self) -> "RouterConfig":
        """Overlay environment variables on top of this config.

        Environment always wins over the file: a *set*
        ``ZELINE_ROUTING_ENABLED`` (even to a falsy value) overrides the
        file's ``enabled``, and a set ``ZELINE_ROUTE_<CATEGORY>`` overrides
        (or, when empty, removes) that category's route.
        """
        if _ENV_ENABLED in os.environ:
            self.enabled = _truthy(os.environ[_ENV_ENABLED])
        for category in CATEGORIES:
            env_name = _env_route_name(category)
            if env_name in os.environ:
                model_name = os.environ[env_name].strip()
                if model_name:
                    self.routes[category] = model_name
                else:
                    # Explicit empty value unsets a file-defined route.
                    self.routes.pop(category, None)
        return self


# ---------------------------------------------------------------------------
# Classification & resolution
# ---------------------------------------------------------------------------


def classify(text: str, history: list[dict] | None = None) -> str:
    """Classify a user turn into a routing category.

    Deterministic and conservative: only strong signals trigger a category;
    anything doubtful returns ``"default"``. Precedence is
    ``long_context`` > ``code`` > ``research`` > ``reasoning`` > ``quick`` >
    ``default`` — in particular, a specific signal always beats ``quick``,
    so a short "halo, tolong debug kode ini" classifies as ``code``.

    ``text`` may be any value (non-strings are treated as empty);
    ``history`` is a list of message dicts and may be empty or None.
    Callers typically pass the conversation *without* the system prompt:
    the system prompt is constant overhead on every turn and carries no
    signal about the current task — including it would push every turn
    into ``long_context``.
    """
    content = text if isinstance(text, str) else ""
    lowered = content.lower()

    context_chars = len(content) + _history_chars(history)
    if context_chars >= LONG_CONTEXT_CHARS:
        return "long_context"
    if _is_code(lowered, history):
        return "code"
    if _RESEARCH_KEYWORDS_RE.search(lowered):
        return "research"
    if len(content) >= REASONING_MIN_CHARS and _REASONING_KEYWORDS_RE.search(lowered):
        return "reasoning"
    if len(content) <= QUICK_MAX_CHARS and _is_quick(lowered):
        return "quick"
    return "default"


@dataclass
class RouteDecision:
    """Outcome of routing one turn.

    ``category`` is the classified category (``"default"`` when disabled or
    when nothing matched); ``model`` is the model the turn should actually
    use; ``routed`` is True only when a configured route changed the model;
    ``reason`` is a short human-readable explanation.
    """

    category: str
    model: str
    routed: bool
    reason: str


def resolve(
    text: str,
    history: list[dict] | None,
    config: RouterConfig,
    default_model: str = "",
) -> RouteDecision:
    """Decide which model a turn should use.

    Disabled config → the default model, unrouted: behaviour identical to
    not having routing at all. Enabled config without a route for the
    classified category (or a ``"default"`` classification) → the default
    model, unrouted (fail-closed towards quality, never towards a cheaper
    model). The decision is logged at debug level via stdlib logging.
    """
    model = default_model or config.default_model or ""
    if not config.enabled:
        return RouteDecision("default", model, False, "routing disabled")
    category = classify(text, history)
    target = config.routes.get(category, "")
    if not target:
        if category == "default":
            reason = "no strong routing signal; using default model"
        else:
            reason = f"no route configured for category {category!r}; using default model"
        log.debug("routing: %s (category=%s)", reason, category)
        return RouteDecision(category, model, False, reason)
    log.debug("routing: category=%s -> model=%s", category, target)
    return RouteDecision(category, target, True, f"matched category {category!r}")
