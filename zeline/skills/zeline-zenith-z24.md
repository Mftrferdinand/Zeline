# MCP-Builder & Prompt Engineering [Zeline Zenith]

> MCP-Builder & Prompt Engineering — modul Zeline Zenith (sumber: zeline-zenith-z24).

# Load when: MCP server, bikin MCP, prompt engineering, fix prompt, FastMCP, tool schema
# Category: AI & Inference
Dua kemampuan meta-dev: bangun **MCP server** (Python/TS) dengan best practices, dan **prompt engineering** (deteksi + fix masalah prompt otomatis). Komplemen zeline-zenith-z7 (AI builder) & zeline-zenith-z16 (software eng). Tool: `tools/mcp_builder.py`.

| Capability | Surface | Output |
|---|---|---|
| MCP server builder | Python (FastMCP) / TS (@modelcontextprotocol/sdk) | server scaffold runnable |
| Prompt engineer | analisa prompt → temuan → rewrite | prompt yang lebih tajam |

---

## 1. MCP-Builder

MCP (Model Context Protocol) = standar buat ngasih tool/resource ke LLM host (Claude, Cursor, dll). Bangun server = define tools + schema yang jelas.

```python
from mcp_builder import scaffold_mcp
scaffold_mcp("weather", lang="python", tools=[
    {"name":"get_forecast", "desc":"Ramalan cuaca per kota",
     "params":{"city":"str", "days":"int"}},
])  # → ./mcp-weather/ : server.py + pyproject + README + run cmd
```

Best practices yang di-enforce scaffold:
- **Tool description tajam** — host milih tool dari deskripsi; vague = salah panggil. Satu kalimat what+when.
- **Schema ketat** — typed params, required vs optional eksplisit, contoh nilai.
- **Error sebagai data** — return error message yang LLM bisa pahami & retry, bukan exception mentah.
- **Idempoten kalau bisa** + jangan side-effect diam-diam.
- **Stdio default**, HTTP kalau remote (kasih auth).

Python pakai `FastMCP` (`pip install mcp`); TS pakai `@modelcontextprotocol/sdk`. Untuk daftar/explore MCP yang ada → host punya registry (lihat juga z0 marketplace untuk skill, beda dari MCP server).

## 2. Prompt-Engineer skill

Deteksi & fix masalah prompt secara sistematis, bukan tebak-tebakan:

```python
from mcp_builder import audit_prompt   # prompt tools sekaligus di mcp_builder
print(audit_prompt(my_system_prompt))
# → temuan: [ambiguous-instruction, no-output-format, conflicting-rules, no-examples, ...]
```

Checklist masalah prompt umum:

| Masalah | Gejala | Fix |
|---|---|---|
| Ambigu | model nebak-nebak intent | spesifik + contoh |
| Gak ada format output | output gak konsisten | tentuin skema/struktur |
| Aturan bentrok | model pilih random | resolve konflik, prioritas eksplisit |
| Negasi doang ("jangan X") | model tetap X | kasih yang HARUS dilakukan, bukan cuma larangan |
| Few-shot lemah | gagal di edge case | contoh yang mewakili kasus sulit |
| Overload | satu prompt 10 tugas | pecah / chain |

Loop optimasi prompt = **eval-driven** (pakai z56): ubah 1 elemen → eval → bandingkan pass_rate & variance → keep yang menang. Jangan ubah banyak sekaligus.

---

## Catatan
- MCP server = kode yang jalan → review sebelum expose; HTTP mode wajib auth (sama prinsip dashboard z17).
- Prompt engineering nyambung ke zeline-zenith-z7 (provider/model) & zeline-zenith-z56 (ukur perbaikan).
- Combo: z24 + zeline-zenith-z7 (LLM) + zeline-zenith-z16 (package/deploy server) + zeline-zenith-z56 (eval prompt).
