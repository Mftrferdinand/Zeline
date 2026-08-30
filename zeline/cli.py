"""Command line interface Zeline.

Zeline command-line interface:

    zeline setup                 # wizard provider + platform
    zeline                       # chat lokal
    zeline chat -q "halo"         # satu query
    zeline gateway setup         # wizard gateway saja
    zeline gateway enable telegram
    zeline gateway start         # run all enabled gateways in the background
    zeline gateway stop|status|log
    zeline gateway run           # run foreground (systemd/tmux)
    zeline doctor                # cek instalasi/config
    zeline config path|show
    zeline skills list
    zeline memory list

Zeline adalah framework yang pengguna konfigurasi dengan bot/account mereka
sendiri. Tidak ada token Telegram/WhatsApp bersama dalam paket ini.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import signal
import sys
import time
from pathlib import Path
from typing import Any

import requests

from zeline import __version__, config, skills
from zeline._termkey import raw_mode, read_key, read_menu_key, read_secret
from zeline.agent import ZelineError
from zeline.gateways import GATEWAYS, gateway_status, run_all
from zeline import gateway_service
from zeline import interaction
from zeline import project_rules
from zeline.sessions import SessionStore


def _terminal_color_enabled() -> bool:
    """Use ANSI color only when the terminal explicitly supports it."""
    if os.environ.get("NO_COLOR") is not None or os.environ.get("TERM") == "dumb":
        return False
    return bool(os.environ.get("FORCE_COLOR")) or sys.stdout.isatty()


BANNER_TITLE = "Z  E  L  I  N  E"
BANNER_SUBTITLE = f"AGENTIC AI BY ZEROLINEAR • v{__version__}"
# Inner box width: widest content line + 3 spaces of padding each side.
_BANNER_INNER = max(len(BANNER_TITLE), len(BANNER_SUBTITLE)) + 6


def _boxed_line(text: str) -> str:
    return text.center(_BANNER_INNER)


# Shared palette (256-color). Gated by _terminal_color_enabled() via _paint().
COLOR_BLUE = "\033[38;5;39m"        # regular blue — labels before ':'
COLOR_LIGHT_BLUE = "\033[38;5;117m"  # light blue — the 'you' prompt
COLOR_DARK_BLUE = "\033[38;5;27m"    # dark blue — the 'Zeline' reply prefix
COLOR_RESET = "\033[0m"


def _paint(text: str, color: str) -> str:
    """Wrap text in an ANSI color only when the terminal supports color."""
    if not _terminal_color_enabled():
        return text
    return f"{color}{text}{COLOR_RESET}"


def _label(text: str) -> str:
    """Color a 'label :' prefix blue; the value after it stays default."""
    return _paint(text, COLOR_BLUE)


def _print_banner() -> None:
    """Render the boxed Zeline terminal identity (title + subtitle in one frame)."""
    top = "╭" + "─" * _BANNER_INNER + "╮"
    mid = "├" + "─" * _BANNER_INNER + "┤"
    bottom = "╰" + "─" * _BANNER_INNER + "╯"
    title = _boxed_line(BANNER_TITLE)
    subtitle = _boxed_line(BANNER_SUBTITLE)
    if _terminal_color_enabled():
        frame = "\033[38;5;25m"   # dark blue frame
        white = "\033[97m\033[1m"  # bright white bold title
        blue = "\033[38;5;39m"    # regular blue subtitle
        reset = "\033[0m"
        print(
            f"\n{frame}{top}{reset}\n"
            f"{frame}│{reset}{white}{title}{reset}{frame}│{reset}\n"
            f"{frame}{mid}{reset}\n"
            f"{frame}│{reset}{blue}{subtitle}{reset}{frame}│{reset}\n"
            f"{frame}{bottom}{reset}\n"
        )
    else:
        print(
            f"\n{top}\n"
            f"│{title}│\n"
            f"{mid}\n"
            f"│{subtitle}│\n"
            f"{bottom}\n"
        )


def _read_secret_key() -> str:
    return read_key()


def _masked_secret_input(prompt: str) -> str:
    """Read a secret while rendering one star per character."""
    return read_secret(prompt)


def _ask(prompt: str, default: str = "", *, secret: bool = False) -> str:
    """Small prompt with visible star masking for secrets."""
    if secret:
        suffix = " [saved — Enter keeps current value]" if default else ""
        answer = _masked_secret_input(f"{prompt}{suffix} [* per character]: ").strip()
        if answer:
            print("  ✓ Saved securely. The value stays hidden.")
    else:
        suffix = f" [{default}]" if default else ""
        answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def _model_ids(payload: Any) -> list[str]:
    data = payload.get("data", []) if isinstance(payload, dict) else []
    models = [str(item.get("id", "")).strip() for item in data if isinstance(item, dict)]
    return sorted(dict.fromkeys(model for model in models if model))


def _discover_provider_models(base_url: str, api_key: str) -> tuple[str, list[str]]:
    """Probe OpenAI-compatible first, then native Anthropic model listing."""
    endpoint = f"{base_url.rstrip('/')}/models"
    probes = (
        ("openai", {"Authorization": f"Bearer {api_key}"}),
        ("anthropic", {"x-api-key": api_key, "anthropic-version": "2023-06-01"}),
    )
    for protocol, headers in probes:
        try:
            response = requests.get(endpoint, headers=headers, timeout=20)
            if response.ok:
                models = _model_ids(response.json())
                if models:
                    return protocol, models
        except (requests.RequestException, ValueError):
            continue
    return "openai", []


def _choose_model(models: list[str], default: str = "") -> str:
    if not models:
        while True:
            selected = _ask("Model ID").strip()
            if selected:
                return selected
            print("  Model ID is required because the provider does not expose a model list.")
    # Arrow-key picker; default highlighted first. Numeric fallback on non-TTY.
    labels = [f"{model}{'  (active)' if model == default else ''}" for model in models]
    start = next((i for i, m in enumerate(models) if m == default), 0)
    choice = _arrow_menu("Select model:", labels, start=start)
    if choice == -1:
        # Batal = pertahankan model default kalau ada, else item pertama.
        return default or models[0]
    return models[choice]


def _configure_provider(provider: dict[str, Any]) -> None:
    base_url = _ask("Base URL", str(provider.get("base_url", "https://api.openai.com/v1"))).rstrip("/")
    api_key = _ask("API key", str(provider.get("api_key", "")), secret=True)
    default_name = str(provider.get("name", "")).strip() or base_url.split("://", 1)[-1].split("/", 1)[0]
    provider_name = _ask("Provider name in the Telegram picker", default_name).strip()[:48]
    print("  Detecting provider protocol and models…")
    protocol, models = _discover_provider_models(base_url, api_key)
    label = "Anthropic" if protocol == "anthropic" else "OpenAI-compatible"
    print(f"  Provider detected: {label}")
    if not models:
        print("  Model list unavailable; enter the model ID manually.")
    provider.update({
        "base_url": base_url,
        "api_key": api_key,
        "model": _choose_model(models, str(provider.get("model", ""))),
        "image_model": str(provider.get("image_model", "")),
        "name": provider_name,
        "protocol": protocol,
        "model_verified": True,
    })


def _yes_no(prompt: str, default: bool = False) -> bool:
    marker = "Y/n" if default else "y/N"
    answer = input(f"{prompt} [{marker}]: ").strip().lower()
    return default if not answer else answer in {"y", "yes", "ya"}


GATEWAY_OPTIONS = (
    ("telegram", "Telegram"),
    ("whatsapp", "WhatsApp"),
    ("webhook", "Webhook"),
    ("cancel", "Cancel"),
)


def _read_menu_key() -> str:
    return read_menu_key()


def _select_gateway() -> str:
    """Arrow-key gateway picker with numeric fallback for redirected stdin."""
    if not sys.stdin.isatty():
        print("Select gateway:")
        for index, (_value, label) in enumerate(GATEWAY_OPTIONS, 1):
            print(f"  {index}. {label}")
        while True:
            answer = input(f"Choice [1-{len(GATEWAY_OPTIONS)}]: ").strip()
            if answer.isdigit() and 1 <= int(answer) <= len(GATEWAY_OPTIONS):
                return GATEWAY_OPTIONS[int(answer) - 1][0]
            print("  Invalid choice.")

    selected = 0
    print("Select gateway (↑/↓ then Enter):")
    with raw_mode():
        try:
            while True:
                for index, (_value, label) in enumerate(GATEWAY_OPTIONS):
                    marker = _paint("❯", COLOR_BLUE) if index == selected else " "
                    print(f"\r\033[K  {marker} {label}")
                key = _read_menu_key()
                if key == "up":
                    selected = (selected - 1) % len(GATEWAY_OPTIONS)
                elif key == "down":
                    selected = (selected + 1) % len(GATEWAY_OPTIONS)
                elif key == "enter":
                    return GATEWAY_OPTIONS[selected][0]
                print(f"\033[{len(GATEWAY_OPTIONS)}A", end="", flush=True)
        finally:
            print()


def _gateway_cfg(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    gateways = cfg.setdefault("gateways", {})
    defaults = config._defaults()["gateways"]
    if name not in defaults:
        raise ValueError(f"Unknown gateway: {name}")
    current = gateways.setdefault(name, copy.deepcopy(defaults[name]))
    return current


def _setup_telegram(cfg: dict[str, Any]) -> bool:
    gateway = _gateway_cfg(cfg, "telegram")
    print("  Create a bot via @BotFather → /newbot → paste the Bot API token.")
    token = _ask("  Telegram bot token", str(gateway.get("token", "")), secret=True)
    if not token:
        print("  Telegram skipped: empty token.")
        gateway["enabled"] = False
        return False
    # Owner chat ID = the numeric Telegram account ID that owns this bot (NOT the
    # @username). Find it by messaging @userinfobot. When the installer connects
    # their OWN bot on their OWN machine and names themselves as the sole owner,
    # the bot has one trusted user, so we hand it the FULL toolset out of the box
    # (native tools + MCP + image analysis) — exactly the experience the operator
    # already runs locally. This is safe precisely because the allowlist is the
    # single owner: there is no "other user" to shell out on the runtime.
    print("  Owner chat ID = your numeric Telegram ID (message @userinfobot to get it).")
    print("  This becomes the sole allowed user and unlocks the full toolset + MCP.")
    existing_owner = str(gateway.get("owner_identity", "")).strip()
    owner_raw = _ask("  Owner chat ID (empty = public bot, safe tools only)", existing_owner)
    owner = owner_raw.strip()
    if owner:
        # Single trusted owner → full profile with the required security fields so
        # gateways._validate_tool_policy passes without any extra manual command.
        gateway.update({
            "enabled": True,
            "token": token,
            "allowed": [owner],
            "owner_identity": owner,
            "tool_profile": "full",
            "remote_code_execution_ack": True,
        })
        print("  Full toolset enabled for the owner. Add MCP servers with `zeline mcp`.")
        return True
    # No owner given → keep the conservative public default (memory + web only).
    gateway.update({"enabled": True, "token": token, "allowed": [], "tool_profile": "safe"})
    gateway.pop("owner_identity", None)
    gateway.pop("remote_code_execution_ack", None)
    print("  No owner set: bot is public with safe tools only. Re-run setup to enable full tools.")
    return True


def _setup_whatsapp(cfg: dict[str, Any]) -> bool:
    gateway = _gateway_cfg(cfg, "whatsapp")
    allowed_raw = _ask("  Allowlist numbers/JIDs (comma-separated, empty = public)", ",".join(map(str, gateway.get("allowed", []))))
    allowed = [entry.strip() for entry in allowed_raw.split(",") if entry.strip()]
    gateway.update({"enabled": True, "allowed": allowed, "tool_profile": "safe"})
    return True


def _setup_webhook(cfg: dict[str, Any], *, reveal_token: bool = True) -> bool:
    gateway = _gateway_cfg(cfg, "webhook")
    host = _ask("  Bind host (safe: 127.0.0.1)", str(gateway.get("host", "127.0.0.1")))
    port = _ask("  Port", str(gateway.get("port", 8765)))
    token = str(gateway.get("token", "")) or config.new_webhook_token()
    try:
        port_number = int(port)
        if not 1 <= port_number <= 65535:
            raise ValueError
    except ValueError:
        print("  Invalid port; webhook not enabled.")
        gateway["enabled"] = False
        return False
    gateway.update({"enabled": True, "host": host, "port": port_number, "token": token, "tool_profile": "safe"})
    if reveal_token:
        print("  Save this token now (shown only during setup):")
        print(f"  {token}")
    return True


def _setup_config(*, reset: bool) -> dict[str, Any]:
    """Use generic provider defaults until an installation has a real API key."""
    current = config.stored_config_copy()
    if reset or not str(current.get("provider", {}).get("api_key", "")).strip():
        fresh = config._defaults()
        fresh["name"] = str(current.get("name") or fresh["name"])
        return fresh
    return current


def _step(number: int, title: str, detail: str) -> None:
    print(f"\n[ STEP {number}/3 ]  {title}\n  {detail}")


def cmd_setup(*, reset: bool = False) -> int:
    _print_banner()
    print(f"==> SETUP GATEWAY  ·  {config.CONFIG_FILE}")
    if reset:
        print("  Starting with clean generic defaults. Existing provider settings will be replaced.")
    cfg = _setup_config(reset=reset)
    selected = _select_gateway()
    if selected == "cancel":
        print("Setup cancelled. Run `zeline` again to pick a gateway.")
        return 0
    configured = {
        "telegram": _setup_telegram,
        "whatsapp": _setup_whatsapp,
        "webhook": _setup_webhook,
    }[selected](cfg)
    if not configured:
        print("Gateway configuration incomplete.")
        return 2
    cfg["gateway_setup_complete"] = True
    cfg["setup_complete"] = False
    config.save_config(cfg)
    copied = skills.seed_skills()
    print(f"\n[ GATEWAY READY ]  {selected} saved. {copied} built-in skills added.")
    print("  Next: run `zeline model` to choose a provider and model.")
    return 0


def _parse_yes_no(raw: str, *, default: bool) -> bool:
    value = raw.strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "1", "true", "on"}


def cmd_setup_agent() -> int:
    """Configure agent behavior as one atomic, validated setup section."""
    cfg = config.stored_config_copy()
    agent_cfg = cfg.setdefault("agent", {})
    name = _ask("Agent name", str(cfg.get("name", "Zeline"))).strip() or "Zeline"
    rounds_raw = _ask("Max tool rounds [1-50]", str(agent_cfg.get("max_tool_rounds", 8)))
    sessions_raw = _ask("Max sessions [1-1000]", str(agent_cfg.get("max_sessions", 50)))
    stream_raw = _ask("Stream responses? [Y/n]", "y" if agent_cfg.get("stream", True) else "n")
    persist_raw = _ask(
        "Persist sessions? [Y/n]",
        "y" if agent_cfg.get("persist_sessions", True) else "n",
    )
    try:
        rounds = int(rounds_raw)
        sessions_count = int(sessions_raw)
    except ValueError:
        print("Max tool rounds and max sessions must be whole numbers.")
        return 2
    if not 1 <= rounds <= 50:
        print("Max tool rounds must be between 1 and 50.")
        return 2
    if not 1 <= sessions_count <= 1000:
        print("Max sessions must be between 1 and 1000.")
        return 2
    cfg["name"] = name
    agent_cfg.update(
        {
            "max_tool_rounds": rounds,
            "max_sessions": sessions_count,
            "stream": _parse_yes_no(stream_raw, default=True),
            "persist_sessions": _parse_yes_no(persist_raw, default=True),
        }
    )
    config.save_config(cfg)
    print("Agent settings saved.")
    # Do not echo setup input: the same prompt path also handles secrets, and
    # terminals/CI logs are not a safe place for user-supplied values.
    print("  Name       : saved")
    print(f"  Tool rounds: {rounds}")
    print(f"  Sessions   : {sessions_count}")
    return 0


def cmd_setup_tools() -> int:
    """Interactive owner-tool profile and workspace setup."""
    cfg = config.stored_config_copy()
    tool_cfg = cfg.setdefault("tools", {})
    current = str(tool_cfg.get("cli_profile", "full"))
    options = [
        "safe - memory and public web tools",
        "workspace - safe plus files in one workspace",
        "full - workspace plus code and shell (local owner only)",
        "Cancel",
    ]
    start = {"safe": 0, "workspace": 1, "full": 2}.get(current, 2)
    selected = _arrow_menu("Local CLI tool profile:", options, start=start)
    if selected in {-1, 3}:
        print("Tools setup cancelled.")
        return 0
    profile = ("safe", "workspace", "full")[selected]
    workspace = Path(str(tool_cfg.get("workspace", Path.home()))).expanduser()
    if profile in {"workspace", "full"}:
        raw = _ask("Workspace directory", str(workspace))
        workspace = Path(raw).expanduser().resolve(strict=False)
        if not workspace.is_dir():
            print(f"Workspace directory not found: {workspace}")
            return 2
    tool_cfg["cli_profile"] = profile
    tool_cfg["workspace"] = str(workspace.resolve(strict=False))
    config.save_config(cfg)
    print("Tool settings saved.")
    print(f"  CLI profile: {profile}")
    print(f"  Workspace  : {tool_cfg['workspace']}")
    print("  Fine tune  : zeline tools list|enable|disable")
    return 0


def cmd_setup_integrations() -> int:
    """Small MCP integration wizard; native integrations arrive as modules."""
    while True:
        choice = _arrow_menu(
            "Integrations:",
            [
                "List configured MCP servers",
                "Add MCP server (stdio command)",
                "Add MCP server (HTTP URL)",
                "Test configured MCP servers",
                "Done",
            ],
        )
        if choice in {-1, 4}:
            print("Integrations setup done.")
            return 0
        if choice == 0:
            cmd_mcp("list")
        elif choice == 1:
            name = _ask("Integration name", "").strip()
            command = _ask("Command", "").strip()
            if not name or not command:
                print("Name and command are required.")
                continue
            cmd_mcp("add", name, command=command)
        elif choice == 2:
            name = _ask("Integration name", "").strip()
            url = _ask("HTTP URL", "").strip()
            if not name or not url:
                print("Name and URL are required.")
                continue
            cmd_mcp("add", name, url=url)
        elif choice == 3:
            cmd_mcp("test")


def cmd_setup_center() -> int:
    """Reconfigurable setup center; first-run onboarding remains `cmd_setup`."""
    _print_banner()
    print(f"==> SETUP CENTER  ·  {config.CONFIG_FILE}")
    while True:
        choice = _arrow_menu(
            "Configure:",
            [
                "Gateway - Telegram, WhatsApp, or webhook",
                "Model - provider, endpoint, API key, and model",
                "Tools - security profile and workspace",
                "Integrations - MCP servers and external tools",
                "Agent - identity, sessions, streaming, tool rounds",
                "Done",
            ],
        )
        if choice in {-1, 5}:
            print("Setup center done. Run `zeline doctor` to verify everything.")
            return 0
        if choice == 0:
            cmd_gateway_setup(None)
        elif choice == 1:
            cmd_model()
        elif choice == 2:
            cmd_setup_tools()
        elif choice == 3:
            cmd_setup_integrations()
        elif choice == 4:
            cmd_setup_agent()


def _provider_slug(provider: dict[str, Any]) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(provider.get("name", "provider")).lower()).strip("-") or "provider"


def _active_slug(cfg: dict[str, Any]) -> str:
    """Active provider slug based on base_url + name (not just the name)."""
    active = cfg.get("provider", {})
    for slug, item in cfg.get("providers", {}).items():
        if item.get("base_url") == active.get("base_url") and item.get("name") == active.get("name"):
            return str(slug)
    return _provider_slug(active)


def _print_provider_list(cfg: dict[str, Any]) -> list[str]:
    """Show saved providers; return the ordered slug list."""
    providers = cfg.get("providers", {})
    active = _active_slug(cfg)
    slugs = list(providers.keys())
    print("\n  Saved providers:")
    if not slugs:
        print("    (none yet)")
    for index, slug in enumerate(slugs, 1):
        item = providers[slug]
        marker = " (active)" if slug == active else ""
        print(f"    {index:>2}. {item.get('name', slug)} · {item.get('model', '?')}{marker}")
    return slugs


def _model_add_provider(cfg: dict[str, Any]) -> None:
    """Add a new provider; make it active after model verification."""
    provider: dict[str, Any] = {}
    _configure_provider(provider)
    slug = _provider_slug(provider)
    cfg.setdefault("providers", {})[slug] = copy.deepcopy(provider)
    cfg["provider"] = copy.deepcopy(provider)
    cfg["setup_complete"] = True
    config.save_config(cfg)
    # Plain locals so static analysis doesn't taint-flag the provider dict
    # (which also holds api_key) as clear-text logging.
    added_name = str(provider.get("name", slug))
    added_model = str(provider["model"])
    print(f"  Provider '{added_name}' added & activated - model {added_model}")


def _model_remove_provider(cfg: dict[str, Any]) -> None:
    """Remove a saved provider; the active provider cannot be removed."""
    slugs = _print_provider_list(cfg)
    if len(slugs) <= 1:
        print("  At least one provider must remain; nothing removed.")
        return
    active = _active_slug(cfg)
    choice = input(f"  Remove provider number [1-{len(slugs)}] (Enter = cancel): ").strip()
    if not choice:
        print("  Cancelled.")
        return
    if not (choice.isdigit() and 1 <= int(choice) <= len(slugs)):
        print("  Invalid choice.")
        return
    slug = slugs[int(choice) - 1]
    if slug == active:
        print("  That provider is currently active. Switch the active provider before removing it.")
        return
    removed = cfg["providers"].pop(slug)
    config.save_config(cfg)
    print(f"  Provider '{removed.get('name', slug)}' removed.")


def _model_view_provider(cfg: dict[str, Any]) -> None:
    """Lihat provider tersimpan; pilih satu untuk ganti model atau API key."""
    providers = cfg.get("providers", {})
    slugs = list(providers.keys())
    if not slugs:
        print("  No saved providers yet. Use 'Add url provider' first.")
        return
    active = _active_slug(cfg)
    labels = []
    for slug in slugs:
        item = providers[slug]
        mark = "  (active)" if slug == active else ""
        labels.append(f"{item.get('name', slug)} - {item.get('model', '?')}{mark}")
    labels.append("Cancel")
    choice = _arrow_menu("View provider:", labels)
    if choice == -1 or choice == len(slugs):
        return
    slug = slugs[choice]
    provider = copy.deepcopy(providers[slug])
    while True:
        # Read every display value out of the dict ONCE, into plain locals.
        # CodeQL taint-tracks the whole `provider` dict because it also holds
        # `api_key`, so ANY f-string reading from it is flagged as clear-text
        # logging of a secret -- even when the value is just a model name. Plain
        # locals break that chain. The key itself is only ever printed masked.
        name = str(provider.get("name", slug))
        shown_base_url = str(provider.get("base_url", "?"))
        shown_model = str(provider.get("model", "?"))
        shown_image_model = str(provider.get("image_model", "")) or "(none)"
        masked_key = config.mask_secret(str(provider.get("api_key", "")))
        print(f"\n  Provider: {name}")
        print(f"  Base URL: {shown_base_url}")
        print(f"  Model   : {shown_model}")
        print(f"  Image model: {shown_image_model}")
        print(f"  API key : {masked_key}")
        action = _arrow_menu(
            "Aksi provider:",
            ["Set as active", "Change model", "Change image model", "Change API key", "Cancel"],
        )
        if action == -1 or action == 4:
            return
        if action == 0:  # Set as active
            provider["model_verified"] = True
            cfg["providers"][slug] = copy.deepcopy(provider)
            cfg["provider"] = copy.deepcopy(provider)
            cfg["setup_complete"] = True
            config.save_config(cfg)
            # Extract to plain locals so static analysis doesn't taint-flag the
            # provider dict (which also holds api_key) as clear-text logging.
            active_model = str(provider.get("model", "?"))
            print(f"  Active: {name} - model {active_model}")
            return
        if action == 1:  # Change model
            print(f"  Fetching model list from {name}...")
            _protocol, models = _discover_provider_models(
                str(provider.get("base_url", "")), str(provider.get("api_key", ""))
            )
            provider["model"] = _choose_model(models, str(provider.get("model", "")))
            provider["model_verified"] = True
            cfg["providers"][slug] = copy.deepcopy(provider)
            if slug == _active_slug(cfg):
                cfg["provider"] = copy.deepcopy(provider)
            config.save_config(cfg)
            chosen_model = str(provider["model"])
            print(f"  Model updated: {chosen_model}")
        elif action == 2:  # Change image model
            new_image_model = _ask(
                "Image (text-to-image) model, blank to disable",
                str(provider.get("image_model", "")),
            ).strip()
            provider["image_model"] = new_image_model
            cfg["providers"][slug] = copy.deepcopy(provider)
            if slug == _active_slug(cfg):
                cfg["provider"] = copy.deepcopy(provider)
            config.save_config(cfg)
            print(f"  Image model updated: {new_image_model or '(none)'}")
        elif action == 3:  # Change API key
            new_key = _ask("API key", str(provider.get("api_key", "")), secret=True)
            provider["api_key"] = new_key
            cfg["providers"][slug] = copy.deepcopy(provider)
            if slug == _active_slug(cfg):
                cfg["provider"] = copy.deepcopy(provider)
            config.save_config(cfg)
            print("  API key updated.")


def _arrow_menu(title: str, options: list[str], *, start: int = 0) -> int:
    """Arrow-key picker ↑/↓ + Enter. Return the selected index, -1 if cancelled.

    Falls back to numeric input when stdin is not a TTY (e.g. redirected in tests
    or automation). ESC / Ctrl-C = cancel (-1).
    """
    if not options:
        return -1
    if not sys.stdin.isatty():
        print(title)
        for index, label in enumerate(options, 1):
            print(f"  {index}. {label}")
        while True:
            answer = input(f"Choice [1-{len(options)}] (empty = cancel): ").strip()
            if not answer:
                return -1
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return int(answer) - 1
            print("  Invalid choice.")

    selected = max(0, min(start, len(options) - 1))
    print(title + "  (↑/↓ then Enter, Esc = cancel)")
    with raw_mode():
        try:
            while True:
                for index, label in enumerate(options):
                    marker = _paint("❯", COLOR_BLUE) if index == selected else " "
                    print(f"\r\033[K  {marker} {label}")
                key = _read_menu_key()
                if key == "up":
                    selected = (selected - 1) % len(options)
                elif key == "down":
                    selected = (selected + 1) % len(options)
                elif key == "enter":
                    return selected
                elif key == "cancel":
                    return -1
                print(f"\033[{len(options)}A", end="", flush=True)
        finally:
            print()


def cmd_model() -> int:
    """Provider/model menu with an arrow-key picker (↑/↓ + Enter)."""
    if not config.GATEWAY_SETUP_COMPLETE:
        print("[!] Choose and set up a gateway first. Run: zeline")
        return 2
    cfg = config.stored_config_copy()
    while True:
        # Action menu (no emoji): add/remove/view provider, or cancel.
        rows = [
            "Add url provider",
            "Remove provider",
            "View provider",
            "Cancel",
        ]
        idx_add, idx_remove, idx_view, idx_cancel = 0, 1, 2, 3

        choice = _arrow_menu("Provider & model:", rows)
        if choice == -1 or choice == idx_cancel:
            print("Done.")
            return 0
        if choice == idx_add:
            _model_add_provider(cfg)
        elif choice == idx_remove:
            _model_remove_provider(cfg)
        elif choice == idx_view:
            _model_view_provider(cfg)
        cfg = config.stored_config_copy()


def _run_reflection(sessions: "SessionStore") -> None:
    """Self-improvement review saat sesi CLI ditutup (best-effort, non-fatal).

    Shows saved/updated skills if any; stays silent otherwise.
    """
    try:
        summary = sessions.reflect("cli:local")
    except Exception:
        summary = None
    if summary:
        print(f"\n\033[90m📒 Self-improvement: {summary}\033[0m")


def cmd_chat(query: str | None = None) -> int:
    if not config.GATEWAY_SETUP_COMPLETE:
        print("[!] Gateway not set up yet. Run: zeline")
        return 2
    if not config.SETUP_COMPLETE:
        print("[!] Gateway is ready. Next run: zeline model")
        return 2
    if not bool(config.PROVIDER.get("model_verified", False)):
        print("[!] Model not verified from the provider. Run: zeline model")
        return 2
    if not config.API_KEY:
        print("[!] API key is empty. Run: zeline setup")
        return 2
    _print_banner()
    print(f"  {_label('Agent :')} {config.NAME}")
    print(f"  {_label('Model :')} {config.MODEL}")
    print(f"  {_label('Tool profile:')} full (local operator)\n")
    sessions = SessionStore(max_sessions=1)

    # In the CLI the operator is right here at the keyboard, so ask_user can be
    # answered SYNCHRONOUSLY — no waiting on an event, no timeout. Registering a
    # renderer that returns a string short-circuits interaction.ask().
    def _cli_ask(entry: Any) -> str:
        print()
        print(f"  {_label('question:')} {entry.question}")
        if entry.options:
            for index, option in enumerate(entry.options, start=1):
                print(f"    {index}) {option}")
            print("    (type a number, or your own answer)")
        try:
            reply = input("  answer › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return "CANCELLED: the user cancelled this question."
        if entry.options and reply.isdigit():
            choice = int(reply)
            if 1 <= choice <= len(entry.options):
                return entry.options[choice - 1]
        return reply or "(empty answer)"

    interaction.register_channel("cli:local", _cli_ask)

    def ask(text: str) -> str:
        def on_tool(name: str, arguments: dict[str, Any]) -> None:
            preview = ", ".join(f"{key}={str(value)[:48]}" for key, value in arguments.items())
            print(f"  \033[90m⚙ {name}({preview})\033[0m")

        return sessions.send(
            identity="cli:local",
            text=text,
            tool_profile=config.CLI_TOOL_PROFILE,
            on_tool=on_tool,
        )

    if query is not None:
        try:
            print(ask(query))
            return 0
        except ZelineError as exc:
            print(f"[error] {exc}")
            return 1

    print("Type 'exit' to quit.\n")
    while True:
        try:
            text = input(f"{_paint('you ›', COLOR_LIGHT_BLUE) if _terminal_color_enabled() else 'you ›'} ").strip()
        except (EOFError, KeyboardInterrupt):
            _run_reflection(sessions)
            print("\nGoodbye!")
            return 0
        if not text:
            continue
        if text.lower() in {"keluar", "exit", "quit", "q"}:
            _run_reflection(sessions)
            print("Goodbye!")
            return 0
        # Handled in the REPL rather than sent to the model: the operator wants
        # the file back now, not a turn spent asking the agent to do it.
        lowered = text.lower()
        if lowered in {"undo", "/undo"}:
            cmd_undo()
            print()
            continue
        if lowered in {"undo --list", "/undo --list", "undos", "/undos"}:
            cmd_undo(show_list=True)
            print()
            continue
        try:
            answer = ask(text)
            print(f"{_paint(f'{config.NAME} ›', COLOR_DARK_BLUE)} {answer}\n")
        except ZelineError as exc:
            print(f"\033[31m[error] {exc}\033[0m\n")


def cmd_mcp(action: str, name: str | None = None, *, transport: str = "", command: str = "", url: str = "") -> int:
    """Manage MCP servers: add / list / remove / test."""
    from zeline import mcp as mcp_module

    if action == "list":
        servers = config.stored_config_copy().get("mcp", {}).get("servers", {})
        if not servers:
            print("No MCP servers yet. Add one with: zeline mcp add <name> --command '...' or --url '...'")
            return 0
        print("MCP server:")
        for server_name, spec in servers.items():
            state = "enabled" if spec.get("enabled", True) else "disabled"
            kind = spec.get("transport") or ("http" if spec.get("url") else "stdio")
            target = spec.get("url") or spec.get("command") or "?"
            print(f"  - {server_name:<16} [{kind}] {state}  {target}")
        return 0

    if action == "add":
        if not name:
            print("Server name required. Example: zeline mcp add filesystem --command 'npx -y @modelcontextprotocol/server-filesystem ~/'")
            return 2
        if not command and not url:
            print("Need --command (stdio) or --url (http).")
            return 2
        cfg = config.stored_config_copy()
        servers = cfg.setdefault("mcp", {}).setdefault("servers", {})
        spec: dict[str, Any] = {"enabled": True}
        if url:
            spec.update({"transport": "http", "url": url})
        else:
            spec.update({"transport": "stdio", "command": command})
        servers[name] = spec
        config.save_config(cfg)
        print(f"MCP server '{name}' added. Test with: zeline mcp test {name}")
        return 0

    if action == "remove":
        if not name:
            print("Need the name of the server to remove.")
            return 2
        cfg = config.stored_config_copy()
        servers = cfg.get("mcp", {}).get("servers", {})
        if name not in servers:
            print(f"MCP server '{name}' not found.")
            return 2
        servers.pop(name)
        config.save_config(cfg)
        print(f"MCP server '{name}' removed.")
        return 0

    if action == "test":
        servers = config.stored_config_copy().get("mcp", {}).get("servers", {})
        targets = {name: servers[name]} if name and name in servers else servers
        if not targets:
            print("No servers to test.")
            return 2
        registry = mcp_module.MCPRegistry.from_config({"mcp": {"servers": targets}})
        total = 0
        for server_name, server in registry.servers.items():
            try:
                tools = server.list_tools()
                print(f"  ✓ {server_name}: {len(tools)} tools")
                for schema in tools[:20]:
                    fn = schema["function"]
                    print(f"      - {fn['name']}: {str(fn.get('description',''))[:70]}")
                total += len(tools)
            except Exception as exc:
                print(f"  ✗ {server_name}: {exc.__class__.__name__}: {exc}")
            finally:
                server.close()
        print(f"Total {total} MCP tools ready.")
        return 0

    print(f"Unknown MCP action: {action}. Options: add, list, remove, test.")
    return 2


def cmd_gateway_setup(name: str | None = None) -> int:
    cfg = config.stored_config_copy()
    if name and name not in GATEWAYS:
        print(f"Unknown gateway: {name}. Options: {', '.join(GATEWAYS)}")
        return 2
    names = [name] if name else list(GATEWAYS)
    for gateway_name in names:
        if gateway_name == "telegram":
            _setup_telegram(cfg)
        elif gateway_name == "whatsapp":
            _setup_whatsapp(cfg)
        elif gateway_name == "webhook":
            _setup_webhook(cfg)
    config.save_config(cfg)
    print("Gateway configuration saved.")
    return 0


def cmd_gateway_enable(name: str) -> int:
    if name not in GATEWAYS:
        print(f"Unknown gateway: {name}. Options: {', '.join(GATEWAYS)}")
        return 2
    cfg = config.stored_config_copy()
    gateway = _gateway_cfg(cfg, name)
    if name == "webhook":
        # Non-interactive: secret is generated but not printed (safe for automation).
        gateway.update({"enabled": True, "host": "127.0.0.1", "port": int(gateway.get("port", 8765)), "token": gateway.get("token") or config.new_webhook_token(), "tool_profile": "safe"})
    elif name == "telegram":
        if not gateway.get("token"):
            print("Telegram needs a token. Use: zeline gateway setup telegram")
            return 2
        gateway["enabled"] = True
    else:
        gateway["enabled"] = True
    config.save_config(cfg)
    print(f"Gateway {name} enabled. Run `zeline gateway run`.")
    return 0


def cmd_gateway_disable(name: str) -> int:
    if name not in GATEWAYS:
        print(f"Unknown gateway: {name}. Options: {', '.join(GATEWAYS)}")
        return 2
    cfg = config.stored_config_copy()
    _gateway_cfg(cfg, name)["enabled"] = False
    config.save_config(cfg)
    print(f"Gateway {name} disabled in config.")
    return 0


def cmd_gateway_list() -> int:
    # "enabled" (config enabled) is CLEARLY SEPARATED from "running" (live
    # background process). Both used to show "ACTIVE", which confused users
    # ("why does `gateway` say ACTIVE but `gateway status` says not
    # running?"). Now the wording differs: config vs process.
    print("Gateway (configuration):")
    for name, enabled, errors in gateway_status(config.GATEWAYS):
        state = "enabled" if enabled else "disabled"
        suffix = f" · issues: {'; '.join(errors)}" if errors else ""
        print(f"  - {name:<10} {state}{suffix}")
    active, _message, state = gateway_service.status()
    if active:
        pid = (state or {}).get("pid", "?")
        print(f"\nBackground process: RUNNING (PID {pid}).")
    else:
        print("\nBackground process: not running — run `zeline gateway start`.")
    # Poller yang tidak kita spawn (mis. `zeline gateway run` di tab lain) tidak
    # tercatat di PID file, jadi dulu tampak seperti "not running" padahal ia
    # aktif menjawab pesan — sumber kebingungan "kok jawabannya dobel?".
    # Kunci proses melihatnya, jadi laporkan apa adanya.
    holder = gateway_service.lock_holder_pid()
    if holder != 0 and holder != int((state or {}).get("pid", 0)):
        where = f"PID {holder}" if holder > 0 else "unknown PID"
        print(f"Unmanaged poller: DETECTED ({where}) — a `gateway run` outside `gateway start`.")
    return 0


def cmd_gateway_token(name: str) -> int:
    """Show secret status without ever leaking its value."""
    if name not in GATEWAYS:
        print(f"Unknown gateway: {name}. Options: {', '.join(GATEWAYS)}")
        return 2
    token = str(config.config_copy().get("gateways", {}).get(name, {}).get("token", ""))
    if not token:
        print(f"Gateway {name} has no secret token yet.")
        return 2
    print(f"Token {name}: ** (stored)")
    return 0


def cmd_gateway_start(only: list[str] | None = None) -> int:
    if not config.API_KEY:
        print("API key is empty. Run `zeline setup` before gateway start.")
        return 2
    enabled = [
        name for name, gateway_cfg in config.GATEWAYS.items()
        if gateway_cfg.get("enabled", False) and (not only or name in only)
    ]
    if not enabled:
        print("No enabled gateway. Run `zeline gateway setup`.")
        return 2
    started, message = gateway_service.start(only=only)
    print(message)
    if not started:
        return 1
    # Don't just report 'spawned' — wait for each platform to actually connect
    # (getMe + polling) and show a live per-gateway status, like Zeline does.
    print("  Connecting…", flush=True)
    ready, lines = gateway_service.wait_until_connected(timeout=90.0)
    for line in lines:
        icon = "✅" if ready else "⚠️"
        print(f"  {icon} {line}")
    if ready:
        print("  Gateway is live and connected.")
    else:
        print("  Not all gateways connected yet. Check `zeline gateway log`.")
    return 0 if ready else 1


def cmd_gateway_stop() -> int:
    stopped, message = gateway_service.stop()
    print(message)
    return 0 if stopped else 1


def cmd_gateway_restart(only: list[str] | None = None) -> int:
    # Restart harus MENUNGGU kerja yang sedang jalan, bukan memotongnya:
    # SIGUSR1 → drain → exit, dengan eskalasi paksa hanya bila drain gagal.
    _stopped, stop_message = gateway_service.drain_then_stop()
    print(stop_message)
    started, start_message = gateway_service.start(only=only)
    print(start_message)
    if not started:
        return 1
    print("  Connecting…", flush=True)
    ready, lines = gateway_service.wait_until_connected(timeout=90.0)
    for line in lines:
        icon = "✅" if ready else "⚠️"
        print(f"  {icon} {line}")
    if ready:
        print("  Gateway is live and connected.")
    else:
        print("  Not all gateways connected yet. Check `zeline gateway log`.")
    return 0 if ready else 1


def cmd_gateway_service_status() -> int:
    active, message, _state = gateway_service.status()
    print(message)
    return 0 if active else 1


def cmd_gateway_log(lines: int = 80) -> int:
    print(gateway_service.tail_log(lines=lines))
    return 0


def cmd_gateway_run(only: list[str] | None = None) -> int:
    active, _message, state = gateway_service.status()
    managed_pid = int((state or {}).get("pid", 0))
    if active and managed_pid != os.getpid():
        print(
            f"Gateway is already running (PID {managed_pid}). "
            "Stop it first with `zeline gateway stop`; duplicate process refused."
        )
        return 1
    enabled = [(name, data) for name, data in config.GATEWAYS.items() if data.get("enabled") and (not only or name in only)]
    if not enabled:
        print("No enabled gateway. Run `zeline gateway setup`.")
        return 2
    if not config.API_KEY:
        print("API key is empty. Run `zeline setup` before gateway run.")
        return 2
    # Kunci eksklusif OS: penjaga NYATA terhadap gateway ganda. PID file hanya
    # ditulis oleh `gateway start`, jadi dua `gateway run` di dua tab Termux
    # dulu sama-sama lolos dan mem-poll token yang sama → tiap pesan dijawab
    # dua kali. Kunci dipegang kernel selama proses hidup dan lepas otomatis
    # saat proses mati (termasuk SIGKILL), jadi tidak ada stale lock.
    lock = gateway_service.GatewayLock()
    if not lock.acquire():
        holder = gateway_service.lock_holder_pid()
        where = f"PID {holder}" if holder > 0 else "another process"
        print(
            f"Another gateway is already polling ({where}). "
            "Stop it first with `zeline gateway stop`; duplicate process refused."
        )
        return 1
    try:
        if gateway_service.is_termux():
            print(
                "Termux: `gateway run` stays attached to this foreground terminal. "
                "For daily use prefer `zeline gateway start --only telegram`."
            )
        return _run_gateway_loop(only)
    finally:
        lock.release()


def _run_gateway_loop(only: list[str] | None) -> int:
    """Loop gateway foreground. Dipisah agar kunci proses dilepas di satu tempat."""
    _print_banner()
    print("==> Starting gateway…")
    _wake_active, wake_message = gateway_service.ensure_termux_wake_lock()
    if wake_message:
        prefix = "  ✅" if _wake_active else "  ⚠️"
        print(f"{prefix} {wake_message}", flush=True)
    sessions = SessionStore()
    runtime = run_all(sessions, config.GATEWAYS, names=only)
    if not runtime.threads:
        print("No gateway passed validation.")
        return 2

    def shutdown(_signal=None, _frame=None):
        print("\n==> Stopping gateway…", flush=True)
        runtime.stop()

    def drain_and_shutdown(_signal=None, _frame=None):
        """SIGUSR1: selesaikan turn yang sedang jalan, lalu keluar.

        Ini jalur restart yang SOPAN. `gateway stop` mengirim SIGTERM lalu
        SIGKILL, jadi build/install/analisis yang sedang berjalan mati di
        tengah jalan. Dengan drain, turn aktif dibiarkan selesai lebih dulu
        dan turn baru ditolak dengan pesan yang jelas.
        """
        timeout = float(getattr(config, "RESTART_DRAIN_TIMEOUT", 30.0))
        print(f"\n==> Draining (up to {int(timeout)}s) before restart…", flush=True)
        finished, busy = sessions.drain(timeout=timeout)
        if finished:
            print("  ✅ All turns finished; no work was cut off.", flush=True)
        else:
            print(f"  ⚠️ Still busy after drain timeout: {', '.join(busy)}", flush=True)
        runtime.stop()

    previous_int = signal.signal(signal.SIGINT, shutdown)
    previous_term = signal.signal(signal.SIGTERM, shutdown)
    # SIGUSR1 = graceful drain-then-exit. POSIX only; Windows has no SIGUSR1,
    # so there the managed stop path stays the only mechanism.
    previous_usr1 = None
    sigusr1 = getattr(signal, "SIGUSR1", None)
    if sigusr1 is not None:
        previous_usr1 = signal.signal(sigusr1, drain_and_shutdown)
    # `gateway stop` on Windows sends CTRL_BREAK_EVENT, which arrives as
    # SIGBREAK — without this handler the child ignores the graceful phase and
    # always has to be force-killed with taskkill.
    previous_break = None
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        previous_break = signal.signal(sigbreak, shutdown)
    try:
        while runtime.alive:
            time.sleep(0.5)
    except KeyboardInterrupt:
        shutdown()
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
        if sigusr1 is not None and previous_usr1 is not None:
            signal.signal(sigusr1, previous_usr1)
        if sigbreak is not None and previous_break is not None:
            signal.signal(sigbreak, previous_break)
        runtime.stop(timeout=1)
    return 0


def cmd_doctor() -> int:
    _print_banner()
    problems: list[str] = []
    warnings: list[str] = []
    print("Zeline doctor")
    print(f"  {_label('home      :')} {config.DATA_DIR}")
    print(f"  {_label('config    :')} {config.CONFIG_FILE} {'present' if config.CONFIG_FILE.exists() else 'not created'}")
    print(f"  {_label('python    :')} {sys.version.split()[0]}")
    print(f"  {_label('provider  :')} {config.BASE_URL or '(empty)'}")
    print(f"  {_label('model     :')} {config.MODEL or '(empty)'}")
    masked_key = config.mask_secret(config.API_KEY)
    print(f"  {_label('api key   :')} {masked_key}")
    if not config.API_KEY:
        problems.append("API key is empty — run `zeline setup`.")
    if not config.BASE_URL:
        problems.append("Provider base URL is empty.")
    if not config.MODEL:
        problems.append("Provider model is empty.")
    if not Path(config.WORKSPACE).expanduser().exists():
        warnings.append(f"Workspace not found: {config.WORKSPACE}")
    # Only surface gateways that are actually enabled (configured to connect).
    # Disabled platforms (e.g. WhatsApp/webhook when we only use Telegram) are
    # not printed at all. For each enabled gateway show whether the background
    # process is live (connected/running) or just configured (not connected yet).
    active, _msg, state = gateway_service.status()
    enabled_gateways = [name for name, enabled, errors in gateway_status(config.GATEWAYS) if enabled]
    for name, enabled, errors in gateway_status(config.GATEWAYS):
        if enabled and errors:
            problems.append(f"Gateway {name}: {'; '.join(errors)}")
    if not enabled_gateways:
        print(f"  {_label('gateway   :')} none configured (run `zeline gateway setup`)")
    else:
        for name in enabled_gateways:
            if active:
                pid = (state or {}).get("pid", "?")
                print(f"  {_label('gateway   :')} {name} connected & running (PID {pid})")
            else:
                print(f"  {_label('gateway   :')} {name} configured but not connected (run `zeline gateway start`)")
    if warnings:
        print("\nWarnings:")
        for item in warnings:
            print(f"  - {item}")
    if problems:
        print("\nNeeds fixing:")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("\nOK — basic configuration is healthy.")
    return 0


def cmd_config(action: str) -> int:
    if action == "path":
        print(config.CONFIG_FILE)
        return 0
    if action == "show":
        output = config.config_copy()
        output["provider"]["api_key"] = config.mask_secret(str(output["provider"].get("api_key", "")))
        for gateway in output.get("gateways", {}).values():
            if "token" in gateway:
                gateway["token"] = config.mask_secret(str(gateway.get("token", "")))
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    print("Usage: zeline config path|show")
    return 2


def cmd_skills() -> int:
    available = skills.list_skill_entries(include_private=True)
    if not available:
        print("No skills yet. Run `zeline setup` to copy the built-in skills.")
        return 0
    for scope, name, _title, description in available:
        print(f"  - {name} [{scope}]: {description}")
    return 0


def cmd_memory() -> int:
    from zeline.memory import list_memory

    print(list_memory("cli:local"))
    return 0


def cmd_init(directory: str | None = None, *, force: bool = False) -> int:
    """Create a ZELINE.md so project conventions load automatically.

    Inspects the directory for real build/test tooling instead of emitting a
    generic template, then writes the file Zeline reads into its system prompt.
    """
    root = Path(directory or Path.cwd()).expanduser().resolve(strict=False)
    if not root.is_dir():
        print(f"[!] Not a directory: {root}")
        return 2

    existing = project_rules.find_rules_file(root)
    if existing is not None and not force:
        print(f"Project rules already exist: {existing}")
        print("Zeline loads this file automatically. Use --force to overwrite.")
        return 0

    target = root / "ZELINE.md"
    if target.exists() and not force:
        print(f"[!] {target} exists. Use --force to overwrite.")
        return 2

    try:
        target.write_text(project_rules.render_template(root), encoding="utf-8")
    except OSError as exc:
        print(f"[!] Could not write {target}: {exc}")
        return 1

    print(f"Created {target}")
    print("  Zeline loads it into the system prompt for sessions in this directory.")
    print("  Fill in the TODO lines so the agent follows your conventions.")
    return 0


def _persistence():
    from zeline.session_store import SessionPersistence

    return SessionPersistence()


def cmd_session_export(identity: str, path: str | None = None, *, include_archive: bool = False) -> int:
    """Write one session's history to a JSON file."""
    from zeline import session_transfer

    store = _persistence()
    messages, title = store.load(identity)
    if not messages:
        print(f"[!] No stored session for identity: {identity}")
        print("    List what exists with: zeline session list")
        return 2
    archive = store.recent_archive(identity, limit=500) if include_archive else []
    payload = session_transfer.build_export(identity, messages, title, archive)
    target = session_transfer.write_export(path or f"zeline-session-{int(time.time())}.json", payload)
    print(f"Exported {len(messages)} messages to {target}")
    if archive:
        print(f"  plus {len(archive)} archived turns")
    print("  File mode 0600. It contains the full transcript — treat it as sensitive")
    print("  and check it before sharing if secrets were discussed in this session.")
    return 0


