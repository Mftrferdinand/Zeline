# Daily Assistant: Briefing & Alerts [Zeline Zenith]

> Daily Assistant: Briefing & Alerts — modul Zeline Zenith (sumber: sk14).

# Load when: briefing, ringkasan harian, alert, kabarin kalau, pantau harga, alarm
# Category: Operations

## What this is

Bikin bot kerasa kayak asisten harian, bukan alat panggil-pakai:
- **Daily briefing** — push ringkasan tiap pagi tanpa diminta.
- **Alert engine** — trigger persisten "kabarin kalau ...".

This skill is an implementation blueprint. It does not bundle `briefing.py`,
`alerts.py`, or a notifier module. Build those components in the user's project
or connect an installed scheduler/notification integration. Prefer keyless data
sources where possible (DexScreener for prices, public RPC for gas).

Every interface and snippet below is **pseudocode** to implement in the target
project, not an importable module or preinstalled command.

Read-only / notify-only — gak ada yang sign tx, jadi gak nyentuh governor. Begitu sebuah alert mau MEMICU aksi dana (mis. "auto-swap pas dip"), aksinya tetap lewat governor + konfirmasi.

---

## Daily briefing

Ngekompos dari yang udah ada — section tanpa data di-skip:

```
💼 Portfolio   ← inject portfolio_provider (balanceOf multicall, keyless)
⛽ Gas         ← inject gas_provider (eth_gasPrice / feeHistory, keyless)
🔔 Alert aktif ← project alert store
🧠 Lesson      ← project memory provider
⏳ Masih open  ← project task provider
📝 Proposal    ← project proposal store
```

Implement providers and notification delivery explicitly in the target project;
do not assume a bundled Python module or environment-variable contract.

`once_per_day=True` (default) ada guard biar gak dobel kalau heartbeat sering. Jadwal: cron harian (sk2/sk4) atau scheduler in-process.

Schedule the implemented project entry point with the platform scheduler.

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
# PSEUDOCODE: implement AlertEngine in the target project.
ae = ProjectAlertEngine()
ae.add_rule("price_below", {"token": "0x...", "chain": "ethereum", "threshold": 2000},
            cooldown_s=3600, label="ETH dip")
ae.add_rule("gas_below", {"chain": "ethereum", "threshold_gwei": 10}, label="gas murah")

# loop poll (jalanin sebagai background task / service)
await ae.run(notifier, poll_interval_s=60, fetchers={"gas_fn": my_gas_fn})
```

**Dedup**: tiap rule punya `cooldown_s` — alert yang udah nyala gak refire sampai cooldown lewat. Gak spam.

**Sumber data keyless**: harga bisa dari DexScreener; gas & wallet activity
di-inject dari provider yang benar-benar diimplementasikan proyek.

---

## State and credentials

Choose project-local, gitignored paths for alert state and load notification
credentials from that project's secret store. Never assume Zeline defines
application-specific alert or notifier environment variables.

## Trigger phrases (router)

`briefing`, `ringkasan harian`, `tiap pagi`, `alert`, `kabarin kalau`, `notify kalau`, `pantau harga`, `pasang alarm`.
