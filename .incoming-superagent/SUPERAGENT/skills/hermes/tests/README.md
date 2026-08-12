# Hermes crypto scripts — test suite

Deterministic, **offline** tests for the pure logic in `../scripts`. No network,
no API keys, and no heavy installs required.

## How it runs without web3 / eth_account / httpx

The production scripts import heavy 3rd-party packages, but those are only used
*inside function bodies* (every module uses `from __future__ import annotations`,
so type hints are never evaluated at import time). `_bootstrap.py` installs a
lightweight import hook that fabricates stub modules for those packages, so the
modules import cleanly and we can exercise their pure helpers.

Real behaviour (no stubs) is tested where the logic is pure stdlib:
`governor.py`, signature/SIWE helpers, mempool filter, RPC failover, jitter, etc.

## Run

```bash
./run_tests.sh          # or: python3 -m unittest discover -p 'test_*.py'
./run_tests.sh -v       # verbose
pytest                  # optional, if installed
```

## Coverage map

| Test file | Module under test | What it locks down |
|---|---|---|
| `test_governor.py` | `governor.py` | allow/block/halt verdicts, per-tx & daily & session caps, per-wallet accounting, rate-limit trip, kill-switch reset, simulation gate |
| `test_web3_connect.py` | `web3_connect.py` | nonce length/alphabet, 65-byte signature split, SIWE message rendering + expiry |
| `test_swap_engine.py` | `swap_engine.py` | explorer URL builder, native sentinel, `SwapResult` default-list safety |
| `test_nft_engine.py` | `nft_engine.py` | `NFTResult` default-list safety, Reservoir chain map |
| `test_bridge_engine.py` | `bridge_engine.py` | `_addr_to_bytes32` left-padding |
| `test_monitoring.py` | `monitoring.py` | `RPCRouter` round-robin failover + exhaustion |
| `test_monitoring_advanced.py` | `monitoring_advanced.py` | `MempoolFilter` matching (to/selector/value/custom), danger selectors |
| `test_airdrop_runner.py` | `airdrop_runner.py` | jitter bounds, `RunState` dedupe, `TaskSpec` default-dict safety |

## Adding tests

1. Start the file with `import _bootstrap  # noqa: F401`.
2. Import the target module by its flat name (e.g. `import governor`).
3. Prefer testing pure functions; for network/chain calls, inject fakes
   (see `test_monitoring.py`'s `r.w3 = lambda: None`).
