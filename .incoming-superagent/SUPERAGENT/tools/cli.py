#!/usr/bin/env python3
"""
tools/cli.py — SUPERAGENT V7 IRONCLAW Unified CLI

Single entry point exposing all major tools via subcommands.
Lazy-loaded: each module is imported only when its subcommand is called,
so missing optional deps don't block unrelated commands.

Usage:
  superagent scan
  superagent auth <challenge> <signature>
  superagent bridge status
  superagent treasury [daily|weekly|monthly]
  superagent deploy-gateway
  superagent dashboard [--serve PORT]
  superagent eval <label> [--case NAME EXPECT_TYPE VALUE...]
  superagent watchdog tick
  superagent reflection cycle
  superagent triage [--source SOURCE] [--top-k N]
  superagent integrity verify
  superagent model list|add|remove
  superagent upgrade <zip_path> [--dry-run]
  superagent version
"""
from __future__ import annotations

import argparse
import json
import sys
import textwrap
from typing import Optional


VERSION = "7.0.0"


def cmd_scan(args) -> int:
    """Run revenue optimizer scan on available data sources."""
    print("🔍 SUPERAGENT V7 — Revenue Scan")
    try:
        from revenue_optimizer import scan as ro_scan

        results = ro_scan()
        if isinstance(results, dict):
            for k, v in results.items():
                print(f"   • {k}: {v}")
        else:
            print(f"   {results}")
        return 0
    except ImportError:
        pass

    # Fallback to revenue_engine (v4.x)
    try:
        from revenue_engine import BulkRunner, Checkpoint, TokenBucket

        print("   ✅ revenue_engine modules loaded")
        print("   → BulkRunner, Checkpoint, TokenBucket ready")
        print("   ℹ️  Wire a worker (api_harvester, scraper, etc.) then call BulkRunner.run()")
        return 0
    except ImportError as e:
        print(f"   ❌ revenue modules not available: {e}")
        return 1


def cmd_auth(args) -> int:
    """Verify team auth challenge/signature pair using team_auth module (EIP-712 / SIWE)."""
    print("🔐 SUPERAGENT V7 — Team Auth Verify")
    challenge = args.challenge
    signature = args.signature

    print(f"   challenge: {challenge[:60]}..." if len(challenge) > 60 else f"   challenge: {challenge}")
    print(f"   signature: {signature[:16]}...")

    # Try team_auth module first (preferred, full SIWE/EIP-712)
    try:
        from team_auth import TeamAuthenticator, CHALLENGE_PREFIX

        # Verify against a dummy challenge record
        recovered = TeamAuthenticator._recover_signer_eip712(challenge, signature)
        print(f"   ✅ recovered address (EIP-712): {recovered}")
        return 0
    except (ImportError, AttributeError):
        pass

    # Try eth-account directly
    try:
        from eth_account.messages import encode_defunct
        from eth_account import Account

        msg = encode_defunct(text=challenge)
        recovered = Account.recover_message(msg, signature=signature)
        print(f"   ✅ recovered address (eth-account): {recovered}")
        return 0
    except ImportError:
        print("   ⚠️  eth-account not installed — install with: pip install superagent-v7[crypto]")
        print("   → displaying raw signature bytes only")
        print(f"   signature hex: {signature}")
        return 0
    except Exception as e:
        print(f"   ❌ verification failed: {e}")
        return 1


