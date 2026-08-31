"""Zeline App Gateway — first-party mobile chat surface.

Transport: HTTP (REST + SSE streaming). Client Android/iOS terhubung via
endpoint REST dan menerima stream SSE.

Gateway ini adapter transport, BUKAN agent kedua: setiap pesan dieksekusi oleh
``zeline.agent.Zeline`` yang sama dengan CLI/Telegram (lihat
``zeline_app_runtime.run_generation``). Tidak ada jawaban terprogram.

Endpoint (semua di bawah ``/api/v1``, ``/health`` juga tersedia telanjang):
  GET  /health
  GET  /status
  GET  /system                     → OS/arch/python/host/version + uptime (no IP)
  POST /auth/login                 → JWT (butuh gateway_token yang benar)
  GET  /agents                     → list agent profiles
  POST /agents                     → create agent
  GET  /agents/{id}                → agent detail
  PUT  /agents/{id}                → update agent
  DEL  /agents/{id}                → delete agent
  GET  /providers                  → provider terkonfigurasi (key ter-mask)
  POST /providers                  → tambah/simpan provider (model auto-detect)
  POST /providers/detect           → deteksi model dari base_url+key (tanpa simpan)
  DEL  /providers/{id}             → hapus provider
  GET  /providers/{id}/models      → model dari provider (live)
  POST /providers/{id}/test        → uji koneksi tanpa membocorkan key
  GET  /sessions                   → sessions (filter ?agent_id=)
  POST /sessions                   → create session
  DEL  /sessions/{sid}             → delete session + history
  GET  /sessions/{sid}/messages    → history pesan
  POST /sessions/{sid}/messages    → kirim (SSE bila ?stream=true)
  POST /sessions/{sid}/cancel      → batalkan generation (idempoten)
  POST /attachments                → metadata attachment

Autentikasi: Bearer JWT hasil ``/auth/login``. Provider API key tidak pernah
muncul di respons, history, atau log.
"""
from __future__ import annotations

import hmac
import json
import re
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse, parse_qs

# Reuse agent + config, TIDAK duplikat runtime
from zeline.agent import Zeline, ZelineError
from zeline import config
from zeline import app_auth, app_data
from zeline.gateways import zeline_app_runtime as runtime

#: Jenis attachment yang dikenali client. Metadata saja — byte-nya diurus
#: storage layer, gateway tidak pernah menyimpan file.
SUPPORTED_ATTACHMENT_TYPES = {"image", "text_file", "document"}


def _attachment_metadata(name: str, size_bytes: int, mime_hint: str) -> dict[str, Any]:
    return {
        "filename": name,
        "size_bytes": size_bytes,
        "mime_type": mime_hint,
        "url_preview": None,  # diisi oleh client/storage layer
    }

API_PREFIX = "/api/v1"
API_VERSION = 1

# Batas produk: satu akun Zeline = satu agent. Bukan roster multi-agent —
# agent tunggal ini punya banyak SESSION (room chat terpisah, history sendiri).
MAX_AGENTS = 1

# Secret penandatangan JWT untuk proses ini. Token lama otomatis invalid setelah
# restart; client menangani 401 dengan login ulang.
_APP_SECRET = secrets.token_bytes(32)

ERRORS: dict[str, tuple[int, str]] = {
    "UNAUTHORIZED": (401, "Missing or invalid Authorization header"),
    "INVALID_TOKEN": (401, "Token invalid or expired"),
    "AGENT_NOT_FOUND": (404, "Agent not found"),
    "SESSION_NOT_FOUND": (404, "Session not found"),
    "PROVIDER_UNAVAILABLE": (502, "Provider unavailable"),
    "MODEL_UNAVAILABLE": (502, "Model unavailable"),
    "VALIDATION_ERROR": (400, "Invalid request payload"),
    "TOOL_FAILURE": (500, "Tool execution failed"),
    "GENERATION_CANCELLED": (409, "Generation cancelled"),
    "LIMIT_REACHED": (409, "Resource limit reached"),
    "NOT_FOUND": (404, "Resource not found"),
    "INTERNAL_ERROR": (500, "Internal error"),
}

AGENT_FIELDS = ("name", "avatar", "icon", "accent", "description", "provider_id", "model",
                "system_instructions", "enabled_tools", "enabled_skills",
                "memory_enabled")


def info() -> dict[str, str]:
    return {
        "label": "Zeline App",
        "hint": "Native mobile chat gateway for Zeline framework (REST + SSE).",
    }


