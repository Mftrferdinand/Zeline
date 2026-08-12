# STANDARD.md — Open SKILL.md, Progressive Disclosure & Cross-Platform (v7.0)

Doc meta yang ngejelasin *kenapa* arsitektur SUPERAGENT begini. Kategori "emerging meta trends" — sebagian besar **sudah jadi sifat bawaan** sistem ini sejak v3/v4. File ini ngebakukan & ngedokumentasiinnya.

---

## 1. Progressive disclosure (sudah jalan)

Skill **gak pernah preload**. Router (`AGENTS.md` + `sk0.md`) cuma muat skill saat keyword-nya kena. Always-on ~3.5k token; sisanya on-demand.

```
input → router scan keyword → load skill yang match doang → sisanya tetap di disk
```

Ini progressive disclosure: konteks dibuka bertahap sesuai kebutuhan, bukan semua sekaligus. Hermes references (15 file) nambah ~0 always-on cost karena prinsip yang sama. **Tambah skill ≠ tambah beban setiap sesi** — makanya v7.0 bisa nambah 12 skill tanpa naikin always-on budget.

## 2. Open SKILL.md standard

Format skill SUPERAGENT (terutama `skills/hermes/SKILL.md`) ngikut pola terbuka: **instruksi + (opsional) frontmatter + scripts**. Konvensi yang dipakai:

```
skills/<name>/
  SKILL.md          # instruksi + kapan dipakai + index kemampuan
  references/*.md   # deep content, load on-trigger
  scripts/*.py      # template runnable (adapt ke env, jangan run as-is)
```

Frontmatter YAML (opsional, buat skill marketplace/portabilitas):
```yaml
---
name: my-skill
description: apa & kapan dipakai (1-2 kalimat — ini yang dipakai router buat milih)
version: 0.1.0
scripts: [scripts/run.py]
---
```

`description` = paling penting: host/router milih skill dari sini. Tajam = ke-trigger benar; vague = salah/gak ke-load. (Lihat juga sk24 prompt-engineer: prinsip sama buat tool MCP.)

Skill `m*.md`/`x*.md` SUPERAGENT pakai bentuk ringkas (header `# mN — Judul` + isi) karena di-route via tabel bobot `AGENTS.md`, bukan frontmatter. Keduanya kompatibel: tabel router = "description" yang dipusatkan.

## 3. Cross-platform compatibility

SUPERAGENT = teks (Markdown + Python). Portabel ke runtime mana pun yang bisa inject system prompt + baca file:

| Platform | Cara pakai |
|---|---|
| **OpenClaw / Hermes** | native — `DEPLOY.md` (workspace inject) |
| **Claude Code** | drop sebagai skill/`CLAUDE.md` context; tool = MCP/bash |
| **Cursor** | `.cursorrules` / context files; skill md di-attach |
| **Codex / Gemini CLI** | system prompt = `AGENTS.md`; skill di-load on-demand |

Yang portabel: router logic, skill content, prinsip safety. Yang **runtime-spesifik**: cara exec tool, env injection, background job (pm2). Adapt lapisan eksekusi, brain-nya sama.

> Catatan jujur: kompatibilitas = "bisa dipakai sebagai konteks", bukan "semua tool jalan identik". Tool yang butuh runtime tertentu (governor pm2, Isaac Sim GPU, osascript macOS) tetap butuh environment yang sesuai.

## 4. Skills marketplace & community hub

Lihat `tools/skill_market.py` (sk0). Akses hub komunitas (skills.sh, 1000+ skill; juga repo publik seperti *Marketing Skills* / *social-media-skills* GitHub) dengan gerbang paranoid: unduh → **quarantine** → audit (sk11) → operator pindah + re-lock. Skill pihak ketiga = surface serangan; jangan auto-trust. Frozen-path melindungi tool ini sendiri dari diedit loop self-improve. Set `SKILLS_MARKET_URL` ke mirror/repo lain kalau perlu.

---

## Ringkas
Emerging meta trends (progressive disclosure, open SKILL.md, cross-platform, marketplace) bukan fitur baru yang ditempel — itu **prinsip arsitektur** yang udah dipegang sejak awal. v7.0 cuma ngebakukan & ngedokumentasiinnya di sini.
