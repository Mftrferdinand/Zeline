# tools/ctf — Autonomous CTF Swarm (whitehat)

Full-auto CTF-solving framework wired into SUPERAGENT. The router skill is
**m32** (CTF/whitehat); the category playbooks are **m43–m47**
(web · pwn · rev · crypto · forensics). This directory holds the executable
runtime + the safety/utility helpers those skills call.

> Original package authored by the original author; integrated into
> SUPERAGENT v4.2 (paths rewired, consensus + flag-validator fixes,
> gmpy2-optional, offline test suite added). See `REVIEW.md` at repo root.

## Layout
```
tools/ctf/
├── scope_guard.py        # authorization allowlist — the whitehat line (offline)
├── flag_validator.py     # anti-hallucination flag check (offline, strict)
├── rsa_attacks.py        # offline RSA math (gmpy2 optional → pure-Python fallback)
├── scope.example.json    # copy → scope.json per event, set allowed hosts
├── run.py                # entrypoint: run | approve | status
├── requirements.txt      # host deps (requests, python-dotenv); solver tools live in sandbox
├── .env.example          # config template
├── sandbox/Dockerfile.sandbox   # isolated toolchain image
├── templates/
│   ├── pwn_template.py            # pwntools exploit skeleton
│   └── angr_solver_template.py    # angr symbolic-exec skeleton
└── coordinator/          # FULL-AUTO RUNTIME
    ├── config.py            env-driven config
    ├── ctfd_client.py       CTFd list/fetch/download/submit
    ├── coordinator.py       poll loop + dispatch + flag handling
    ├── swarm.py             race N models per challenge (consensus-aware)
    ├── solver.py            single-model tool-use agent loop
    ├── llm.py               provider adapters (Anthropic + OpenAI + Gemini, REST/zero-SDK)
    ├── sandbox.py           per-challenge Docker container
    └── state.py             resumable JSON state
```

## Quick start
```bash
cd tools/ctf
pip install -r requirements.txt
docker build -f sandbox/Dockerfile.sandbox -t ctf-sandbox .
cp .env.example .env                 # fill CTFD_URL, CTFD_TOKEN, API keys, real MODELS
cp scope.example.json scope.json     # set authorized hosts for THIS event

python3 run.py            # start poll/solve loop
python3 run.py status     # per-challenge state
python3 run.py approve    # submit all HITL-queued flags (operator action)
```

## Safety (why this stays whitehat)
- **Scope-locked** — `scope_guard.py` refuses any host not on the allowlist; deny rules win.
- **Sandboxed** — challenge binaries run only inside the Docker container, default `--network none`.
- **Verified flags** — `flag_validator.py` requires a full format match and rejects placeholders; flags are scraped from target output, never invented.
- **HITL by default** — `HITL_SUBMIT=true` queues flags for operator approval. Unattended auto-submit requires `AUTO_SUBMIT_CONSENSUS` independent workers to agree.
- Aligns with SUPERAGENT m32 R9 gate: foreign / out-of-scope targets are held pending operator authorization.

## Offline tests
`tools/tests/test_ctf_swarm.py` covers scope_guard, flag_validator, and rsa_attacks
(the deterministic, zero-network pieces). The online runtime is import-checked.
```bash
python3 -m unittest tools.tests.test_ctf_swarm
```
