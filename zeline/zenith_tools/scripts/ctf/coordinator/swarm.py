"""swarm.py — race multiple models on one challenge.

Two modes:
  * consensus<=1 (default): FIRST valid flag wins; remaining workers cancelled
    immediately (fastest, used for HITL or single-flag auto submit).
  * consensus>=2: do NOT cancel early — let workers finish (or hit timeout) and
    collect every validated flag, so the coordinator can require N independent
    workers to agree before an UNATTENDED auto-submit. This is what makes
    AUTO_SUBMIT_CONSENSUS actually reachable.
"""
from __future__ import annotations

import concurrent.futures as cf
from collections import Counter

from .solver import solve


def race(chal: dict, model_specs: list[str], keys: dict, *,
         sandbox_image: str, host_workdir: str, network: str,
         max_steps: int, timeout: int, on_log=None,
         consensus: int = 1) -> tuple[str | None, dict, list[str]]:
    """
    Launch one solver per model in parallel.

    Returns (winning_flag_or_None, per_model_status, all_validated_flags).
    """
    statuses: dict[str, str] = {m: "running" for m in model_specs}
    all_flags: list[str] = []
    flag: str | None = None

    def _key_for(spec: str) -> str:
        provider = spec.split(":", 1)[0]
        return keys.get(provider, "")

    with cf.ThreadPoolExecutor(max_workers=len(model_specs)) as ex:
        futs = {
            ex.submit(
                solve, chal, spec, _key_for(spec),
                sandbox_image=sandbox_image,
                host_workdir=host_workdir,
                network=network,
                max_steps=max_steps,
                on_log=on_log,
            ): spec
            for spec in model_specs
        }
        try:
            for fut in cf.as_completed(futs, timeout=timeout):
                spec = futs[fut]
                try:
                    result = fut.result()
                except Exception as exc:  # noqa: BLE001
                    statuses[spec] = f"error: {exc}"
                    continue
                if result:
                    statuses[spec] = "solved"
                    all_flags.append(result)
                    if consensus <= 1:
                        # first valid flag wins — cancel the rest
                        flag = result
                        for other in futs:
                            if other is not fut:
                                other.cancel()
                        break
                else:
                    statuses[spec] = "no_flag"
        except cf.TimeoutError:
            for spec in model_specs:
                if statuses[spec] == "running":
                    statuses[spec] = "timeout"

    if consensus > 1 and all_flags:
        # pick the flag that >= `consensus` independent workers produced
        flag_counts = Counter(all_flags)
        best, count = flag_counts.most_common(1)[0]
        flag = best if count >= consensus else None
    elif consensus <= 1 and flag is None and all_flags:
        flag = all_flags[0]

    return flag, statuses, all_flags
