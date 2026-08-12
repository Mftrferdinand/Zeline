# sk30 — Client Revenue Engine (SUPERAGENT V7)
# Load when: client revenue, garapan, harvest, scrape API, anti-browser, parse curl, mass akun
# Category: Business & Monetization

## DOCTRINE — browser is the LAST resort
Garapan bulk = lakuin lewat **kode & API**, bukan klik-klik browser. Headless browser cuma kalau:
1. Data cuma ada di DOM yang dirender JS, **DAN**
2. Gak ada API/endpoint internal yang bisa direplikasi.

Decision tree tiap task:
```
Ada API resmi?            → pakai itu (httpx) ........................... STOP
Situs manggil XHR/fetch?  → tiru request-nya (DevTools → Copy as cURL → parse_curl) ... STOP
Butuh login / JS berat?   → headless browser SEKALI buat ambil token/cookie → balik ke API
Render-only, no API?      → headless browser (H8 / browser_engine) — pilihan TERAKHIR
```

## NET-NEW TOOLS (v4.1.2)
- `tools/api_harvester.py` — `parse_curl()` (DevTools → RequestSpec), `paginate_offset` / `paginate_cursor`, `extract()` (JSON path `data.items[*].addr`), `to_jsonl` / `to_csv`, `send()` (httpx, lazy import).
- `tools/revenue_engine.py` — `BulkRunner` (concurrency + retry/backoff + **checkpoint-resume** + dedupe), `TokenBucket` (rate limit), `Checkpoint`.

Pola anti-browser standar:
1. DevTools → Network → temukan call data → **Copy as cURL**.
2. `spec = parse_curl(curl)` → replikasi di kode.
3. `paginate_*` buat semua halaman; `extract()` buat ambil field.
4. Bungkus di `BulkRunner` → rate-limit + resume kalau putus.

## SCOPE & DELEGATION (sk30 = orkestrator, BUKAN duplikat)
| Pattern | sk30 nyetir | Delegasi ke |
|---|---|---|
| **A. Harvest / scrape API** (anti-browser) | parse_curl + paginate + extract + BulkRunner | sk6 (API), sk5 (data shape), H8/browser_engine (last resort) |
| **B. Mass akun / airdrop / on-chain** | BulkRunner + Checkpoint (resume) + dedupe | sk10 + hermes (`wallet_manager`, `airdrop_runner`) — **WAJIB lewat `governor.py`** |
| **C. Integrasi API & otomasi job klien** | RequestSpec + retry + provider cascade | sk6 (webhook/SDK), sk16 (scaffold service), sk4 (schedule) |

Kalau task murni 1 domain (mis. cuma batch tanpa API) → jangan load sk30, pakai sk12/sk16 langsung. sk30 nyala pas garapannya **API-first + bulk + anti-browser**.

## SAFETY RAILS (non-negotiable)
- On-chain apa pun di Pattern B → lewat **Spend Governor** (caps + kill-switch). Gak ada bypass walau `auto_confirm`.
- Hormati rate-limit & ToS target; default `TokenBucket` konservatif — jangan hammer.
- Jangan harvest data pribadi/PII di luar izin klien. Simpan kredensial klien di vault (`hermes/references/security.md`), bukan hardcode.
- Resume-from-checkpoint harus **idempotent** — jangan double-submit / double-charge.

## QUICKSTART
```bash
bash tools/tests/run_tests.sh      # buktiin engine-nya hijau (offline, stdlib)
```
```python
from api_harvester import parse_curl, paginate_offset, extract, to_jsonl
from revenue_engine import BulkRunner, TokenBucket, Checkpoint

spec = parse_curl(open("call.curl").read())     # DevTools → Copy as cURL
# build fetch(offset, limit) dari spec pakai httpx, lalu:
rows = list(paginate_offset(fetch, limit=100))

runner = BulkRunner(
    worker,                                     # fn(task) -> hasil
    max_workers=8,
    rate=TokenBucket(5),                        # 5 req/detik
    checkpoint=Checkpoint("state.jsonl"),       # resume kalau putus
)
report = runner.run(rows)
print(len(report.succeeded), len(report.failed), len(report.skipped))
```

→ Next: tinggal isi `fetch()` / `worker()` sesuai garapan.
🔧 Upgrade: combo sk4 buat jadwalin harian, sk8 buat ekspor hasil ke XLSX klien.
