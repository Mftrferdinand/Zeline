# Solver usage

## Names and roles

| File | Actual engine | Kind |
|---|---|---|
| `z3_solver.py` | Z3 command-line executable | Complete SMT/SAT engine used here for Boolean CNF |
| `yalsat_solver.py` | YalSAT | Stochastic local search; SAT witnesses only |
| `kissat_solver.py` | Kissat | Complete CDCL SAT solver |
| `cadical_solver.py` | CaDiCaL | Complete CDCL SAT solver |
| `probsat_solver.py` | Supplied custom `probsab` executable | probSAT-style stochastic local search; not official probSAT |
| `glucose_solver.py` | Glucose executable, if installed | Optional complete CDCL solver |
| `minisat_solver.py` | MiniSat executable, if installed | Optional complete CDCL solver |
| `probsat_official_solver.py` | Official `probSAT`/`probsat` | Stochastic local search; separate from custom probsab |
| `cryptominisat_solver.py` | `cryptominisat5`/`cryptominisat` | Complete SAT engine, single-threaded CNF route |
| `bitwuzla_solver.py` | `bitwuzla` | Complete SMT engine through generated Boolean SMT-LIB |
| `portfolio.py` | Available engines above | Bounded sequential orchestration and independent SAT checking |

The original upload's `sat_solver.py` used Python Z3 but did not implement its advertised DIMACS support. The active adapter is named `z3_solver.py`, uses the actual Z3 executable and needs no Python `z3-solver` package. The original file remains unchanged under the owner's optional private `assets/supplied/sat_solver/` extras; uploads are not part of the public package.

## Output and v3 compatibility

The portfolio and named adapters print JSON status to stdout. `--out` writes **only a verified bitstring**, never JSON or an UNSAT/timeout placeholder, using a new owner-private file (0600). Existing destinations and input aliases are rejected. A nondecision/error exits nonzero; always inspect the status too.

The original v3 command shape is available as:

```text
python /absolute/skill/path/scripts/nonce_solver_v3.py clauses.json 30 --vars 384 --out new-assignment.txt
```

This compatibility entrypoint delegates to the new verified CLI portfolio, **not the unchanged archived v3 algorithm**. It prints only the accepted bitstring to stdout and metadata to stderr; UNSAT exits 20, other failures exit 2. Its default is 30 seconds rather than the original 270. The `--solver` option can select a specific real backend.

The deadline includes parsing and is shared among sequential solver slices; startup, serialization, verification, OS scheduling and cleanup are not real-time guarantees. Results observed after the total deadline are refused. Always recheck the actual protocol expiry before any separately authorized submission.

## Input

JSON signed integer literals, variable numbering starting at one:

```json
{"num_vars": 4, "clauses": [[1], [-1, 2], [3]]}
```

A raw list `[[1], [-1, 2], [3]]` is also accepted. Pass `--vars 4` when the raw list does not declare the unused fourth variable. Never truncate unused variables from an NFT challenge's required assignment.

Equivalent DIMACS:

```text
p cnf 4 3
1 0
-1 2 0
3 0
```

JSON zero literals, booleans, floats, out-of-range variables and malformed DIMACS are errors. A zero in DIMACS is only the clause terminator. An empty clause is a contradiction, not a malformed stochastic input. Duplicate literals and tautologies are normalized before external execution, with final verification against the original input.

## Examples

```text
python /absolute/skill/path/scripts/portfolio.py inspect
python /absolute/skill/path/scripts/portfolio.py solve /private/work/challenge.json --solver auto --timeout 30 --vars 1536 --out /private/work/new-assignment.txt
python /absolute/skill/path/scripts/kissat_solver.py /private/work/challenge.cnf --timeout 30
```

The number 1,536 is an example, not a protocol constant. Never turn an old timeout of 270/900 seconds into an assumed fresh challenge deadline. Do not accept a returned assignment until `status=SAT`, `verified=true`, the bit count matches the declared count, and no deadline has expired.

## Dependencies and local installation

See [engine setup](engines.md) for all ten backend identities, official URLs, exact CLI differences, native-build and publication boundaries. All backends are optional executable dependencies; their listing here is not host availability evidence.

The companion directory is `~/.local/share/zeline/solvers/bin/`. Set `ZELINE_SAT_BIN_DIR` to another owner-controlled directory if required. Only install official, reviewed source builds; an executable filename alone does not establish provenance. Build receipts and retained upstream LICENSE files live beside local source builds, outside the Zeline repository and Python package.

- Z3 is provided by the host's official package manager; on Termux the package is `z3`.
- YalSAT: <https://github.com/arminbiere/yalsat>.
- Kissat: <https://github.com/arminbiere/kissat>.
- CaDiCaL: <https://github.com/arminbiere/cadical>.
- The adapted custom engine can be compiled locally with `clang -O2 <skill-directory>/assets/adapted/probsab.c -lm -o <owner-binary-directory>/probsab`. Its original license was not declared; do not publish it without authorization.

Do not install Python `pysat` expecting SAT tooling: the commonly intended SAT package is `python-sat`, which is optional and not required by the active CLI portfolio. The uploaded v3's `cd195`/`g4` names refer to PySAT engines, not a claim those Python dependencies are installed here.

## Meaning of results

- SAT: bitstring independently satisfies every original clause. This is a verified witness, not a mint receipt.
- UNSAT: exact-backend verdict or trivial contradiction preprocessing; no independent DRAT/LRAT certificate verification is implemented.
- TIMEOUT/UNKNOWN: no accepted witness within the local budget. This does not mean the formula is unsatisfiable.
- UNAVAILABLE: the requested engine is not installed/discoverable.
- ERROR: invalid input, invalid output, refused destination or worker failure. Never submit a stale output file after any failure.

All tests must be labeled synthetic/offline unless the exact fresh authorized challenge was actually provided and solved. Upstream README benchmark claims are not current measurements.
