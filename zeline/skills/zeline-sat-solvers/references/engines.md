# Engine identities, setup and deployment

The builtin skill ships independent Python adapters, not ten native executables. Availability is discovered on the actual host; `inspect` reports paths, while a real solve is the execution check. Do not infer installation from a wrapper filename or a GitHub link. Runtime requires Python's standard library plus at least one trusted native solver; Python Z3/PySAT bindings are not needed.

| Key / adapter | Executable | Official source / role |
|---|---|---|
| `z3` / `z3_solver.py` | `z3` | https://github.com/Z3Prover/z3 — SMT/SAT, DIMACS mode |
| `kissat` / `kissat_solver.py` | `kissat` | https://github.com/arminbiere/kissat — CDCL |
| `cadical` / `cadical_solver.py` | `cadical` | https://github.com/arminbiere/cadical — CDCL |
| `glucose` / `glucose_solver.py` | `glucose` | https://github.com/audemard/glucose — CDCL; MiniSat-style result file |
| `minisat` / `minisat_solver.py` | `minisat` | https://github.com/niklasso/minisat — CDCL; result file |
| `yalsat` / `yalsat_solver.py` | `yalsat` | https://github.com/arminbiere/yalsat — stochastic local search |
| `probsat_official` / `probsat_official_solver.py` | `probSAT`, fallback `probsat` | https://github.com/adrianopolus/probSAT — official stochastic implementation; not custom probsab |
| `cryptominisat` / `cryptominisat_solver.py` | `cryptominisat5`, fallback `cryptominisat` | https://github.com/msoos/cryptominisat — SAT/XOR engine, CNF-only adapter here |
| `bitwuzla` / `bitwuzla_solver.py` | `bitwuzla` | https://github.com/bitwuzla/bitwuzla — SMT engine, Boolean-CNF conversion here |
| `probsat` / `probsat_solver.py` | `probsab` | Operator-supplied custom worker; private source, not official probSAT |

## Native setup contract

1. Prefer a trusted OS package when available (Termux: the existing `z3` package), otherwise inspect the official upstream build guide/license before fetching and pinning source. Never download an arbitrary executable disguised as a solver.
2. Keep native sources/builds under a separate owner directory, e.g. `~/.local/share/zeline/solvers/src/`, outside the repository. Retain exact upstream commits, relevant dependency commits, licenses, patches and compiler logs. Check memory first and use one compilation job on constrained Android hosts; do not start a full upstream benchmark suite just to build a CLI.
3. Use the upstream build system: Make for probSAT/Glucose/MiniSat; CMake for CryptoMiniSat (configure its optional dependencies explicitly); Bitwuzla's documented Meson/configure flow and its declared SAT backend dependencies. These are not interchangeable `pip install` engines. Dependency fetching during a manually authorized build is separate from offline solve execution.
4. Place the verified executable under `~/.local/share/zeline/solvers/bin/` with its actual name. The official probSAT binary is `probSAT`, custom binary `probsab`; do not replace one with the other. Set `ZELINE_SAT_BIN_DIR` to a different trusted directory if desired. Keep paths private and preserve existing binaries until a new candidate has passed smoke tests.
5. Run a tiny positive/negative Boolean CNF fixture through the actual named adapter. Verify SAT models independently against every original clause, unused declared variables included. Exact engines may return UNSAT; stochastic exhaustion/timeout is not UNSAT. The adapters reject abnormal exits, invalid witnesses and late results.
6. Store a build receipt with source URL/commit, actual executable path/SHA256, compiler invocation, local patches and observed CLI/status/model format. Native Android binaries belong only in an explicitly architecture-specific companion artifact with upstream notices, never the universal Zeline wheel.

## Protocol differences

- Z3 is invoked with `-dimacs`.
- CryptoMiniSat is invoked with `--threads=1 --verb=0`; input is ordinary validated CNF, not XOR-extended DIMACS. XOR extraction/Gaussian elimination benefits must be benchmarked on the actual problem rather than inferred from its name.
- Official probSAT requires `-a` to print a witness and is given an explicit reproducible seed (`1`). It receives standard DIMACS, unlike custom `probsab`, which receives the supplied private header format and guarded width <=16. Parallel/multi-seed search is not silently enabled.
- MiniSat/Glucose receive a fresh per-attempt model filename. Never read an old `result.txt` from a previous solve.
- Bitwuzla receives generated `(set-logic QF_BV)` SMT-LIB containing Bool declarations for every declared variable, Boolean clauses and one `(check-sat)`. `--print-model` avoids issuing `(get-model)` after an UNSAT query. Parse Bool definitions, then verify the returned bitstring against the original CNF. Arbitrary SMT input, floating-point models and bit-vector model verification are not implemented by this adapter.

Known extra JSON theory fields (`xor`, `xors`, `xor_clauses`, `smt2`, `assertions`, `constraints`) are rejected rather than silently ignored. Export every required constraint as CNF; a verified partial CNF witness is not a verified SMT/XOR challenge.

## Why more engines does not guarantee faster solving

Local-search engines are useful candidates for satisfiable random/planted k-SAT; CDCL routes handle structured CNF and may prove UNSAT. SMT backends add different heuristics but do not turn cryptographic one-way functions into easy inversions. The default portfolio shares one budget sequentially among available engines, so adding engines reduces their individual slices. For a known workload, benchmark a specifically selected `--solver` with the same total deadline. Do not claim wins from tiny fixtures or invent successful challenge/mint receipts.

## GitHub/publication boundary

Zeline's independent adapters, docs, strict CNF parser and examples use the repository MIT license. External projects retain their own licenses; an API's ambiguous license label is not a substitute for reading LICENSE/COPYING and dependency notices. Original uploads and the derived custom C worker have no declared redistribution license: they remain intact privately, ignored by Git and excluded from public wheel/sdist package resources. Establish redistribution authorization before publishing those files. A public install can still use a separately supplied trusted `probsab` executable, and reports it unavailable otherwise.

Before push, inspect the exact candidate diff and fresh built wheel, scan for credentials/private payloads, run focused tests, and preserve unrelated dirty work. Passing solver checks does not mean the entire dirty framework is release-ready or that remote CI passed. No commit/push/release or gateway restart is performed by installing the skill.
