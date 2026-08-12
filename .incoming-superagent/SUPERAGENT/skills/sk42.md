# sk42 — Hook A/B Lab (SUPERAGENT V7)
# Load when: hook, judul konten, headline, a/b test judul, stop scroll, opening line, prediksi engagement
# Category: Content & Marketing

## DOCTRINE — 3 detik pertama menentukan segalanya
Hook menentukan konten ditonton atau di-scroll. sk42 generate banyak varian hook + skor prediksi "stop-scroll" 0-100 berbasis heuristik pola engagement → pilih top-N secara data-driven, bukan feeling.

Murni heuristik (offline, deterministik). Skor = pendukung keputusan, BUKAN jaminan viral.

## TOOL (v4.2, net-new)
- `tools/hook_lab.py` — `score_hook(text)` → `HookScore` (0-100 + signals). `rank_hooks(list)` urut desc. `generate_hooks(topic, n_points)` → 8 varian dari template terbukti.

## SINYAL SKOR
+angka · +curiosity gap (rahasia/ternyata/kenapa) · +emosi (cuan/awas/gratis) · +power word (terbukti/panduan/worth) · +urgency (sekarang/deadline) · +pertanyaan · +panjang ideal 4-12 kata. Penalti: kepanjangan (>18 kata), kependekan (<3), ALL CAPS berlebih.

## ALUR STANDAR
```python
from hook_lab import generate_hooks, rank_hooks
hooks = generate_hooks("Airdrop Base", n_points=3)
for h in rank_hooks(hooks)[:3]:
    print(h.report())   # [ 76] 3 Airdrop Base yang wajib kamu tau sekarang
```

## SCOPE & DELEGATION
| Butuh | sk42 | Delegasi |
|---|---|---|
| Konten lengkap dari hook | hasilkan hook | sk40 repurpose / sk41 video / sk28 |
| Data performa aktual post | — | sk6 / platform analytics (feed balik manual) |
| Publish varian A/B | hasilkan kandidat | sk4 / sk14 |

## SAFETY RAILS
- Heuristik ≠ kebenaran: validasi dengan performa aktual, kalibrasi template dari hasil nyata.
- Jangan clickbait bohong — hook harus tetap jujur sama isi konten (reputasi brand > CTR).

🔧 Upgrade: loop tertutup — hasil engagement aktual masuk balik jadi penyesuaian bobot template.
