---
name: skill-provisioner
description: Auto-provisioning skill + tool: detect capability gap, cari referensi GitHub, adaptasi, dry-run, inject setelah approve.
category: self-improvement
---

# skill-provisioner

Sistem **self-provisioning** buat Zeline: ketika lo minta sesuatu tapi **gak ada skill atau custom tool-nya**, agent ini:

1. **Deteksi gap** — cek capability yang lo butuhin udah ada atau belum.
2. **Cari referensi di GitHub** — repo publik yang punya pola/tool serupa.
3. **Baca implementasi referensi** — fetch file kunci, grep pola penting.
4. **Adaptasi** — rombak/ambil pola dari tool/skill yang *udah ada* di setup lo, atau generate scaffold baru dari referensi.
5. **DRY-RUN dulu** — tampilin rencana lengkap + preview code, **JANGAN inject apa-apa sebelum lo approve**.
6. **Inject setelah approve** — custom tool ke `~/.zeline/tools/`, skill ke `~/.zeline/skills/private/`.

Skill ini auto-generated + diadaptasi manual. Auto-trigger: ketika agent bilang "gak ada tool/skill" atau lo bilang "bikinin tool X", skill ini wajib ditawarin.

## Utility: `custom_skill_provision`

Custom tool Python di `~/.zeline/tools/skill_provision.py` — ini otak provisioning. Fungsi yang lo pake:

| Fungsi | Buat apa |
|---|---|
| `list_capabilities()` | Inventaris semua skill + custom tool yang terinstall. |
| `has_capability(intent)` | Cek `intent` udah ada belum; balikin `{"found": bool, "kind": ..., "path": ..., "similar": [...]}`. |
| `search_github(query, per_page=5)` | Cari public repo GitHub (stars sorted). |
| `fetch_repo_tree(repo, branch)` | List semua file di repo. |
| `fetch_file(repo, path, branch)` | Ambil isi satu file mentah (max 200KB). |
| `grep_repo(repo, pattern, branch)` | Clone shallow ke cache, grep regex di semua file, balikin snippet ~25 baris. |
| `generate_tool_scaffold(name, description, args, reference)` | Draft custom tool Python. |
| `generate_skill_scaffold(name, description, reference)` | Draft skill markdown + frontmatter. |
| `dry_run_provision(intent)` | **Workflow lengkap mode DRY-RUN** — gap → referensi → rencana → preview scaffold. Output juga disimpen ke `~/.zeline/provisioning/dry_run_*.md`. |
| `inject_tool(code, name)` | Tulis tool ke `~/.zeline/tools/` (auto-backup `.bak` kalo udah ada). **Hanya setelah approve.** |
| `inject_skill(name, content)` | Buat skill baru via `manage_skill("create")`. **Hanya setelah approve.** |
| `cleanup_cache()` | Hapus cache clone GitHub (hemat storage). |

## Langkah kerja (prosedur eksekusi)

### A. Trigger — capability gak ada
1. User minta fitur/tool/skill yang belum ada.
2. Panggil `custom_skill_provision.has_capability("<intent>")`.
3. Kalo `found == True` → selesai, laporin skill/tool yang udah ada + cara pakainya.
4. Kalo `found == False` → lanjut ke B.

### B. Cari referensi — URUTAN WAJIB (hemat GitHub quota)

**JANGAN langsung GitHub.** Ikuti urutan ini:

