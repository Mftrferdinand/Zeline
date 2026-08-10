# Check LLM Provider Health

> Prosedur generik untuk mendiagnosis provider OpenAI-compatible saat Zeline mendapat error 401, 403, 410, 429, atau 5xx.

## Langkah

1. Baca konfigurasi aman:
   ```bash
   zeline config show
   zeline doctor
   ```
2. Periksa kategori error:
   - `401`: API key invalid/tidak terkirim.
   - `403`: akses model/provider ditolak atau saldo/kuota tidak cukup.
   - `404`/`410`: model atau route tidak tersedia.
   - `429`: rate limit; tunggu lalu retry dengan backoff.
   - `5xx`: gangguan provider; coba lagi nanti atau ganti route.
3. Tes endpoint dengan request kecil **tanpa** menempel secret ke log/screenshot.
4. Pastikan `base_url` memiliki suffix `/v1` jika provider membutuhkan API OpenAI-compatible.
5. Jika model gagal sementara endpoint sehat, pilih model yang tersedia pada provider tersebut dan update lewat `zeline setup`.

## Verifikasi

Sebutkan kode/status aktual dan rekomendasi perbaikan. Jangan menganggap model sehat tanpa respons provider yang berhasil.