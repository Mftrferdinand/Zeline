---
name: zeline-sat-solvers
description: Local Boolean CNF challenge solving with Z3, YalSAT, Kissat, CaDiCaL, Glucose, MiniSat, official probSAT, CryptoMiniSat, Bitwuzla and the operator-supplied custom probsab worker. Discover actual installed engines, independently verify every original clause, preserve declared variables and share one deadline. No wallet access, signature, submission or minting.
license: MIT for independent adapters; external engines and private uploads have separate rights
version: 1.1.0
---

# Zeline SAT Solvers

Use for an authorized NFT/NONCE/AgentID puzzle that supplies **Boolean CNF** and expects a bitstring assignment. This is not a generic hash/PoW breaker, Binance endorsement, mint bot, or guarantee an NP-complete instance finishes in time. Load [usage](references/usage.md), [engine setup](references/engines.md) and [provenance](references/provenance.md). Use the actual absolute directory returned by `load_skill`, not an assumed checkout.

## Commands

```text
python <skill-directory>/scripts/portfolio.py inspect
python <skill-directory>/scripts/portfolio.py solve <input.json-or-cnf> --solver auto --timeout 30 --vars <declared-count> --out <new-private-file>
python <skill-directory>/scripts/<engine>_solver.py <input.json-or-cnf> --timeout 30 --vars <declared-count>
```

Named engines/adapters:

- `z3`, `kissat`, `cadical`, `glucose`, `minisat`: exact Boolean CNF solver routes.
- `yalsat`, `probsat_official`: stochastic SAT-witness search; **no UNSAT proof**.
- `cryptominisat`: exact CryptoMiniSat CLI, forced to one thread. Its native XOR support does not mean this CNF adapter reconstructs or accepts XOR input.
- `bitwuzla`: exact SMT backend via generated Boolean SMT-LIB and validated Boolean models. This adapter does **not** accept arbitrary raw SMT-LIB, bit-vector/floating-point formulas or claim to verify those models.
- `probsat`: compatibility key for the supplied **custom** `probsab` executable, not official probSAT. `probsat_official` resolves `probSAT`/`probsat`, separately from `probsab`.

Each adapter is `<engine>_solver.py`, including `probsat_official_solver.py` and `cryptominisat_solver.py`. A listed adapter is not proof its executable is installed: run `inspect` and exercise the exact backend. Auto uses only available backends, bounded sequential slices and one total deadline, not an uncontrolled parallel race. All default execution is offline and CPU-based.

The supplied v3 command shape is preserved by the independently written `nonce_solver_v3.py <input> [timeout] --out <new-file> --vars <declared-count>`. It delegates to this portfolio, not to the archived v3 algorithm. Portfolio/named adapters print JSON; `--out` is only a new 0600 verified bitstring. The v3 compatibility command prints bits on stdout and status on stderr.

## Challenge workflow

1. Get the exact fresh authorized challenge, complete fragment set, declared variable count, attempt identifier and expiry. Do not reuse historical 384/1,536-variable examples, attempt paths or inferred contract addresses.
2. Save the validated input outside installed skill resources. Preserve unused variables using the declared count. Read only regular files; JSON/DIMACS operational size/count limits apply.
3. Reserve submission/verification time. The solve budget covers all backend attempts and parsing; OS startup, file I/O and cleanup are not real-time guarantees. Refuse results observed after the deadline.
4. Accept only `SAT`, `verified=true`, correct bit count and a bitstring satisfying **every original clause**. Never accept exit code alone or a stale shared output. Existing output paths and input aliases are rejected.
5. `TIMEOUT`, `UNKNOWN`, `UNAVAILABLE`, malformed data and invalid models are not UNSAT. Stochastic engines cannot prove UNSAT. Exact-backend UNSAT is a solver verdict without independent DRAT/LRAT verification.
6. Hand a verified artifact to a **separately authorized** submission flow. Solving is not signing, submitting, minting or an on-chain receipt.

## Installation, publication and boundaries

- This skill never downloads, compiles, updates or starts a service on load. Executable lookup is owner-controlled `ZELINE_SAT_BIN_DIR`, `~/.local/share/zeline/solvers/bin/`, then PATH. See engine setup for reviewed upstream URLs and build requirements.
- Independent adapters, metadata and examples can ship with Zeline. Do not put Android binaries into a platform-independent wheel.
- All 13 uploaded originals and the adapted custom C source remain intact in this owner's local integration under `assets/supplied/` and `assets/adapted/`. Those optional private extras are **excluded from Git and public wheels** pending redistribution authorization; their absence in a public install is expected, not a failed installation. Their checksums remain in `assets/provenance.json`.
- Never execute uploaded MCP/attempt runners as an install or solve step. They contain historical dimensions, host paths and authenticated calls; they are not wired into this portfolio. The original v3 has separate Python dependencies and incomplete timeout behavior and is not the default engine.
- No HTTP/MCP/wallet calls, session reading, authentication, signature, submission, mint, trading, polling or updater occurs in these solve entrypoints. Load `binance-agentic-wallet` only for a separately requested wallet operation; preserve sessions and require per-transaction approval.
