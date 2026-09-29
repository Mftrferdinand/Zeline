---
name: file-converter
description: |
  Konversi file: PDF↔gambar dan gambar antar format. img2pdf (banyak
  gambar jadi 1 PDF), pdf2img (PDF jadi JPG/PNG per halaman), dan konversi
  gambar (jpg/png/webp/bmp/gif/tiff). Engine ringan prebuilt: Pillow +
  poppler (pdftoppm) — jalan di Termux tanpa compile berat. Hasil dikirim ke
  chat Telegram sebagai file (MEDIA:path); kalau > 50MB kirim path lokal. Load
  saat user minta "convert pdf ke jpg", "jpg ke pdf", "png ke webp", "gabung
  gambar jadi pdf", "ubah format gambar".
metadata:
  zeline:
    tags: [convert, pdf, image, jpg, png, webp, pdftoppm, pillow, file]
    category: productivity
---

# File Converter — PDF ↔ gambar + konversi gambar

Kirim file → agent konversi → kirim hasil ke chat. Script:
`scripts/file_convert.py`.

## Prasyarat

- **Pillow** (gambar antar format + gambar→PDF) — biasanya sudah ada
- **poppler** (`pdftoppm`, `pdfinfo`) untuk PDF→gambar
  - Termux: `pkg install poppler libjpeg-turbo`
  - Debian/Ubuntu: `apt install poppler-utils`
  - macOS: `brew install poppler`
- `libjpeg-turbo` diperlukan agar Pillow bisa baca/tulis JPEG (`features.check('jpg')` harus True)

## Perintah script

```bash
SC=~/.zeline/skills/.../file_convert.py

# metadata: tipe, ukuran, (pdf) jumlah halaman, (img) dimensi
python3 "$SC" info <file>

# gambar → PDF (1+ gambar, urutan = urutan argumen)
python3 "$SC" img2pdf a.jpg b.png c.jpg -o hasil.pdf

# PDF → gambar per halaman (ke ~/downloads/)
python3 "$SC" pdf2img dok.pdf --fmt jpg --dpi 150        # semua halaman
python3 "$SC" pdf2img dok.pdf --fmt png --page 3          # halaman 3 saja

# konversi gambar antar format
python3 "$SC" img foto.png -o foto.webp
python3 "$SC" img foto.webp -o foto.jpg
```

Script cetak `FILE: <path>` per hasil + `SIZE_MB: <n>`. `pdf2img` juga cetak
`PAGES: <n>`.

## Cara agent pakai (contoh)

**PDF → JPG:**
```
User: [kirim dok.pdf] convert ke jpg
Agent: [python3 SC pdf2img dok.pdf --fmt jpg]
       → MEDIA:/…/dok-1.jpg  MEDIA:/…/dok-2.jpg  (tiap halaman 1 file)
```

**Gambar → PDF (gabung):**
```
User: [kirim 3 foto] jadikan 1 pdf
Agent: [python3 SC img2pdf 1.jpg 2.jpg 3.jpg -o hasil.pdf]
       → MEDIA:/…/hasil.pdf
```

**Ubah format gambar:**
```
User: [kirim foto.png] jadiin webp
Agent: [python3 SC img foto.png -o foto.webp] → MEDIA:/…/foto.webp
```

## Pengiriman file

- Kirim via `MEDIA:<path>` — Telegram render gambar & PDF inline.
- **pdf2img menghasilkan 1 file per halaman** — kalau PDF banyak halaman
  (misal 20+), tanya user dulu apakah mau semua atau halaman tertentu
  (`--page N`), biar chat ga kebanjiran.
- **Limit Telegram 50MB** — kalau `SIZE_MB` > 50, kirim path lokal atau turunkan
  `--dpi` (mis. 100) untuk pdf2img.

## Format yang didukung

- **Gambar:** jpg/jpeg, png, webp, bmp, gif, tiff (baca & tulis via Pillow)
- **PDF → gambar:** jpg, png (via poppler, DPI bisa diatur)
- **Gambar → PDF:** semua format gambar di atas → 1 PDF multi-halaman

## Pitfalls

- **PNG/WebP transparan → JPG**: JPEG tak dukung alpha. Script auto-`convert("RGB")`
  untuk output jpg/bmp; area transparan jadi hitam. Kalau mau jaga transparansi,
  target png/webp.
- **PDF banyak halaman**: pdf2img bikin 1 gambar per halaman → banyak file. Untuk
  PDF tebal, pakai `--page N` atau konfirmasi ke user dulu.
- **DPI vs ukuran**: DPI tinggi (300) = gambar tajam tapi file besar (bisa lewat
  50MB). Default 150 seimbang; turunkan ke 100 kalau kena limit.
- **Pillow JPEG error `KeyError: 'JPEG'`**: libjpeg belum terpasang. `pkg install
  libjpeg-turbo` (Termux) lalu cek `python3 -c "from PIL import features;
  print(features.check('jpg'))"` → harus True.
- **Dokumen Office (docx/xlsx→pdf)**: butuh LibreOffice (`soffice`) yang berat di
  Termux — di luar scope skill ini. Fokus skill ini: PDF ↔ gambar + gambar antar
  format. Kalau user minta docx→pdf, kasih tahu perlu LibreOffice.
