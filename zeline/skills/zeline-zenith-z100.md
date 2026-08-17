# zeline-zenith-z100 — Community Intelligence (dengerin komunitas)

> zeline-zenith-z100 — Community Intelligence (dengerin komunitas) — modul Zeline Zenith (sumber: zeline-zenith-z100).

# Load ONLY when: topik trending komunitas, pertanyaan member, sentimen komunitas, FUD, analisis chat, apa yang lagi rame, ide konten dari komunitas, monitoring telegram discord, suara member

---

## DOCTRINE — komunitas = sumber sinyal, bukan cuma audiens
Ribuan pesan member = data: topik yang lagi panas, pertanyaan berulang (bahan FAQ/konten), sentimen, FUD yang harus diredam cepat. zeline-zenith-z100 ubah itu jadi laporan terstruktur + ide konten.

Murni analitik teks (offline, deterministik). Pengambilan pesan mentah didelegasi ke m4 (telegram) / m6 / skill browser. `now` di-inject buat window trending (TIME.md).

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
- FAQ/konten dari `trending_questions` (feed ke m35/m40)
- Respons cepat `fud_alerts` (klarifikasi resmi sebelum membesar)
- Prioritas topik konten minggu ini dari `top_topics`

## SCOPE & DELEGATION
| Butuh | zeline-zenith-z100 | Delegasi |
|---|---|---|
| Tarik pesan TG/Discord/X | konsumsi `Message` | m4 / m6 / skill browser |
| Bikin konten dari insight | hasilkan ide | m35 (guide) / m40 (repurpose) / m28 |
| Balas FUD publik | deteksi + draft ide | operator review → m4 |

## SAFETY RAILS
- Heuristik keyword — sentimen kasar, BUKAN pengganti baca manual buat isu sensitif.
- Privasi: analisis agregat; jangan expose identitas member individual di output publik.
- FUD soal "scam/gak cair" → cek fakta dulu (m37/m38) sebelum klarifikasi.

🔧 Upgrade: jadwal harian via m14 → laporan pagi "apa kata komunitas semalam".
