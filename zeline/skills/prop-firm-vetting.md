# Prop Firm Vetting

> Vet prop trading firms (FundingPips, FTMO, WeMasterTrade, dll) — model eval, matematika return, red flag payout, analisis pola review Trustpilot. Pakai saat user nanya "firm X legit?", "bisa payout?", "2-step/1-step/instant?", target profit bulanan, atau minta daftar firm bagus.

Vet firm modal trading (prop firm) buat user trader. Bahasa Indonesia, terse, tabel.

## Return math (cekin dulu sebelum jawab target)
- Konversi: target bulanan ÷ ukuran akun = %/bln. Contoh: $5.200/bln dari $50k = 10,4%/bln.
- **Realistis:** 2-5%/bln = top 5% trader profesional. 10%/bln sustain = mustahil, resep blow account (daily loss ±5%, max loss ±10% di mayoritas firm).
- Kompensasi logika: kalau 10%/bln sustain itu nyata, firm bangkrut bayar — firm nggak sanggup sustain bayar segitu.
- Multi-akun: jumlahkan dulu total modal sebelum hitung (contoh: 100k + 50k×2 + 25k×3 = 275k → 3% = $8.250/bln).

## Model eval
| Tipe | Cocok | Risiko |
|---|---|---|
| 2-step | mayoritas; fee termurah; gagal cuma hangus fee kecil | proses 2 fase |
| 1-step | trader agresif yang paham risk | 1 salah besar = langsung mati |
| Instant | cuma yang udah terbukti profit 3+ bulan | bayar 2-3x lipat; blow = duit asli hangus |

Aturan: kalau belum terbukti konsisten, 2-step. Jangan pernah instant buat yang belum ada track record.

## Payout mechanics
- Standar: bi-weekly (2 minggu) sesuai jadwal; split biasanya 80-90% (FundingPips 80/20, FTMO s/d 90%).
- **On-demand payout** (FundingPips dll): request kapan aja di luar jadwal → kena fee/admin charge. Pakai cuma kalau butuh cash cepat; profit tipis lebih worth nunggu jadwal.
- Split rendah dari awal (misal 50% payout pertama) = sinyal firm pelit/minimalkan risiko payout.

## Red flag checklist (user mau setor duit → cek semua)
1. Track record bayar: cari "payout proof" + komplain di Trustpilot/ForexFactory → nol bukti = red flag
2. Minta bayar dulu/course/refundable deposit → scam
3. Badge "award" palsu: FinCEN itu regulator bukan pemberi award; "Best X 2025" self-awarded → gimmick
4. Rule payout samar di T&C (misal "high-frequency trading" tanpa definisi jelas) → dipakai seenaknya buat tolak payout
5. Domain utama nggak resolve = nggak ada situs → kemungkinan gede copycat/scam
6. Payout harus gampang, nggak ada alasan "pending forever"

## Firm verified (per sesi ini)
- **Aman/solid:** FTMO (paling tua, track record bayar 10+ thn), FundedNext (payout mingguan), The5ers (no time limit), Alpha Capital Group, OANDA Prop Trader (di-back broker besar). Futures: Topstep (paling solid), Apex Trader Funding.
- **Mati/scam — JANGAN sentuh:** MyForexFunds (tutup 2024 kabur), True Forex Funds (kena regulator), The Funded Trader (bayar kacau).
- **WeMasterTrade (wemastertrade.com):** firm nyata tapi payout bermasalah — pola Trustpilot: beberapa payout pertama lancar, lalu ditolak atas rule "scalping/HFT" yang nggak jelas di T&C; badge FinCEN palsu; split pertama cuma 50%. Kalau dipaksa pakai: jaga trade jarang di bawah 2 menit, hindari scaling sering.

## Teknik riset: search engine 429 di jaringan user
Google/Bing/DDG/searx sering block (429/captcha) dari Termux. Yang jalan:
- **https://r.jina.ai/<full-url>** = reader proxy, baca halaman web + hasil search jadi markdown: `curl -s "https://r.jina.ai/https://www.bing.com/search?q=..."` dan `curl -s "https://r.jina.ai/https://wemastertrade.com/payout/"`
- Bing via jina bisa kasih hasil garbage (fallback generic) → kalau hasil nggak relevan = sinyal nggak ada entri indexed persis, jangan dipakai sebagai bukti definitif; kombinasikan dgn cek DNS langsung
- Cek domain hidup: `curl -sIL --max-time 12 -A "Mozilla/5.0" "https://domain"` — kosong = DNS gagal
- Trustpilot review per firm: `https://r.jina.ai/https://www.trustpilot.com/review/<domain>` → baca pola review, jangan cuma skor