1. **Adaptasi dari skill/tool yang UDAH ADA di setup lo** (prioritas #1 — zero quota, paling cepat, paling aman).
   - `custom_skill_provision.list_capabilities()` — inventaris penuh.
   - `custom_skill_provision.has_capability("<intent>")` — dapetin `similar` matches.
   - Baca file aslinya, copy pola (command runner, API wrapper, flow), edit seperlunya.
   - Cocok buat 80% kasus: "tambah command", "wrap CLI yang udah keinstall", "extend skill serupa".

2. **Generate scaffold umum** (kalo ada pola umum yang jelas tapi gak ada yang mirip sama sekali).
   - `generate_tool_scaffold()` / `generate_skill_scaffold()`.
   - Isi body dari pengetahuan umum; minta user lengkapi bagian yang butuh kredensial/API key.

3. **GitHub — LAST RESORT** (butuh `GITHUB_TOKEN`; unauthenticated cuma 60 req/jam).
   - Set token dulu: `export GITHUB_TOKEN=ghp_xxx` (scope minimal `public_repo`, atau `repo` buat private repo).
   - `custom_skill_provision.search_github("<intent> zeline tool")`, fallback query alternatif.
   - Baca isi beneran via `fetch_repo_tree` + `fetch_file` / `grep_repo` (1-2 repo teratas aja, jangan rakus).
   - Adaptasi pola, bukan copy-paste mentah — pastikan aman (no shell=True, ada timeout, return string).

Kalo GitHub ke-lock/rate-limited dan gak ada `GITHUB_TOKEN`: laporin jujur + tawarin opsi (a) adaptasi skill existing, (b) scaffold umum yang user lengkapi sendiri, (c) minta token dulu. Jangan fabricate endpoint atau pura-pura baca repo.

### C. Baca implementasi (hanya kalo lewat ke GitHub)
1. `fetch_repo_tree(repo)` — cari file kunci (main entry point, requirements, config).
2. `grep_repo(repo, "<pola inti>")` — clone shallow ke cache, grep regex di semua file, balikin snippet ~25 baris.
3. `fetch_file(repo, path)` buat file-file penting (max 3-5 file).
4. **PENTING**: jangan clone/fetch semuanya — ambil yang relevan aja. Rate limit GitHub unauthenticated cuma 60 request/jam; kalo udah ada `GITHUB_TOKEN` di env, set sebelum mulai.

### D. Adaptasi
Ada 2 strategi, pilih yang paling murah & solid:

1. **Rombak skill/tool yang udah ada** (prioritas).
   - Cari di `custom_skill_provision.list_capabilities()` skill/tool yang mirip.
   - Baca file aslinya (`read_file` untuk skill, `read_file` untuk tool di `~/.zeline/tools/`).
   - Edit: `manage_skill("patch", ...)` untuk skill, atau `write_file` langsung untuk tool di `~/.zeline/tools/` (backup manual `.bak` dulu).
   - Cocok buat "tambah command ke tool yang udah ada" atau "extend skill serupa".

2. **Generate dari referensi GitHub** (kalo gak ada yang mirip sama sekali).
   - Pake `generate_tool_scaffold()` + `generate_skill_scaffold()` sebagai starting point.
   - Isi body implementasi dari pola yang lo baca di referensi (command, endpoint, flow).
   - Pastikan: import aman, `subprocess` pake list args (jangan shell=True), timeout di semua I/O eksternal, return string (tool wajib balikin string).

### E. DRY-RUN (WAJIB — approval gate)
Sebelum inject apa pun, tampilin laporan terstruktur:

```
## Gap
- <capability> belum ada

## Referensi yang dibaca
- repo X (file A, B)
- skill/tool existing yang diadaptasi: Y

## Rencana
- Buat custom_tool: custom_<name> di ~/.zeline/tools/<name>.py
- Buat skill: <name> di ~/.zeline/skills/private/<name>/SKILL.md

## Preview code (tool)
```python
...
```

## Preview skill (SKILL.md)
...

Ketik "approve" / "gas" buat inject, atau kasih revisi.
```

**Jangan pernah inject tanpa dry-run + approve eksplisit.** Custom tool = Python yang di-import langsung ke proses agent; code jelek bisa break tool lain.

### F. Inject (setelah approve)
1. `custom_skill_provision.inject_tool(code, name)` — verifikasi file ke-write.
2. `custom_skill_provision.inject_skill(name, content)` — verifikasi via `manage_skill("list")` atau `read_file`.
3. Verifikasi tool ke-load: panggil tool-nya dengan argumen aman/dummy, pastikan balikin string (bukan traceback).
4. Kasih tau user cara pakainya (nama tool + 1 contoh call).
5. Rekam ke memori/lesson kalo workflow ini punya pola khusus (misal API endpoint tertentu, rate limit).

### G. Cleanup
- `custom_skill_provision.cleanup_cache()` kalo cache clone udah gak kepake (tiap clone bisa puluhan MB).

## Pitfalls (wajib hindari)

- **Jangan asal inject.** Selalu dry-run + approve. Ini batas safety skill ini.
- **Jangan fabricate tool output.** Pas verifikasi, panggil beneran; kalo error, laporin error aslinya + fix, jangan nutup-nutupin.
- **Rate limit GitHub.** Unauthenticated = 60 req/jam. Kalo user punya token, suruh set `GITHUB_TOKEN` di env. Prioritaskan `search_github` + `grep_repo` (satu clone udah cukup buat banyak grep).
- **File gede.** `fetch_file` max 200KB; kalo lebih, minta user approve explicit atau ambil via `git clone` + grep lokal.
- **Tool wajib return string.** Provider butuh string result; wrap semua logic di try/except, balikin `"ERROR: <pesan>"` kalo gagal.
- **Jangan shadow tool native.** Nama custom tool otomatis diprefix `custom_` sama framework; jangan coba bikin nama tanpa prefix.
- **Skill name valid.** Pake `[a-z0-9_-]` aja. `manage_skill` bakal nolak nama jelek.
- **Backup sebelum rombak.** Utility udah auto-backup `.bak` buat tool; buat skill pake `patch` (framework auto-checkpoint) atau copy manual.
- **Kalo referensi gak ada / GitHub ke-lock.** Fall back ke (a) skill/tool existing yang diadaptasi, (b) generate scaffold dari pengetahuan umum + minta user lengkapi bagian yang butuh kredensial/API key. Jangan fabricate endpoint.

## Contoh call (buat agent)

```
custom_skill_provision.dry_run_provision("cek NIK Dukcapil")
custom_skill_provision.has_capability("aircrack")
custom_skill_provision.search_github("aircrack-ng termux")
custom_skill_provision.grep_repo("aircrack-ng/aircrack-ng", "aircrack-ng\\s", "master")
custom_skill_provision.inject_tool(code="<full python source>", name="aircrack_wrapper")
custom_skill_provision.inject_skill(name="aircrack-termux", content="<full markdown>")
custom_skill_provision.cleanup_cache()
```

## Referensi arsitektur Zeline yang dipake

- Custom tools: `~/.zeline/tools/*.py` → auto jadi tool `custom_<nama>`, schema dari signature + docstring.
- Skills: `~/.zeline/skills/private/<name>/SKILL.md` (+ `references/`, `scripts/`, `templates/`, `assets/`).
- `manage_skill(action=create|patch|write_file|delete|list)` — satu-satunya cara aman edit skill.
- `load_skill(name)` — inject skill ke prompt.
