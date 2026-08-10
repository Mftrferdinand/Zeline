# Self Analysis

> Analisis identitas, konfigurasi runtime, model, provider, protokol, tools, dan batas kemampuan Zeline secara aman.

## Kapan digunakan

Gunakan skill ini saat user bertanya:

- "Kamu model apa?"
- "Provider kamu apa?"
- "Kamu bisa apa saja?"
- "Analisis diri sendiri"
- "Kenapa tool/fitur tertentu tidak tersedia?"

## Langkah

1. Panggil `runtime_info` untuk membaca fakta runtime aktual.
2. Jelaskan secara langsung:
   - identity/framework/lab;
   - model ID aktif;
   - provider base URL;
   - protokol `openai` atau `anthropic`;
   - tool profile dan daftar tools yang tersedia.
3. Bedakan fakta dari inferensi. Jangan menebak vendor hanya dari nama model bila provider adalah router/proxy.
4. Bila user meminta diagnosis provider, gunakan skill `check-provider-health` dan laporkan status aktual.
5. Untuk audit kemampuan, petakan permintaan user terhadap daftar tools dan skill yang tersedia; sebutkan keterbatasan dengan jujur.

## Batas rahasia

- Model ID, base URL provider, protokol, nama framework, profile, dan nama tools **bukan secret** dan boleh dijelaskan.
- API key, bot token, webhook token, private key, seed phrase, serta isi credential **tidak boleh ditampilkan**.
- Jangan mengarang model/provider. Jika `runtime_info` tidak tersedia atau gagal, katakan bahwa runtime tidak dapat diverifikasi.

## Verifikasi

Jawaban selesai bila menyebut fakta runtime yang diminta, menyembunyikan seluruh secret, dan tidak mengklaim kemampuan yang tidak ada di daftar tools/skills.
