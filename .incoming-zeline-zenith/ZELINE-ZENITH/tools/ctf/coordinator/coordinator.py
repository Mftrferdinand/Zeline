"""coordinator.py — poll CTFd, dispatch challenges to the swarm, manage flags.

Flow:
  poll -> for each unsolved challenge not already done -> download files ->
  scope-check any remote target -> race models in sandboxes -> collect flag ->
  HITL gate / consensus -> submit -> persist state -> repeat.
"""
from __future__ import annotations

import importlib.util
import os
import re
import threading
import time
import concurrent.futures as cf

from .config import CONFIG
from .ctfd_client import CTFdClient, Challenge
from .state import StateStore, ChallengeState
from .swarm import race
from .solver import _is_llm_category

# scope guard from the orchestrator skill
_SG = os.path.join(os.path.dirname(__file__), "..", "scope_guard.py")
_spec = importlib.util.spec_from_file_location("scope_guard", _SG)
scope_guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scope_guard)


def log(msg: str):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


# URLs embedded in a challenge description (AI/LLM & some web challenges put the
# target here instead of in connection_info).
_URL_RE = re.compile(r"https?://[^\s\"'>)\]]+", re.IGNORECASE)


def extract_targets(chal: Challenge) -> list[str]:
    """Authorized-target candidates for scope checking: connection_info first,
    then any URL found in the description (deduped, order-preserving)."""
    targets: list[str] = []
    if chal.connection_info:
        targets.append(chal.connection_info.strip())
    for m in _URL_RE.findall(chal.description or ""):
        if m not in targets:
            targets.append(m)
    return targets