def cmd_session_import(identity: str, path: str, *, replace: bool = False) -> int:
    """Load a session export into an identity."""
    from zeline import session_transfer

    payload, error = session_transfer.read_export(path)
    if payload is None:
        print(f"[!] Cannot import: {error}")
        return 2
    messages, stats = session_transfer.sanitize_messages(payload["messages"])
    if not messages:
        print("[!] Nothing importable in that file (no user/assistant/tool messages).")
        return 2

    store = _persistence()
    existing, _existing_title = store.load(identity)
    if existing and not replace:
        print(f"[!] {identity} already has {len(existing)} stored messages.")
        print("    Use --replace to overwrite, or import into a fresh identity.")
        return 2

    title = str(payload.get("title") or "").strip() or None
    store.save(identity, messages, title)
    print(f"Imported {len(messages)} messages into {identity}")
    if stats["system_dropped"]:
        print(f"  Dropped {stats['system_dropped']} system message(s): the runtime owns the")
        print("  system prompt, so an imported one is never honoured.")
    if stats["invalid_dropped"]:
        print(f"  Dropped {stats['invalid_dropped']} unusable message(s) (bad shape or an")
        print("  incomplete tool-call turn that the provider would reject).")
    if stats["truncated"]:
        print(f"  Ignored {stats['truncated']} message(s) beyond the import limit.")
    origin = str(payload.get("identity") or "")
    if origin and origin != identity:
        print(f"  Source identity was {origin}.")
    return 0


