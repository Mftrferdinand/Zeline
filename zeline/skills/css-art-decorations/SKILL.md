---
name: css-art-decorations
description: "Seni dekoratif pure-CSS untuk halaman one-off (kartu hadiah/romantis, landing dekoratif): bloom 2-lapis (bunga), bouquet dalam bucket/ember, hati 3D glossy, grid background kotak-kotak + lantai 3D. Termasuk preferensi iterasi UI user (normal-size font, subtil, premium)."
---

# CSS Art Decorations

Pola pure-CSS untuk hiasan kartu/landing satu halaman (mobile-first, tanpa gambar eksternal). Dikembangkan dari proyek kartu "For Puji" (twenty3ph, web3addicter-site) — dipakai berulang, bukan sekali pakai.

## Preferensi user (Aes) untuk halaman dekoratif

- **Font jangan gede-gede** — "tulisan font nya jangan gede semua, normal aja". Nama: `clamp(27px,7vw,38px)`; note 15px; closing 17px. Nama 1 baris: `white-space:nowrap`.
- **Subtil & premium** — elemen nyatu, bukan blok mencolok. Opacity rendah untuk depth (`filter:blur(.3-.9px)` di elemen jauh).
- Iterasi cepat "perbagus": terima instruksi singkat, ubah langsung, verifikasi `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:PORT/`, lalu minta user refresh. "balikin ke sebelumnya tp percantik" = placement lama + treatment halus.
- Hapus elemen yang diminta (mis. label "FOR PUJI") tanpa tanya.
- Kalau `vision_analyze` 502 (proxy down) berulang kali: lanjut dari instruksi teks user, jangan berhenti minta gambar.

## Bloom 2-lapis (bunga mekar) — pure CSS

```html
<div class="bloom"><span class="petal pnk"></span>×8 <span class="petal2 pnk"></span>×8 <span class="core"></span></div>
```

```css
.petal{position:absolute;left:50%;top:50%;width:32%;height:50%;transform-origin:50% 100%;
  border-radius:100% 0 100% 0; /* ujung kelopak runcing */
  background:linear-gradient(135deg,#fff 0%,var(--pink1) 45%,var(--pink3) 100%);
  box-shadow:inset -3px -4px 9px rgba(220,120,170,.35),inset 2px 2px 5px rgba(255,255,255,.9);
  border:1px solid rgba(255,255,255,.75)}
/* rotasi kelopak: nth-child(1..8) → 0,45,90,...,315deg, translate(-50%,-100%) */
.petal2{/* sama, width:21%;height:36%; dirotasi 22.5,67.5,...,337.5deg (stagger) z-index:1 */}
.core{/* circle 20%, radial #fff6d6→#f6c453→#e0a83e, z-index:2 */}
```

Kunci "mekar": 2 lapis kelopak staggered (offset 22.5°) + gradien tepi lebih pekat. Palette via modifier class: `.pnk` (pink: #ffd7e4→#ff8fb3→#ff6f9e), `.gld` (kuning: #fff7d1→#ffd54f→#f2b23d). Daun: `border-radius:100% 0 100% 0`, `scaleX(-1)` untuk variasi.

## Bucket bouquet (ember penuh bunga)

- `.bq-wrap`: `display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:center` + margin negatif per bloom (`margin:-12px -8px -22px`) biar tumpang tindih rapat — **6 bloom minimal**, bukan 3, biar keliatan penuh.
- Bucket: `border-radius:10px 10px 22px 22px`, gradient pink, `::before` = rim lebih lebar (left/right:-8px), `::after` = highlight oval kecil di tengah.
- **Trik "bunga di dalam ember"**: bucket `position:absolute;top:118px;z-index:2` menimpa bagian bawah bloom (z-index lebih tinggi), plus `.mouth` (gradient gelap tipis di bibir ember) di belakang bunga.
- Animasi bloom: `bloomDrift` translateY -6px + rotate, delay selang-seling `nth-child(odd/even)` negatif.

## Background grid kotak-kotak

- Full-page: `background-image: linear-gradient(rgba(150,180,255,.14) 1px,transparent 1px), linear-gradient(90deg,...)` `background-size:38px 38px`.
- **Floor grid 3D** (lantai jalan ke belakang): `.plane` `transform:rotateX(62deg)` + `background-size:46px 46px`, animasi `gridMove` (`to{background-position:0 46px,0 0}`), `mask-image:radial-gradient(ellipse 90% 100% at 50% 100%,#000 20%,transparent 72%)` buat fade — subtle, jangan ganggu keterbacaan teks.

## Hati 3D glossy

Kotak 52px `rotate(-45deg)` + `::before/::after` lingkaran 52px (atas/kiri) = bentuk hati; gradient biru (#8fb4ff→#4a78f2→#2e57d6), `box-shadow` inset highlight kaca + bayangan bawah, gloss ellipse putih blur, beat `scale(1)→1.07`.

## Iterasi yang harus tampak nyata

- Jika user bilang **“tidak ada perubahan”**, bedakan dahulu antara cache dan desain yang terlalu subtil:
  1. Periksa respons aktual serta marker HTML/CSS yang dilayani.
  2. Cari `304 Not Modified` atau header cache pada log/server.
  3. Untuk preview statis yang sedang diiterasi, pakai server dengan `Cache-Control: no-store` dan query version sementara.
  4. Setelah cache dipastikan bersih, buat perubahan pembeda yang jelas tetapi tetap premium (jumlah, ukuran, posisi, atau material), lalu render viewport target.
- Instruksi **“hilangkan X”** berarti hapus markup dan CSS/animasi terkait, bukan hanya `display:none`; cek marker lama tidak tersisa pada HTML yang dilayani.
- Ketika dekorasi dipindahkan ke page berikutnya, jangan menduplikasi aset di page pertama. Gunakan section `100svh`, `scroll-snap-align:start`, dan pastikan aset hanya muncul sekali.
- Untuk permintaan berurutan seperti “hapus animasi, hapus love, pindahkan bunga ke Page 2, hapus teks, kecilkan caption”, perlakukan sebagai checklist atomik dan verifikasi setiap syarat sebelum melapor.

Panduan komposisi dua halaman: `references/romantic-two-page-composition.md`.

Modal pemutar lagu liquid-glass (disc vinyl berputar, progress bar, judul+artis+lirik, auto-stop saat ditutup): `references/glass-music-player-modal.md`.

## Verifikasi

Server lokal: cek HTTP 200, marker versi yang benar, jumlah elemen, dan ketiadaan marker yang diminta dihapus. HTTP 200 saja tidak membuktikan browser melihat versi baru; periksa juga header cache/log `304`. Render pada viewport mobile target dan pastikan tidak ada overflow horizontal, bentrokan dekorasi, atau aset yang muncul di page salah. File contoh ada di `~/twenty3ph/`, `~/web3addicter-site/` (multi-session, edit lanjutan dari referensi session_search "Puji").
