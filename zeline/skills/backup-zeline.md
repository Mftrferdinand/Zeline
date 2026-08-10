# Backup Zeline

> Prosedur backup aman untuk config, memory, skills, dan state Zeline milik satu user.

## Langkah

1. Tentukan folder data Zeline:
   ```bash
   zeline config path
   ```
   Biasanya `~/.zeline/`.
2. Hentikan gateway bila sedang menulis state penting.
3. Buat archive privat:
   ```bash
   tar -czf ~/zeline-backup.tar.gz -C ~ .zeline
   ```
4. Verifikasi archive:
   ```bash
   tar -tzf ~/zeline-backup.tar.gz | head
   ```
5. Simpan archive di lokasi terenkripsi/privat karena dapat memuat token platform dan API key.

## Pemulihan

```bash
tar -xzf ~/zeline-backup.tar.gz -C ~
```

Lalu jalankan `zeline doctor` sebelum menyalakan gateway.