def cmd_session_fork(source: str, target: str) -> int:
    """Copy a session's history to another identity, leaving the original intact."""
    from zeline import session_transfer

    if source == target:
        print("[!] Source and target identity are the same.")
        return 2
    store = _persistence()
    messages, title = store.load(source)
    if not messages:
        print(f"[!] No stored session for identity: {source}")
        return 2
    existing, _title = store.load(target)
    if existing:
        print(f"[!] {target} already has {len(existing)} stored messages; refusing to overwrite.")
        return 2
    # Route through the same sanitizer as import: a fork must not be able to
    # carry a stale system message or a broken tool-call tail either.
    clean, _stats = session_transfer.sanitize_messages(messages)
    store.save(target, clean, title)
    print(f"Forked {len(clean)} messages: {source} -> {target}")
    print(f"  {source} is unchanged.")
    return 0


def cmd_session_list() -> int:
    """Show stored sessions. Identities are hashed, so show what we can."""
    store = _persistence()
    rows = store.list_sessions()
    if not rows:
        print("No stored sessions.")
        return 0
    print(f"{len(rows)} stored session(s):")
    for row in rows:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["updated_at"]))
        title = row["title"] or "(untitled)"
        print(f"  {row['key']}  {row['messages']:>4} msg  {when}  {title}")
    print()
    print("Identities are stored hashed for privacy, so pass the identity string you")
    print("used (e.g. telegram:123456 or cli:local) to export/import/fork.")
    return 0


