# Prop Firm Trading

> Evaluasi prop firm (FundingPips, FTMO, dll) untuk trading — kelayakan target profit, tipe eval (2-step/1-step/instant), mekanisme payout, struktur multi-akun, firm reputasi vs scam. Untuk pertanyaan "bisa dapat X/bln dari akun Y?" atau "firm mana yang bagus/bayar?

## Trigger
- User tanya kelayakan profit target dari akun funded ("83jt/bln dari $50k?")
- Perbandingan firm (FundingPips vs FTMO vs ...), tipe eval, payout
- Struktur multi-akun, mirror trade, risiko blow

## Aturan emas (selalu cek angka dulu)
1. **Konversi dulu:** target IDR→USD, hitung %/bulan dari modal. Baru nilai.
2. **Realisme:** 2-5%/bln = bagus (top trader). 10%/bln = tidak sustain — kalau bisa sustain, firm bangkrut. Target marketing "1M/thn" = red flag, bukan target waras.
3. **Risk rule firm:** daily loss (~5%) & max loss (~10%) membatasi target — ambisi % tinggi = resep blow.
4. **Struktur multi-akun = bagus** (diversifikasi risiko). Prioritas: jaga akun terbesar, akun kecil boleh agresif. Mirror trade biar konsisten.
5. **Cek format bayaran:** gaji tetap vs "potensi income" — yang dijanjikan bukan dikontrak = red flag.

## Tipe eval — rekomendasi berdasar bukti skill
- **Belum terbukti profit konsisten** → 2-step (fee termurah, gagal cuma rugi fee kecil, sekalian latihan disiplin)
- **Sudah 3+ bln konsisten 3%/bln** → instant baru worth (skip waktu, tapi bayar 2-3x lipat; blow = duit asli hilang)
- 1-step: target tinggi sekali jalan, 1 salah besar = langsung mati. Buat trader agresif paham risk.

## Payout
- Standar: bi-weekly/mingguan sesuai jadwal. On-demand (FundingPips): request kapan aja + **fee ekstra** + minimal profit. Profit kecil → tunggu jadwal lebih hemat.
- **Selalu cek angka persis di dashboard** — site firm sering bot-protected (fundingpips.com return 429 ke curl), jangan klaim detail tanpa verifikasi.

## Direktori firm
Lihat `references/firms-directory.md` — firm reputasi vs firm mati/scam.

## Konteks user (per 2026-08)
- Punya FundingPips funded: 100k×1, 50k×2, 25k×3 = 275k total. Target 3%/bln ≈ $8.250 ≈ 132jt/bln.
- Minat: forex prop trading, payout, ekspansi akun. Bahasa: Indonesia.

## Pitfalls
- Jangan langsung percaya klaim situs firm; cek payout record & kasus regulator.
- MyForexFunds (tutup 2024, kabur bawa duit), True Forex Funds (kena regulator), The Funded Trader (bayar kacau) — JANGAN rekomendasikan.
- Jawab %/bln dulu sebelum bahas strategi — grounding dulu, opini belakangan.


---

## Lampiran: `references/firms-directory.md`

# Direktori Prop Firm (per 2026-08)

## Reputasi — terbukti bayar
| Firm | Model | Profit Split | Payout | Catatan |
|---|---|---|---|---|
| FTMO | 2-step | s/d 90% | mingguan/bi-weekly | Paling tua & terpercaya, track record 10+ thn |
| FundedNext | 2-step/1-step/instant | s/d 90% | mingguan | Payout cepat, ada opsi challenge-free |
| The5ers | 2-step, no time limit (high stakes) | s/d 90% | bulanan | Fokus long-term, tanpa batas waktu |
| Alpha Capital Group | 2-step/instant | s/d 90% | mingguan | Proses cepat |
| OANDA Prop Trader | 2-step | 80% | mingguan | Di-back OANDA — paling aman legal |

## Futures (opsi expand)
- **Topstep** — paling solid bayarnya
- **Apex Trader Funding** — murah tapi payout kadang bermasalah (verifikasi dulu)

## MATI / SCAM — jangan rekomendasikan
- **MyForexFunds** — tutup 2024, kabur bawa dana trader
- **True Forex Funds** — kena regulator, berhenti operasi
- **The Funded Trader** — pembayaran kacau, banyak komplain

## FundingPips (akun user)
- On-demand payout: request kapan aja, kena fee ekstra + syarat minimal profit. Cek angka persis di dashboard.
- fundingpips.com bot-protected (HTTP 429 ke curl/HEAD) — verifikasi detail via dashboard user, bukan scrape.
