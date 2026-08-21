# zeline-zenith-z93 — Agentic Eval & Self-Critique (NEW in v4.1)

> zeline-zenith-z93 — Agentic Eval & Self-Critique (NEW in v4.1) — modul Zeline Zenith (sumber: zeline-zenith-z93).

Ngukur diri sebelum operator yang ngukur. Eval terstruktur, self-critique adversarial, dan **variance testing** (jalanin task N kali → lihat konsistensi). Melengkapi zeline-zenith-z29 (audit sistem) & zeline-zenith-z32 (self-improve loop). Tool: `tools/eval.py`. **Read/measure only — gak nyentuh dana, gak edit skill.**

---

## Kenapa ada

z29 audit struktur, z32 belajar dari kegagalan. zeline-zenith-z93 ngisi celah: **bukti kuantitatif** kalau output bener & stabil. "Kelihatan benar" ≠ "benar konsisten". Eval = jaring sebelum operator ketemu bug.

## 1. Self-critique adversarial (sebelum kirim output berisiko)

Sebelum kirim output yang mahal kalau salah (kode yang gerakin dana, query DB destruktif, strategi), jalankan satu putaran *refute*:

```
1. Tulis output kandidat.
2. Ambil peran SKEPTIK: "gimana ini gagal/salah?" — default ke 'ada yang salah'.
3. Cek tiap klaim: ada bukti? edge case? asumsi tak teruji?
4. Lolos → kirim. Gak lolos → revisi, ulang.
```

Beda dari reflection loop zeline-zenith-z1 (yang cek kualitas/kelengkapan): self-critique zeline-zenith-z93 **mencari cara output ini salah secara faktual/logis**, bukan cuma rapi.

## 2. Eval terstruktur

```python
from eval import Eval, Case
ev = Eval("mint-detector")
ev.add(Case(input="opensea.io/.../1", expect=lambda r: r["fn"]=="mintPublic"))
ev.add(Case(input="zora.co/collect/...", expect=lambda r: r["price_eth"] > 0))
result = ev.run(target_fn, repeat=1)
print(result.summary())   # pass_rate, failures dengan input + actual vs expect
```

Pola: kumpulkan kasus nyata (terutama yang pernah gagal — tarik dari memory/z32), assert properti, jalankan tiap kali ubah logic. Regression net.

## 3. Variance testing (rare — high value)

LLM/agent non-deterministik. Task yang "kadang jalan" itu jebakan. Ukur:

```python
from eval import variance
v = variance(task_fn, input=payload, runs=10)
print(v.report())
# → consistency: 0.7 (7/10 sama), modes: {"ok":7,"timeout":2,"wrong_fn":1}
#   verdict: FLAKY — jangan auto_confirm task ini sampai >0.95
```

Aturan keputusan:
- consistency ≥ 0.95 → boleh masuk automation/`auto_confirm`.
- 0.7–0.95 → butuh konfirmasi per-run, atau perbaiki prompt/logic.
- < 0.7 → **jangan otomasi**. Cari sumber variance (prompt ambigu? RPC flaky? race?) — masuk z34.

Variance tinggi di jalur dana = blokir sampai stabil. Ini mencegah automation yang "biasanya bener" nguras saldo di run ke-8.

## 4. Optimization loop (eval-driven)

```
baseline eval → ubah 1 variabel (prompt/model/param) → eval ulang → bandingkan →
keep kalau pass_rate naik & variance turun → ulang. Satu variabel per iterasi.
```
Jangan ubah banyak sekaligus — gak akan tau mana yang ngefek. Hasil yang menang → catat ke memory (z32) jadi lesson; kalau menyentuh skill file → proposal (frozen-paths, operator acc).

## 5. LLM-as-judge (production-grade)

Untuk output yang gak bisa di-assert dengan `==` (kualitas tulisan, kebenaran reasoning, helpfulness) → pakai LLM lain sebagai juri, dengan rubrik eksplisit:

```python
from eval import llm_judge
verdict = llm_judge(
    output=candidate,
    rubric=["faktual & ada bukti?", "jawab pertanyaan sebenarnya?", "ada halusinasi?"],
    scale=5,                    # skor 1-5 per kriteria + alasan
)   # → {scores:{...}, pass: bool, reasons:[...]}  via model_registry cascade
```

Aturan biar juri gak jadi teater:
- **Rubrik konkret**, bukan "bagus/jelek" — kriteria terpisah + skala.
- **Juri ≠ pembuat** — model/role beda dari yang menghasilkan output (hindari bias self-love).
- **Panel buat taruhan tinggi** — N juri, ambil mayoritas (kurangi variance juri itu sendiri).
- **Juri pun di-eval** — cek konsistensi juri pakai `variance()`; juri yang flaky gak dipercaya.
- Gabung dengan self-critique (§1): juri = perspektif luar, self-critique = refute internal.

---

## Output ke operator

```
📊 eval mint-detector: 18/20 pass (90%) | variance 0.85 FLAKY
   fail: zora-1155 (price=0, expect>0), manifold-edition (timeout 2/10)
   → 2 kasus masuk zeline-zenith-z34 (hypothesis: Zora protocol fee belum di-handle)
```

Eval & variance = bukti. Temuan → zeline-zenith-z34 (debug) atau zeline-zenith-z32 (proposal). zeline-zenith-z93 sendiri gak ngubah apa-apa, cuma ngukur & nunjuk.
