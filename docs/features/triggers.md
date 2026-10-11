# Event Triggers

Trigger memantau event dari connector (Gmail / GitHub) dengan interval polling,
lalu menjalankan **action** setiap kali ada item baru yang cocok dengan filter.

## Konsep

- **Trigger**: definisi "kalau ada X baru → lakukan Y", tersimpan di
  `~/.zeline/triggers.json` (0600).
- **State**: `~/.zeline/trigger_state.json` mencatat item terakhir yang sudah
  diproses per trigger, jadi tidak ada double-fire.
- **Poll pertama** sebuah trigger hanya mencatat baseline (tidak fire) — trigger
  baru tidak akan membanjiri owner dengan email/issues lama.

## Connector & event yang didukung

| Connector | Event | Filter |
|---|---|---|
| `gmail` | `new_email` | `from_contains`, `subject_contains` |
| `github` | `new_issue` | `repo` (wajib, `owner/name`), `labels` (opsional) |
| `github` | `new_pr` | `repo` (wajib, `owner/name`), `labels` (opsional) |

## Action

| Action | Efek |
|---|---|
| `notify` | Kirim notifikasi ke owner. Default: dicatat ke `~/.zeline/trigger_outbox.jsonl`; koordinator me-rebind `triggers.notify_owner` ke pengiriman Telegram. Template: `{"text_template": "Email baru: {subject}"}`. Placeholder yang tersedia: `{from}`, `{sender}`, `{subject}`, `{title}`, `{repo}`, `{number}`, `{url}`, `{labels}`, `{snippet}`, `{date}`. |
| `run_workflow` | Minta eksekusi workflow: `{"workflow_id": "wf_1"}`. Default: dicatat ke outbox; koordinator me-rebind `triggers.run_workflow` ke `zeline.workflows`. |
| `append_memory` | Tambah catatan ke `~/.zeline/memory/inbox/<tanggal>.md`: `{"note_prefix": "GH"}`. |

Action yang gagal di-retry maksimal 3x pada poll berikutnya, lalu item
tersebut di-skip (dicatat di log) — satu item bandel tidak menghentikan
trigger lain.

## CLI

```bash
# Tambah trigger: email baru dari bos -> notifikasi
zeline trigger add \
  --connector gmail \
  --event new_email \
  --filter '{"from_contains": "boss@corp.com", "subject_contains": "urgent"}' \
  --action notify \
  --action-params '{"text_template": "Email penting dari {from}: {subject}"}' \
  --interval 5

# Tambah trigger: issue berlabel bug di repo -> jalankan workflow
zeline trigger add \
  --connector github \
  --event new_issue \
  --filter '{"repo": "myorg/myrepo", "labels": ["bug"]}' \
  --action run_workflow \
  --action-params '{"workflow_id": "triage_bug"}' \
  --interval 10

# Lihat semua trigger
zeline trigger list

# Hapus trigger
zeline trigger remove trg_a1b2c3d4

# Dry-run: tampilkan item yang akan fire TANPA menjalankan action
# dan tanpa mengubah state
zeline trigger test trg_a1b2c3d4

# Dry-run tapi benar-benar fire + update state
zeline trigger test trg_a1b2c3d4 --commit
```

Catatan:

- `--interval` dalam menit, di-clamp ke 1–1440.
- `--id` opsional untuk id kustom (karakter `a-z A-Z 0-9 _ -`, maks 64).
- `zeline trigger test` aman secara default: tidak mengirim notifikasi asli.

## Tool agent

Agent bisa mengelola trigger lewat tool `trigger_add`, `trigger_list`,
`trigger_remove` (lihat `triggers.get_tool_defs()` / `triggers.TOOL_HANDLERS`).

## Daemon

```python
import threading
from zeline import triggers

stop = threading.Event()
triggers.run_loop(stop_event=stop)   # poll tiap 60 dtk; interval per-trigger mengatur fetch
# atau sekali jalan (cron/systemd):
triggers.run_loop(once=True)
```

## Keamanan

- Filter `from`/`subject` dicocokkan di sisi Python, tidak pernah ditempel ke
  query Gmail — teks filter tidak bisa meng-inject query.
- Segmen `owner`/`repo` GitHub divalidasi regex ketat sebelum masuk URL.
- Isi template notifikasi dibersihkan dulu (whitespace dilipat, dipotong
  300 karakter per nilai) sebelum disubstitusi.
- `triggers.json`, `trigger_state.json`, dan outbox disimpan dengan mode 0600;
  penulisan memakai file temp + `os.replace` (atomic).