## Live pricing scrape (harga akun per firm)
Metode + snapshot harga 50K per firm: `references/live-pricing-50k.md`. Ringkas:
- **FTMO:** pricing di homepage `#pricing` (bukan `/pricing/` — 404). 2-step fee refundable, 1-step non-refundable. FTMO TIDAK punya instant funding (forex) — cuma 2-step & 1-step.
- **FundedNext:** harga per plan di halaman `/usa/cfds/stellar-<plan>` (bukan /pricing). Stellar Instant CFD max $20K — $50K instant TIDAK tersedia. Diskon NEW25 25% (harga promo = reguler × 0.75).
- **The5ers:** program = High Stakes (2-step), Bootcamp (3-step). TIDAK ada instant funding. Harga di tab JS yang jina cuma render tab default → ambil raw HTML dan parse JSON escape: cari `\"row_label\":\"Cost\"`.
- **FundingPips:** 429 Vercel checkpoint, direct & via jina. Fallback: archive.org wayback snapshot, bukan klaim dari ingatan.
- Tab harga = JS state; kalau grep jina cuma keluar 1 harga per tab selector, itu default tab doang — jangan generalize ke semua ukuran.
- File sementara: `/tmp` kadang nggak writable di Termux → pakai `~/.cache/<dir>`.

## Pitfalls
- Jangan vonis scam tanpa bukti kuat — sebutkan batas verifikasi dengan jujur ("search unreliable dari jaringan ini").
- Pola payout denial: "payout lancar N kali lalu ditolak dengan rule samar" = pola klasik firm nakal, sebutkan ke user.
- Jawab target uang: selalu mulai dari hitung matematika ($$ / ukuran akun = %), baru verdict.


---

## Lampiran: `references/live-pricing-50k.md`

# Snapshot harga akun $50K (per 2026-08) + resep scrape

Dicetak live via r.jina.ai + curl. Harga promo berubah — selalu re-scrape, jangan pakai snapshot ini sebagai harga final. Ini peta URL yang benar per firm (mayoritas /pricing/ itu 404).

## FundingPips
- **Blocked:** fundingpips.com/pricing → Vercel Security Checkpoint 429, direct & via jina.
- Fallback: `https://r.jina.ai/https://web.archive.org/web/2026id_/https://fundingpips.com/pricing/` (archive.org rate-limited juga, retry).
- Dikenal publik: split 80/20, payout bi-weekly + on-demand (fee ekstra).

## FTMO
- URL benar: `https://ftmo.com/en/` (homepage, seksi `#pricing`). `/pricing/`, `/en/2-step-challenge/`, `/en/instant-funding/` → 404.
- $50K: **2-Step €345 (refundable)**, **1-Step €319 (non-refundable)** — dari `/en/1-step-challenge/`. 100K: €439/€540. Split up to 90%, payout tiap 14 hari.
- FTMO tidak punya instant funding forex — cuma 2-step & 1-step.

## FundedNext
- URL benar: `https://fundednext.com/usa/cfds/stellar-2-step` (dan `-1-step`, `-lite`, `-instant`). `/cfds` cuma daftar plan tanpa harga.
- $50K (harga reguler / promo NEW25 25%):
  - Stellar 2-Step: $269.99 / $202.49
  - Stellar 1-Step: $309.99 / $232.49
  - Stellar Lite: $229.99 / $172.49
  - Stellar Instant: **max $20K** ($599.99 / $449.99) — $50K instant TIDAK tersedia di CFD
- Split: Stellar up to 95%, Instant 70%→80% (tier 3+). Reward: bi-weekly, instant on-demand/bi-weekly.

## The5ers
- URL benar: `https://the5ers.com/high-stakes/` (2-step) & `/bootcamp/` (3-step). `/pricing/`, `/forex-funding-program/`, `/faq/`, `/payouts/` → 404.
- $50K High Stakes (2-step): **$545** (promo summer $491). Split 80/20 → 100/0 + fixed payout, withdraw tiap 14 hari.
- Bootcamp (3-step): entry fee + bayar sisa saat sukses. Tab $20K/$100K/$250K; $50K masuk tab $100K (entry $95, final $205).
- TIDAK ada produk instant funding (teks "Instant Funding accounts" di FAQ = legacy).
- **Teknik:** harga di tab JS — jina cuma render tab default ($2.5K). Ambil raw HTML:
  ```bash
  curl -s -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" "https://the5ers.com/high-stakes/" -o hs.html
  python3 - <<'EOF'
  import re
  h = open('hs.html', encoding='utf-8', errors='ignore').read()
  for m in re.finditer(r'\\"row_label\\":\\"Cost\\"', h):
      seg = h[m.start():m.start()+1200]
      print(re.findall(r'\\"val_text\\":\\"([^"\\\\]{0,50})\\"', seg)[:8])
  EOF
  ```
  Output HS: fees per ukuran 2.5K/5K/10K/25K/50K = $22/$39/$78/$195/$545 (+ value_new = harga promo).
