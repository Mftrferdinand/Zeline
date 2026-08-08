# Check Service Health

> Prosedur generik untuk memeriksa apakah layanan HTTP lokal sehat.

## Langkah

1. Identifikasi port atau URL yang perlu diperiksa.
2. Cek listener lokal dengan tool shell yang tersedia, misalnya:
   ```bash
   ss -tlnp
   ```
3. Cek respons HTTP:
   ```bash
   curl -sS -o /dev/null -w "%{http_code}" http://127.0.0.1:PORT/health
   ```
4. Interpretasi hasil:
   - `200`–`299`: layanan merespons sehat.
   - `000`: tidak ada koneksi / service kemungkinan mati.
   - `4xx`/`5xx`: service hidup tetapi endpoint atau aplikasi bermasalah.
5. Laporkan port, kode HTTP, dan langkah lanjutan secara singkat.

## Verifikasi

Jangan menyatakan service sehat sebelum ada hasil HTTP aktual.