"""config.py — runtime configuration loaded from environment (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    # --- CTFd target ---
    ctfd_url: str = os.environ.get("CTFD_URL", "")
    ctfd_token: str = os.environ.get("CTFD_TOKEN", "")

    # --- scope / safety ---
    scope_path: str = os.environ.get("SCOPE_PATH", "tools/ctf/scope.json")
    # HITL: if True, flags are queued for human approval instead of auto-submitted
    hitl_submit: bool = _bool("HITL_SUBMIT", True)
    # auto-submit only if a flag was produced by >=N independent workers
    auto_submit_consensus: int = int(os.environ.get("AUTO_SUBMIT_CONSENSUS", "2"))

    # --- orchestration ---
    poll_interval: int = int(os.environ.get("POLL_INTERVAL", "10"))      # seconds
    max_parallel_challenges: int = int(os.environ.get("MAX_PARALLEL_CHALLENGES", "4"))
    max_steps_per_solver: int = int(os.environ.get("MAX_STEPS", "40"))   # agent tool-loop cap
    solver_timeout: int = int(os.environ.get("SOLVER_TIMEOUT", "900"))   # seconds per worker

    # --- sandbox ---
    sandbox_image: str = os.environ.get("SANDBOX_IMAGE", "ctf-sandbox")
    sandbox_network: str = os.environ.get("SANDBOX_NETWORK", "none")     # 'none' | 'bridge'

    # --- state ---
    workdir: str = os.environ.get("WORKDIR", "work")
    state_path: str = os.environ.get("STATE_PATH", "work/state.json")

    # --- model lineup (raced per challenge) ---
    # Each entry: provider:model. Anthropic implemented; others via adapter.
    models: list[str] = field(default_factory=lambda: os.environ.get(
        "MODELS",
        "anthropic:claude-sonnet-4-5,anthropic:claude-opus-4-1"
    ).split(","))

    # --- API keys ---
    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    gemini_api_key: str = os.environ.get("GEMINI_API_KEY", "")

    def validate(self) -> list[str]:
        errs = []
        if not self.ctfd_url:
            errs.append("CTFD_URL not set")
        if not self.ctfd_token:
            errs.append("CTFD_TOKEN not set")
        if not any(m.strip() for m in self.models):
            errs.append("no MODELS configured")
        for provider, key_attr, env in (
            ("anthropic", "anthropic_api_key", "ANTHROPIC_API_KEY"),
            ("openai", "openai_api_key", "OPENAI_API_KEY"),
            ("gemini", "gemini_api_key", "GEMINI_API_KEY"),
        ):
            if any(m.strip().startswith(provider + ":") for m in self.models) \
                    and not getattr(self, key_attr):
                errs.append(f"{env} required for {provider} models")
        return errs


CONFIG = Config()
