# Backup Aesora

> Prosedur backup aman untuk config, memory, skills, dan state Aesora milik satu user.

## Langkah

1. Tentukan folder data Aesora:
   ```bash
   aesora config path
   ```
   Biasanya `~/.aesora/`.
2. Hentikan gateway bila sedang menulis state penting.
3. Buat archive privat:
   ```bash
   tar -czf ~/aesora-backup.tar.gz -C ~ .aesora
   ```
4. Verifikasi archive:
   ```bash
   tar -tzf ~/aesora-backup.tar.gz | head
   ```
5. Simpan archive di lokasi terenkripsi/privat karena dapat memuat token platform dan API key.

## Pemulihan

```bash
tar -xzf ~/aesora-backup.tar.gz -C ~
```

Lalu jalankan `aesora doctor` sebelum menyalakan gateway.