def validate_config(cfg: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    token = str(cfg.get("token", ""))
    if len(token) < 16:
        errors.append("app token empty/too short (min 16 chars)")
    try:
        port = int(cfg.get("port", 8082))
        if not 1 <= port <= 65535:
            errors.append("app port must be 1-65535")
    except (TypeError, ValueError):
        errors.append("invalid app port")
    return errors


def _check_auth(headers: Any) -> bool:
    """Kompatibilitas lama: verifikasi bentuk header Bearer."""
    auth = headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return app_auth.verify_token(auth[7:].strip(), secret=_APP_SECRET) is not None
    return False


def _now() -> str:
    return runtime.now_iso()


_SYSTEM_CACHE: dict[str, Any] = {}


def _system_info() -> dict[str, Any]:
    """Describe the machine/runtime this gateway is running on — the answer to
    'where is Zeline running?'. Static bits are cached; uptime is recomputed.

    Deliberately excludes IP addresses (public or private) — too sensitive for a
    UI card. Only OS/arch/python/host/version + uptime + online state.
    """
    import platform
    import sys as _sys

    if not _SYSTEM_CACHE:
        try:
            from zeline import __version__ as _zver
        except Exception:  # noqa: BLE001
            _zver = "?"

        machine = (platform.machine() or "").lower()
        arch = {
            "aarch64": "ARM64", "arm64": "ARM64",
            "x86_64": "x86_64", "amd64": "x86_64",
            "armv7l": "ARM32", "armv8l": "ARM64",
        }.get(machine, platform.machine() or "unknown")

        # Detect the host kind: Termux/Android vs a normal Linux/mac/Windows box.
        import os as _os
        is_termux = bool(_os.environ.get("PREFIX", "").startswith("/data/data/com.termux")) \
            or _os.path.isdir("/data/data/com.termux/files/usr")
        system = platform.system() or "Unknown"
        if is_termux:
            kind = "Termux"
            os_name = "Android"
            os_detail = ""
            # Android release via getprop if available
            try:
                import subprocess
                rel = subprocess.run(["getprop", "ro.build.version.release"],
                                     capture_output=True, text=True, timeout=3).stdout.strip()
                if rel:
                    os_detail = f"Android {rel}"
            except Exception:  # noqa: BLE001
                os_detail = "Android"
        elif system == "Linux":
            kind = "Linux"
            os_name = "Linux"
            distro = ""
            try:
                info: dict[str, str] = {}
                with open("/etc/os-release", "r", encoding="utf-8") as fh:
                    for line in fh:
                        if "=" in line:
                            k, _, v = line.partition("=")
                            info[k.strip()] = v.strip().strip('"')
                distro = info.get("PRETTY_NAME") or (
                    (info.get("NAME", "") + " " + info.get("VERSION_ID", "")).strip())
            except Exception:  # noqa: BLE001
                distro = ""
            os_detail = distro or f"Linux {platform.release()}"
        elif system == "Darwin":
            kind = "macOS"
            os_name = "macOS"
            os_detail = f"macOS {platform.mac_ver()[0]}".strip()
        elif system == "Windows":
            kind = "Windows"
            os_name = "Windows"
            os_detail = f"Windows {platform.release()}"
        else:
            kind = system
            os_name = system
            os_detail = platform.release()

        try:
            host = platform.node() or ""
        except Exception:  # noqa: BLE001
            host = ""

        # Second-line environment label: "<OS> · <context>". Auto-detected only,
        # never hardcoded per-install. No IP / user / home / secrets.
        def _linux_context() -> str:
            # VPS/server vs Desktop. Prefer virtualization detection; fall back to
            # "is there a graphical session?" (desktop) else server/VPS.
            try:
                import subprocess
                virt = subprocess.run(["systemd-detect-virt"],
                                      capture_output=True, text=True, timeout=3).stdout.strip()
                if virt and virt not in ("none",):
                    return "VPS"          # kvm/qemu/vmware/xen/lxc/docker/... → hosted
            except Exception:  # noqa: BLE001
                pass
            # Container markers → treat as server/VPS.
            if _os.path.exists("/.dockerenv") or _os.environ.get("container"):
                return "VPS"
            # Graphical session present → a real desktop.
            if _os.environ.get("DISPLAY") or _os.environ.get("WAYLAND_DISPLAY") \
                    or _os.environ.get("XDG_CURRENT_DESKTOP"):
                return "Desktop"
            return "VPS"                  # headless Linux → assume server/VPS

        if kind == "Termux":
            env_label = "Termux · Android"
        elif system == "Linux":
            env_label = f"Linux · {_linux_context()}"
        elif system == "Darwin":
            env_label = "macOS · Apple Silicon" if arch == "ARM64" else "macOS · Intel"
        elif system == "Windows":
            env_label = f"Windows · {arch}"
        else:
            env_label = ""               # unknown platform → client shows "Not detected"

        _SYSTEM_CACHE.update({
            "kind": kind,                       # Termux | Linux | macOS | Windows
            "os": os_name,                      # Android | Linux | macOS | Windows
            "os_detail": os_detail,             # "Android 14" | "Ubuntu 24.04" | ...
            "arch": arch,                       # ARM64 | x86_64 | ...
            "env_label": env_label,             # "Termux · Android" | "Linux · VPS" | ...
            "python": platform.python_version(),
            "zeline_version": _zver,
            "hostname": host,
            "runtime": "zeline.agent.Zeline",
        })

    data = dict(_SYSTEM_CACHE)
    # Uptime relative to gateway start.
    try:
        started = time.mktime(time.strptime(STARTED, "%Y-%m-%dT%H:%M:%S"))
        data["uptime_s"] = max(0, int(time.time() - started))
    except Exception:  # noqa: BLE001
        data["uptime_s"] = None
    data["online"] = True
    data["time"] = _now()
    return data


def _default_agent() -> dict[str, Any]:
    provider_refs = runtime.provider_refs()
    active = next((p for p in provider_refs if p.get("is_active")), None) or (
        provider_refs[0] if provider_refs else {})
    return {
        "id": "agent_general",
        "name": "General",
        "icon": "◆",
        "accent": "#6EA8FF",
        "description": "Asisten harian serbaguna",
        "provider_id": str(active.get("id", "")),
        "model": str(config.MODEL),
        "system_instructions": "",
        "enabled_tools": [],
        "enabled_skills": [],
        "memory_enabled": True,
        "agent_api_token": app_auth.generate_agent_api_token(),
        "created_at": _now(),
        "updated_at": _now(),
    }


def _agents() -> list[dict[str, Any]]:
    agents = app_data.load_agents()
    if not agents:
        agents = [app_data.add_agent(_default_agent())]
    return [app_auth.sanitize_for_client(a) for a in agents]


def _agent_by_id(agent_id: str) -> dict[str, Any] | None:
    return next((a for a in app_data.load_agents() if a.get("id") == agent_id), None)


def _sessions() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for session in app_data.load_sessions():
        item = dict(session)
        item["id"] = item.get("id") or item.get("session_id")
        item.setdefault("message_count", len(runtime.load_messages(str(item["id"]))))
        item.setdefault("last_preview", "")
        item.setdefault("created_at", _now())
        item.setdefault("updated_at", item["created_at"])
        out.append(item)
    return sorted(out, key=lambda s: str(s.get("updated_at", "")), reverse=True)


def _session_by_id(session_id: str) -> dict[str, Any] | None:
    return next((s for s in _sessions() if s.get("id") == session_id), None)


def _connected_agent_ids(scoped_agent_id: str | None,
                         configured_tokens: list[str] | tuple[str, ...]) -> set[str]:
    """Profiles explicitly attached to this gateway using Agent Tokens."""
    if scoped_agent_id:
        return {scoped_agent_id}
    token_set = {str(token) for token in configured_tokens if token}
    return {str(agent.get("id")) for agent in app_data.load_agents()
            if agent.get("agent_api_token") in token_set}


def _save_session(session: dict[str, Any]) -> dict[str, Any]:
    session["session_id"] = session.get("session_id") or session.get("id")
    session["id"] = session["session_id"]
    app_data.add_session(session)
    return session


def _touch_session(session_id: str, preview: str) -> dict[str, Any] | None:
    session = _session_by_id(session_id)
    if not session:
        return None
    session["updated_at"] = _now()
    session["message_count"] = len(runtime.load_messages(session_id))
    session["last_preview"] = (preview or "")[:80]
    return _save_session(session)


def start(sessions, cfg: dict[str, Any], stop_event, ready=None) -> None:
    port = int(cfg.get("port", 8082))
    host = str(cfg.get("host", "127.0.0.1"))
    token = str(cfg.get("token", secrets.token_hex(16)))
    tool_profile = str(cfg.get("tool_profile", "safe"))

    def configured_agent_tokens() -> list[str]:
        app_cfg = config.stored_config_copy().get("zeline_app", {})
        if "linked_agents" in app_cfg:
            rows = app_cfg.get("linked_agents", [])
            return [str(row.get("token", "")) for row in rows
                    if isinstance(row, dict) and row.get("token")] if isinstance(rows, list) else []
        legacy = app_cfg.get("agent_tokens", [])
        source = legacy if isinstance(legacy, list) else cfg.get("agent_tokens", [])
        return [str(value) for value in source if value]

    def save_agent_tokens(tokens: list[str]) -> None:
        stored = config.stored_config_copy()
        app_cfg = stored.setdefault("zeline_app", {})
        existing = app_cfg.get("linked_agents", [])
        names = {str(row.get("token", "")): str(row.get("name", ""))
                 for row in existing if isinstance(row, dict)} if isinstance(existing, list) else {}
        unique = list(dict.fromkeys(tokens))
        app_cfg["linked_agents"] = [
            {"name": names.get(token) or f"Agent {index}", "token": token}
            for index, token in enumerate(unique, 1)
        ]
        app_cfg.pop("agent_tokens", None)
        config.save_config(stored)

    def connection_rows() -> list[dict[str, Any]]:
        connected = _connected_agent_ids(None, configured_agent_tokens())
        return [{"agent_id": str(agent.get("id")), "name": str(agent.get("name", "Agent")),
                 "connected": True} for agent in app_data.load_agents()
                if str(agent.get("id")) in connected]

    class Handler(BaseHTTPRequestHandler):
        server_version = "ZelineApp/1.0"
        protocol_version = "HTTP/1.1"

        # ------------------------------------------------------------ output
        def log_message(self, format, *args):  # noqa: A002,D401 - jangan log Authorization
            return

        def _raw(self, status: int, body: bytes, ctype: str,
                 extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            # CORS: the Zeline App web client runs on a different origin (dev server
            # :5173, or a static host) and talks straight to this gateway. Allow it.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:  # noqa: N802 - CORS preflight
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
            self.send_header("Access-Control-Max-Age", "86400")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._raw(status, body, "application/json; charset=utf-8")

        def ok(self, data: Any, status: int = 200) -> None:
            self._json(status, {
                "status": "ok",
                "data": app_auth.sanitize_for_client(data) if isinstance(data, dict) else data,
                "request_id": "req_" + secrets.token_hex(8),
            })

        def fail(self, code: str, detail: str | None = None) -> None:
            status, message = ERRORS.get(code, ERRORS["INTERNAL_ERROR"])
            self._json(status, {"error": {
                "code": code,
                "message": detail or message,
                "request_id": "req_" + secrets.token_hex(8),
            }})

        # ------------------------------------------------------------- input
        def body_json(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    return {}
                raw = self.rfile.read(length)
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {}
            except (ValueError, json.JSONDecodeError):
                return {}

        def authed(self) -> bool:
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return False
            supplied = auth[7:].strip()
            if app_auth.verify_token(supplied, secret=_APP_SECRET) is not None:
                return True
            return any(a.get("agent_api_token") == supplied for a in app_data.load_agents())

        def auth_agent_id(self) -> str | None:
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return None
            supplied = auth[7:].strip()
            return next((str(a.get("id")) for a in app_data.load_agents()
                         if a.get("agent_api_token") == supplied), None)

        def master_authed(self) -> bool:
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return False
            return app_auth.verify_token(auth[7:].strip(), secret=_APP_SECRET) is not None

        def agent_allowed(self, agent_id: str) -> bool:
            scoped = self.auth_agent_id()
            return scoped is None or scoped == agent_id

        def _route(self) -> tuple[str, dict[str, list[str]]]:
            parsed = urlparse(self.path)
            path = parsed.path
            if path.startswith(API_PREFIX):
                path = path[len(API_PREFIX):] or "/"
            return path.rstrip("/") or "/", parse_qs(parsed.query)

        # ------------------------------------------------------------ GET
        def do_GET(self) -> None:
            path, query = self._route()

            if path in ("/health", "/"):
                return self.ok({
                    "ok": True,
                    "service": "zeline-app",
                    "gateway": "zeline_app",
                    "api_version": API_VERSION,
                    "sse": True,
                    "provider_agnostic": True,
                    "time": _now(),
                })
            if path == "/status":
                return self.ok({
                    "gateway": "zeline_app",
                    "api_version": API_VERSION,
                    "runtime": "zeline.agent.Zeline",
                    "agents": len(app_data.load_agents()),
                    "sessions": len(app_data.load_sessions()),
                    "providers": len(runtime.provider_refs()),
                    "active_streams": runtime.active_streams(),
                    "tool_profile": tool_profile,
                    "model_default": config.MODEL,
                    "started_at": STARTED,
                })
            if path == "/system":
                # Where is Zeline running? OS/arch/python/host/version + uptime.
                # No auth required (same tier as /health, /status) and no IP data.
                return self.ok(_system_info())

            if not self.authed():
                return self.fail("UNAUTHORIZED")

            if path == "/agents":
                # List ALL agents so the app shows the full roster. Mark ONLY the
                # agent this session's token is bound to as connected; when authed
                # by the master gateway JWT (no agent scope) none is flagged and the
                # client falls back to the active agent. Mutations stay scoped via
                # agent_allowed().
                scoped = self.auth_agent_id()
                connected_ids = _connected_agent_ids(scoped, configured_agent_tokens())
                agents = _agents()
                for a in agents:
                    if isinstance(a, dict):
                        a["is_connected_agent"] = str(a.get("id")) in connected_ids
                return self.ok({"agents": agents})
            if path == "/agent-connections":
                if not self.master_authed():
                    return self.fail("UNAUTHORIZED")
                return self.ok({"connections": connection_rows(),
                                "count": len(configured_agent_tokens())})
            match = re.fullmatch(r"/agents/([\w-]+)", path)
            if match:
                agent = _agent_by_id(match.group(1))
                if agent and not self.agent_allowed(str(agent.get("id"))):
                    return self.fail("UNAUTHORIZED")
                return self.ok(agent) if agent else self.fail("AGENT_NOT_FOUND")

            if path == "/providers":
                return self.ok({"providers": runtime.provider_refs()})
            match = re.fullmatch(r"/providers/([\w-]+)/models", path)
            if match:
                return self._provider_models(match.group(1))

            if path == "/sessions":
                agent_id = (query.get("agent_id") or [None])[0]
                items = [s for s in _sessions()
                         if (not agent_id or s.get("agent_id") == agent_id)
                         and self.agent_allowed(str(s.get("agent_id", "")))]
                return self.ok({"sessions": items})
            match = re.fullmatch(r"/sessions/([\w-]+)/messages", path)
            if match:
                session_id = match.group(1)
                if not _session_by_id(session_id):
                    return self.fail("SESSION_NOT_FOUND")
                return self.ok({"session_id": session_id,
                                "messages": runtime.load_messages(session_id)})

            self.fail("NOT_FOUND")

        # ------------------------------------------------------------ POST
        def do_POST(self) -> None:
            path, query = self._route()

            if path == "/auth/login":
                body = self.body_json()
                supplied = str(body.get("gateway_token") or body.get("passcode") or "")
                if not supplied:
                    return self.fail("INVALID_TOKEN", "gateway_token wajib")
                if not hmac.compare_digest(supplied, token):
                    return self.fail("INVALID_TOKEN", "gateway_token salah")
                access = app_auth.generate_token("zeline_app", secret=_APP_SECRET)
                return self.ok({
                    "access_token": access,
                    "token_type": "Bearer",
                    "expires_in": 86400,
                    "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                time.gmtime(time.time() + 86400)),
                    "refresh_supported": False,
                    "gateway": "zeline_app",
                })

            if not self.authed():
                return self.fail("UNAUTHORIZED")

            if path == "/gateway/restart":
                # Lightweight "restart": clear the cached system snapshot and
                # re-read state from disk so the UI reflects any out-of-band edits.
                # Does NOT kill the process — it refreshes the in-memory view and
                # confirms the gateway is alive. Clients re-fetch /system + /agents.
                _SYSTEM_CACHE.clear()
                try:
                    if hasattr(runtime, "reload"):
                        runtime.reload()
                except Exception:
                    pass
                return self.ok({
                    "gateway": "zeline_app",
                    "restarted": True,
                    "api_version": API_VERSION,
                    "system": _system_info(),
                    "at": _now(),
                })

            if path == "/agents":
                body = self.body_json()
                if not str(body.get("name", "")).strip():
                    return self.fail("VALIDATION_ERROR", "name wajib")
                # Satu akun = satu agent. Tolak pembuatan agent kedua.
                if len(app_data.load_agents()) >= MAX_AGENTS:
                    return self.fail("LIMIT_REACHED",
                                     "Satu akun Zeline hanya punya satu agent. Buat session baru untuk room chat terpisah.")
                agent = _default_agent()
                agent["id"] = "agent_" + secrets.token_hex(4)
                for field in AGENT_FIELDS:
                    if field in body:
                        agent[field] = body[field]
                agent["created_at"] = agent["updated_at"] = _now()
                app_data.add_agent(agent)
                return self.ok({**agent, "agent_api_token_created": True}, 201)

            if path == "/agent-connections":
                if not self.master_authed():
                    return self.fail("UNAUTHORIZED")
                supplied = str(self.body_json().get("agent_token", "")).strip()
                agent = next((item for item in app_data.load_agents()
                              if item.get("agent_api_token") == supplied), None)
                if not agent:
                    return self.fail("INVALID_TOKEN", "Agent Token tidak ditemukan")
                tokens = configured_agent_tokens()
                if supplied not in tokens:
                    tokens.append(supplied)
                    save_agent_tokens(tokens)
                return self.ok({"agent_id": agent.get("id"), "name": agent.get("name"),
                                "connected": True}, 201)

            match = re.fullmatch(r"/providers/([\w-]+)/test", path)
            if match:
                return self._provider_check(match.group(1))

            if path == "/providers":
                # Tambah/simpan provider. User cukup kirim name + base_url + api_key;
                # daftar model dideteksi otomatis dari endpoint /models provider.
                body = self.body_json()
                name = str(body.get("name", "")).strip()
                base_url = str(body.get("base_url", "")).strip()
                api_key = str(body.get("api_key", "")).strip()
                if not name:
                    return self.fail("VALIDATION_ERROR", "name wajib")
                if not base_url:
                    return self.fail("VALIDATION_ERROR", "base_url wajib")
                # Model manual (opsional) diterima, tapi default-nya auto-detect.
                manual = body.get("models")
                manual = [str(m).strip() for m in manual if str(m).strip()] \
                    if isinstance(manual, list) else None
                ref = runtime.save_provider(
                    name, base_url, api_key,
                    protocol=str(body.get("protocol", "openai") or "openai"),
                    provider_id=str(body.get("id", "")).strip(),
                    models=manual,
                )
                return self.ok({**ref, "detected_models": ref.get("models", [])}, 201)

            if path == "/providers/detect":
                # Deteksi model TANPA menyimpan — untuk preview di form sebelum simpan.
                body = self.body_json()
                base_url = str(body.get("base_url", "")).strip()
                api_key = str(body.get("api_key", "")).strip()
                if not base_url:
                    return self.fail("VALIDATION_ERROR", "base_url wajib")
                models = runtime.detect_models(base_url, api_key)
                if not models:
                    return self.fail("PROVIDER_UNAVAILABLE",
                                     "Tidak ada model terdeteksi — cek base_url/API key.")
                return self.ok({"models": models, "count": len(models)})

            if path == "/sessions":
                body = self.body_json()
                agent_id = str(body.get("agent_id", ""))
                if not _agent_by_id(agent_id):
                    return self.fail("AGENT_NOT_FOUND")
                session = {
                    "id": "sess_" + secrets.token_hex(4),
                    "agent_id": agent_id,
                    "title": str(body.get("title") or "Sesi baru"),
                    "created_at": _now(),
                    "updated_at": _now(),
                    "message_count": 0,
                    "last_preview": "",
                }
                return self.ok(_save_session(session), 201)

            match = re.fullmatch(r"/sessions/([\w-]+)/cancel", path)
            if match:
                session_id = match.group(1)
                if not _session_by_id(session_id):
                    return self.fail("SESSION_NOT_FOUND")
                cancelled, stream_id = runtime.stream_cancel(session_id)
                return self.ok({"session_id": session_id, "stream_id": stream_id,
                                "cancelled": cancelled})

            match = re.fullmatch(r"/sessions/([\w-]+)/steer", path)
            if match:
                session_id = match.group(1)
                if not _session_by_id(session_id):
                    return self.fail("SESSION_NOT_FOUND")
                guidance = str(self.body_json().get("content", "")).strip()
                if not guidance:
                    return self.fail("VALIDATION_ERROR", "content wajib")
                accepted = runtime.stream_steer(session_id, guidance)
                return self.ok({"session_id": session_id, "accepted": accepted})

            if path == "/attachments":
                body = self.body_json()
                mime = str(body.get("mime_type", "application/octet-stream"))
                meta = _attachment_metadata(
                    str(body.get("filename", "file.bin")),
                    int(body.get("size_bytes", 0) or 0),
                    mime,
                )
                kind = ("image" if mime.startswith("image/")
                        else "text" if mime.startswith("text/") else "document")
                return self.ok({"id": "att_" + secrets.token_hex(4), "type": kind,
                                "created_at": _now(), **meta}, 201)

            match = re.fullmatch(r"/sessions/([\w-]+)/messages", path)
            if match:
                stream = str((query.get("stream") or ["false"])[0]).lower() == "true"
                return self._send_message(match.group(1), self.body_json(), stream)

            self.fail("NOT_FOUND")

        # ------------------------------------------------------- PUT / DELETE
        def do_PUT(self) -> None:
            path, _ = self._route()
            if not self.authed():
                return self.fail("UNAUTHORIZED")
            session_match = re.fullmatch(r"/sessions/([\w-]+)", path)
            if session_match:
                session = _session_by_id(session_match.group(1))
                if not session:
                    return self.fail("SESSION_NOT_FOUND")
                body = self.body_json()
                if "saved_message_ids" in body:
                    valid = {str(message.get("id")) for message in runtime.load_messages(str(session["id"]))}
                    values = body.get("saved_message_ids") or []
                    session["saved_message_ids"] = list(dict.fromkeys(
                        str(value) for value in values if str(value) in valid
                    ))
                if "title" in body and str(body["title"]).strip():
                    session["title"] = str(body["title"]).strip()
                session["updated_at"] = _now()
                return self.ok(_save_session(session))
            match = re.fullmatch(r"/agents/([\w-]+)", path)
            if not match:
                return self.fail("NOT_FOUND")
            agent = _agent_by_id(match.group(1))
            if not agent:
                return self.fail("AGENT_NOT_FOUND")
            body = self.body_json()
            for field in AGENT_FIELDS:
                if field in body:
                    agent[field] = body[field]
            agent["updated_at"] = _now()
            app_data.add_agent(agent)
            return self.ok(agent)

        def do_DELETE(self) -> None:
            path, _ = self._route()
            if not self.authed():
                return self.fail("UNAUTHORIZED")
            connection = re.fullmatch(r"/agent-connections/([\w-]+)", path)
            if connection:
                if not self.master_authed():
                    return self.fail("UNAUTHORIZED")
                agent = _agent_by_id(connection.group(1))
                if not agent:
                    return self.fail("AGENT_NOT_FOUND")
                target = str(agent.get("agent_api_token", ""))
                save_agent_tokens([value for value in configured_agent_tokens() if value != target])
                return self.ok({"agent_id": agent.get("id"), "connected": False})
            match = re.fullmatch(r"/agents/([\w-]+)", path)
            if match:
                agent_id = match.group(1)
                if not app_data.delete_agent(agent_id):
                    return self.fail("AGENT_NOT_FOUND")
                for session in _sessions():
                    if session.get("agent_id") == agent_id:
                        runtime.drop_session_runtime(str(session["id"]))
                remaining = [s for s in app_data.load_sessions()
                             if s.get("agent_id") != agent_id]
                app_data.save_sessions(remaining)
                return self.ok({"deleted": agent_id})
            match = re.fullmatch(r"/providers/([\w-]+)", path)
            if match:
                if not runtime.delete_provider(match.group(1)):
                    return self.fail("NOT_FOUND", "Provider tidak ditemukan")
                return self.ok({"deleted": match.group(1)})
            match = re.fullmatch(r"/sessions/([\w-]+)", path)
            if match:
                session_id = match.group(1)
                if not _session_by_id(session_id):
                    return self.fail("SESSION_NOT_FOUND")
                app_data.save_sessions([s for s in app_data.load_sessions()
                                        if (s.get("session_id") or s.get("id")) != session_id])
                runtime.drop_session_runtime(session_id)
                return self.ok({"deleted": session_id})
            self.fail("NOT_FOUND")

        # ------------------------------------------------------------ provider
        def _provider_models(self, provider_id: str) -> None:
            provider = runtime.resolve_provider(provider_id)
            base_url = str(provider.get("base_url", "")).rstrip("/")
            api_key = str(provider.get("api_key", ""))
            if not base_url:
                return self.fail("PROVIDER_UNAVAILABLE", "base_url belum dikonfigurasi")
            try:
                import requests
                response = requests.get(
                    f"{base_url}/models",
                    headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                    timeout=8,
                )
                payload = response.json()
            except Exception as exc:
                return self.fail("PROVIDER_UNAVAILABLE", f"{exc.__class__.__name__}")
            items = payload.get("data") if isinstance(payload, dict) else None
            models = [str(m.get("id")) for m in items if isinstance(m, dict) and m.get("id")] \
                if isinstance(items, list) else []
            if not models:
                configured = str(provider.get("model", ""))
                models = [configured] if configured else []
            return self.ok({"provider_id": provider_id, "models": sorted(models)})

        def _provider_check(self, provider_id: str) -> None:
            provider = runtime.resolve_provider(provider_id)
            base_url = str(provider.get("base_url", "")).rstrip("/")
            api_key = str(provider.get("api_key", ""))
            if not base_url:
                return self.fail("PROVIDER_UNAVAILABLE", "base_url belum dikonfigurasi")
            if not api_key:
                return self.fail("PROVIDER_UNAVAILABLE", "Credential belum dikonfigurasi")
            started = time.time()
            try:
                import requests
                response = requests.get(
                    f"{base_url}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=8,
                )
                payload = response.json() if response.content else {}
            except Exception as exc:
                return self.fail("PROVIDER_UNAVAILABLE", f"{exc.__class__.__name__}")
            latency = int((time.time() - started) * 1000)
            items = payload.get("data") if isinstance(payload, dict) else None
            visible = len(items) if isinstance(items, list) else 0
            if response.status_code >= 400:
                return self.fail("PROVIDER_UNAVAILABLE", f"HTTP {response.status_code}")
            return self.ok({"provider_id": provider_id, "status": "connected",
                            "latency_ms": latency, "models_visible": visible})

        # -------------------------------------------------------- chat + SSE
        def _send_message(self, session_id: str, body: dict[str, Any], stream: bool) -> None:
            session = _session_by_id(session_id)
            if not session:
                return self.fail("SESSION_NOT_FOUND")
            agent = _agent_by_id(str(session.get("agent_id", "")))
            if not agent:
                return self.fail("AGENT_NOT_FOUND")
            content = str(body.get("content", "")).strip()
            if not content:
                return self.fail("VALIDATION_ERROR", "content wajib")
            if body.get("model"):
                agent = {**agent, "model": str(body["model"])}

            user_message = runtime.new_message(
                session_id, str(agent["id"]), "user", content,
                attachments=body.get("attachments", []) or [],
            )
            runtime.append_message(session_id, user_message)
            if str(session.get("title", "")) in ("", "Sesi baru"):
                session["title"] = content[:42]
                _save_session(session)
            _touch_session(session_id, content)

            if not stream:
                return self._send_blocking(session_id, agent, content, user_message, tool_profile)
            return self._send_stream(session_id, agent, content, user_message, tool_profile)

        def _send_blocking(self, session_id, agent, content, user_message, profile) -> None:
            stream_id = "stream_" + secrets.token_hex(6)
            runtime.stream_start(session_id, stream_id)
            try:
                result = runtime.run_generation(
                    session_id, agent, content, lambda _event: True,
                    tool_profile=profile,
                )
            finally:
                runtime.stream_finish(session_id)
            if result["status"] == "failed":
                return self.fail("PROVIDER_UNAVAILABLE", str(result.get("error") or ""))
            assistant = runtime.new_message(
                session_id, str(agent["id"]), "assistant",
                result["final"] or result["content"], status=result["status"],
                metadata={"model": result["model"], "tool_events": result["tool_events"]},
            )
            runtime.append_message(session_id, assistant)
            _touch_session(session_id, assistant["content"])
            return self.ok({"user_message": user_message, "assistant_message": assistant})

        def _send_stream(self, session_id, agent, content, user_message, profile) -> None:
            stream_id = "stream_" + secrets.token_hex(6)
            runtime.stream_start(session_id, stream_id)

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Access-Control-Allow-Origin", "*")
            # SSE tanpa Content-Length: koneksi ditutup di akhir agar client tahu
            # body dibaca sampai EOF.
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.close_connection = True

            def emit(event: dict[str, Any]) -> bool:
                event.setdefault("version", API_VERSION)
                event.setdefault("session_id", session_id)
                event.setdefault("timestamp", _now())
                try:
                    frame = ("event: " + str(event["type"]) + "\ndata: "
                             + json.dumps(event, ensure_ascii=False) + "\n\n")
                    self.wfile.write(frame.encode("utf-8"))
                    self.wfile.flush()
                    return True
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return False

            try:
                result = runtime.run_generation(
                    session_id, agent, content, emit, tool_profile=profile,
                )
                assistant = runtime.new_message(
                    session_id, str(agent["id"]), "assistant",
                    result["final"] or result["content"],
                    status=result["status"],
                    metadata={"model": result["model"],
                              "tool_events": result["tool_events"],
                              "error": result.get("error")},
                )
                runtime.append_message(session_id, assistant)
                session = _touch_session(session_id, assistant["content"])
                if session:
                    emit({"type": "session.updated", "session": {
                        "id": session["id"], "title": session.get("title", ""),
                        "agent_id": session.get("agent_id", ""),
                        "message_count": session.get("message_count", 0),
                        "updated_at": session.get("updated_at", ""),
                        "last_preview": session.get("last_preview", ""),
                    }})
            finally:
                runtime.stream_finish(session_id)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.timeout = 0.5
    bound = int(server.server_address[1])
    if ready:
        ready(bound)
    print(f"  [zeline-app] listening http://{host}:{bound}{API_PREFIX} "
          f"(real agent runtime, tool_profile={tool_profile})", flush=True)
    try:
        while not stop_event.is_set():
            server.handle_request()
    finally:
        server.server_close()
        print("  [zeline-app] stopped", flush=True)


STARTED = runtime.now_iso()