def cmd_bridge(args) -> int:
    """Show HermesBridge status (multi-chain bridge adapter)."""
    print("🌉 SUPERAGENT V7 — HermesBridge Status")

    # Try hermes_bridge adapter first (v7)
    try:
        from hermes_bridge import HermesBridgeAdapter

        adapter = HermesBridgeAdapter()
        print("   ✅ HermesBridgeAdapter loaded (V7)")
        print(f"   → Supported protocols: {', '.join(adapter.list_protocols()) if hasattr(adapter, 'list_protocols') else 'LayerZero, Stargate, LI.FI, Across'}")

        # Quick status check on common routes
        print()
        print("   Quick route check:")
        for src, dst, token in [("ethereum", "arbitrum", "USDC"), ("arbitrum", "optimism", "ETH")]:
            status = adapter.check_bridge_status(src, dst, token)
            active = getattr(status, 'active_routes', 0) or getattr(status, 'total_routes', 0)
            icon = "✅" if active > 0 else "❌"
            best = getattr(status, 'best_route', None)
            fee = f"${best.estimated_fee_usd:.2f}" if best and hasattr(best, 'estimated_fee_usd') else "N/A"
            print(f"   {icon} {src} → {dst} ({token}): routes={getattr(status, 'active_routes', '?')}/{getattr(status, 'total_routes', '?')}, best_fee={fee}")
        return 0
    except ImportError:
        pass

    # Fallback: try skills/hermes bridge_engine
    try:
        from bridge_engine import BridgeEngine

        print("   ✅ bridge_engine module loaded (v4.x)")
        print("   → BridgeEngine available for cross-chain operations")
        print("   ℹ️  Supported: LayerZero, Stargate, LI.FI, Across, deBridge, Wormhole")
        return 0
    except ImportError:
        print("   ⚠️  No bridge module found in path")
        print("   💡 Run from workspace root or set PYTHONPATH")
        return 1


def cmd_treasury(args) -> int:
    """Show P&L / cost ledger report."""
    window = args.window or "daily"
    windows = {"daily": 86400, "weekly": 604800, "monthly": 2592000}
    seconds = windows.get(window, 86400)

    print(f"💰 SUPERAGENT V7 — Treasury Report ({window})")

    try:
        from cost_ledger import CostLedger

        ledger = CostLedger()
        summary = ledger.summary(window_s=seconds)
        print()
        print(summary.report())
        ledger.close()
        return 0
    except ImportError as e:
        print(f"   ❌ cost_ledger not available: {e}")
        return 1
    except Exception as e:
        print(f"   ❌ error reading ledger: {e}")
        return 1


def cmd_deploy_gateway(args) -> int:
    """Deploy gateway manager — manages multi-provider AI gateway (V7)."""
    print("🚀 SUPERAGENT V7 — Deploy Gateway")

    # Try gateway_manager first (V7)
    try:
        from gateway_manager import deploy as gm_deploy

        result = gm_deploy()
        if isinstance(result, dict):
            print(f"   ✅ gateway deployed: {json.dumps(result, indent=2)}")
        else:
            print(f"   ✅ {result}")
        return 0
    except ImportError:
        pass
    except Exception as e:
        print(f"   ❌ gateway_manager deploy failed: {e}")
        # Fall through to manual instructions

    # Fallback: manual instructions
    print("   ℹ️  Gateway deployment via OpenClaw/Hermes runtime.")
    print()
    print("   Manual steps:")
    print("   1. Ensure openclaw gateway is running:  openclaw gateway status")
    print("   2. Configure nodes:                     openclaw gateway nodes add ...")
    print("   3. Start gateway:                       openclaw gateway start")
    print()
    print("   💡 For automated deploy, see skills/hermes/scripts/deploy_engine.py")
    return 0


def cmd_dashboard(args) -> int:
    """Generate or serve the live web dashboard."""
    print("📊 SUPERAGENT V7 — Dashboard")

    try:
        from dashboard import build_dashboard_html, gather

        data = gather()
        html = build_dashboard_html(data)

        if args.serve:
            import http.server
            import socket
            import threading
            import webbrowser
            from pathlib import Path

            port = int(args.serve)
            html_path = Path("/tmp/superagent-dashboard.html")
            html_path.write_text(html)

            class Handler(http.server.SimpleHTTPRequestHandler):
                def __init__(self, *a, **kw):
                    super().__init__(*a, directory=str(html_path.parent), **kw)

                def do_GET(self):
                    if self.path == "/" or self.path == "/index.html":
                        self.path = f"/{html_path.name}"
                    return super().do_GET()

                def log_message(self, fmt, *a):
                    print(f"   🌐 {fmt % a}")

            server = http.server.HTTPServer(("127.0.0.1", port), Handler)
            print(f"   ✅ Dashboard serving at http://127.0.0.1:{port}")
            print(f"   Press Ctrl+C to stop")
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                print("\n   🛑 Server stopped")
                server.shutdown()
        else:
            out_path = args.output or "/tmp/superagent-dashboard.html"
            from pathlib import Path

            Path(out_path).write_text(html)
            print(f"   ✅ Dashboard written to {out_path}")
            print(f"   → Open in browser or serve with: superagent dashboard --serve 8080")
        return 0
    except ImportError as e:
        print(f"   ❌ dashboard module not available: {e}")
        return 1


