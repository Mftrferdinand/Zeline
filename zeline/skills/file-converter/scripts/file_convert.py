#!/usr/bin/env python3
"""file_convert.py — konversi file untuk skill file-converter.

Engine (semua prebuilt, ringan di Termux):
- Pillow           → gambar antar format + gambar→PDF
- poppler pdftoppm → PDF→gambar (per halaman)

Perintah:
  info   <file>                        → tipe, ukuran, (PDF: jumlah halaman)
  img2pdf <img...> -o out.pdf          → 1+ gambar jadi 1 PDF (urutan sesuai argumen)
  pdf2img <file.pdf> [--fmt jpg|png] [--dpi 150] [--page N]
                                       → PDF jadi gambar per halaman → ~/downloads/
  img    <in> -o <out.EXT>             → konversi gambar (jpg/png/webp/bmp/gif/tiff)

Output ke ~/downloads/ (kecuali -o absolut). Cetak "FILE: <path>" per hasil +
"SIZE_MB: <n>" biar agent tahu apa yang dikirim ke chat & cek limit 50MB Telegram.
"""
import argparse
import glob
import os
import subprocess
import sys

DL_DIR = os.path.expanduser("~/downloads")
TELEGRAM_LIMIT_MB = 50
IMG_EXT = {"jpg", "jpeg", "png", "webp", "bmp", "gif", "tiff", "tif"}


def _ensure_dir():
    os.makedirs(DL_DIR, exist_ok=True)


def _out_path(name):
    _ensure_dir()
    return name if os.path.isabs(name) else os.path.join(DL_DIR, name)


def _emit(path):
    if not path or not os.path.exists(path):
        print(f"ERROR: hasil tidak ditemukan: {path}", file=sys.stderr)
        return 1
    mb = os.path.getsize(path) / (1024 * 1024)
    print(f"FILE: {path}")
    print(f"SIZE_MB: {mb:.2f}")
    if mb > TELEGRAM_LIMIT_MB:
        print(f"WARN: {mb:.1f}MB > {TELEGRAM_LIMIT_MB}MB limit Telegram — kirim path lokal.",
              file=sys.stderr)
    return 0


def cmd_info(a):
    p = a.file
    if not os.path.exists(p):
        print(f"ERROR: file tidak ada: {p}", file=sys.stderr)
        return 1
    ext = p.rsplit(".", 1)[-1].lower() if "." in p else "?"
    mb = os.path.getsize(p) / (1024 * 1024)
    print(f"FILE: {p}")
    print(f"TYPE: {ext}")
    print(f"SIZE_MB: {mb:.2f}")
    if ext == "pdf":
        r = subprocess.run(["pdfinfo", p], capture_output=True, text=True, timeout=30)
        for line in r.stdout.splitlines():
            if line.lower().startswith(("pages:", "page size:")):
                print("  " + line.strip())
    elif ext in IMG_EXT:
        try:
            from PIL import Image
            with Image.open(p) as im:
                print(f"  DIMENSIONS: {im.width}x{im.height}  MODE: {im.mode}")
        except Exception:  # noqa: BLE001
            pass
    return 0


def cmd_img2pdf(a):
    from PIL import Image
    imgs = []
    for f in a.images:
        if not os.path.exists(f):
            print(f"ERROR: gambar tidak ada: {f}", file=sys.stderr)
            return 1
        imgs.append(Image.open(f).convert("RGB"))
    out = _out_path(a.output)
    imgs[0].save(out, save_all=True, append_images=imgs[1:])
    return _emit(out)


def cmd_pdf2img(a):
    p = a.pdf
    if not os.path.exists(p):
        print(f"ERROR: PDF tidak ada: {p}", file=sys.stderr)
        return 1
    _ensure_dir()
    fmt = (a.fmt or "jpg").lower()
    stem = os.path.splitext(os.path.basename(p))[0]
    prefix = os.path.join(DL_DIR, stem)
    cmd = ["pdftoppm", f"-{'jpeg' if fmt in ('jpg','jpeg') else fmt}",
           "-r", str(a.dpi or 150)]
    if a.page:
        cmd += ["-f", str(a.page), "-l", str(a.page)]
    cmd += [p, prefix]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        print("ERROR pdf2img:", (r.stderr or "").strip()[:200], file=sys.stderr)
        return 1
    real_ext = "jpg" if fmt in ("jpg", "jpeg") else fmt
    pages = sorted(glob.glob(f"{prefix}*.{real_ext}") + glob.glob(f"{prefix}*.jpeg"))
    if not pages:
        print("ERROR: tidak ada halaman dihasilkan", file=sys.stderr)
        return 1
    rc = 0
    for pg in pages:
        rc |= _emit(pg)
    print(f"PAGES: {len(pages)}")
    return rc


def cmd_img(a):
    from PIL import Image
    if not os.path.exists(a.input):
        print(f"ERROR: input tidak ada: {a.input}", file=sys.stderr)
        return 1
    out = _out_path(a.output)
    ext = out.rsplit(".", 1)[-1].lower()
    im = Image.open(a.input)
    # JPEG/BMP tak dukung alpha → konversi RGB
    if ext in ("jpg", "jpeg", "bmp"):
        im = im.convert("RGB")
    im.save(out)
    return _emit(out)


def main(argv=None):
    p = argparse.ArgumentParser(description="Konversi file: pdf<->img + img antar format")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("info"); i.add_argument("file"); i.set_defaults(func=cmd_info)

    ip = sub.add_parser("img2pdf")
    ip.add_argument("images", nargs="+")
    ip.add_argument("-o", "--output", default="output.pdf")
    ip.set_defaults(func=cmd_img2pdf)

    pi = sub.add_parser("pdf2img")
    pi.add_argument("pdf")
    pi.add_argument("--fmt", choices=["jpg", "jpeg", "png"], default="jpg")
    pi.add_argument("--dpi", type=int, default=150)
    pi.add_argument("--page", type=int, help="cuma halaman N (default semua)")
    pi.set_defaults(func=cmd_pdf2img)

    im = sub.add_parser("img")
    im.add_argument("input")
    im.add_argument("-o", "--output", required=True, help="output.EXT (png/jpg/webp/...)")
    im.set_defaults(func=cmd_img)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
