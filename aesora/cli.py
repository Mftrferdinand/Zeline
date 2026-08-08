"""Command line interface Aesora.

Aesora command-line interface:

    aesora setup                 # wizard provider + platform
    aesora                       # chat lokal
    aesora chat -q "halo"         # satu query
    aesora gateway setup         # wizard gateway saja
    aesora gateway enable telegram
    aesora gateway start         # jalankan semua gateway aktif di background
    aesora gateway stop|status|log
    aesora gateway run           # jalankan foreground (systemd/tmux)
    aesora doctor                # cek instalasi/config
    aesora config path|show
    aesora skills list
    aesora memory list

Aesora adalah framework yang pengguna konfigurasi dengan bot/account mereka
sendiri. Tidak ada token Telegram/WhatsApp bersama dalam paket ini.
"""
from __future__ import annotations

import argparse
import copy
import getpass
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from aesora import __version__, config, skills
from aesora.agent import Aesora, AesoraError
from aesora.gateways import GATEWAYS, gateway_status, run_all
from aesora import gateway_service
from aesora.sessions import SessionStore


def _terminal_color_enabled() -> bool:
    """Use ANSI color only when the terminal explicitly supports it."""
    if os.environ.get("NO_COLOR") is not None or os.environ.get("TERM") == "dumb":
        return False
    return bool(os.environ.get("FORCE_COLOR")) or sys.stdout.isatty()


def _print_banner() -> None:
    """Render the Aesora terminal identity without breaking plain terminals."""
    title = "AESORA-AGENT"
    subtitle = f"SELF-HOSTED AI AGENT FRAMEWORK  ·  v{__version__}"
    width = 68
    if _terminal_color_enabled():
        cyan = "\033[38;5;51m"
        blue = "\033[38;5;33m"
        deep_blue = "\033[38;5;27m"
        dim = "\033[38;5;75m"
        reset = "\033[0m"
        print(
            f"\n{deep_blue}╔{'═' * width}╗{reset}\n"
            f"{blue}║{reset}  {cyan}A E S O R A{reset}{blue} ━ {deep_blue}A G E N T{reset}"
            f"{' ' * (width - 29)}{blue}║{reset}\n"
            f"{blue}║{reset}  {dim}{subtitle}{reset}"
            f"{' ' * (width - len(subtitle) - 2)}{blue}║{reset}\n"
            f"{deep_blue}╚{'═' * width}╝{reset}\n"
        )
    else:
        print(
            f"\n+{'-' * width}+\n"
            f"|  {title}{' ' * (width - len(title) - 2)}|\n"
            f"|  {subtitle}{' ' * (width - len(subtitle) - 2)}|\n"
            f"+{'-' * width}+\n"
        )


def _ask(prompt: str, default: str = "", *, secret: bool = False) -> str:
    """Prompt kecil. Secret memakai getpass agar tidak tampil di terminal."""
    if secret:
        suffix = " [tersimpan — Enter untuk pertahankan]" if default else ""
        answer = getpass.getpass(f"{prompt}{suffix}: ").strip()
    else:
        suffix = f" [{default}]" if default else ""
        answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def _yes_no(prompt: str, default: bool = False) -> bool:
    marker = "Y/n" if default else "y/N"
    answer = input(f"{prompt} [{marker}]: ").strip().lower()
    return default if not answer else answer in {"y", "yes", "ya"}