class Coordinator:
    def __init__(self, cfg=CONFIG):
        self.cfg = cfg
        self.client = CTFdClient(cfg.ctfd_url, cfg.ctfd_token)
        self.state = StateStore(cfg.state_path)
        self._inflight: set[int] = set()
        self._lock = threading.Lock()
        self.keys = {
            "anthropic": cfg.anthropic_api_key,
            "openai": cfg.openai_api_key,
            "gemini": cfg.gemini_api_key,
        }

    # --- scope ---
    def _scope_ok(self, targets: list[str]) -> bool:
        # Every remote target the agent might touch must be authorized.
        for target in targets:
            try:
                scope_guard.assert_in_scope(target, self.cfg.scope_path)
            except Exception as exc:  # ScopeError / FileNotFoundError
                log(f"  scope refused for target '{target}': {exc}")
                return False
        return True

    # --- per-challenge worker ---
    def _work(self, chal_summary: Challenge):
        cid = chal_summary.id
        try:
            chal = self.client.get_challenge(cid)
            cs = self.state.get(cid) or ChallengeState(
                id=cid, name=chal.name, category=chal.category)
            cs.status = "running"
            cs.attempts += 1
            self.state.upsert(cs)

            # A challenge needs network if it has a remote target: explicit
            # connection_info, a URL in the description, or it's an AI/LLM
            # (prompt-injection) challenge whose target is a remote model.
            targets = extract_targets(chal)
            needs_net = bool(targets) or _is_llm_category(chal.category)
            # AI/LLM with no parseable URL: can't scope-check a host → refuse,
            # operator must add the target host to scope.json / connection_info.
            if _is_llm_category(chal.category) and not targets:
                log(f"  AI/LLM challenge '{chal.name}' has no parseable target URL "
                    f"to scope-check; skipping (add target to scope/description).")
                cs.status = "failed"
                self.state.upsert(cs)
                return
            if needs_net and not self._scope_ok(targets):
                cs.status = "failed"
                self.state.upsert(cs)
                return

            workdir = os.path.abspath(os.path.join(self.cfg.workdir, f"chal-{cid}"))
            os.makedirs(workdir, exist_ok=True)
            files = self.client.download_files(chal, workdir)

            chal_ctx = {
                "name": chal.name, "category": chal.category, "value": chal.value,
                "description": chal.description, "connection_info": chal.connection_info,
                "files": files, "flag_format": r"flag\{.*\}",
            }
            network = self.cfg.sandbox_network if needs_net else "none"
            if needs_net and network == "none":
                network = "bridge"

            log(f"  solving '{chal.name}' [{chal.category}] with {len(self.cfg.models)} models")
            # In unattended (non-HITL) mode we need real consensus, so tell the
            # swarm not to cancel-on-first-flag when AUTO_SUBMIT_CONSENSUS>1.
            consensus = 1 if self.cfg.hitl_submit else max(1, self.cfg.auto_submit_consensus)
            flag, statuses, all_flags = race(
                chal_ctx, [m.strip() for m in self.cfg.models], self.keys,
                sandbox_image=self.cfg.sandbox_image,
                host_workdir=workdir, network=network,
                max_steps=self.cfg.max_steps_per_solver,
                timeout=self.cfg.solver_timeout,
                on_log=lambda m: log("    " + m),
                consensus=consensus,
            )
            cs.workers = statuses
            # record every independent candidate so consensus is meaningful
            for cand in all_flags:
                self.state.add_candidate(cid, cand, "swarm")

            if flag:
                self._handle_flag(chal, cs, flag)
            elif all_flags:
                # flags were found but consensus threshold not met → queue HITL
                cs.status = "awaiting_approval"
                cs.submitted_flag = all_flags[0]
                self.state.upsert(cs)
                log(f"  candidates without consensus, queued for approval: {set(all_flags)}")
            else:
                cs.status = "failed"
                self.state.upsert(cs)
                log(f"  no flag for '{chal.name}' ({statuses})")
        except Exception as exc:  # noqa: BLE001
            log(f"  worker error on {cid}: {exc}")
        finally:
            with self._lock:
                self._inflight.discard(cid)

    # --- flag handling: HITL / consensus / submit ---
    def _handle_flag(self, chal: Challenge, cs: ChallengeState, flag: str):
        # HITL: never auto-submit, always queue for the operator.
        if self.cfg.hitl_submit:
            cs.status = "awaiting_approval"
            cs.submitted_flag = flag
            self.state.upsert(cs)
            log(f"  [HITL] '{chal.name}' flag ready, awaiting approval: {flag}")
            return
        # Unattended: the swarm only returns a flag here once the consensus
        # threshold (AUTO_SUBMIT_CONSENSUS) is already satisfied, so submit.
        self.submit(chal.id, flag)

    def submit(self, cid: int, flag: str):
        res = self.client.submit_flag(cid, flag)
        cs = self.state.get(cid)
        if res["status"] == "correct":
            cs.status = "solved"
            cs.submitted_flag = flag
            log(f"  SOLVED {cid}: {flag}")
        elif res["status"] == "already_solved":
            cs.status = "solved"
        else:
            cs.status = "failed"
            log(f"  submit rejected {cid}: {res}")
        self.state.upsert(cs)

    # --- operator action for HITL queue ---
    def approve_pending(self):
        for cs in self.state.pending_approval():
            log(f"approving {cs.id} '{cs.name}' -> {cs.submitted_flag}")
            self.submit(cs.id, cs.submitted_flag)

    # --- main loop ---
    def run(self):
        errs = self.cfg.validate()
        if errs:
            raise SystemExit("config errors: " + "; ".join(errs))
        log(f"coordinator up. target={self.cfg.ctfd_url} models={self.cfg.models}")
        pool = cf.ThreadPoolExecutor(max_workers=self.cfg.max_parallel_challenges)
        try:
            while True:
                try:
                    chals = self.client.list_challenges()
                except Exception as exc:  # noqa: BLE001
                    log(f"poll error: {exc}")
                    time.sleep(self.cfg.poll_interval)
                    continue

                for c in chals:
                    if c.solved or self.state.is_done(c.id):
                        continue
                    with self._lock:
                        if c.id in self._inflight:
                            continue
                        if len(self._inflight) >= self.cfg.max_parallel_challenges:
                            break
                        self._inflight.add(c.id)
                    pool.submit(self._work, c)

                time.sleep(self.cfg.poll_interval)
        except KeyboardInterrupt:
            log("shutting down...")
            pool.shutdown(wait=False, cancel_futures=True)
