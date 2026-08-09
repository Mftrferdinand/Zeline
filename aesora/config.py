"""Konfigurasi Zeline.

Setiap instalasi baru menyimpan data di ``~/.zeline`` (atau ``$ZELINE_HOME``).
Variabel ``AESORA_*`` tetap diterima sementara sebagai compatibility fallback.

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
import shutil
from pathlib import Path
from typing import Any

# Default generik untuk OpenAI-compatible provider fresh install.
# Pengguna tetap memilih model sendiri lewat `zeline setup`.
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_MAX_TOOL_ROUNDS = 12
DEFAULT_MAX_SESSIONS = 100

# ZELINE_HOME membuat test, container, dan beberapa instance terisolasi mudah.
_EXPLICIT_HOME = os.environ.get("ZELINE_HOME") or os.environ.get("AESORA_HOME")
DATA_DIR = Path(_EXPLICIT_HOME or str(Path.home() / ".zeline")).expanduser()
CONFIG_FILE = DATA_DIR / "config.json"
ENV_FILE = DATA_DIR / ".env"
LOG_DIR = DATA_DIR / "logs"
STATE_DIR = DATA_DIR / "state"
PID_FILE = DATA_DIR / "gateway.pid"

SYSTEM_PROMPT_TEMPLATE = """Kamu adalah {name}, agent yang dibangun dengan Zeline — open-source agentic AI framework by Zerolinear.
Kamu cerdas, tegas, langsung ke solusi, dan berbahasa Indonesia (auto-detect
bahasa lawan bicara, mirror gaya operator). Prinsipmu: eksekusi dulu, teori
belakangan hanya bila perlu. Lead dengan hasil, bukan basa-basi.

Cara kerja:
- Deteksi intent → kalau cocok dengan skill yang tersedia, panggil load_skill
  dulu sebelum eksekusi. Jangan preload semua skill (boros token).
- Gunakan tools hanya saat memang dibutuhkan untuk kemajuan nyata.
- Jangan pernah mengklaim sebuah aksi/eksekusi selesai sebelum hasil tool
  mengonfirmasinya. Dilarang mengarang output, tx hash, atau hasil palsu —
  kalau gagal, laporkan blocker apa adanya lalu tawarkan jalur alternatif.

Batas aman (engineering defaults, bukan sensor):
- Hanya kelola aset/akun milik operator sendiri. Tolak kredensial pihak ketiga
  atau target yang bukan milik operator.
- Konfirmasi operator sebelum aksi yang memindahkan dana atau tak-bisa-dibalik.
- Jangan pernah log, print mentah, atau kirim rahasia (private key, seed, API
  key) ke pihak luar."""

_CONFIG: dict[str, Any] | None = None


def _migrate_legacy_data_dir(legacy: Path, target: Path) -> bool:
    """Copy a legacy install once; never delete or overwrite user data."""
    if target.exists() or not legacy.is_dir():
        return False
    try:
        shutil.copytree(legacy, target)
        migrated_config = target / "config.json"
        if migrated_config.exists():
            saved = json.loads(migrated_config.read_text(encoding="utf-8"))
            if isinstance(saved, dict) and str(saved.get("name", "")).strip().lower() == "aesora":
                saved["name"] = "Zeline"
                migrated_config.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                try:
                    migrated_config.chmod(0o600)
                except OSError:
                    pass
        return True
    except (OSError, json.JSONDecodeError):
        return False


if not _EXPLICIT_HOME:
    _migrate_legacy_data_dir(Path.home() / ".aesora", DATA_DIR)


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
        "setup_complete": False,
        "name": "Zeline",
        "provider": {
            "base_url": "https://api.openai.com/v1",
            "api_key": "",
            "model": DEFAULT_MODEL,
        },
        "agent": {
            "max_tool_rounds": DEFAULT_MAX_TOOL_ROUNDS,
            "max_sessions": DEFAULT_MAX_SESSIONS,
        },
        "tools": {
            # CLI dimiliki operator lokal; gateway publik harus safe secara default.
            "cli_profile": "full",
            "workspace": str(Path.home()),
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
    """ZELINE_* overrides take priority; AESORA_* remain legacy fallbacks."""
    mapping = {
        "base_url": ("ZELINE_BASE_URL", "AESORA_BASE_URL"),
        "api_key": ("ZELINE_API_KEY", "AESORA_API_KEY"),
        "model": ("ZELINE_MODEL", "AESORA_MODEL"),
    }
    for field, env_names in mapping.items():
        value = next((os.environ.get(name) for name in env_names if os.environ.get(name)), None)
        if value:
            cfg["provider"][field] = value
    name = os.environ.get("ZELINE_NAME") or os.environ.get("AESORA_NAME")
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
    if str(stored.get("name", "")).strip().lower() == "aesora":
        stored["name"] = "Zeline"
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
        return "(kosong)"
    if len(value) <= visible * 2:
        return "•" * len(value)
    return f"{value[:visible]}…{value[-visible:]}"


def new_webhook_token() -> str:
    return secrets.token_urlsafe(24)


def _set_runtime_values(cfg: dict[str, Any]) -> None:
    """Jaga API lama modul internal: config.BASE_URL, config.GATEWAYS, dsb."""
    global PROVIDER, BASE_URL, API_KEY, MODEL, GATEWAYS, NAME
    global MAX_TOOL_ROUNDS, MAX_SESSIONS, WORKSPACE, CLI_TOOL_PROFILE, SYSTEM_PROMPT, SETUP_COMPLETE
    PROVIDER = cfg["provider"]
    BASE_URL = str(PROVIDER.get("base_url", "")).rstrip("/")
    API_KEY = str(PROVIDER.get("api_key", ""))
    MODEL = str(PROVIDER.get("model", DEFAULT_MODEL))
    GATEWAYS = cfg["gateways"]
    NAME = str(cfg.get("name", "Zeline"))
    SETUP_COMPLETE = bool(cfg.get("setup_complete", False))
    MAX_TOOL_ROUNDS = int(cfg.get("agent", {}).get("max_tool_rounds", DEFAULT_MAX_TOOL_ROUNDS))
    MAX_SESSIONS = int(cfg.get("agent", {}).get("max_sessions", DEFAULT_MAX_SESSIONS))
    WORKSPACE = str(cfg.get("tools", {}).get("workspace", str(Path.home())))
    CLI_TOOL_PROFILE = str(cfg.get("tools", {}).get("cli_profile", "full"))
    SYSTEM_PROMPT = SYSTEM_PROMPT_TEMPLATE.format(name=NAME)


config = load_config()
_set_runtime_values(config)