def _gateway_cfg(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    gateways = cfg.setdefault("gateways", {})
    defaults = config._defaults()["gateways"]
    if name not in defaults:
        raise ValueError(f"Gateway tidak dikenal: {name}")
    current = gateways.setdefault(name, copy.deepcopy(defaults[name]))
    return current


def _setup_telegram(cfg: dict[str, Any]) -> bool:
    gateway = _gateway_cfg(cfg, "telegram")
    if not _yes_no("Aktifkan Telegram?", bool(gateway.get("enabled"))):
        gateway["enabled"] = False
        return False
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
    if not _yes_no("Aktifkan WhatsApp (Baileys / QR pairing)?", bool(gateway.get("enabled"))):
        gateway["enabled"] = False
        return False
    allowed_raw = _ask("  Allowlist nomor/JID (koma, kosong = publik)", ",".join(map(str, gateway.get("allowed", []))))
    allowed = [entry.strip() for entry in allowed_raw.split(",") if entry.strip()]
    gateway.update({"enabled": True, "allowed": allowed, "tool_profile": "safe"})
    return True


def _setup_webhook(cfg: dict[str, Any], *, reveal_token: bool = True) -> bool:
    gateway = _gateway_cfg(cfg, "webhook")
    if not _yes_no("Aktifkan webhook HTTP lokal?", bool(gateway.get("enabled"))):
        gateway["enabled"] = False
        return False
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


def cmd_setup() -> int:
    _print_banner()
    print(f"==> Setup Aesora · config: {config.CONFIG_FILE}")
    cfg = config.stored_config_copy()


    cfg["name"] = _ask("Nama agent", str(cfg.get("name", "Aesora")))
    provider = cfg["provider"]
    print("\n-- Provider LLM (OpenAI-compatible) --")
    provider["base_url"] = _ask("Base URL", str(provider.get("base_url", "https://api.openai.com/v1"))).rstrip("/")
    provider["api_key"] = _ask("API key", str(provider.get("api_key", "")), secret=True)
    provider["model"] = _ask("Model", str(provider.get("model", config.DEFAULT_MODEL)))

    print("\n-- Platform --")
    _setup_telegram(cfg)
    _setup_whatsapp(cfg)
    _setup_webhook(cfg)

    config.save_config(cfg)
    copied = skills.seed_skills()
    print(f"\nSelesai. {copied} skill bawaan ditambahkan.")
    print("Lanjutkan dengan: aesora doctor  →  aesora gateway run")
    return 0


def cmd_chat(query: str | None = None) -> int:
    if not config.API_KEY:
        print("[!] API key kosong. Jalankan: aesora setup")
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
        except AesoraError as exc:
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
        except AesoraError as exc:
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
            print("Telegram butuh token. Gunakan: aesora gateway setup telegram")
            return 2
        gateway["enabled"] = True
    else:
        gateway["enabled"] = True
    config.save_config(cfg)
    print(f"Gateway {name} diaktifkan. Jalankan `aesora gateway run`.")
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
    """Tampilkan secret hanya saat owner memintanya secara eksplisit."""
    if name not in GATEWAYS:
        print(f"Gateway tidak dikenal: {name}. Pilihan: {', '.join(GATEWAYS)}")
        return 2
    token = str(config.config_copy().get("gateways", {}).get(name, {}).get("token", ""))
    if not token:
        print(f"Gateway {name} belum memiliki token rahasia.")
        return 2
    print("⚠ Ini rahasia. Jangan kirim ke chat publik, screenshot, atau commit Git.")
    print(token)
    return 0


def cmd_gateway_start(only: list[str] | None = None) -> int:
    if not config.API_KEY:
        print("API key kosong. Jalankan `aesora setup` sebelum gateway start.")
        return 2
    enabled = [
        name for name, gateway_cfg in config.GATEWAYS.items()
        if gateway_cfg.get("enabled", False) and (not only or name in only)
    ]
    if not enabled:
        print("Tidak ada gateway aktif. Jalankan `aesora gateway setup`.")
        return 2
    started, message = gateway_service.start(only=only)
    print(message)
    return 0 if started else 1


def cmd_gateway_stop() -> int:
    stopped, message = gateway_service.stop()
    print(message)
    return 0 if stopped else 1


def cmd_gateway_service_status() -> int:
    active, message, _state = gateway_service.status()
    print(message)
    return 0 if active else 1


def cmd_gateway_log(lines: int = 80) -> int:
    print(gateway_service.tail_log(lines=lines))
    return 0


def cmd_gateway_run(only: list[str] | None = None) -> int:
    enabled = [(name, data) for name, data in config.GATEWAYS.items() if data.get("enabled") and (not only or name in only)]
    if not enabled:
        print("Tidak ada gateway aktif. Jalankan `aesora gateway setup`.")
        return 2
    if not config.API_KEY:
        print("API key kosong. Jalankan `aesora setup` sebelum gateway run.")
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
    print("Aesora doctor")
    print(f"  home      : {config.DATA_DIR}")
    print(f"  config    : {config.CONFIG_FILE} {'ada' if config.CONFIG_FILE.exists() else 'belum dibuat'}")
    print(f"  python    : {sys.version.split()[0]}")
    print(f"  provider  : {config.BASE_URL or '(kosong)'}")
    print(f"  model     : {config.MODEL or '(kosong)'}")
    print(f"  api key   : {config.mask_secret(config.API_KEY)}")
    if not config.API_KEY:
        problems.append("API key kosong — jalankan `aesora setup`.")
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
    print("Usage: aesora config path|show")
    return 2


def cmd_skills() -> int:
    available = skills.list_skill_entries(include_private=True)
    if not available:
        print("Belum ada skill. Jalankan `aesora setup` untuk menyalin skill bawaan.")
        return 0
    for scope, name, _title, description in available:
        print(f"  - {name} [{scope}]: {description}")
    return 0


def cmd_memory() -> int:
    from aesora.memory import list_memory

    print(list_memory("cli:local"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aesora", description="Aesora AI Agent multi-platform")
    parser.add_argument("--version", action="version", version=f"aesora {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    setup = subparsers.add_parser("setup", help="wizard provider dan gateway")

    chat = subparsers.add_parser("chat", help="chat di terminal")
    chat.add_argument("-q", "--query", help="satu query tanpa mode interaktif")

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
    log = gateway_sub.add_parser("log", help="lihat log gateway background")
    log.add_argument("-n", "--lines", type=int, default=80, help="jumlah baris log")
    run = gateway_sub.add_parser("run", help="jalankan gateway aktif foreground")
    run.add_argument("--only", choices=list(GATEWAYS), action="append", help="hanya jalankan gateway ini (bisa diulang)")

    config_parser = subparsers.add_parser("config", help="lihat lokasi/konfigurasi aman")
    config_parser.add_argument("action", choices=["path", "show"])
    subparsers.add_parser("doctor", aliases=["status"], help="cek dependency dan konfigurasi")
    subparsers.add_parser("skills", aliases=["skill"], help="list skill")
    subparsers.add_parser("memory", help="lihat memory CLI lokal")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    # No arguments start the interactive local chat.
    if not args:
        return cmd_chat()
    parser = build_parser()
    namespace = parser.parse_args(args)
    command = namespace.command
    if command == "setup":
        return cmd_setup()
    if command == "chat":
        return cmd_chat(namespace.query)
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
        if action == "log":
            return cmd_gateway_log(namespace.lines)
        if action == "run":
            return cmd_gateway_run(namespace.only)
        return cmd_gateway_list()
    if command in {"doctor", "status"}:
        return cmd_doctor()
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