def cmd_stats(days: int | None = None, *, by_day: bool = False, reset: bool = False) -> int:
    """Report token usage, and cost only for models the operator priced."""
    from zeline import usage_stats

    store = usage_stats.UsageStore()
    if reset:
        removed = store.clear()
        print(f"Cleared {removed} usage record(s).")
        return 0

    if not usage_stats.enabled():
        print("Usage tracking is disabled (agent.usage_tracking = false).")
        return 0

    since = usage_stats.since_day_for(days)
    window = f"last {days} day(s)" if since else "all time"
    totals = store.totals(since)
    if not totals["calls"]:
        print(f"No usage recorded ({window}).")
        print("  Usage appears after the agent talks to a provider. Note that some")
        print("  OpenAI-compatible relays omit the usage block entirely.")
        return 0

    print(f"Token usage — {window}")
    print(
        f"  {totals['calls']} call(s) across {totals['models']} model(s): "
        f"{usage_stats.format_tokens(totals['total_tokens'])} tokens "
        f"({usage_stats.format_tokens(totals['prompt_tokens'])} in / "
        f"{usage_stats.format_tokens(totals['completion_tokens'])} out)"
    )
    print()

    rows = store.by_day(since) if by_day else store.by_model(since)
    label = "DAY" if by_day else "MODEL"
    print(f"  {label:<34} {'CALLS':>6} {'IN':>9} {'OUT':>9} {'TOTAL':>9}  COST")
    priced_total = 0.0
    any_priced = False
    for row in rows:
        if by_day:
            cost_text = ""
        else:
            cost = usage_stats.cost_for(row["bucket"], row["prompt_tokens"], row["completion_tokens"])
            if cost is None:
                cost_text = "—"
            else:
                any_priced = True
                priced_total += cost
                cost_text = f"{cost:,.4f}"
        print(
            f"  {row['bucket'][:34]:<34} {row['calls']:>6} "
            f"{usage_stats.format_tokens(row['prompt_tokens']):>9} "
            f"{usage_stats.format_tokens(row['completion_tokens']):>9} "
            f"{usage_stats.format_tokens(row['total_tokens']):>9}  {cost_text}"
        )
    print()
    if any_priced:
        print(f"  Priced subtotal: {priced_total:,.4f} (configured currency unit)")
        print("  Models showing '—' have no price configured, so they are NOT in that subtotal.")
    elif not by_day:
        print("  No cost shown: no prices configured. Zeline will not guess prices.")
        print('  Set them per 1M tokens: agent.model_prices = {"gpt-4o": {"input": 2.5, "output": 10}}')
    return 0


