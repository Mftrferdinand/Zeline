# zeline-zenith-z103 — Hook A/B Lab + Performance Predictor

> zeline-zenith-z103 — Hook A/B Lab + Performance Predictor — modul Zeline Zenith (sumber: zeline-zenith-z103).

# Load ONLY when: hook, judul konten, headline, opening line, a/b test judul, prediksi engagement, stop scroll, varian judul, hook tiktok, judul thread, clickbait sehat

---

## DOCTRINE — 3 detik pertama menentukan segalanya
Hook menentukan konten ditonton atau di-scroll. zeline-zenith-z103 generate banyak varian hook + skor prediksi "stop-scroll" 0-100 berbasis heuristik pola engagement → pilih top-N secara data-driven, bukan feeling.

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
| Butuh | zeline-zenith-z103 | Delegasi |
|---|---|---|
| Konten lengkap dari hook | hasilkan hook | m40 repurpose / m41 video / m28 |
| Data performa aktual post | — | m6 / platform analytics (feed balik manual) |
| Publish varian A/B | hasilkan kandidat | m4 / m14 |

## SAFETY RAILS
- Heuristik ≠ kebenaran: validasi dengan performa aktual, kalibrasi template dari hasil nyata.
- Jangan clickbait bohong — hook harus tetap jujur sama isi konten (reputasi brand > CTR).

🔧 Upgrade: loop tertutup — hasil engagement aktual masuk balik jadi penyesuaian bobot template.
