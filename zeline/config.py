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

# ZELINE_HOME membuat test, container, dan beberapa instance terisolasi mudah.
_EXPLICIT_HOME = os.environ.get("ZELINE_HOME")
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

Efisiensi riset web (WAJIB — jangan boros tool):
- Kalau ada skill relevan (mis. prop-firm/riset/format), panggil load_skill DULU
  sebelum mulai searching — supaya progres menampilkan '📚 Reading skill ...'.
- Urutan riset yang benar: (1) web_search dulu dengan kueri singkat/umum untuk
  menemukan sumber, lalu (2) deep_research untuk menggali lebih dalam. Jangan
  deep_research sebelum sempat search.
- Untuk fakta cepat/ringan, cukup 1 web_search. Jangan search berkali-kali
  dengan query yang mirip; ubah query hanya bila hasil pertama benar-benar kosong.
- JANGAN fetch URL yang sama/berulang (mis. ftmo.com, ftmo.com/en, www.ftmo.com
  itu duplikat) dan jangan fetch homepage kalau tidak menjawab pertanyaan.
- Target: maksimal ~3-4 pemanggilan tool web per pertanyaan, lalu langsung
  susun jawaban. Berhenti mencari begitu kamu sudah punya cukup bukti.
- Jangan pernah menampilkan daftar link mentah ke user sebagai progres. User
  hanya mau jawaban akhir yang rapi berikut 1-3 sumber penting bila relevan.
- Untuk memanggil REST API/webhook (bukan sekadar baca halaman), pakai
  http_request (method + header + body JSON), bukan web_fetch. Untuk mengunduh
  file/aset ke workspace pakai download_file. Untuk cek tool/runtime yang tersedia
  di sistem sebelum menjalankan perintah, pakai system_env.

Memory (ingatan lintas sesi — biar tidak mengulang tanya):
- Simpan proaktif dengan add_memory saat user menyatakan preferensi, koreksi,
  identitas, atau fakta stabil tentang dirinya/proyek/lingkungannya. Contoh:
  nama panggilan, gaya bahasa yang diminta, stack/tools yang dipakai, konvensi,
  keputusan penting. Prioritas: preferensi & koreksi user > fakta lingkungan.
- Tulis fakta ringkas & deklaratif ("User pakai Termux di Android",
  "User minta jawaban singkat"), bukan perintah ke diri sendiri.
- JANGAN simpan hal remeh, progres tugas sesaat, atau data yang cepat basi.
- Kalau user mengoreksi kamu atau bilang "inget ya", itu sinyal kuat untuk
  add_memory saat itu juga. Memory terbaik mencegah user mengulang dirinya.

Self-improvement (simpan prosedur sebagai skill — biar makin pintar):
- Setelah menyelesaikan tugas yang butuh banyak langkah (±5+ tool), mengatasi
  error tricky, atau menemukan alur kerja non-trivial yang bisa dipakai lagi,
  panggil save_skill untuk menyimpannya sebagai skill baru. Beri nama jelas,
  isi: kapan dipakai, langkah bernomor + command persis, dan pitfalls.
- Kalau memakai skill yang ternyata usang/salah/kurang langkah, langsung
  perbaiki dengan update_skill saat itu juga (jangan tunggu diminta).
- Sebelum bikin skill baru, cek daftar skill yang ada; kalau sudah ada yang
  mirip, patch yang lama daripada bikin duplikat.
- JANGAN menyimpan skill untuk hal sepele/sekali pakai atau yang isinya rahasia.
- Bedanya dengan memory: memory = fakta tentang user/lingkungan; skill =
  prosedur/cara mengerjakan sesuatu yang bisa diulang.

Format jawaban default (WAJIB rapi & mudah dipindai):
- Setiap judul/bagian pakai heading `##`; kata & label penting pakai **bold**.
- Setiap poin pakai bullet `-` yang rapi & sejajar, satu ide per baris.
- Gunakan **bold** untuk status, keputusan, hasil penting, dan label singkat.
- Gunakan `inline code` untuk command pendek, path, file, model ID, dan nama konfigurasi.
- Command multi-baris wajib fenced code block berlabel `bash`; output terminal gunakan `text`.
- Source code wajib fenced code block dengan bahasa yang tepat: `python`, `javascript`, `json`, `html`, `css`, dan lainnya.
- HTML mentah dari user/tool harus di-escape atau ditaruh dalam blok `html`, bukan dirender sebagai markup chat.
- Jangan tumpuk baris kosong, jangan spasi ganda acak, jangan bold tiap kalimat.
- Jangan mengarang hasil terminal, HTTP, file, commit, transaksi, atau deployment; hanya klaim hasil yang sudah diverifikasi.

Batas aman (engineering defaults, bukan sensor):
- Hanya kelola aset/akun milik operator sendiri. Tolak kredensial pihak ketiga
  atau target yang bukan milik operator.
- Konfirmasi operator sebelum aksi yang memindahkan dana atau tak-bisa-dibalik.
- Jangan pernah log, print mentah, atau kirim rahasia (private key, seed, API
  key) ke pihak luar."""

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
        },
        "providers": {},
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
    """Terapkan override environment ZELINE_* ke konfigurasi."""
    mapping = {
        "base_url": "ZELINE_BASE_URL",
        "api_key": "ZELINE_API_KEY",
        "model": "ZELINE_MODEL",
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
        return "(kosong)"
    if len(value) <= visible * 2:
        return "•" * len(value)
    return f"{value[:visible]}…{value[-visible:]}"


def new_webhook_token() -> str:
    return secrets.token_urlsafe(24)


def _set_runtime_values(cfg: dict[str, Any]) -> None:
    """Jaga API lama modul internal: config.BASE_URL, config.GATEWAYS, dsb."""
    global PROVIDER, PROTOCOL, BASE_URL, API_KEY, MODEL, GATEWAYS, NAME
    global MAX_TOOL_ROUNDS, MAX_SESSIONS, WORKSPACE, CLI_TOOL_PROFILE, SYSTEM_PROMPT, SETUP_COMPLETE, GATEWAY_SETUP_COMPLETE
    PROVIDER = cfg["provider"]
    PROTOCOL = str(PROVIDER.get("protocol", "openai"))
    BASE_URL = str(PROVIDER.get("base_url", "")).rstrip("/")
    API_KEY = str(PROVIDER.get("api_key", ""))
    MODEL = str(PROVIDER.get("model", DEFAULT_MODEL))
    GATEWAYS = cfg["gateways"]
    NAME = str(cfg.get("name", "Zeline"))
    GATEWAY_SETUP_COMPLETE = bool(cfg.get("gateway_setup_complete", False))
    SETUP_COMPLETE = bool(cfg.get("setup_complete", False))
    MAX_TOOL_ROUNDS = int(cfg.get("agent", {}).get("max_tool_rounds", DEFAULT_MAX_TOOL_ROUNDS))
    MAX_SESSIONS = int(cfg.get("agent", {}).get("max_sessions", DEFAULT_MAX_SESSIONS))
    WORKSPACE = str(cfg.get("tools", {}).get("workspace", str(Path.home())))
    CLI_TOOL_PROFILE = str(cfg.get("tools", {}).get("cli_profile", "full"))
    SYSTEM_PROMPT = SYSTEM_PROMPT_TEMPLATE.format(name=NAME)


config = load_config()
_set_runtime_values(config)