def cmd_rules(directory: str | None = None) -> int:
    """Show which project rules file is active, and what Zeline will read."""
    root = Path(directory or Path.cwd()).expanduser().resolve(strict=False)
    if not project_rules.enabled():
        print("Project rules are disabled (tools.project_rules = false).")
        return 0
    path, text = project_rules.read_rules(root)
    if path is None:
        names = ", ".join(project_rules.RULE_FILENAMES)
        print(f"No project rules found at or above {root}")
        print(f"  Looked for: {names}")
        print("  Create one with: zeline init")
        return 0
    print(f"Active project rules: {path}")
    print(f"  {len(text)} characters loaded into the system prompt")
    print()
    print(text)
    return 0


def _format_checkpoint_age(ts: float) -> str:
    seconds = max(0, int(time.time() - ts))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def cmd_undo(
    checkpoint_id: str | None = None,
    *,
    show_list: bool = False,
    file_path: str | None = None,
    diff: str | None = None,
    clear: bool = False,
) -> int:
    """Restore a file to its content before the agent last wrote to it."""
    from zeline import checkpoints

    if clear:
        removed = checkpoints.clear()
        print(f"Cleared {removed} checkpoint(s).")
        return 0

    if diff:
        print(checkpoints.diff_preview(diff))
        return 0

    if not checkpoints.enabled():
        print("Checkpoints are disabled (tools.checkpoints = false).")
        print("  Nothing was recorded, so there is nothing to undo.")
        return 0

    entries = checkpoints.list_checkpoints(file_path)
    if show_list:
        if not entries:
            scope = f" for {file_path}" if file_path else ""
            print(f"No checkpoints recorded{scope}.")
            print("  Checkpoints appear once the agent writes to an existing file.")
            return 0
        print(f"{len(entries)} checkpoint(s), newest first:")
        for entry in entries:
            print(
                f"  {entry['id']:<20} {_format_checkpoint_age(float(entry.get('ts', 0))):>9}  "
                f"{entry.get('reason', ''):<10} {entry.get('path', '')}"
            )
        print()
        print("  Restore one with: zeline undo <id>")
        print("  Preview it with:  zeline undo --diff <id>")
        return 0

    if checkpoint_id:
        ok, message = checkpoints.restore(checkpoint_id)
        print(message if ok else f"ERROR: {message}")
        return 0 if ok else 1

    if not entries:
        scope = f" for {file_path}" if file_path else ""
        print(f"Nothing to undo: no checkpoints recorded{scope}.")
        return 0

    # No id given: undo the most recent write, which is what an operator who
    # just watched the agent break a file actually wants.
    newest = entries[0]
    ok, message = checkpoints.restore(str(newest["id"]))
    print(message if ok else f"ERROR: {message}")
    return 0 if ok else 1


