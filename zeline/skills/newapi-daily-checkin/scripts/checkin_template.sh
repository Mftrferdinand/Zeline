#!/usr/bin/env bash
# New-API / one-api daily check-in — SANITIZED TEMPLATE.
# Solves Cloudflare Turnstile via 2Captcha, then checks in each account.
# Supports both plain panels (tabitoken) and panels needing a
# New-Api-User header (gorouter). Fill in the REPLACE_ME values and put
# real secrets in a local, gitignored tokens file — never commit them.
#
# tokens file line formats:
#   plain panel:    TOKEN|label
#   New-Api-User:   TOKEN|USERID|label
set -o pipefail

CAP="REPLACE_ME_2CAPTCHA_KEY"          # 2captcha api key (keep local!)
BASE="https://REPLACE_ME_PANEL"        # e.g. https://tabitoken.com
SITEKEY="REPLACE_ME_TURNSTILE_SITEKEY" # from GET /api/status
NEEDS_USER_HEADER="false"              # "true" for gorouter-style panels
TOKFILE="${TOKFILE:-./REPLACE_ME_tokens.txt}"
UA="Mozilla/5.0"

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
      echo "$r" | python3 -c "import sys,json;print(json.load(sys.stdin)['request'])"; return 0
    fi
  done
  return 1
}

echo "=== check-in $(date '+%Y-%m-%d %H:%M') @ $BASE ==="
while IFS= read -r line; do
  line="${line%$'\r'}"; [ -z "$line" ] && continue
  case "$line" in \#*) continue;; esac

  TK="${line%%|*}"; rest="${line#*|}"
  if [ "$NEEDS_USER_HEADER" = "true" ]; then
    UUID="${rest%%|*}"; LABEL="${rest#*|}"; [ "$LABEL" = "$rest" ] && LABEL="?"
    H=(-H "Authorization: Bearer $TK" -H "New-Api-User: $UUID" -A "$UA")
  else
    LABEL="$rest"; [ "$LABEL" = "$line" ] && LABEL="?"
    H=(-H "Authorization: Bearer $TK" -A "$UA")
  fi

  SELF=$(curl -s "${H[@]}" "$BASE/api/user/self" 2>/dev/null)
  NAME=$(echo "$SELF" | python3 -c "import sys,json
try: print(json.load(sys.stdin)['data']['display_name'])
except: print('')" 2>/dev/null)
  [ -z "$NAME" ] && { echo "[$LABEL] SKIP: token/id invalid"; continue; }
  BEFORE=$(echo "$SELF" | python3 -c "import sys,json;print('%.2f'%(json.load(sys.stdin)['data']['quota']/500000))" 2>/dev/null)

  CI=$(curl -s "${H[@]}" "$BASE/api/user/checkin" 2>/dev/null)
  if echo "$CI" | grep -q '"checked_in_today":true'; then
    echo "[$LABEL] $NAME — already checked in (\$$BEFORE)"; continue
  fi

  TS=$(solve_turnstile)
  [ -z "$TS" ] && { echo "[$LABEL] $NAME — FAILED turnstile"; continue; }
  curl -s "${H[@]}" -X POST "$BASE/api/user/checkin?turnstile=$TS" >/dev/null 2>&1
  AFTER=$(curl -s "${H[@]}" "$BASE/api/user/self" 2>/dev/null | python3 -c "import sys,json;print('%.2f'%(json.load(sys.stdin)['data']['quota']/500000))" 2>/dev/null)
  echo "[$LABEL] $NAME — checked in: yesterday \$$BEFORE -> now \$$AFTER"
  sleep 2
done < "$TOKFILE"
echo "=== done ==="
