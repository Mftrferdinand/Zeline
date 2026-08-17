# Community Intelligence [Zeline Zenith]

> Community Intelligence — modul Zeline Zenith (sumber: zeline-zenith-z39).

# Load when: topik trending komunitas, sentimen komunitas, FUD, analisis chat, pertanyaan member
# Category: Meta & Self-Improvement

## DOCTRINE — komunitas = sumber sinyal, bukan cuma audiens
Ribuan pesan member = data: topik yang lagi panas, pertanyaan berulang (bahan FAQ/konten), sentimen, FUD yang harus diredam cepat. z39 ubah itu jadi laporan terstruktur + ide konten.

Murni analitik teks (offline, deterministik). Pengambilan pesan mentah didelegasi ke zeline-zenith-z4 (telegram) / z6 / skill browser. `now` di-inject buat window trending (TIME.md).

## TOOL (v4.2, net-new)
- `tools/community_intel.py` — `Message(text, ts, reactions)` → `analyze(msgs, now, window_hours)` → `IntelReport` (top_topics, trending_questions, sentiment pos/neg/netral, fud_alerts, content_ideas).

## ALUR STANDAR
```python
from community_intel import Message, analyze
msgs = [Message("Kapan claim ZkProtoX dibuka min?", ts=..., reactions=5), ...]
r = analyze(msgs, now=now, window_hours=24)
print(r.report())   # 🔥 topik · ❓ sering ditanya · 🚨 FUD · 💡 ide konten
```

## OUTPUT DIPAKAI BUAT
- FAQ/konten dari `trending_questions` (feed ke z35/z40)
- Respons cepat `fud_alerts` (klarifikasi resmi sebelum membesar)
- Prioritas topik konten minggu ini dari `top_topics`

## SCOPE & DELEGATION
| Butuh | z39 | Delegasi |
|---|---|---|
| Tarik pesan TG/Discord/X | konsumsi `Message` | z4 / z6 / skill browser |
| Bikin konten dari insight | hasilkan ide | zeline-zenith-z35 (guide) / zeline-zenith-z40 (repurpose) / z28 |
| Balas FUD publik | deteksi + draft ide | operator review → z4 |

## SAFETY RAILS
- Heuristik keyword — sentimen kasar, BUKAN pengganti baca manual buat isu sensitif.
- Privasi: analisis agregat; jangan expose identitas member individual di output publik.
- FUD soal "scam/gak cair" → cek fakta dulu (z37/z38) sebelum klarifikasi.

🔧 Upgrade: jadwal harian via z14 → laporan pagi "apa kata komunitas semalam".
