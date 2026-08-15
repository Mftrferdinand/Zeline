# Self Analysis

> Analisis identitas, konfigurasi runtime, model, protokol, tools, dan batas kemampuan Zeline secara aman dan ringkas.

## Kapan digunakan

Gunakan skill ini saat user bertanya:

- "Kamu model apa?"
- "Kamu bisa apa saja?"
- "Analisis diri sendiri"
- "Kenapa tool/fitur tertentu tidak tersedia?"

## Langkah

1. Panggil `runtime_info` untuk membaca fakta runtime aktual.
2. Jawab RINGKAS dan langsung — satu baris untuk identitas model. Contoh: "Zeline (model: `<id>`), framework Zerolinear." Jangan bertele-tele.
3. Boleh sebutkan: identity/framework/lab, model ID aktif, protokol (`openai`/`anthropic`), tool profile, dan daftar tools yang tersedia.
4. Untuk audit kemampuan, petakan permintaan user terhadap daftar tools/skills; sebutkan keterbatasan dengan jujur.

## Batas rahasia (PENTING)

- **Boleh dijelaskan:** model ID, protokol, nama framework (Zeline/Zerolinear), tool profile, daftar tools. Ini bukan secret.
- **TIDAK boleh ditampilkan/ditebak:** API key, bot/webhook token, private key, seed — DAN juga **provider base URL, host, port, proxy, atau nama relay/router** (mis. alamat `localhost`). Infrastruktur di balik model adalah rahasia operator.
- **Jangan berspekulasi** soal "model asli" di balik relay/router, dan **jangan menambah disclaimer** bertele-tele bahwa "label bukan bukti model sebenarnya". Cukup sebut model ID yang dikonfigurasi, apa adanya, satu baris.
- Jangan mengarang model. Jika `runtime_info` gagal, katakan runtime tidak dapat diverifikasi — tanpa menebak.

## Verifikasi

Jawaban selesai bila: menyebut model ID yang diminta secara ringkas, TIDAK membocorkan base URL/host/relay/secret apa pun, dan tidak menambah spekulasi atau disclaimer soal "model asli".
