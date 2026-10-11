# Cost Telemetry

Catat berapa token yang dipakai dan berapa estimasi biayanya — per panggilan
LLM maupun per tool — lalu tampilkan sebagai laporan. Modulnya
`zeline/cost.py`.

## Cara kerja singkat

1. Setiap panggilan LLM / tool dicatat satu baris JSON ke jurnal append-only
   `~/.zeline/cost_journal.jsonl`:

   ```json
   {"timestamp":"2026-10-11T03:58:43.123456+00:00","identity":"cli:local",
    "kind":"llm","tool_name":"__llm__","model":"gpt-4o",
    "input_tokens":2000,"output_tokens":1000,"cost_usd":0.015,"duration_ms":250.0}
   ```

   Yang disimpan **hanya metadata** (waktu, identitas, nama model/tool, jumlah
   token, biaya, durasi). Isi prompt, argumen tool, dan respons model **tidak
   pernah** ditulis ke jurnal.

2. Biaya dihitung dari tabel harga per 1 juta token (USD). Prioritas sumber
   harga, dari tertinggi:

   1. `cost.set_model_prices({"gpt-4o": (2.5, 10.0)})` — override runtime.
   2. Environment variable `ZELINE_PRICE_<MODEL>` — contoh
      `ZELINE_PRICE_GPT_4O="2.5,10.0"` menimpa harga `gpt-4o`.
   3. Config `agent.model_prices` — contoh
      `{"gpt-4o": {"input": 2.5, "output": 10.0}}` (harga kontrak operator).
   4. Estimasi bawaan `zeline.cost.MODEL_PRICES`.

   ⚠️ **Harga bawaan adalah ESTIMASI dari harga publik yang diketahui dan bisa
   stale** — jangan dipakai untuk penagihan. Kalau model tidak punya harga di
   semua sumber, biayanya dicatat `$0` dan dihitung sebagai `unpriced_calls`
   di laporan (jujur, bukan ditebak).

3. Fungsi `record_llm_call()` / `record_tool_call()` bersifat **best-effort**:
   tidak pernah melempar exception ke pemanggil. Kalau disk penuh atau jurnal
   tidak bisa ditulis, catatannya hilang — jawaban user tetap aman.

## Pakai dari chat (tool agent)

- `cost_report` — ringkasan biaya per periode (`period`: `day`/`week`/`month`).
- `cost_today` — alias cepat, sama dengan `cost_report` periode `day`.

Contoh balasan tool:

```
💰 Biaya Zeline — 24 jam terakhir
Total: $0.183 · 48.2k token (32.1k in / 16.1k out) · 57 panggilan
⚠️ 3 panggilan tanpa harga (model tidak ada di tabel harga — biaya tercatat $0).

Termahal:
  • __llm__: $0.121 (41x)
  • web_search: $0.038 (9x)
  • read_file: $0.024 (7x)
```

## Pakai dari CLI

```bash
zeline cost today                  # ringkasan 24 jam terakhir
zeline cost report                 # sama dengan di atas
zeline cost report --period week   # 7 hari terakhir
zeline cost report --period month  # 30 hari terakhir
```

## Pakai dari Python

```python
from zeline import cost

# Catat (tidak pernah raise)
cost.record_llm_call("cli:local", "gpt-4o", 2000, 1000, 250.0)
cost.record_tool_call("cli:local", "web_search", "gpt-4o", 120.0)

# Agregat mentah
summary = cost.cost_summary("week")
print(summary["total_usd"], summary["by_tool"])

# Harga
print(cost.cost_usd("deepseek-chat", 1_000_000, 500_000))  # estimasi USD
cost.set_model_prices({"my-model": (1.0, 2.0)})           # override sesi ini
```

`cost_summary()` membaca jurnal secara streaming (baris per baris), jadi aman
untuk jurnal besar. Periode yang tidak dikenal melempar `CostError`.

## Menimpa harga secara permanen

Cara paling tepat: isi `agent.model_prices` di config Zeline dengan harga
kontrak/provider yang benar-benar dipakai:

```json
{ "agent": { "model_prices": { "gpt-4o": {"input": 2.5, "output": 10.0} } } }
```

Untuk sekali jalan tanpa ubah config, pakai env:

```bash
ZELINE_PRICE_DEEPSEEK_CHAT="0.27,1.10" zeline cost today
```

## Perbedaan dengan `zeline stats` / `usage_stats`

`usage_stats` mencatat **token mentah per model per hari** (SQLite) dan hanya
menampilkan biaya bila operator mengisi harga sendiri. Cost telemetry mencatat
**event per panggilan** (termasuk tool non-LLM) dalam format JSONL yang mudah
dibaca manusia, menghitung biaya dari tabel estimasi, dan fokus pada
JURNAL + BIAYA + LAPORAN — bukan duplikasi accounting token.
