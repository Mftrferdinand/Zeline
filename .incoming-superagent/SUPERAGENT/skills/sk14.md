# sk14 — Daily Assistant: Briefing & Alerts (SUPERAGENT V7)
# Load when: briefing, ringkasan harian, alert, kabarin kalau, pantau harga, alarm
# Category: Operations

## What this is

Bikin bot kerasa kayak asisten harian, bukan alat panggil-pakai:
- **Daily briefing** — push ringkasan tiap pagi tanpa diminta.
- **Alert engine** — trigger persisten "kabarin kalau ...".

Scripts: `tools/briefing.py` + `tools/alerts.py`. Notifier reuse `monitoring.Notifier` (Telegram/Discord). Keyless di mana bisa (harga DexScreener, gas via RPC).

Read-only / notify-only — gak ada yang sign tx, jadi gak nyentuh governor. Begitu sebuah alert mau MEMICU aksi dana (mis. "auto-swap pas dip"), aksinya tetap lewat governor + konfirmasi.

---

## Daily briefing

Ngekompos dari yang udah ada — section tanpa data di-skip:

```
💼 Portfolio   ← inject portfolio_provider (balanceOf multicall, keyless)
⛽ Gas         ← inject gas_provider (eth_gasPrice / feeHistory, keyless)
🔔 Alert aktif ← alerts.py
🧠 Lesson      ← memory_engine (lesson terbaru)
⏳ Masih open  ← memory_engine (blocker/decision belum kelar)
📝 Proposal    ← reflection.py (yang nunggu review)
```

```python
from briefing import push_briefing
from memory_engine import MemoryEngine
from alerts import AlertEngine
from monitoring import Notifier   # dari skills/hermes/scripts

notifier = Notifier(telegram=(os.environ["HERMES_TG_BOT_TOKEN"], os.environ["HERMES_TG_CHAT_ID"]))
await push_briefing(notifier, memory_engine=MemoryEngine(), alert_engine=AlertEngine(),
                    portfolio_provider=my_portfolio_fn, gas_provider=my_gas_fn)
```

`once_per_day=True` (default) ada guard biar gak dobel kalau heartbeat sering. Jadwal: cron harian (sk2/sk4) atau scheduler in-process.

```bash
# contoh cron jam 7 pagi WIB
0 7 * * *  cd ~/superagent-v3 && python -c "import asyncio; from tools.briefing import ..."  # sesuaikan
```

---

## Alert engine

Trigger persisten di SQLite. Sekali set, jalan terus.

| kind | params | contoh |
|---|---|---|
| `price_below` / `price_above` | token, chain, threshold | ETH < $2000 |
| `gas_below` / `gas_above` | chain, threshold_gwei | gas < 10 gwei |
| `wallet_activity` | wallet, chain | whale gerak |
| `claim_window` | label, opens_ts | airdrop claim buka |
| `custom` | expr | kondisi sendiri |

```python
from alerts import AlertEngine
ae = AlertEngine()
ae.add_rule("price_below", {"token": "0x...", "chain": "ethereum", "threshold": 2000},
            cooldown_s=3600, label="ETH dip")
ae.add_rule("gas_below", {"chain": "ethereum", "threshold_gwei": 10}, label="gas murah")

# loop poll (jalanin sebagai background task / service)
await ae.run(notifier, poll_interval_s=60, fetchers={"gas_fn": my_gas_fn})
```

**Dedup**: tiap rule punya `cooldown_s` — alert yang udah nyala gak refire sampai cooldown lewat. Gak spam.

**Sumber data keyless**: harga default dari DexScreener (free, no key); gas & wallet activity di-inject (eth RPC / monitoring.py). Semua bisa di-override dengan fetcher sendiri.

---

## Env var

```bash
export HERMES_ALERTS_DB=~/.hermes/alerts.db
export HERMES_BRIEFING_STATE=~/.hermes/briefing-last.txt
# Notifier pakai HERMES_TG_BOT_TOKEN / HERMES_TG_CHAT_ID / HERMES_DISCORD_WEBHOOK (udah ada)
```

## Trigger phrases (router)

`briefing`, `ringkasan harian`, `tiap pagi`, `alert`, `kabarin kalau`, `notify kalau`, `pantau harga`, `pasang alarm`.
