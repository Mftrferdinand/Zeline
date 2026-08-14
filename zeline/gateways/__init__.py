"""Gateway registry Zeline.

Gateway adalah adapter platform terpisah. Core agent tidak tahu detail Telegram,
WhatsApp, atau HTTP webhook; adapter hanya menerjemahkan pesan platform ke
``SessionStore.send()``. Menambah platform baru cukup membuat modul dengan:

- ``info()``: metadata untuk setup wizard
- ``validate_config(cfg)``: daftar error konfigurasi
- ``start(sessions, cfg, stop_event)``: loop blocking adapter

Registry ini sengaja kecil agar pihak ketiga nantinya dapat memasang adapter
sebagai plugin tanpa mengubah agent core.
"""
from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from . import telegram, webhook, whatsapp

GATEWAYS: dict[str, ModuleType] = {
    "telegram": telegram,
    "whatsapp": whatsapp,
    "webhook": webhook,
}


@dataclass
class GatewayRuntime:
    stop_event: threading.Event
    threads: list[tuple[str, threading.Thread]]

    def stop(self, timeout: float = 8.0) -> None:
        self.stop_event.set()
        for _name, thread in self.threads:
            thread.join(timeout=timeout)

    @property
    def alive(self) -> list[str]:
        return [name for name, thread in self.threads if thread.is_alive()]


def _validate_tool_policy(name: str, cfg: dict[str, Any]) -> list[str]:
    """Fail closed before an elevated gateway becomes a remote execution API."""
    profile = str(cfg.get("tool_profile", "safe"))
    if profile == "safe":
        return []
    if profile not in {"workspace", "full"}:
        return [f"invalid {name} tool_profile: {profile}"]
    # A webhook has one bearer token and a caller-controlled chat_id. It cannot
    # prove an individual owner identity, so keep it safe-only.
    if name == "webhook":
        return ["webhook tool_profile must remain safe; use an owner-allowlisted messaging gateway for elevated tools"]
    allowed = cfg.get("allowed")
    owner = str(cfg.get("owner_identity", "")).strip()
    if not isinstance(allowed, list) or len(allowed) != 1 or str(allowed[0]).strip() in {"", "*"}:
        return [f"tool_profile {profile} requires one exact owner allowlist entry"]
    if not owner or owner != str(allowed[0]).strip():
        return ["owner_identity must exactly match the sole allowlist entry"]
    if profile == "full" and cfg.get("remote_code_execution_ack") is not True:
        return ["tool_profile full requires explicit remote_code_execution_ack=true"]
    return []


def validate_gateway(name: str, cfg: dict[str, Any]) -> list[str]:
    module = GATEWAYS.get(name)
    if module is None:
        return [f"unknown gateway: {name}"]
    validator = getattr(module, "validate_config", None)
    errors = list(validator(cfg) if validator else [])
    errors.extend(_validate_tool_policy(name, cfg))
    return errors


def run_all(sessions, cfg: dict[str, dict[str, Any]], names: list[str] | None = None) -> GatewayRuntime:
    """Run enabled gateways in threads and capture adapter crashes."""
    stop_event = threading.Event()
    threads: list[tuple[str, threading.Thread]] = []

    for name, module in GATEWAYS.items():
        if names and name not in names:
            continue
        gateway_cfg = cfg.get(name, {})
        if not gateway_cfg.get("enabled", False):
            continue
        errors = validate_gateway(name, gateway_cfg)
        if errors:
            print(f"  [gateway:{name}] not started: {'; '.join(errors)}", flush=True)
            continue

        def worker(adapter=module, adapter_cfg=gateway_cfg, adapter_name=name):
            try:
                adapter.start(sessions, adapter_cfg, stop_event)
            except Exception as exc:  # jangan bunuh gateway lain bila satu adapter crash
                print(f"  [gateway:{adapter_name}] crash: {exc}", flush=True)
                traceback.print_exc()

        thread = threading.Thread(target=worker, daemon=True, name=f"zeline-{name}")
        thread.start()
        threads.append((name, thread))
        print(f"  [gateway:{name}] starting", flush=True)

    return GatewayRuntime(stop_event=stop_event, threads=threads)


def gateway_status(cfg: dict[str, dict[str, Any]]) -> list[tuple[str, bool, list[str]]]:
    """Status konfigurasi deterministik, tanpa menyalakan koneksi platform."""
    result = []
    for name, module in GATEWAYS.items():
        gateway_cfg = cfg.get(name, {})
        enabled = bool(gateway_cfg.get("enabled", False))
        result.append((name, enabled, validate_gateway(name, gateway_cfg) if enabled else []))
    return result
