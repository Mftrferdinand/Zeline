# REVIEW — CTF Agent Package → SUPERAGENT integration

Review of the operator-authored `ctf-agent-skills` package and the changes made
while integrating it into SUPERAGENT v7.0 (router skill **sk32**, category
sub-skills **sk43–sk47**, runtime under `tools/ctf/`). Version unchanged (v7.0).

## Verdict
Strong, well-structured package: clean orchestrator → category split, a real
full-auto coordinator (CTFd poll → multi-model swarm in Docker sandbox →
flag validate → submit), and the right safety primitives (scope allowlist,
flag validator, sandbox isolation, HITL). Below are the issues found and fixed.

## Bugs / correctness fixes
1. **Consensus path unreachable (coordinator + swarm).** `swarm.race()` returned
   only the first valid flag and cancelled the other workers, so
   `cs.flags_found` never held ≥2 distinct candidates → `AUTO_SUBMIT_CONSENSUS>=2`
   could never be satisfied; every flag silently fell through to the HITL queue.
   *Fix:* `race()` is now consensus-aware — `consensus<=1` keeps first-flag-wins,
   `consensus>=2` collects all workers' flags and uses a `Counter` to require N
   independent agreements. Returns `(flag, statuses, all_flags)`; coordinator
   records every candidate and enforces consensus before auto-submit.

2. **`flag_validator.validate()` too loose.** It accepted a candidate if the
   pattern matched *anywhere* (`re.search`), so junk-wrapped text like
   `"see flag{x} here"` validated as a flag. *Fix:* `validate()` now requires a
   `re.fullmatch`. Scraping flags out of blobs is the job of `extract_all()`.

3. **`scope_guard` needless DNS for IP literals.** It resolved the host before
   checking whether it was already an IP literal. *Fix:* literal-IP check first,
   resolve only real hostnames.

## Portability / config fixes
4. **`rsa_attacks.py` hard-required `gmpy2`** (present only inside the Docker
   sandbox, not on a vanilla host) → module failed to import for offline use/tests.
   *Fix:* `gmpy2` optional with a pure-Python fallback (`_iroot` integer e-th root
   via binary search, `_invert` via `pow(a, -1, m)`). All call sites routed through
   helpers. Same results, just slower on huge numbers.

5. **Import / scope paths.** After moving `scope_guard.py` and `flag_validator.py`
   up one level (`tools/ctf/`), the coordinator/solver importlib paths and the
   `SCOPE_PATH` default were rewired to match.

6. **Placeholder model IDs.** `MODELS` defaulted to non-existent names
   (`claude-opus-4-8`, …). Set to real-looking IDs with a comment to replace per
   provider; documented in `.env.example`.

7. **Sandbox Dockerfile tool gaps.** Several tools referenced in the playbooks
   were missing from the image. Added `radare2`, `john`, `exiftool`, `ropper`,
   `hashpumpy`, and `jwt_tool`; documented heavier web/forensics tools
   (ffuf/feroxbuster/nuclei/sqlmap/stegseek/ghidra) to add per-event so the base
   image stays slim.

## Notes (both since resolved)
- ~~LLM adapters: only Anthropic is fully implemented; OpenAI/Gemini are stubs.~~
  **Resolved**: OpenAI (Chat Completions) and Gemini (generateContent) adapters
  are now fully implemented in `coordinator/llm.py` — pure-REST tool calling,
  canonical Anthropic-shaped history converted per provider, offline-tested
  converters, normalized `tool_use` stop reason. Cross-provider racing works.
- ~~Web challenges whose target lives in the description may skip the scope
  pre-check.~~ **Resolved**: `coordinator.extract_targets()` now scans the
  description for URLs; every target (connection_info + description) is
  scope-checked before the sandbox gets network.

## Tests
`tools/tests/test_ctf_swarm.py` — 24 offline tests covering `scope_guard`
(allow/deny/wildcard/CIDR/IP-literal/parse), `flag_validator`
(valid/placeholder/junk/extract), and `rsa_attacks`
(small-e/Fermat/Wiener/common-modulus/decrypt). Online runtime is import-checked.
All green under `-W error::ResourceWarning`.