def cmd_eval(args) -> int:
    """Run agentic eval suite."""
    print(f"🧪 SUPERAGENT V7 — Eval: {args.label}")

    try:
        from eval import Eval, Case

        ev = Eval(args.label)

        # Build cases from CLI args
        if args.cases:
            it = iter(args.cases)
            for name, expect_type, value in zip(it, it, it):
                # expect_type: "eq" | "contains" | "truthy" | "type"
                if expect_type == "eq":
                    ev.add(Case(name=name, input=name, expect=lambda r, v=value: str(r) == v))
                elif expect_type == "contains":
                    ev.add(Case(name=name, input=name, expect=lambda r, v=value: v in str(r)))
                elif expect_type == "truthy":
                    ev.add(Case(name=name, input=name, expect=lambda r, v=value: bool(r) == (v.lower() == "true")))
                elif expect_type == "type":
                    ev.add(Case(name=name, input=name, expect=lambda r, v=value: type(r).__name__ == v))

        # Run with identity target by default
        target = lambda x: f"processed:{x}"
        result = ev.run(target)
        print(result.summary())

        if result.pass_rate < 1.0:
            return 1
        return 0
    except ImportError as e:
        print(f"   ❌ eval module not available: {e}")
        return 1


def cmd_watchdog(args) -> int:
    """Run a single watchdog tick."""
    print("🛡️ SUPERAGENT V7 — Watchdog Tick")

    try:
        from watchdog import Watchdog

        wd = Watchdog()
        events = wd.tick()
        if events:
            for ev in events:
                icon = "❌" if ev["status"] in ("down_ratelimited", "restart_failed") else "✅"
                detail = f" ({ev.get('detail')})" if ev.get("detail") else ""
                print(f"   {icon} {ev['proc']} → {ev['status']}{detail}")
        else:
            print("   ✅ all processes healthy")
        return 0 if not any(e["status"] in ("down_ratelimited", "restart_failed") for e in events) else 1
    except ImportError as e:
        print(f"   ❌ watchdog module not available: {e}")
        return 1


def cmd_reflection(args) -> int:
    """Run self-improvement reflection cycle."""
    print("🔄 SUPERAGENT V7 — Reflection Cycle")

    try:
        from reflection import daily_cycle, SAFE_AUTO_ACTIONS, FROZEN_PATHS

        # Minimal memory engine stub for standalone use
        class StubMemory:
            def recent(self, n): return []
            def remember(self, *a, **kw): pass

        result = daily_cycle(StubMemory())
        print(f"   lessons learned: {len(result.get('lessons_learned', []))}")
        print(f"   pending proposals: {result.get('pending_proposals', 0)}")
        print(f"   proposals dir: {result.get('proposals_dir', 'N/A')}")
        print(f"   safe auto-actions: {len(SAFE_AUTO_ACTIONS)}")
        print(f"   frozen paths: {len(FROZEN_PATHS)}")
        return 0
    except ImportError as e:
        print(f"   ❌ reflection module not available: {e}")
        return 1


def cmd_triage(args) -> int:
    """Run inbox triage (demo mode if no messages fed)."""
    print("📥 SUPERAGENT V7 — Inbox Triage")

    try:
        from triage import Message, triage as do_triage, format_digest

        # Demo messages when no real input
        messages = [
            Message(source=args.source or "telegram", sender="operator",
                    text="URGENT: gas murah nih, swap skrg?"),
            Message(source=args.source or "telegram", sender="partner",
                    text="claim window LayerZero expire 2 jam lagi!"),
            Message(source="discord", sender="rando", text="gm"),
        ]

        vips = {"operator", "partner"}
        digest = do_triage(messages, vips=vips, top_k=int(args.top_k))
        print(format_digest(digest))
        return 0
    except ImportError as e:
        print(f"   ❌ triage module not available: {e}")
        return 1


