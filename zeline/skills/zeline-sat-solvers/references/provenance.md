# Provenance and review

## Operator-provided archives

`assets/provenance.json` records the original ZIP digests and every retained member digest. All 13 regular files from the three uploads were retained byte-for-byte under separate `assets/supplied/` directories so similarly named files are not overwritten:

- `sat_solver`: original `sat_solver_standalone/sat_solver.py` and README.
- `nonce-synced-v1-v3`: interpreter-corrected `nonce_solver_v3.py`, custom `probsab.c`, generator, README and original MCP/attempt runner scripts.
- `nonce_solver_v3`: the other original v3, `probsab.c`, generator and README.

No declared redistribution license was included in these uploads. Their retention is for this operator's private integration and provenance, not a claim of MIT licensing or permission for public redistribution. Git ignores `assets/supplied/` and `assets/adapted/`; package discovery and resource exclusions keep them out of public wheels. Public installs intentionally lack these optional extras, while this owner's complete copies are preserved. Do not publish these supplied assets, or the custom derived C source, without establishing authorization. The independent wrappers do not change the original files' rights.

## Identified issues in the original uploads

- Standalone Z3 implementation claimed DIMACS support but only loaded JSON. Its variable count inference fails for empty formulas; no independent all-clause verification or time bound was implemented.
- The two v3 files differ in interpreter selection and Kissat discovery. One hard-codes another machine's Python path. Both depend on Python SAT bindings for several routes.
- The v3 race can spend the entire component budget waiting for exited/failed workers, starving fallback routes. `kill()` does not reap workers, result writers retain unmanaged handles, and all component time accounting is not an end-to-end solve deadline.
- Sparse/unused-variable metadata and empty clauses are not handled safely by the originals. Statuses conflate unavailable workers and unsolved instances rather than proving UNSAT.
- The supplied C engine is a **custom probSAT-style implementation**, not the official distribution. Its fixed `w[16]`/`vars[16]` arrays can overflow on wider clauses; input dimensions, literal indices, empty clauses and unterminated clauses were not adequately checked.
- The uploaded runner is tied to a historical attempt, fixed 384-variable/6,912-clause metadata and `/tmp` state paths. The MCP helper can read a session and call arbitrary tools immediately. Neither belongs in automatic skill initialization or local SAT solving.
- Benchmark timings, successful mints, source deployment addresses and dated historical claims in the uploaded READMEs were not independently reproduced or verified.

## Adapted C worker

`assets/adapted/probsab.c` retains the supplied local-search algorithm and original filename. It adds rejection before fixed-array access for widths greater than 16, dimension and literal-range validation, empty-clause rejection in the raw C worker, and required clause terminators. The Python CNF layer handles empty-clause contradictions before this worker, normalizes duplicate literals/tautologies, supplies validated data, independently checks models and applies its own process deadline. These guards are not a general-purpose audit of C allocation failures or a proof of native-code safety.

Original and adapted source hashes, exact compile command, architecture, binary digest and offline smoke evidence are retained in the owner-local `~/.local/share/zeline/solvers/build-receipts/probsab.json`. That custom binary is not disguised as official probSAT and is not automatically downloaded or executed by loading the skill.

## External engines

Z3 uses the host package-manager executable. YalSAT, Kissat and CaDiCaL are separate official-source builds, with exact upstream commits, retained licenses and binary digests in the owner's companion solver directory. Their licenses apply independently to the wrappers. Do not bundle host-specific binaries into a platform-independent Zeline wheel; a separate owner-local architecture-specific distribution must retain each upstream license and its build receipt.

Glucose, MiniSat, official probSAT, CryptoMiniSat and Bitwuzla have separate named adapters and official source links in [engine setup](engines.md). They indicate compatibility only unless `inspect` and actual solve tests prove their executables are present. CryptoMiniSat is single-threaded; Bitwuzla converts Boolean CNF to SMT-LIB and does not implement arbitrary bit-vector model verification. Official probSAT remains distinct from private custom probsab. Independent adapter code follows the repository MIT license; no external solver source or license is silently relicensed. No Python `z3` or `pysat` installation is claimed by the CLI-only integration.

## Verification boundary

Synthetic CNF tests establish parsing, normalization, solver invocation, SAT model correctness, status handling, deadlines and local output behavior on this host. They do not establish that an arbitrary difficult NFT instance is satisfiable, finishes in a deadline, passes server admission, is submitted, or mints an NFT. An exact solver UNSAT result is not independently certified: no DRAT/LRAT checker is included.

An official-source build receipt is provenance evidence for the local binary, not a third-party security audit. Solving never authorizes wallet actions or public release of user-provided code.