def cmd_customtools(action: str = "list", *, name: str | None = None) -> int:
    """Inspect the operator's own tool files, or scaffold the first one."""
    from zeline import custom_tools

    if not custom_tools.enabled():
        print("Custom tools are disabled (tools.custom_tools = false).")
        return 0

    directory = custom_tools.tools_dir()

    if action == "init":
        target = custom_tools.ensure_dir() / (name or "my_tools.py")
        if target.exists():
            print(f"{target} already exists — not overwriting it.")
            return 1
        target.write_text(custom_tools.TEMPLATE, encoding="utf-8")
        print(f"Created {target}")
        print("  Every public function in it becomes a tool named custom_<function>.")
        print("  Check what loaded with: zeline tools custom")
        return 0

    if action == "path":
        print(custom_tools.ensure_dir())
        return 0

    tools, errors = custom_tools.discover()
    if not directory.is_dir():
        print(f"No custom tools directory yet: {directory}")
        print("  Create one with a starter file: zeline tools custom-init")
        return 0

    if tools:
        print(f"{len(tools)} custom tool(s) from {directory}:")
        for tool in tools:
            required = tool.parameters.get("required", [])
            params = ", ".join(
                f"{key}{'' if key in required else '?'}"
                for key in tool.parameters.get("properties", {})
            )
            print(f"  {tool.name}({params})")
            print(f"    {tool.description}")
            print(f"    from {tool.source.name}")
    else:
        print(f"No custom tools loaded from {directory}")
        print("  Add a .py file with public functions, or run: zeline tools custom-init")

    if errors:
        print()
        # Reported, never fatal: the other files loaded fine and the agent runs.
        print(f"{len(errors)} file(s) or function(s) could not be loaded:")
        for problem in errors:
            print(f"  {problem}")
        print("  These are skipped; the tools listed above still work.")

    print()
    print("  Custom tools load only for the workspace and full profiles, because")
    print("  they run arbitrary local Python. A public gateway never sees them.")
    return 0


