# News Scraper

> Ambil berita atau cari info apapun dari Google News RSS. Format link + deskripsi, English title, spasi tiap paragraf.

Cari berita/info dari Google News RSS buat topik apapun. Output bisa langsung dipake di Fundamental section.

## Scripts

| Topik | Perintah |
|-------|----------|
| Gold / XAUUSD | `python3 ~/.zeline/scripts/news_xauusd.py` |
| BTC / Crypto | `python3 ~/.zeline/scripts/news_btc.py` |
| Apapun | `python3 ~/.zeline/scripts/news_search.py "keyword"` |

## Format Output

```\nTOPIK - News\n\n1. [Judul Berita](link) : Deskripsi singkat.\n\n2. [Judul Berita Lain](link) : Deskripsi singkat.\n...\n```\n\nLihat `references/output-format.md` untuk contoh lengkap XAUUSD, BTC, dan generic search.

- Judul pake English title: "XAUUSD - Gold News", "BTC - Crypto News"
- Format tiap berita: `[Judul](link) : deskripsi singkat`
- Ada spasi tiap paragraf (baris kosong antar berita)
- Link HARUS asli — jangan pernah potong jadi "(link)" doang
- Maksimal 8-9 berita

## Cara Kerja

### Link HARUS Lengkap
Jangan pernah potong link jadi "(link)" doang. Setiap berita WAJIB punya URL asli. User bakal komentar kalo ada link ilang. Kalo panjang ya tulis aja.

### Format output langsung dari script (tanpa edit manual):
1. Panggil script → langsung output
2. Kalo perlu summarise buat Fundamental section, ambil 2-3 berita teratas
3. Tulis ulang dalam Bahasa Indonesia — 2-3 kalimat naratif
4. Jangan nambahin link lagi di ringkasan Fundamental

### Keyword Mapping (Google News gak kirim deskripsi asli)
Google News RSS gak kirim deskripsi asli. Pake keyword mapping:
- "death cross" -> Death Cross terjadi, sinyal bearish jangka panjang
- "4,000" atau "4.000" -> Gold tembus $4,000 pertama kali sejak Nov 2025
- "weekly" -> Gold menuju pekan keempat kerugian beruntun
- "bearish" -> Sentimen bearish masih dominan
- "Fed" -> Kekhawatiran kenaikan suku bunga The Fed
- "PCE" -> Data PCE bisa melemahkan ekspektasi kenaikan suku bunga
- "NFP" -> NFP akhir pekan ini jadi katalis utama
- "forecast" -> Proyeksi harga jangka pendek hingga menengah
- "China" -> Pembelian China bisa jadi penahan harga
- "etf" -> ETF Bitcoin spot jadi sorotan pasar
- "halving" -> Dampak halving Bitcoin masih terasa

## Cara Pakai buat Fundamental Section
1. Panggil script berita yang sesuai
2. Ambil 2-3 headline paling relevan (Fed, inflasi, geopolitik)
3. Tulis ulang jadi paragraf naratif — jangan copy paste mentah
4. Jangan sebut statistik harga (turun $X, turun X% dalam Y menit)
5. Jangan sebut harga spot/futures — itu urusan Technical section
6. **WAJIB embed link di kalimat** pake format `[teks](link)`. Jangan taruh link terpisah di akhir paragraf
7. **Fear & Greed Index WAJIB** disertakan dengan link ke `[alternative.me](https://alternative.me)`


---

## Lampiran: `references/output-format.md`

# News Output Format Reference

## XAUUSD

```
XAUUSD - Gold News

1. [Gold Soars As Falling Crude Oil Prices...](https://www.nasdaq.com/...) : Harga emas melonjak setelah minyak turun.

2. [Gold Headed For Fourth Weekly Loss...](https://www.nasdaq.com/...) : Emas menuju pekan keempat kerugian beruntun.
...
```

## BTC / Crypto

```
BTC - Crypto News

1. [Bitcoin ETFs Set for Worst Month...](https://news.google.com/...) : Outflow Bitcoin ETF $4 miliar bulan ini.

2. [Bitcoin's high-conviction holders...](https://news.google.com/...) : Pemegang setia Bitcoin mulai jual saat harga terendah.
...
```

## Generic Search

```
Ipad 11Th Gen Price 2026 - News

1. [The Cheapest iPad Is Also the Best Tablet...](https://news.google.com/...) : Ulasan iPad termurah.
...
```

## Aturan Format Output

1. Judul English: `TOPIK - News` (bukan "Berita XAUUSD Terbaru")
2. Setiap berita: `[Judul](link) : deskripsi`
3. Ada baris kosong antar setiap berita
4. Link HARUS asli — jangan potong jadi "(link)"
5. Maksimal 8-9 berita
6. Deskripsi dalam Bahasa Indonesia kalo bisa, English kalo dari RSS feed asli
