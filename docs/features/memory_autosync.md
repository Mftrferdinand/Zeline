# Memory Autosync

Tarik ringkasan harian otomatis dari konektor (Gmail, GitHub, Calendar) ke
Memory Tree. Satu episode per source per hari — tidak pernah duplikat.

## Cara enable

Secara default **semua source MATI**. Tidak ada yang di-sync sampai kamu
menyalakan secara eksplisit:

```bash
# 1. Nyalakan master switch
zeline memory autosync on

# 2. Enable source yang diinginkan (contoh via Python)
python3 -c "
from zeline import memory_autosync as ma
ma.configure(sources=['gmail', 'calendar'], interval_min=20)
ma.configure(repos=['myorg/myrepo'])  # repo GitHub yang di-watch
"

# 3. Cek status
zeline memory autosync status

# Sync manual kapan saja (mengabaikan interval)
zeline memory sync          # semua source yang enabled
zeline memory sync gmail    # satu source

# Matikan total
zeline memory autosync off
```

Interval default 20 menit per source; ubah via
`configure(interval_min=...)`. Untuk mematikan total (mis. di mesin
bersama), set environment variable `ZELINE_AUTOSYNC_OFF=1`.

## Yang masuk ke Memory Tree

Setiap source menulis **satu episode per hari** berjudul
`Autosync · <Source> · <tanggal>`, berisi satu baris ringkasan, contoh:

- `Gmail 2026-10-11: 7 email — 2 penting: "URGENT: invoice due", ...`
- `GitHub 2026-10-11: 4 update (24 jam) di 2 repo — myorg/myrepo #12 "Fix login" (PR); ...`
- `Calendar 2026-10-12 (besok): 3 event — 09:00 Standup; 14:00 Rapat X; ...`

Episode ini tampil di halaman `daily/` saat vault di-export/sync
(`zeline vault export` / `zeline vault sync`).

Heuristik "penting" Gmail sederhana: subject mengandung kata
urgent/penting/important/asap/segera/dll. Tidak ada machine learning,
tidak ada over-engineering.

## Privacy note

- **Opt-in penuh**: default semua OFF. `is_enabled()` menolak sync source
  yang tidak di-enable, dan `ZELINE_AUTOSYNC_OFF=1` mematikan semuanya.
- **Hanya metadata**: yang dibaca cuma subject/pengirim/tanggal email,
  judul issue/PR, dan judul+waktu event kalender. **Isi email tidak pernah
  diambil** (`gmail_read` tidak dipakai) dan tidak pernah di-log.
- **State lokal 0600**: `~/.zeline/memory_autosync.json` menyimpan
  metadata terakhir (id email, judul) agar tidak duplikat — permission
  0600, tidak berisi kredensial.
- **Token konektor tidak disentuh**: modul ini memakai konektor apa
  adanya (`zeline connect google` / `zeline connect github` seperti biasa);
  tidak membaca, menyimpan ulang, atau me-log token.

## Untuk koordinator / developer

Entry points: `sync_source(source) -> str`, `sync_all() -> dict`,
`run_loop(stop_event=None)`, `configure(...)`, `is_enabled(source)`,
`status()`, `register_memory_sub(memory_parser)`, `dispatch_memory(ns)`.
Tambah source baru: tulis `sync_baru(cfg) -> str`, daftarkan di `SYNCERS`.

`run_loop()` best-effort: satu source gagal tidak menggagalkan yang lain,
dan loop tidak pernah mati karena exception.
