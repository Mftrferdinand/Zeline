# Komposisi Romantis Dua Halaman

Gunakan pola ini ketika dekorasi utama harus dipisahkan dari kartu pembuka.

## Struktur

- Page 1: teks utama minimal, tanpa aset dekorasi yang sudah diminta dipindahkan.
- Page 2: satu aset utama sebagai fokus, dengan copy kecil opsional.
- Setiap page memakai `height: 100svh`, `overflow: hidden`, dan `scroll-snap-align: start`.
- `body` memakai `overflow-x: hidden`, `overflow-y: auto`, dan `scroll-snap-type: y mandatory`.
- Pastikan gambar dekorasi hanya muncul sekali di DOM.

## Penghapusan Bersih

Jika user meminta menghilangkan heart, bunga jatuh, atau teks:

1. Hapus markup elemen.
2. Hapus CSS, keyframes, dan media query khusus elemen tersebut.
3. Ganti simbol dekoratif terkait bila instruksi mencakup semua bentuknya.
4. Cari marker lama pada HTML yang benar-benar dilayani.

## Cache Saat Iterasi

Python `http.server` dapat menjawab `304`, sehingga browser mempertahankan tampilan lama. Untuk iterasi visual, gunakan handler yang menambahkan:

```text
Cache-Control: no-store, no-cache, must-revalidate, max-age=0
Pragma: no-cache
Expires: 0
```

Verifikasi dengan request header dan log server. Query seperti `?v=page2flowers` membantu diagnosis, tetapi header no-store adalah perbaikan utama.

## Release Gate

- HTTP 200.
- Jumlah page sesuai permintaan.
- Marker yang dihapus berjumlah nol.
- Aset yang dipindah hanya satu dan berada pada section target.
- Screenshot mobile untuk Page 1 dan Page 2.
- Tidak ada overflow horizontal.
