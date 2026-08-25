#!/data/data/com.termux/files/usr/bin/bash
# Tabitoken (one-api / New API panel) multi-account daily check-in.
# Solves Cloudflare Turnstile via 2Captcha PER ACCOUNT, then checks in.
# CRITICAL: checks checked_in_today BEFORE solving so we never waste a
# 2Captcha fee on an account that already checked in.
# Tokens live in tabi_tokens.txt (one per line, "TOKEN|label", # = comment).
set -o pipefail

CAP="${TABI_2CAPTCHA_KEY:-PUT_2CAPTCHA_KEY_HERE}"   # 2captcha api key
SITEKEY="0x4AAAAAAEGV81TArluaPQGB"                   # tabitoken turnstile site key
BASE="https://tabitoken.com"
UA="Mozilla/5.0"
TOKFILE="$HOME/.zeline/scripts/tabi_tokens.txt"
QPU=500000   # quota_per_unit: credits per $1

[ -f "$TOKFILE" ] || { echo "ERR: $TOKFILE tidak ada"; exit 1; }

solve_turnstile() {
  local id r i
  id=$(curl -s "https://2captcha.com/in.php" --data-urlencode "key=$CAP" \
    --data-urlencode "method=turnstile" --data-urlencode "sitekey=$SITEKEY" \
    --data-urlencode "pageurl=$BASE/" -d "json=1" 2>/dev/null \
    | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['request'] if d.get('status')==1 else '')")
  [ -z "$id" ] && return 1
  for i in $(seq 1 20); do
    sleep 7
    r=$(curl -s "https://2captcha.com/res.php?key=$CAP&action=get&id=$id&json=1" 2>/dev/null)
    if echo "$r" | grep -q '"status":1'; then
      echo "$r" | python3 -c "import sys,json;print(json.load(sys.stdin)['request'])"
      return 0
    fi
  done
  return 1
}

echo "=== Tabitoken check-in $(date '+%Y-%m-%d %H:%M') ==="
while IFS= read -r line; do
  line="${line%$'\r'}"
  [ -z "$line" ] && continue
  case "$line" in \#*) continue;; esac
  TK="${line%%|*}"; LABEL="${line#*|}"; [ "$LABEL" = "$line" ] && LABEL="?"

  SELF=$(curl -s -H "Authorization: Bearer $TK" -A "$UA" "$BASE/api/user/self" 2>/dev/null)
  NAME=$(echo "$SELF" | python3 -c "import sys,json
try: print(json.load(sys.stdin)['data']['display_name'])
except: print('')" 2>/dev/null)
  if [ -z "$NAME" ]; then echo "[$LABEL] SKIP: token invalid/expired"; continue; fi

  # CHECK FIRST — never solve captcha for an already-done account.
  CI=$(curl -s -H "Authorization: Bearer $TK" -A "$UA" "$BASE/api/user/checkin" 2>/dev/null)
  if echo "$CI" | grep -q '"checked_in_today":true'; then
    BAL=$(echo "$SELF" | python3 -c "import sys,json;print('%.2f'%(json.load(sys.stdin)['data']['quota']/$QPU))" 2>/dev/null)
    echo "[$LABEL] $NAME — sudah check-in hari ini ✅ (saldo \$$BAL)"; continue
  fi

  TS=$(solve_turnstile)
  if [ -z "$TS" ]; then echo "[$LABEL] $NAME — GAGAL solve turnstile"; continue; fi
  RES=$(curl -s -H "Authorization: Bearer $TK" -A "$UA" -X POST \
    "$BASE/api/user/checkin?turnstile=$TS" 2>/dev/null)
  # report in $ using the awarded quota
  MSG=$(curl -s -H "Authorization: Bearer $TK" -A "$UA" "$BASE/api/user/checkin" 2>/dev/null | python3 -c "import sys,json
try:
 recs=json.load(sys.stdin)['data']['stats']['records']; q=recs[0]['quota_awarded']; print('OK +\$%.2f'%(q/$QPU))
except: print('done')" 2>/dev/null)
  echo "[$LABEL] $NAME — $MSG"
  sleep 2
done < "$TOKFILE"
echo "=== selesai ==="
