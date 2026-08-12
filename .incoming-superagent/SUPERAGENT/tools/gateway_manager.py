#!/usr/bin/env python3
"""
tools/gateway_manager.py — Multi-Provider AI Gateway Manager (V7 | sk59)
Deploy, configure, and manage the sovereign AI gateway.
Pools Cloudflare Workers AI, OpenRouter, Groq, Together AI, HuggingFace, Cohere.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional

# ─── Provider definitions ───
PROVIDERS = {
    "cf": {
        "name": "Cloudflare Workers AI",
        "free_tier": "10,000 neurons/day per account",
        "rate_limit": "300 req/min",
        "models_source": "https://api.cloudflare.com/client/v4/accounts/{id}/ai/models/search",
        "auth_field": "token",
        "auth_prefix": "cfut_",
    },
    "openrouter": {
        "name": "OpenRouter",
        "free_tier": "Free models available",
        "rate_limit": "Varies by model",
        "models_source": "https://openrouter.ai/api/v1/models",
        "auth_field": "api_key",
        "auth_prefix": "sk-or-v1-",
    },
    "groq": {
        "name": "Groq Cloud",
        "free_tier": "30 req/min, 14,400 req/day",
        "rate_limit": "30 RPM",
        "models_source": "https://api.groq.com/openai/v1/models",
        "auth_field": "api_key",
        "auth_prefix": "gsk_",
    },
    "together": {
        "name": "Together AI",
        "free_tier": "$1 credit",
        "rate_limit": "60 RPM",
        "models_source": "https://api.together.xyz/v1/models",
        "auth_field": "api_key",
        "auth_prefix": "",
    },
    "huggingface": {
        "name": "HuggingFace Inference",
        "free_tier": "Free tier available",
        "rate_limit": "Rate-limited",
        "models_source": "https://huggingface.co/api/models",
        "auth_field": "api_key",
        "auth_prefix": "hf_",
    },
    "cohere": {
        "name": "Cohere",
        "free_tier": "Trial API key",
        "rate_limit": "100 RPM",
        "models_source": "https://api.cohere.com/v1/models",
        "auth_field": "api_key",
        "auth_prefix": "",
    },
}

@dataclass
class GatewayConfig:
    """Gateway deployment configuration."""
    port: int = 8750
    host: str = "0.0.0.0"
    api_key: str = ""
    providers: list[str] = field(default_factory=lambda: ["cf"])
    cf_accounts: list[dict] = field(default_factory=list)
    openrouter_key: str = ""
    groq_key: str = ""
    together_key: str = ""
    hf_key: str = ""
    cohere_key: str = ""
    cooldown_429: int = 90
    max_retries: int = 5
    log_level: str = "INFO"
    dashboard: bool = True
    nginx_proxy: bool = False
    domain: str = ""

    def to_env(self) -> str:
        """Generate .env file content."""
        lines = [
            f"GATEWAY_PORT={self.port}",
            f"GATEWAY_HOST={self.host}",
            f"GATEWAY_API_KEY={self.api_key}",
            f"CF_ACCOUNTS={json.dumps(self.cf_accounts)}" if self.cf_accounts else "# CF_ACCOUNTS=[]",
            f"OPENROUTER_API_KEY={self.openrouter_key}" if self.openrouter_key else "# OPENROUTER_API_KEY=",
            f"GROQ_API_KEY={self.groq_key}" if self.groq_key else "# GROQ_API_KEY=",
            f"TOGETHER_API_KEY={self.together_key}" if self.together_key else "# TOGETHER_API_KEY=",
            f"HF_API_KEY={self.hf_key}" if self.hf_key else "# HF_API_KEY=",
            f"COHERE_API_KEY={self.cohere_key}" if self.cohere_key else "# COHERE_API_KEY=",
            f"CF_COOLDOWN_429={self.cooldown_429}",
            f"MAX_RETRIES={self.max_retries}",
            f"LOG_LEVEL={self.log_level}",
        ]
        return "\n".join(lines)

    def provider_count(self) -> int:
        count = 0
        if self.cf_accounts:
            count += 1
        if self.openrouter_key:
            count += 1
        if self.groq_key:
            count += 1
        if self.together_key:
            count += 1
        if self.hf_key:
            count += 1
        if self.cohere_key:
            count += 1
        return count


class GatewayManager:
    """Deploy, configure, and manage the multi-provider AI gateway."""

    def __init__(self, install_dir: Optional[Path] = None):
        self.install_dir = install_dir or Path.home() / "superagent-gateway"
        self.config = GatewayConfig()

    def load_config(self, config_path: Optional[Path] = None) -> GatewayConfig:
        """Load configuration from .env file."""
        env_path = config_path or self.install_dir / ".env"
        cfg = GatewayConfig()
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key == "GATEWAY_PORT":
                    cfg.port = int(val)
                elif key == "GATEWAY_HOST":
                    cfg.host = val
                elif key == "GATEWAY_API_KEY":
                    cfg.api_key = val
                elif key == "CF_ACCOUNTS":
                    try:
                        cfg.cf_accounts = json.loads(val)
                    except json.JSONDecodeError:
                        pass
                elif key == "OPENROUTER_API_KEY":
                    cfg.openrouter_key = val
                elif key == "GROQ_API_KEY":
                    cfg.groq_key = val
                elif key == "TOGETHER_API_KEY":
                    cfg.together_key = val
                elif key == "HF_API_KEY":
                    cfg.hf_key = val
                elif key == "COHERE_API_KEY":
                    cfg.cohere_key = val
                elif key == "CF_COOLDOWN_429":
                    cfg.cooldown_429 = int(val)
                elif key == "MAX_RETRIES":
                    cfg.max_retries = int(val)
                elif key == "LOG_LEVEL":
                    cfg.log_level = val
        self.config = cfg
        return cfg

    def save_config(self):
        """Save configuration to .env file."""
        self.install_dir.mkdir(parents=True, exist_ok=True)
        (self.install_dir / ".env").write_text(self.config.to_env())

    def deploy(self, repo_url: str = "https://github.com/waguriagentic/cf-proxy.git",
               branch: str = "main") -> dict:
        """Deploy the gateway from source."""
        results = {"steps": [], "success": True}

        # Step 1: Clone
        if not self.install_dir.exists():
            cmd = f"git clone -b {branch} {repo_url} {self.install_dir}"
            r = self._run(cmd)
            results["steps"].append({"step": "clone", "ok": r["ok"], "output": r["out"]})
            if not r["ok"]:
                results["success"] = False
                return results

        # Step 2: Install dependencies
        cmd = f"cd {self.install_dir} && npm install"
        r = self._run(cmd)
        results["steps"].append({"step": "npm_install", "ok": r["ok"], "output": r["out"][-200:] if r["out"] else ""})

        # Step 3: Build dashboard
        cmd = f"cd {self.install_dir} && npm run build"
        r = self._run(cmd)
        results["steps"].append({"step": "npm_build", "ok": r["ok"], "output": r["out"][-200:] if r["out"] else ""})

        # Step 4: Save config
        self.save_config()
        results["steps"].append({"step": "config", "ok": True})

        results["providers"] = self.config.provider_count()
        results["port"] = self.config.port
        return results

    def status(self) -> dict:
        """Check gateway status."""
        try:
            # Check PM2
            r = subprocess.run(["pm2", "jlist"], capture_output=True, text=True, timeout=5)
            processes = json.loads(r.stdout)
            gateway = [p for p in processes if "gateway" in p.get("name", "").lower()]
        except Exception:
            gateway = []

        # Check health endpoint
        health = {}
        try:
            import urllib.request
            url = f"http://localhost:{self.config.port}/health"
            r = urllib.request.urlopen(url, timeout=3)
            health = json.loads(r.read())
        except Exception:
            health = {"error": "Gateway not reachable"}

        return {
            "running": len(gateway) > 0 and gateway[0].get("pm2_env", {}).get("status") == "online",
            "pm2_name": gateway[0].get("name", "N/A") if gateway else "N/A",
            "port": self.config.port,
            "providers_configured": self.config.provider_count(),
            "health": health,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def start(self) -> dict:
        """Start the gateway as PM2 service."""
        pm2_name = "superagent-gateway"
        cmd = f"cd {self.install_dir} && pm2 start server.js --name {pm2_name} --env-file .env --no-warnings"
        r = self._run(cmd)
        return {"started": r["ok"], "name": pm2_name, "port": self.config.port}

    def stop(self) -> dict:
        """Stop the gateway."""
        r = self._run("pm2 stop superagent-gateway")
        return {"stopped": r["ok"]}

    def restart(self) -> dict:
        """Restart the gateway."""
        r = self._run("pm2 restart superagent-gateway")
        return {"restarted": r["ok"]}

    def add_cf_account(self, token: str, account_id: str = "") -> dict:
        """Add a Cloudflare Workers AI account."""
        self.load_config()
        new_acct = {"token": token, "id": account_id or f"acct-{len(self.config.cf_accounts)+1}"}
        self.config.cf_accounts.append(new_acct)
        self.save_config()
        self.restart()
        return {"added": True, "account": new_acct["id"], "total_cf_accounts": len(self.config.cf_accounts)}

    def provider_summary(self) -> dict:
        """Get provider pool summary."""
        self.load_config()
        return {
            "cloudflare_accounts": len(self.config.cf_accounts),
            "openrouter": bool(self.config.openrouter_key),
            "groq": bool(self.config.groq_key),
            "together": bool(self.config.together_key),
            "huggingface": bool(self.config.hf_key),
            "cohere": bool(self.config.cohere_key),
            "total_providers": self.config.provider_count(),
            "estimated_daily_capacity_requests": self._estimate_capacity(),
        }

    def _estimate_capacity(self) -> int:
        """Estimate total daily request capacity across all providers."""
        capacity = 0
        if self.config.cf_accounts:
            # ~300 req/account/day at free tier
            capacity += len(self.config.cf_accounts) * 300
        if self.config.openrouter_key:
            capacity += 500  # Conservative for free models
        if self.config.groq_key:
            capacity += 14400  # 30 RPM * 60 * 8 (active hours)
        if self.config.together_key:
            capacity += 1000
        if self.config.hf_key:
            capacity += 500
        if self.config.cohere_key:
            capacity += 5000
        return capacity

    def _run(self, cmd: str) -> dict:
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
            return {"ok": r.returncode == 0, "out": r.stdout[-500:] if r.stdout else "",
                    "err": r.stderr[-500:] if r.stderr else ""}
        except subprocess.TimeoutExpired:
            return {"ok": False, "out": "", "err": "Timeout after 120s"}
        except Exception as e:
            return {"ok": False, "out": "", "err": str(e)}


# ─── CLI ───
if __name__ == "__main__":
    mgr = GatewayManager()

    if len(sys.argv) < 2 or sys.argv[1] in ("--help", "-h"):
        print(__doc__)
        print("\nCommands:")
        print("  deploy [repo_url]     Clone, install, build, configure gateway")
        print("  start                  Start gateway as PM2 service")
        print("  stop                   Stop gateway")
        print("  restart                Restart gateway")
        print("  status                 Check gateway status + health")
        print("  config                 Show current configuration")
        print("  providers              Show provider pool summary")
        print("  add-cf <token> [id]    Add Cloudflare Workers AI account")
        print("  add-key <provider> <key>  Add API key for provider")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "deploy":
        repo = sys.argv[2] if len(sys.argv) > 2 else "https://github.com/waguriagentic/cf-proxy.git"
        print(json.dumps(mgr.deploy(repo), indent=2))
    elif cmd == "start":
        print(json.dumps(mgr.start(), indent=2))
    elif cmd == "stop":
        print(json.dumps(mgr.stop(), indent=2))
    elif cmd == "restart":
        print(json.dumps(mgr.restart(), indent=2))
    elif cmd == "status":
        mgr.load_config()
        print(json.dumps(mgr.status(), indent=2))
    elif cmd == "config":
        mgr.load_config()
        print(json.dumps(mgr.config.__dict__, indent=2, default=str))
    elif cmd == "providers":
        mgr.load_config()
        print(json.dumps(mgr.provider_summary(), indent=2))
    elif cmd == "add-cf":
        if len(sys.argv) < 3:
            print("Usage: add-cf <token> [account_id]")
            sys.exit(1)
        token = sys.argv[2]
        acct_id = sys.argv[3] if len(sys.argv) > 3 else ""
        print(json.dumps(mgr.add_cf_account(token, acct_id), indent=2))
    elif cmd == "add-key":
        if len(sys.argv) < 4:
            print("Usage: add-key <provider> <api_key>")
            sys.exit(1)
        provider = sys.argv[2]
        key = sys.argv[3]
        mgr.load_config()
        setattr(mgr.config, f"{provider}_key", key)
        mgr.save_config()
        print(f"✅ Added {provider} API key. Restart gateway to apply.")
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
