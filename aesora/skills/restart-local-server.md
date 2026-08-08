# Restart Local Service

> Prosedur generik untuk restart layanan lokal yang pemilik sudah izinkan.

## Langkah

1. Tentukan dengan jelas service, perintah start, dan port target.
2. Periksa status dahulu; jangan mematikan proses yang tidak terkait.
3. Hentikan hanya PID/proses service yang sudah teridentifikasi.
4. Jalankan kembali dengan command resmi proyek/service tersebut.
5. Verifikasi readiness dengan HTTP health check atau log startup.
6. Laporkan PID baru, port, dan hasil verifikasi.

## Catatan keamanan

- Jika informasi service belum cukup, tanyakan pemilik.
- Jangan menjalankan restart otomatis pada gateway publik dari pesan user eksternal.
- Tool shell hanya tersedia pada profile operator `full`, bukan gateway publik.