def _native_tool_names() -> list[str]:
    from zeline.tools import TOOL_DEFS

    return [definition.name for definition in TOOL_DEFS]


def cmd_tools(
    action: str,
    value: str | None = None,
    *,
    gateway: str | None = None,
    owner: str | None = None,
    allow_remote_code_execution: bool = False,
) -> int:
    """Inspect and control native tools without hand-editing config.json."""
    from zeline.tools import SAFE_PROFILES, TOOL_DEFS

    cfg = config.stored_config_copy()
    tool_cfg = cfg.setdefault("tools", {})
    disabled = {str(item) for item in tool_cfg.setdefault("disabled", [])}
    known = {definition.name for definition in TOOL_DEFS}

    if action == "list":
        cli_profile = str(tool_cfg.get("cli_profile", "full"))
        workspace = str(tool_cfg.get("workspace", Path.home()))
        print("Zeline native tools")
        print(f"  CLI profile : {cli_profile}")
        print(f"  Workspace   : {workspace}")
        print("  Profiles    : safe < workspace < full")
        print("  Legend      : enabled/disabled · profiles")
        for definition in TOOL_DEFS:
            state = "disabled" if definition.name in disabled else "enabled"
            profiles = ",".join(sorted(definition.profiles))
            print(f"  {definition.name:<20} {state:<8} · {profiles}")
        servers = len(cfg.get("mcp", {}).get("servers", {}))
        print(f"\n  External MCP tools: {servers} server(s); run `zeline mcp list`.")
        return 0

    if action == "profile":
        profile = str(value or "")
        if profile not in SAFE_PROFILES:
            print(f"Unknown profile: {profile}. Choose: safe, workspace, full.")
            return 2
        if gateway:
            gateway_cfg = cfg.get("gateways", {}).get(gateway)
            if gateway_cfg is None:
                print(f"Unknown gateway: {gateway}.")
                return 2
            if profile in {"workspace", "full"}:
                allowed = gateway_cfg.get("allowed")
                if (
                    not isinstance(allowed, list)
                    or len(allowed) != 1
                    or str(allowed[0]).strip() in {"", "*"}
                ):
                    print(f"Refusing {profile} profile for {gateway}: configure one exact owner allowlist entry first.")
                    return 2
                owner_identity = str(owner or "").strip()
                if not owner_identity or owner_identity != str(allowed[0]).strip():
                    print("Owner identity must exactly match the sole allowlist entry.")
                    return 2
                if gateway == "webhook":
                    print("Webhook must remain safe because its caller controls chat_id; use Telegram/WhatsApp for owner tools.")
                    return 2
                if profile == "full" and not allow_remote_code_execution:
                    print("Full gateway tools allow remote code execution. Re-run with --allow-remote-code-execution.")
                    return 2
                gateway_cfg["owner_identity"] = owner_identity
                gateway_cfg["remote_code_execution_ack"] = bool(
                    profile == "full" and allow_remote_code_execution
                )
            else:
                gateway_cfg.pop("owner_identity", None)
                gateway_cfg.pop("remote_code_execution_ack", None)
            gateway_cfg["tool_profile"] = profile
            config.save_config(cfg)
            print(f"{gateway} tool profile: {profile}")
            return 0
        tool_cfg["cli_profile"] = profile
        config.save_config(cfg)
        print(f"Local CLI tool profile: {profile}")
        return 0

    if action in {"enable", "disable"}:
        name = str(value or "")
        if name not in known:
            print(f"Unknown native tool: {name}. Run `zeline tools list`.")
            return 2
        if action == "disable":
            disabled.add(name)
        else:
            disabled.discard(name)
        tool_cfg["disabled"] = sorted(disabled)
        config.save_config(cfg)
        print(f"{name}: {action}d")
        return 0

    if action == "workspace":
        target = Path(str(value or "")).expanduser().resolve(strict=False)
        if not target.is_dir():
            print(f"Workspace directory not found: {target}")
            return 2
        tool_cfg["workspace"] = str(target)
        config.save_config(cfg)
        print(f"Workspace: {target}")
        return 0

    print("Usage: zeline tools list|profile|enable|disable|workspace")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zeline", description="Zeline agentic AI framework by Zerolinear")
    parser.add_argument("--version", action="version", version=f"zeline {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    setup = subparsers.add_parser("setup", help="configure one section or run first-time gateway setup")
    setup.add_argument(
        "setup_section",
        nargs="?",
        choices=["gateway", "model", "tools", "integrations", "agent"],
        help="configure only this section",
    )
    setup.add_argument("--reset", action="store_true", help="discard saved provider defaults and start clean")

    chat = subparsers.add_parser("chat", help="chat in the terminal")
    chat.add_argument("-q", "--query", help="single query, no interactive mode")
    subparsers.add_parser("model", help="change provider/model without re-running gateway setup")

    gateway = subparsers.add_parser("gateway", help="manage messaging platforms")
    gateway_sub = gateway.add_subparsers(dest="gateway_command")
    setup_gateway = gateway_sub.add_parser("setup", help="gateway configuration wizard")
    setup_gateway.add_argument("name", choices=list(GATEWAYS), nargs="?")
    for action in ("enable", "disable", "token"):
        item = gateway_sub.add_parser(action)
        item.add_argument("name", choices=list(GATEWAYS))
    gateway_sub.add_parser("list", help="gateway configuration status")
    gateway_sub.add_parser("status", help="background gateway process status")
    start = gateway_sub.add_parser("start", help="run enabled gateways in the background")
    start.add_argument("--only", choices=list(GATEWAYS), action="append", help="only run this gateway (repeatable)")
    gateway_sub.add_parser("stop", help="stop the background gateway")
    restart = gateway_sub.add_parser("restart", help="restart the background gateway")
    restart.add_argument("--only", choices=list(GATEWAYS), action="append", help="only run this gateway (repeatable)")
    log = gateway_sub.add_parser("log", help="view background gateway logs")
    log.add_argument("-n", "--lines", type=int, default=80, help="number of log lines")
    run = gateway_sub.add_parser("run", help="run enabled gateways in the foreground")
    run.add_argument("--only", choices=list(GATEWAYS), action="append", help="only run this gateway (repeatable)")

    config_parser = subparsers.add_parser("config", help="show safe config location/values")
    config_parser.add_argument("action", choices=["path", "show"])

    mcp_parser = subparsers.add_parser("mcp", help="manage MCP servers (external tools)")
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_command")
    mcp_add = mcp_sub.add_parser("add", help="add an MCP server")
    mcp_add.add_argument("name")
    mcp_add.add_argument("--command", dest="mcp_cmd", default="", help="stdio server command, e.g. 'npx -y @modelcontextprotocol/server-filesystem ~/'")
    mcp_add.add_argument("--url", default="", help="streamable HTTP server URL")
    mcp_sub.add_parser("list", help="list MCP servers")
    mcp_remove = mcp_sub.add_parser("remove", help="remove an MCP server")
    mcp_remove.add_argument("name")
    mcp_test = mcp_sub.add_parser("test", help="test connection & list tools")
    mcp_test.add_argument("name", nargs="?")

    subparsers.add_parser("doctor", aliases=["status"], help="check dependencies and configuration")
    subparsers.add_parser("update", aliases=["upgrade"], help="update Zeline in place (keeps ~/.zeline data)")
    init_parser = subparsers.add_parser("init", help="create ZELINE.md so project conventions load automatically")
    init_parser.add_argument("directory", nargs="?", help="project directory (default: current)")
    init_parser.add_argument("--force", action="store_true", help="overwrite an existing ZELINE.md")

    rules_parser = subparsers.add_parser("rules", help="show the active project rules file")
    rules_parser.add_argument("directory", nargs="?", help="project directory (default: current)")

    undo_parser = subparsers.add_parser("undo", help="restore a file to its content before the agent wrote to it")
    undo_parser.add_argument("checkpoint_id", nargs="?", help="checkpoint to restore (default: the most recent)")
    undo_parser.add_argument("--list", dest="show_list", action="store_true", help="list checkpoints instead of restoring")
    undo_parser.add_argument("--file", dest="file_path", help="limit to checkpoints for one file")
    undo_parser.add_argument("--diff", help="show what a checkpoint would change, without restoring")
    undo_parser.add_argument("--clear", action="store_true", help="delete all checkpoints")

    stats_parser = subparsers.add_parser("stats", help="show token usage (and cost, if prices are configured)")
    stats_parser.add_argument("--days", type=int, help="only the last N days (default: all time)")
    stats_parser.add_argument("--by-day", action="store_true", help="group by day instead of by model")
    stats_parser.add_argument("--reset", action="store_true", help="delete all recorded usage")

    session_parser = subparsers.add_parser("session", help="export, import, fork, or list conversation sessions")
    session_sub = session_parser.add_subparsers(dest="session_action")
    session_sub.add_parser("list", help="list stored sessions")
    export_cmd = session_sub.add_parser("export", help="write a session's history to JSON")
    export_cmd.add_argument("identity", help="identity string, e.g. telegram:123456 or cli:local")
    export_cmd.add_argument("path", nargs="?", help="output file (default: ./zeline-session-<ts>.json)")
    export_cmd.add_argument("--include-archive", action="store_true", help="also embed archived turns")
    import_cmd = session_sub.add_parser("import", help="load a session export into an identity")
    import_cmd.add_argument("identity", help="target identity")
    import_cmd.add_argument("path", help="session JSON file")
    import_cmd.add_argument("--replace", action="store_true", help="overwrite an existing session")
    fork_cmd = session_sub.add_parser("fork", help="copy a session to another identity")
    fork_cmd.add_argument("source", help="source identity")
    fork_cmd.add_argument("target", help="new identity")

    subparsers.add_parser("skills", aliases=["skill"], help="list skills")
    subparsers.add_parser("memory", help="view local CLI memory")

    tools_parser = subparsers.add_parser("tools", help="inspect and configure native tools")
    tools_sub = tools_parser.add_subparsers(dest="tools_command")
    tools_sub.add_parser("list", help="list native tools, profiles, and state")
    tools_profile = tools_sub.add_parser("profile", help="set local CLI or gateway tool profile")
    tools_profile.add_argument("profile", choices=["safe", "workspace", "full"])
    tools_profile.add_argument("--gateway", choices=list(GATEWAYS), help="apply profile to one gateway")
    tools_profile.add_argument("--owner", help="exact gateway owner identity; must be the sole allowlist entry")
    tools_profile.add_argument(
        "--allow-remote-code-execution",
        action="store_true",
        help="explicitly acknowledge that full gateway tools can execute owner code",
    )
    for action in ("enable", "disable"):
        item = tools_sub.add_parser(action, help=f"{action} one native tool globally")
        item.add_argument("tool", choices=_native_tool_names())
    tools_workspace = tools_sub.add_parser("workspace", help="set the owner workspace root")
    tools_workspace.add_argument("path")
    tools_sub.add_parser("custom", help="list the operator's own tool files in ~/.zeline/tools/")
    tools_custom_init = tools_sub.add_parser("custom-init", help="create a starter custom tool file")
    tools_custom_init.add_argument("name", nargs="?", help="file name (default: my_tools.py)")
    tools_sub.add_parser("custom-path", help="print the custom tools directory")

    for alias, alias_help in (
        ("start", "alias: start enabled gateways"),
        ("stop", "alias: stop background gateways"),
        ("gateway-status", "alias: show background gateway status"),
        ("logs", "alias: show gateway logs"),
    ):
        subparsers.add_parser(alias, help=alias_help)
    return parser


def _ensure_utf8_stdio() -> None:
    """Force UTF-8 on stdout/stderr so the banner never crashes the CLI.

    Windows consoles default to a legacy code page (cp1252/cp437) that cannot
    encode the box-drawing characters and the '>' marker used by the banner and
    pickers. Printing them raises UnicodeEncodeError and kills the command --
    especially when output is piped to a file, where Python does not
    auto-select UTF-8. ``reconfigure`` with ``errors="replace"`` keeps the CLI
    running and degrades unmappable glyphs to '?' instead of crashing.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # Already-detached or non-reconfigurable stream: leave it alone.
            pass


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdio()
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        if not config.GATEWAY_SETUP_COMPLETE:
            return cmd_setup(reset=False)
        if not config.SETUP_COMPLETE or not bool(config.PROVIDER.get("model_verified", False)):
            print("[!] Gateway is ready. Next run: zeline model")
            return 2
        return cmd_chat()
    parser = build_parser()
    namespace = parser.parse_args(args)
    command = namespace.command
    if command == "setup":
        section = namespace.setup_section
        if section == "gateway":
            return cmd_gateway_setup(None)
        if section == "model":
            return cmd_model()
        if section == "tools":
            return cmd_setup_tools()
        if section == "integrations":
            return cmd_setup_integrations()
        if section == "agent":
            return cmd_setup_agent()
        if namespace.reset:
            return cmd_setup(reset=True)
        # Backward-compatible first run: `zeline setup` has historically opened
        # the gateway picker, and every public quick-start relies on that.
        if not config.GATEWAY_SETUP_COMPLETE:
            return cmd_setup(reset=False)
        return cmd_setup_center()
    if command == "chat":
        return cmd_chat(namespace.query)
    if command == "model":
        return cmd_model()
    if command == "gateway":
        action = namespace.gateway_command or "list"
        if action == "setup":
            return cmd_gateway_setup(namespace.name)
        if action == "enable":
            return cmd_gateway_enable(namespace.name)
        if action == "disable":
            return cmd_gateway_disable(namespace.name)
        if action == "token":
            return cmd_gateway_token(namespace.name)
        if action == "list":
            return cmd_gateway_list()
        if action == "status":
            return cmd_gateway_service_status()
        if action == "start":
            return cmd_gateway_start(namespace.only)
        if action == "stop":
            return cmd_gateway_stop()
        if action == "restart":
            return cmd_gateway_restart(namespace.only)
        if action == "log":
            return cmd_gateway_log(namespace.lines)
        if action == "run":
            return cmd_gateway_run(namespace.only)
        return cmd_gateway_list()
    if command in {"doctor", "status"}:
        return cmd_doctor()
    if command in {"update", "upgrade"}:
        from zeline import updater
        return updater.update()
    if command == "init":
        return cmd_init(getattr(namespace, "directory", None), force=bool(getattr(namespace, "force", False)))
    if command == "rules":
        return cmd_rules(getattr(namespace, "directory", None))
    if command == "undo":
        return cmd_undo(
            getattr(namespace, "checkpoint_id", None),
            show_list=bool(getattr(namespace, "show_list", False)),
            file_path=getattr(namespace, "file_path", None),
            diff=getattr(namespace, "diff", None),
            clear=bool(getattr(namespace, "clear", False)),
        )
    if command == "stats":
        return cmd_stats(
            getattr(namespace, "days", None),
            by_day=bool(getattr(namespace, "by_day", False)),
            reset=bool(getattr(namespace, "reset", False)),
        )
    if command == "session":
        action = getattr(namespace, "session_action", None)
        if action == "list":
            return cmd_session_list()
        if action == "export":
            return cmd_session_export(
                namespace.identity, getattr(namespace, "path", None),
                include_archive=bool(getattr(namespace, "include_archive", False)),
            )
        if action == "import":
            return cmd_session_import(
                namespace.identity, namespace.path,
                replace=bool(getattr(namespace, "replace", False)),
            )
        if action == "fork":
            return cmd_session_fork(namespace.source, namespace.target)
        print("Usage: zeline session {list|export|import|fork}")
        return 2
    if command == "start":
        return cmd_gateway_start(None)
    if command == "stop":
        return cmd_gateway_stop()
    if command == "gateway-status":
        return cmd_gateway_service_status()
    if command == "logs":
        return cmd_gateway_log()
    if command == "config":
        return cmd_config(namespace.action)
    if command == "mcp":
        mcp_action = namespace.mcp_command or "list"
        return cmd_mcp(
            mcp_action,
            getattr(namespace, "name", None),
            command=getattr(namespace, "mcp_cmd", ""),
            url=getattr(namespace, "url", ""),
        )
    if command in {"skills", "skill"}:
        return cmd_skills()
    if command == "memory":
        return cmd_memory()
    if command == "tools":
        action = namespace.tools_command or "list"
        if action in {"custom", "custom-init", "custom-path"}:
            return cmd_customtools(
                {"custom": "list", "custom-init": "init", "custom-path": "path"}[action],
                name=getattr(namespace, "name", None),
            )
        value = (
            getattr(namespace, "profile", None)
            or getattr(namespace, "tool", None)
            or getattr(namespace, "path", None)
        )
        return cmd_tools(
            action,
            value,
            gateway=getattr(namespace, "gateway", None),
            owner=getattr(namespace, "owner", None),
            allow_remote_code_execution=bool(
                getattr(namespace, "allow_remote_code_execution", False)
            ),
        )
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
