#!/data/data/com.termux/files/usr/bin/bash
#
# gh-unfollow-batch.sh — unfollow akun GitHub non-mutual secara bertahap.
#
# Dijalankan lewat cron sekali sehari. Tiap run memproses maksimal $DAILY_LIMIT
# akun dari queue, lalu berhenti. Queue = akun yang gua follow tapi dia nggak
# follow balik (dihitung sekali di awal, disimpan di file).
#
# Kenapa bertahap: GitHub AUP melarang "rank abuse, such as automated starring
# or following". Yang diincar kebijakan itu adalah inflasi angka (follow massal
# untuk menaikkan peringkat), bukan pembersihan. Tetap saja, pola paling aman
# adalah volume rendah + jeda lebar + berhenti total begitu ketemu sinyal limit.
#
# Aman dijalankan berkali-kali (idempoten): akun yang sudah diproses dihapus
# dari queue, jadi nggak akan disentuh dua kali.

set -uo pipefail

HOME_DIR="/data/data/com.termux/files/home"
QUEUE="$HOME_DIR/gh-unfollow-queue.txt"
DONE_LOG="$HOME_DIR/gh-unfollow-done.txt"
FAIL_LOG="$HOME_DIR/gh-unfollow-failed.txt"
STATE="$HOME_DIR/gh-unfollow-state.txt"

DAILY_LIMIT="${DAILY_LIMIT:-400}"
# Jeda antar request. Dokumen GitHub: "wait at least one second between each"
# untuk request mutatif (DELETE). Gua pakai 3s = 3x lebih longgar dari minimum.
SLEEP_SECS="${SLEEP_SECS:-3}"

export PATH="/data/data/com.termux/files/usr/bin:$PATH"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

# ── Guard 1: gh harus ada & login ────────────────────────────────────────────
if ! command -v gh >/dev/null 2>&1; then
  log "FATAL: gh CLI nggak ketemu di PATH"
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  log "FATAL: gh belum login"
  exit 1
fi

# ── Guard 2: scope user:follow WAJIB ada ────────────────────────────────────
# Tanpa ini GitHub balas 404 (bukan 403) dan tiap akun akan tercatat gagal
# tanpa sebab yang jelas. Lebih baik berhenti di sini.
SCOPES="$(gh api -i user 2>/dev/null | grep -i '^x-oauth-scopes:' | cut -d: -f2-)"
if ! printf '%s' "$SCOPES" | grep -qE '(^|[ ,])(user|user:follow)([ ,]|$)'; then
  log "FATAL: token nggak punya scope 'user:follow'."
  log "       Scope sekarang:$SCOPES"
  log "       Jalankan: gh auth refresh -h github.com -s user:follow"
  exit 78   # EX_CONFIG — cron nggak akan nganggap ini sukses
fi

# ── Bangun queue kalau belum ada ─────────────────────────────────────────────
if [ ! -f "$QUEUE" ]; then
  log "Queue belum ada, membangun dari data live GitHub..."
  TMP_ING="$(mktemp)"; TMP_ERS="$(mktemp)"
  if ! gh api user/following --paginate -q '.[].login' > "$TMP_ING" 2>/dev/null; then
    log "FATAL: gagal ambil daftar following"; rm -f "$TMP_ING" "$TMP_ERS"; exit 1
  fi
  if ! gh api user/followers --paginate -q '.[].login' > "$TMP_ERS" 2>/dev/null; then
    log "FATAL: gagal ambil daftar followers"; rm -f "$TMP_ING" "$TMP_ERS"; exit 1
  fi
  # Backup mentah — ini satu-satunya jalan pulih kalau salah unfollow
  cp "$TMP_ING" "$HOME_DIR/gh-following-backup.txt"
  cp "$TMP_ERS" "$HOME_DIR/gh-followers-backup.txt"
  sort "$TMP_ING" -o "$TMP_ING"; sort "$TMP_ERS" -o "$TMP_ERS"
  # comm -23 = ada di following, TIDAK ada di followers → non-mutual
  comm -23 "$TMP_ING" "$TMP_ERS" > "$QUEUE"
  rm -f "$TMP_ING" "$TMP_ERS"
  log "Queue dibuat: $(wc -l < "$QUEUE") akun non-mutual (mutual dilewati)"
fi

REMAIN_START=$(wc -l < "$QUEUE" | tr -d ' ')
if [ "$REMAIN_START" -eq 0 ]; then
  log "Queue kosong — semua akun non-mutual sudah diproses. Nggak ada kerjaan."
  exit 0
fi

log "Mulai. Queue: $REMAIN_START | target run ini: $DAILY_LIMIT | jeda: ${SLEEP_SECS}s"

ok=0; failed=0; processed=0; aborted=""

while [ "$processed" -lt "$DAILY_LIMIT" ]; do
  user="$(head -1 "$QUEUE")"
  [ -z "$user" ] && break

  # DELETE /user/following/{user} → 204 sukses, 404 scope/akun hilang
  http="$(gh api -X DELETE "user/following/$user" -i 2>/dev/null | head -1 | grep -oE '[0-9]{3}' | head -1)"
  http="${http:-000}"

  case "$http" in
    204)
      ok=$((ok+1))
      printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$user" >> "$DONE_LOG"
      ;;
    403|429)
      # Sinyal rate limit / abuse detection → BERHENTI TOTAL, jangan retry.
      # Akun ini TIDAK dihapus dari queue supaya dicoba lagi besok.
      log "STOP: HTTP $http di '$user' — sinyal rate limit. Berhenti, sisa queue diteruskan besok."
      aborted="rate-limit-$http"
      break
      ;;
    404)
      # Akun dihapus/ganti nama, atau memang sudah nggak di-follow. Bukan error fatal.
      failed=$((failed+1))
      printf '%s %s http=404\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$user" >> "$FAIL_LOG"
      ;;
    *)
      failed=$((failed+1))
      printf '%s %s http=%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$user" "$http" >> "$FAIL_LOG"
      ;;
  esac

  # Buang dari queue HANYA kalau bukan abort, biar nggak ada yang kelewat
  sed -i '1d' "$QUEUE"
  processed=$((processed+1))
  sleep "$SLEEP_SECS"
done

REMAIN=$(wc -l < "$QUEUE" | tr -d ' ')
FOLLOWING_NOW=$(gh api user -q '.following' 2>/dev/null || echo '?')

log "Selesai. sukses=$ok gagal=$failed | sisa queue=$REMAIN | following sekarang=$FOLLOWING_NOW"

# Ringkasan buat cron (stdout terakhir = isi notifikasi)
if [ -n "$aborted" ]; then
  echo "STATUS=aborted:$aborted ok=$ok failed=$failed remaining=$REMAIN following=$FOLLOWING_NOW"
else
  echo "STATUS=ok ok=$ok failed=$failed remaining=$REMAIN following=$FOLLOWING_NOW"
fi
