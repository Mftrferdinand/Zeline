"""Command line interface Zeline.

Zeline command-line interface:

    zeline setup                 # wizard provider + platform
    zeline                       # chat lokal
    zeline chat -q "halo"         # satu query
    zeline gateway setup         # wizard gateway saja
    zeline gateway enable telegram
    zeline gateway start         # jalankan semua gateway aktif di background
    zeline gateway stop|status|log
    zeline gateway run           # jalankan foreground (systemd/tmux)
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
import getpass
import json
import os
import re
import signal
import sys
import termios
import time
import tty
from pathlib import Path
from typing import Any

import requests

from zeline import __version__, config, skills
from zeline.agent import ZelineError
from zeline.gateways import GATEWAYS, gateway_status, run_all
from zeline import gateway_service
from zeline.sessions import SessionStore


def _terminal_color_enabled() -> bool:
    """Use ANSI color only when the terminal explicitly supports it."""
    if os.environ.get("NO_COLOR") is not None or os.environ.get("TERM") == "dumb":
        return False
    return bool(os.environ.get("FORCE_COLOR")) or sys.stdout.isatty()


BANNER_ART = (
    " _______ ___  ___  _    ___ _  _ ___   _   ___ ",
    "|_  / __| _ \\/ _ \\| |  |_ _| \\| | __| /_\\ | _ \\",
    " / /| _||   / (_) | |__ | || .` | _| / _ \\|   /",
    "/___|___|_|_\\\\___/|____|___|_|\\_|___/_/ \\_\\_|_\\",
)
BANNER_SUBTITLE = f"ZELINE AGENTIC AI · v{__version__} · BY MFTRFERDINAND"
BANNER_WIDTH = max(len(line) for line in BANNER_ART)


def _centered_banner_line(text: str) -> str:
    return text.center(BANNER_WIDTH)


def _print_banner() -> None:
    """Render the portable ZEROLINEAR / Zeline terminal identity."""
    lines = BANNER_ART
    subtitle = BANNER_SUBTITLE
    if _terminal_color_enabled():
        colors = ("\033[38;5;51m", "\033[38;5;45m", "\033[38;5;39m", "\033[38;5;27m")
        reset = "\033[0m"
        wordmark = "\n".join(f"{color}{line}{reset}" for color, line in zip(colors, lines))
        print(f"\n{wordmark}\n\033[38;5;75m{subtitle}{reset}\n")
    else:
        wordmark = "\n".join(lines)
        print(f"\n{wordmark}\n{subtitle}\n")


def _read_secret_key() -> str:
    return os.read(sys.stdin.fileno(), 1).decode("utf-8", errors="ignore")


def _masked_secret_input(prompt: str) -> str:
    """Read a secret while rendering one star per character."""
    if not sys.stdin.isatty():
        return getpass.getpass(prompt)
    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    chars: list[str] = []
    print(prompt, end="", flush=True)
    try:
        tty.setraw(fd)
        while True:
            char = _read_secret_key()
            if char in {"\r", "\n"}:
                print()
                break
            if char == "\x03":
                raise KeyboardInterrupt
            if char in {"\x7f", "\b"}:
                if chars:
                    chars.pop()
                    print("\b \b", end="", flush=True)
                continue
            if char and char.isprintable():
                chars.append(char)
                print("*", end="", flush=True)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)
    return "".join(chars)


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
            print("  Model ID wajib diisi karena provider tidak menyediakan daftar model.")
    print("  Model tersedia:")
    for index, model in enumerate(models, 1):
        marker = " (aktif)" if model == default else ""
        print(f"    {index:>2}. {model}{marker}")
    while True:
        choice = input(f"Pilih model [1-{len(models)}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(models):
            return models[int(choice) - 1]
        print("  Pilihan tidak valid.")


def _configure_provider(provider: dict[str, Any]) -> None:
    base_url = _ask("Base URL", str(provider.get("base_url", "https://api.openai.com/v1"))).rstrip("/")
    api_key = _ask("API key", str(provider.get("api_key", "")), secret=True)
    default_name = str(provider.get("name", "")).strip() or base_url.split("://", 1)[-1].split("/", 1)[0]
    provider_name = _ask("Nama provider di picker Telegram", default_name).strip()[:48]
    print("  Mendeteksi protokol dan model provider…")
    protocol, models = _discover_provider_models(base_url, api_key)
    label = "Anthropic" if protocol == "anthropic" else "OpenAI-compatible"
    print(f"  Provider terdeteksi: {label}")
    if not models:
        print("  Daftar model tidak tersedia; masukkan model ID manual.")
    provider.update({
        "base_url": base_url,
        "api_key": api_key,
        "model": _choose_model(models, str(provider.get("model", ""))),
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
    char = _read_secret_key()
    if char in {"\r", "\n"}:
        return "enter"
    if char == "\x03":
        raise KeyboardInterrupt
    if char == "\x1b":
        second = _read_secret_key()
        third = _read_secret_key()
        if second == "[" and third == "A":
            return "up"
        if second == "[" and third == "B":
            return "down"
    return ""


def _select_gateway() -> str:
    """Arrow-key gateway picker with numeric fallback for redirected stdin."""
    if not sys.stdin.isatty():
        print("Pilih gateway:")
        for index, (_value, label) in enumerate(GATEWAY_OPTIONS, 1):
            print(f"  {index}. {label}")
        while True:
            answer = input(f"Pilihan [1-{len(GATEWAY_OPTIONS)}]: ").strip()
            if answer.isdigit() and 1 <= int(answer) <= len(GATEWAY_OPTIONS):
                return GATEWAY_OPTIONS[int(answer) - 1][0]
            print("  Pilihan tidak valid.")

    selected = 0
    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    print("Pilih gateway (↑/↓ lalu Enter):")
    try:
        tty.setraw(fd)
        while True:
            for index, (_value, label) in enumerate(GATEWAY_OPTIONS):
                marker = "❯" if index == selected else " "
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
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)
        print()


def _gateway_cfg(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    gateways = cfg.setdefault("gateways", {})
    defaults = config._defaults()["gateways"]
    if name not in defaults:
        raise ValueError(f"Gateway tidak dikenal: {name}")
    current = gateways.setdefault(name, copy.deepcopy(defaults[name]))
    return current


def _setup_telegram(cfg: dict[str, Any]) -> bool:
    gateway = _gateway_cfg(cfg, "telegram")
    print("  Buat bot di @BotFather → /newbot → tempel token Bot API.")
    token = _ask("  Telegram bot token", str(gateway.get("token", "")), secret=True)
    if not token:
        print("  Telegram dilewati: token kosong.")
        gateway["enabled"] = False
        return False
    allowed_raw = _ask("  Allowlist chat ID (koma, kosong = publik)", ",".join(map(str, gateway.get("allowed", []))))
    allowed = [entry.strip() for entry in allowed_raw.split(",") if entry.strip()]
    gateway.update({"enabled": True, "token": token, "allowed": allowed, "tool_profile": "safe"})
    return True


def _setup_whatsapp(cfg: dict[str, Any]) -> bool:
    gateway = _gateway_cfg(cfg, "whatsapp")
    allowed_raw = _ask("  Allowlist nomor/JID (koma, kosong = publik)", ",".join(map(str, gateway.get("allowed", []))))
    allowed = [entry.strip() for entry in allowed_raw.split(",") if entry.strip()]
    gateway.update({"enabled": True, "allowed": allowed, "tool_profile": "safe"})
    return True


def _setup_webhook(cfg: dict[str, Any], *, reveal_token: bool = True) -> bool:
    gateway = _gateway_cfg(cfg, "webhook")
    host = _ask("  Host bind (aman: 127.0.0.1)", str(gateway.get("host", "127.0.0.1")))
    port = _ask("  Port", str(gateway.get("port", 8765)))
    token = str(gateway.get("token", "")) or config.new_webhook_token()
    try:
        port_number = int(port)
        if not 1 <= port_number <= 65535:
            raise ValueError
    except ValueError:
        print("  Port invalid; webhook tidak diaktifkan.")
        gateway["enabled"] = False
        return False
    gateway.update({"enabled": True, "host": host, "port": port_number, "token": token, "tool_profile": "safe"})
    if reveal_token:
        print("  Simpan token ini sekarang (hanya ditampilkan saat setup):")
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
        print("Setup dibatalkan. Jalankan `zeline` lagi untuk memilih gateway.")
        return 0
    configured = {
        "telegram": _setup_telegram,
        "whatsapp": _setup_whatsapp,
        "webhook": _setup_webhook,
    }[selected](cfg)
    if not configured:
        print("Gateway belum selesai dikonfigurasi.")
        return 2
    cfg["gateway_setup_complete"] = True
    cfg["setup_complete"] = False
    config.save_config(cfg)
    copied = skills.seed_skills()
    print(f"\n[ GATEWAY READY ]  {selected} disimpan. {copied} built-in skills added.")
    print("  Berikutnya: jalankan `zeline model` untuk memilih provider dan model.")
    return 0


def cmd_model() -> int:
    """Update provider/model only; preserve every gateway setting."""
    if not config.GATEWAY_SETUP_COMPLETE:
        print("[!] Pilih dan siapkan gateway dulu. Jalankan: zeline")
        return 2
    cfg = config.stored_config_copy()
    provider = cfg["provider"]
    _configure_provider(provider)
    slug = re.sub(r"[^a-z0-9]+", "-", str(provider.get("name", "provider")).lower()).strip("-") or "provider"
    cfg.setdefault("providers", {})[slug] = copy.deepcopy(provider)
    cfg["setup_complete"] = True
    config.save_config(cfg)
    print(f"Model disimpan: {provider['model']}")
    return 0


def cmd_chat(query: str | None = None) -> int:
    if not config.GATEWAY_SETUP_COMPLETE:
        print("[!] Gateway belum disiapkan. Jalankan: zeline")
        return 2
    if not config.SETUP_COMPLETE:
        print("[!] Gateway sudah siap. Berikutnya jalankan: zeline model")
        return 2
    if not bool(config.PROVIDER.get("model_verified", False)):
        print("[!] Model belum diverifikasi dari provider. Jalankan: zeline model")
        return 2
    if not config.API_KEY:
        print("[!] API key kosong. Jalankan: zeline setup")
        return 2
    _print_banner()
    print(f"  Agent : {config.NAME}")
    print(f"  Model : {config.MODEL}")
    print("  Tool profile: full (operator lokal)\n")
    sessions = SessionStore(max_sessions=1)

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

    print("Ketik 'keluar' untuk berhenti.\n")
    while True:
        try:
            text = input("\033[36mkamu ›\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSampai jumpa!")
            return 0
        if not text:
            continue
        if text.lower() in {"keluar", "exit", "quit", "q"}:
            print("Sampai jumpa!")
            return 0
        try:
            answer = ask(text)
            print(f"\033[35m{config.NAME} ›\033[0m {answer}\n")
        except ZelineError as exc:
            print(f"\033[31m[error] {exc}\033[0m\n")


def cmd_gateway_setup(name: str | None = None) -> int:
    cfg = config.stored_config_copy()
    if name and name not in GATEWAYS:
        print(f"Gateway tidak dikenal: {name}. Pilihan: {', '.join(GATEWAYS)}")
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
    print("Konfigurasi gateway disimpan.")
    return 0


def cmd_gateway_enable(name: str) -> int:
    if name not in GATEWAYS:
        print(f"Gateway tidak dikenal: {name}. Pilihan: {', '.join(GATEWAYS)}")
        return 2
    cfg = config.stored_config_copy()
    gateway = _gateway_cfg(cfg, name)
    if name == "webhook":
        # Non-interaktif: secret dibuat tapi tidak dicetak (aman untuk automation).
        gateway.update({"enabled": True, "host": "127.0.0.1", "port": int(gateway.get("port", 8765)), "token": gateway.get("token") or config.new_webhook_token(), "tool_profile": "safe"})
    elif name == "telegram":
        if not gateway.get("token"):
            print("Telegram butuh token. Gunakan: zeline gateway setup telegram")
            return 2
        gateway["enabled"] = True
    else:
        gateway["enabled"] = True
    config.save_config(cfg)
    print(f"Gateway {name} diaktifkan. Jalankan `zeline gateway run`.")
    return 0


def cmd_gateway_disable(name: str) -> int:
    if name not in GATEWAYS:
        print(f"Gateway tidak dikenal: {name}. Pilihan: {', '.join(GATEWAYS)}")
        return 2
    cfg = config.stored_config_copy()
    _gateway_cfg(cfg, name)["enabled"] = False
    config.save_config(cfg)
    print(f"Gateway {name} dimatikan di config.")
    return 0


def cmd_gateway_list() -> int:
    print("Gateway:")
    for name, enabled, errors in gateway_status(config.GATEWAYS):
        state = "AKTIF" if enabled else "mati"
        suffix = f" · masalah: {'; '.join(errors)}" if errors else ""
        print(f"  - {name:<10} {state}{suffix}")
    return 0


def cmd_gateway_token(name: str) -> int:
    """Tampilkan status secret tanpa pernah membocorkan nilainya."""
    if name not in GATEWAYS:
        print(f"Gateway tidak dikenal: {name}. Pilihan: {', '.join(GATEWAYS)}")
        return 2
    token = str(config.config_copy().get("gateways", {}).get(name, {}).get("token", ""))
    if not token:
        print(f"Gateway {name} belum memiliki token rahasia.")
        return 2
    print(f"Token {name}: ** (tersimpan)")
    return 0


def cmd_gateway_start(only: list[str] | None = None) -> int:
    if not config.API_KEY:
        print("API key kosong. Jalankan `zeline setup` sebelum gateway start.")
        return 2
    enabled = [
        name for name, gateway_cfg in config.GATEWAYS.items()
        if gateway_cfg.get("enabled", False) and (not only or name in only)
    ]
    if not enabled:
        print("Tidak ada gateway aktif. Jalankan `zeline gateway setup`.")
        return 2
    started, message = gateway_service.start(only=only)
    print(message)
    return 0 if started else 1


def cmd_gateway_stop() -> int:
    stopped, message = gateway_service.stop()
    print(message)
    return 0 if stopped else 1


def cmd_gateway_restart(only: list[str] | None = None) -> int:
    _stopped, stop_message = gateway_service.stop()
    print(stop_message)
    started, start_message = gateway_service.start(only=only)
    print(start_message)
    return 0 if started else 1


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
            f"Gateway sudah berjalan (PID {managed_pid}). "
            "Hentikan dulu dengan `zeline gateway stop`; proses duplikat ditolak."
        )
        return 1
    enabled = [(name, data) for name, data in config.GATEWAYS.items() if data.get("enabled") and (not only or name in only)]
    if not enabled:
        print("Tidak ada gateway aktif. Jalankan `zeline gateway setup`.")
        return 2
    if not config.API_KEY:
        print("API key kosong. Jalankan `zeline setup` sebelum gateway run.")
        return 2
    _print_banner()
    print("==> Menjalankan gateway…")
    sessions = SessionStore()
    runtime = run_all(sessions, config.GATEWAYS, names=only)
    if not runtime.threads:
        print("Tidak ada gateway yang lolos validasi.")
        return 2

    def shutdown(_signal=None, _frame=None):
        print("\n==> Menghentikan gateway…", flush=True)
        runtime.stop()

    previous_int = signal.signal(signal.SIGINT, shutdown)
    previous_term = signal.signal(signal.SIGTERM, shutdown)
    try:
        while runtime.alive:
            time.sleep(0.5)
    except KeyboardInterrupt:
        shutdown()
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
        runtime.stop(timeout=1)
    return 0


def cmd_doctor() -> int:
    _print_banner()
    problems: list[str] = []
    warnings: list[str] = []
    print("Zeline doctor")
    print(f"  home      : {config.DATA_DIR}")
    print(f"  config    : {config.CONFIG_FILE} {'ada' if config.CONFIG_FILE.exists() else 'belum dibuat'}")
    print(f"  python    : {sys.version.split()[0]}")
    print(f"  provider  : {config.BASE_URL or '(kosong)'}")
    print(f"  model     : {config.MODEL or '(kosong)'}")
    print(f"  api key   : {config.mask_secret(config.API_KEY)}")
    if not config.API_KEY:
        problems.append("API key kosong — jalankan `zeline setup`.")
    if not config.BASE_URL:
        problems.append("Base URL provider kosong.")
    if not config.MODEL:
        problems.append("Model provider kosong.")
    if not Path(config.WORKSPACE).expanduser().exists():
        warnings.append(f"Workspace tidak ditemukan: {config.WORKSPACE}")
    for name, enabled, errors in gateway_status(config.GATEWAYS):
        if enabled and errors:
            problems.append(f"Gateway {name}: {'; '.join(errors)}")
        print(f"  gateway {name:<9}: {'aktif' if enabled else 'mati'}")
    print(f"  skills    : {len(skills.list_skills())}")
    if warnings:
        print("\nPeringatan:")
        for item in warnings:
            print(f"  - {item}")
    if problems:
        print("\nPerlu diperbaiki:")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("\nOK — konfigurasi dasar sehat.")
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
        print("Belum ada skill. Jalankan `zeline setup` untuk menyalin skill bawaan.")
        return 0
    for scope, name, _title, description in available:
        print(f"  - {name} [{scope}]: {description}")
    return 0


def cmd_memory() -> int:
    from zeline.memory import list_memory

    print(list_memory("cli:local"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zeline", description="Zeline agentic AI framework by Zerolinear")
    parser.add_argument("--version", action="version", version=f"zeline {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    setup = subparsers.add_parser("setup", help="configure agent, provider, and gateways")
    setup.add_argument("--reset", action="store_true", help="discard saved provider defaults and start clean")

    chat = subparsers.add_parser("chat", help="chat di terminal")
    chat.add_argument("-q", "--query", help="satu query tanpa mode interaktif")
    subparsers.add_parser("model", help="ubah provider/model tanpa setup ulang gateway")

    gateway = subparsers.add_parser("gateway", help="kelola platform messaging")
    gateway_sub = gateway.add_subparsers(dest="gateway_command")
    setup_gateway = gateway_sub.add_parser("setup", help="wizard konfigurasi gateway")
    setup_gateway.add_argument("name", choices=list(GATEWAYS), nargs="?")
    for action in ("enable", "disable", "token"):
        item = gateway_sub.add_parser(action)
        item.add_argument("name", choices=list(GATEWAYS))
    gateway_sub.add_parser("list", help="status konfigurasi gateway")
    gateway_sub.add_parser("status", help="status proses gateway background")
    start = gateway_sub.add_parser("start", help="jalankan gateway aktif di background")
    start.add_argument("--only", choices=list(GATEWAYS), action="append", help="hanya jalankan gateway ini (bisa diulang)")
    gateway_sub.add_parser("stop", help="hentikan gateway background")
    restart = gateway_sub.add_parser("restart", help="restart gateway background")
    restart.add_argument("--only", choices=list(GATEWAYS), action="append", help="hanya jalankan gateway ini (bisa diulang)")
    log = gateway_sub.add_parser("log", help="lihat log gateway background")
    log.add_argument("-n", "--lines", type=int, default=80, help="jumlah baris log")
    run = gateway_sub.add_parser("run", help="jalankan gateway aktif foreground")
    run.add_argument("--only", choices=list(GATEWAYS), action="append", help="hanya jalankan gateway ini (bisa diulang)")

    config_parser = subparsers.add_parser("config", help="lihat lokasi/konfigurasi aman")
    config_parser.add_argument("action", choices=["path", "show"])
    subparsers.add_parser("doctor", aliases=["status"], help="cek dependency dan konfigurasi")
    subparsers.add_parser("skills", aliases=["skill"], help="list skill")
    subparsers.add_parser("memory", help="lihat memory CLI lokal")
    for alias, alias_help in (
        ("start", "alias: start enabled gateways"),
        ("stop", "alias: stop background gateways"),
        ("gateway-status", "alias: show background gateway status"),
        ("logs", "alias: show gateway logs"),
    ):
        subparsers.add_parser(alias, help=alias_help)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        if not config.GATEWAY_SETUP_COMPLETE:
            return cmd_setup(reset=False)
        if not config.SETUP_COMPLETE or not bool(config.PROVIDER.get("model_verified", False)):
            print("[!] Gateway sudah siap. Berikutnya jalankan: zeline model")
            return 2
        return cmd_chat()
    parser = build_parser()
    namespace = parser.parse_args(args)
    command = namespace.command
    if command == "setup":
        return cmd_setup(reset=namespace.reset)
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
    if command in {"skills", "skill"}:
        return cmd_skills()
    if command == "memory":
        return cmd_memory()
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
