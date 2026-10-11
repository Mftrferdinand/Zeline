# Tool Router

Pre-filter cepat yang menyempitkan ~279 native tools menjadi kandidat
teratas sebelum prompt dikirim ke model. Tujuannya sederhana: prompt lebih
kecil, token lebih hemat, dan model tidak bingung memilih di antara ratusan
tools yang tidak relevan.

## Cara kerja

Modul: `zeline/tool_router.py`. Murni keyword/heuristic — **tanpa** model
call, tanpa I/O jaringan, tanpa baca/tulis file di path scoring.

1. **Tokenisasi query** — lowercase, split pada karakter non-alfanumerik,
   buang stopwords kecil (EN + ID) dan token 1 huruf.
2. **Scoring per tool** — untuk tiap token query:
   - cocok dengan token nama tool (mis. `send` di `email_send`): **bobot 3**
   - cocok dengan prefix kategori nama (mis. `meeting` di `meeting_join`): **bobot 2**
   - cocok dengan token di deskripsi tool: **bobot 1**
   - cocok via **peta sinonim/alias** (lihat bawah): nama tool persis **+6**,
     prefix kategori **+4**
3. Urutkan berdasarkan skor (seri: alfabetis), ambil **top-K** (default 15).
4. **`ask_user` selalu disertakan** di urutan paling depan — ini kanal
   konfirmasi/approval agent, jadi tidak boleh hilang dari daftar.

### Peta sinonim/alias

Mencakup istilah umum EN + ID, misalnya:

| Kata query | Tool yang di-boost |
|---|---|
| `email`, `mail`, `gmail` | `email_send`, `gmail_send`, `gmail_read`, `gmail_search`, … |
| `kirim`, `send` | semua tool pengirim (email, slack, telegram, whatsapp, sms, …) |
| `rapat`, `meeting`, `zoom` | `meeting_join`, `meeting_leave`, `meeting_notes` |
| `tugas`, `task`, `todo`, `kanban`, `kartu` | `kanban_*`, `todoist_*`, `asana_*`, … |
| `cari`, `search`, `web` | `web_search`, `deep_research`, `search_files`, … |
| `pengingat`, `ingatkan`, `jadwal`, `reminder`, `schedule` | `schedule_task` |
| `biaya`, `cost` | `runtime_info` (belum ada tool biaya khusus — ini yang paling dekat: info provider/model) |
| `cuaca`, `weather` | `openweathermap_*` |
| `tanya`, `konfirmasi` | `ask_user` |

Query kosong → `ask_user` + tool umum (`web_search`, `run_shell`,
`read_file`, …) lalu sisanya alfabetis sampai top-K.

## API

```python
from zeline import tool_router

# 1. Untuk ToolDef / dict {name, description} — dipakai pre-agent loop
top = tool_router.route("kirim email ke budi", tools)   # list objek yang sama, top-15

# 2. Untuk schema format OpenAI (seperti ToolExecutor.schemas di agent.py)
filtered = tool_router.filter_schemas("join zoom meeting", schemas)

# 3. Untuk test / debugging — ranking nama saja
names = tool_router.route_names("cari di web", [(name, desc), ...])

# 4. Konfigurasi
tool_router.configure(enabled=False)   # matikan: route/filter pass-through semua
tool_router.configure(top_k=20)        # top_k di-clamp ke 5..50
```

Fungsi yang di-expose: `route()`, `filter_schemas()`, `route_names()`,
`configure()`, `is_enabled()`, konstanta `ROUTER_ENABLED`,
`ESSENTIAL_TOOLS`, dan exception `ToolRouterError`.

**Fail-open:** `filter_schemas()` tidak pernah melempar exception ke agent
loop. Kalau input rusak atau ada bug internal, schema asli dikembalikan
apa adanya supaya agent tetap jalan. `route()` melempar `ToolRouterError`
hanya untuk entri tool yang malformed.

## Cara disable

Tiga cara, dari yang paling ringan:

1. **Env var** (tanpa ubah kode): `ZELINE_ROUTER_OFF=1`
2. **Runtime**: `tool_router.configure(enabled=False)`
3. Koordinator cukup tidak memanggil `filter_schemas()` sebelum `_call_llm`.

## Performa

`route()` atas 279 tools: **~6ms** (budget <50ms, diukur max dari 5 run
via `time.perf_counter`). Skor dihitung sekali per tool per query; tidak
ada cache antar-query supaya hasil selalu konsisten dengan daftar tool
yang diberikan.

## Keamanan (audit sendiri)

1. **Tanpa I/O di path scoring** — hanya operasi CPU atas string; env var
   cuma dibaca saat cek config.
2. **Tanpa `eval`/`exec`** atas query user — query hanya di-tokenisasi.
3. **Regex aman** — satu-satunya regex adalah `[^a-z0-9]+` (negated
   character class, linear-time, tidak ada risiko catastrophic
   backtracking).
4. **Fail-open** — router tidak bisa mematikan agent loop.
5. `ruff check` + `ruff format`: bersih.