def cmd_integrity(args) -> int:
    """Verify skill integrity (SKILLS.lock)."""
    print("🔒 SUPERAGENT V7 — Skill Integrity")

    try:
        from skill_integrity import build_manifest, sha256
        from pathlib import Path

        if args.generate:
            manifest = build_manifest()
            lock_path = Path("SKILLS.lock")
            lock_path.write_text(json.dumps(manifest, indent=2) + "\n")
            print(f"   ✅ lockfile generated: {lock_path.resolve()}")
            print(f"      {len(manifest)} files hashed")
            return 0

        # Verify mode: rebuild manifest, compare with stored
        current = build_manifest()
        lock_path = Path("SKILLS.lock")
        if not lock_path.exists():
            print("   ⚠️  SKILLS.lock not found — run 'superagent integrity generate' first")
            return 1

        stored = json.loads(lock_path.read_text())
        violations = []
        verified = 0
        for path_str, stored_hash in stored.items():
            p = Path(path_str)
            if not p.exists():
                violations.append(f"MISSING: {path_str}")
            else:
                current_hash = sha256(p)
                if current_hash != stored_hash:
                    violations.append(f"MODIFIED: {path_str}")
                else:
                    verified += 1

        if violations:
            print(f"   ❌ {len(violations)} integrity violation(s):")
            for v in violations:
                print(f"      {v}")
            return 1
        else:
            print(f"   ✅ all {verified} files verified — integrity OK")
            return 0
    except ImportError as e:
        print(f"   ❌ skill_integrity module not available: {e}")
        return 1
    except Exception as e:
        print(f"   ❌ integrity check failed: {e}")
        return 1


def cmd_model(args) -> int:
    """Manage LLM model registry (list / add / remove)."""
    action = args.model_action

    try:
        from model_registry import ModelRegistry

        reg = ModelRegistry()

        if action == "list":
            models = reg.list_models()
            if not models:
                print("   (no models registered)")
                return 0
            print(f"   {len(models)} model(s) registered:")
            for m in models:
                key_display = m.get("api_key", "")[:8] + "..." if m.get("api_key") else "(none)"
                print(f"   • {m['name']:30s} {m.get('model',''):25s} "
                      f"kind={m.get('kind','?')}  prio={m.get('priority','?')}  "
                      f"key={key_display}")
            return 0

        elif action == "add":
            if not args.model_name or not args.model_id:
                print("   ❌ --name and --model required for add")
                return 1
            reg.add_model(
                name=args.model_name,
                api_key=args.api_key or "",
                base_url=args.base_url or "",
                model=args.model_id,
                kind=args.model_kind or "openai",
                priority=int(args.priority or 50),
            )
            print(f"   ✅ model '{args.model_name}' added")
            return 0

        elif action == "remove":
            if not args.model_name:
                print("   ❌ --name required for remove")
                return 1
            reg.remove_model(args.model_name)
            print(f"   ✅ model '{args.model_name}' removed")
            return 0
    except ImportError as e:
        print(f"   ❌ model_registry not available: {e}")
        print("   💡 Install deps: pip install httpx cryptography")
        return 1
    except Exception as e:
        print(f"   ❌ error: {e}")
        return 1


def cmd_upgrade(args) -> int:
    """Run SUPERAGENT upgrade from zip."""
    print("⬆️  SUPERAGENT V7 — Upgrade")

    try:
        from upgrade import main as upgrade_main

        # Reconstruct sys.argv for the upgrade module
        sys.argv = [
            "superagent-upgrade",
            args.zip_path,
        ]
        if args.dry_run:
            sys.argv.append("--dry-run")
        if args.list_backups:
            sys.argv = ["superagent-upgrade", "--list-backups"]
        if args.rollback:
            sys.argv = ["superagent-upgrade", "--rollback", args.rollback]

        upgrade_main()
        return 0
    except ImportError as e:
        print(f"   ❌ upgrade module not available: {e}")
        return 1
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1


