#!/usr/bin/env python3
"""Jalankan gateway Zeline App standalone (tanpa menyalakan gateway lain).

Dipakai untuk dev/verifikasi: gateway ini memanggil agent ASLI
(zeline.agent.Zeline), jadi jawabannya nyata dari provider terkonfigurasi.

  python3 run_zeline_app.py [--port 8082] [--host 127.0.0.1] [--profile full]

Token dibaca dari ZELINE_APP_TOKEN bila ada; kalau tidak, dibuat dan dicetak.
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from zeline.gateways import zeline_app
from zeline import config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("ZELINE_APP_PORT", "8082")))
    parser.add_argument("--host", default=os.environ.get("ZELINE_APP_HOST", "127.0.0.1"))
    parser.add_argument("--profile", default=os.environ.get("ZELINE_APP_PROFILE", "safe"),
                        choices=["safe", "workspace", "full"])
    args = parser.parse_args()

    app_cfg = config.stored_config_copy().get("zeline_app", {})
    token = os.environ.get("ZELINE_APP_TOKEN") or str(app_cfg.get("gateway_token", "")) or secrets.token_urlsafe(24)
    stop_event = threading.Event()
    cfg = {
        "enabled": True,
        "token": token,
        "port": args.port if "ZELINE_APP_PORT" in os.environ or args.port != 8082 else int(app_cfg.get("port", 8082)),
        "host": args.host if "ZELINE_APP_HOST" in os.environ or args.host != "127.0.0.1" else str(app_cfg.get("host", "127.0.0.1")),
        "tool_profile": args.profile,
        "agent_tokens": [str(row.get("token", "")) for row in
                         config.stored_config_copy().get("zeline_app", {}).get("linked_agents", [])
                         if isinstance(row, dict) and row.get("token")],
    }
    errors = zeline_app.validate_config(cfg)
    if errors:
        print("config invalid: " + "; ".join(errors), flush=True)
        return 1

    print(f"gateway_token={token}", flush=True)
    print(f"tool_profile={args.profile}", flush=True)
    try:
        zeline_app.start(None, cfg, stop_event)
    except KeyboardInterrupt:
        stop_event.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