def cmd_version(args) -> int:
    """Print SUPERAGENT version and tool summary."""
    print(f"🔥 SUPERAGENT V7 IRONCLAW — v{VERSION}")
    print(f"   Codename: ironclaw")
    print(f"   Python:   {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    print(f"   Tools:    53 (m0-m48, H1-H10, core)")
    print(f"   Skills:   59")
    print()

    # Scan available tool modules
    import importlib

    tools_available = []
    tools_missing = []
    tool_modules = [
        "alerts", "alpha_radar", "api_harvester", "automation", "backtest",
        "briefing", "claim_watcher", "community_intel", "content",
        "contract_watch", "cost_ledger", "ctf", "dashboard",
        "desktop_control", "dryrun", "eligibility", "eval",
        "exit_planner", "explain", "farm_roi", "guide_studio",
        "hids", "hook_lab", "humanizer", "mcp_builder",
        "memory_engine", "model_registry", "multimodal", "planner",
        "prd", "reflection", "repurpose", "research_q",
        "revenue_engine", "router_log", "rugcheck", "scam_sentinel",
        "scene_prep", "secret_tripwire", "skill_forge",
        "skill_integrity", "skill_market", "swarm", "sybil_audit",
        "triage", "unlock_engine", "upgrade", "vault",
        "video_pipeline", "voice", "watchdog",
    ]

    for mod in tool_modules:
        try:
            importlib.import_module(mod)
            tools_available.append(mod)
        except ImportError:
            tools_missing.append(mod)

    print(f"   Available: {len(tools_available)}/{len(tool_modules)}")
    if tools_missing:
        print(f"   Missing deps: {len(tools_missing)} — {', '.join(tools_missing[:8])}" +
              (f" +{len(tools_missing)-8} more" if len(tools_missing) > 8 else ""))

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="superagent",
        description="SUPERAGENT V7 IRONCLAW — Sovereign Autonomous Agent Framework CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              superagent scan                      # run revenue scan
              superagent treasury monthly          # monthly P&L report
              superagent dashboard --serve 8080    # serve live dashboard
              superagent model list                # list registered LLM models
              superagent version                   # version + tool summary
        """),
    )

    sub = parser.add_subparsers(dest="command", title="commands")

    # ── scan ──
    p_scan = sub.add_parser("scan", help="Run revenue engine scan")
    p_scan.set_defaults(func=cmd_scan)

    # ── auth ──
    p_auth = sub.add_parser("auth", help="Verify team auth challenge/signature")
    p_auth.add_argument("challenge", help="Challenge string")
    p_auth.add_argument("signature", help="Signature hex")
    p_auth.set_defaults(func=cmd_auth)

    # ── bridge ──
    p_bridge = sub.add_parser("bridge", help="HermesBridge operations")
    p_bridge.add_argument("action", nargs="?", default="status",
                          choices=["status"], help="Bridge action (default: status)")
    p_bridge.set_defaults(func=cmd_bridge)

    # ── treasury ──
    p_treasury = sub.add_parser("treasury", help="P&L / cost ledger report")
    p_treasury.add_argument("window", nargs="?", default="daily",
                            choices=["daily", "weekly", "monthly"],
                            help="Report window (default: daily)")
    p_treasury.set_defaults(func=cmd_treasury)

    # ── deploy-gateway ──
    p_dg = sub.add_parser("deploy-gateway", help="Deploy gateway manager")
    p_dg.set_defaults(func=cmd_deploy_gateway)

    # ── dashboard ──
    p_dash = sub.add_parser("dashboard", help="Generate or serve live dashboard")
    p_dash.add_argument("--serve", metavar="PORT", help="Serve dashboard on localhost:PORT")
    p_dash.add_argument("--output", "-o", help="Output HTML path (default: /tmp/superagent-dashboard.html)")
    p_dash.set_defaults(func=cmd_dashboard)

    # ── eval ──
    p_eval = sub.add_parser("eval", help="Run agentic eval suite")
    p_eval.add_argument("label", help="Eval label/name")
    p_eval.add_argument("--cases", nargs="*", metavar="NAME EXPECT_TYPE VALUE",
                        help="Case triples: NAME EXPECT_TYPE VALUE ...")
    p_eval.set_defaults(func=cmd_eval)

    # ── watchdog ──
    p_wd = sub.add_parser("watchdog", help="Watchdog operations")
    p_wd.add_argument("action", nargs="?", default="tick",
                      choices=["tick", "heartbeat"], help="Action (default: tick)")
    p_wd.set_defaults(func=cmd_watchdog)

    # ── reflection ──
    p_ref = sub.add_parser("reflection", help="Self-improvement reflection")
    p_ref.add_argument("action", nargs="?", default="cycle",
                       choices=["cycle"], help="Action (default: cycle)")
    p_ref.set_defaults(func=cmd_reflection)

    # ── triage ──
    p_tr = sub.add_parser("triage", help="Inbox notification triage")
    p_tr.add_argument("--source", default="telegram", help="Message source")
    p_tr.add_argument("--top-k", default="10", help="Max other messages to show")
    p_tr.set_defaults(func=cmd_triage)

    # ── integrity ──
    p_int = sub.add_parser("integrity", help="Skill integrity operations")
    p_int.add_argument("action", nargs="?", default="verify",
                       choices=["verify", "generate"],
                       help="verify checksums or generate lockfile")
    p_int.set_defaults(func=cmd_integrity, generate=False)

    # ── model ──
    p_mod = sub.add_parser("model", help="LLM model registry management")
    p_mod.add_argument("model_action", choices=["list", "add", "remove"],
                       help="Action: list, add, or remove models")
    p_mod.add_argument("--name", dest="model_name", help="Model registry name")
    p_mod.add_argument("--model", dest="model_id", help="Model identifier (e.g. gpt-4o)")
    p_mod.add_argument("--api-key", help="API key for the model provider")
    p_mod.add_argument("--base-url", help="Base URL for OpenAI-compatible API")
    p_mod.add_argument("--kind", dest="model_kind", choices=["openai", "anthropic"],
                       default="openai", help="Provider kind")
    p_mod.add_argument("--priority", type=int, default=50,
                       help="Cascade priority (lower = preferred)")
    p_mod.set_defaults(func=cmd_model)

    # ── upgrade ──
    p_up = sub.add_parser("upgrade", help="Upgrade SUPERAGENT from zip")
    p_up.add_argument("zip_path", nargs="?", help="Path to upgrade zip")
    p_up.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    p_up.add_argument("--rollback", help="Rollback to a backup directory")
    p_up.add_argument("--list-backups", action="store_true", help="List available backups")
    p_up.set_defaults(func=cmd_upgrade)

    # ── version ──
    p_ver = sub.add_parser("version", help="Show version and tool summary")
    p_ver.set_defaults(func=cmd_version)

    # ── hidden e2e (for compatibility) ──
    p_e2e = sub.add_parser("e2e", help="End-to-end test (placeholder)")
    p_e2e.set_defaults(func=lambda _: print("📋 e2e demo — configure test scenarios in tools/tests/"))

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point. Callable from setup.cfg console_scripts."""
    parser = build_parser()

    # Handle special argv override for integrity generate
    args_list = argv if argv is not None else sys.argv[1:]

    # Pre-process: integrity generate = explicit action
    if len(args_list) >= 2 and args_list[0] == "integrity" and args_list[1] == "generate":
        args = parser.parse_args(args_list)
        args.generate = True
    elif len(args_list) >= 1 and args_list[0] in ("integrity",) and len(args_list) == 1:
        args = parser.parse_args(args_list + ["verify"])
    elif len(args_list) == 0:
        # Special-case: `model list` when input is just `model`
        parser.print_help()
        return 0
    else:
        args = parser.parse_args(args_list)

    if not hasattr(args, "func"):
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
