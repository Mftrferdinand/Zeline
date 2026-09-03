#!/usr/bin/env bash
# Daily check-in for ANY "New-API / one-api" token panel.
#
# Nothing about a specific site is baked in. The panel tells this script how to
# drive it: GET /api/status carries the Turnstile site key, whether a captcha is
# required at all, the credits-per-unit divisor, and the display currency. A
# hardcoded site key or a hardcoded "500000 credits = $1" is wrong the moment you
# point the script at a different fork -- and every fork of one-api answers
# /api/status the same way.
#
# Usage:
#   PANEL=https://panel.example bash newapi_checkin.sh
#   PANEL=https://panel.example TOKENS=~/my_tokens.txt bash newapi_checkin.sh
#
# Environment:
#   PANEL              required. Panel origin, e.g. https://panel.example
#   TOKENS             tokens file. Default: $ZELINE_HOME/scripts/<host>_tokens.txt
#   CAPTCHA_KEY        2Captcha API key. Read from CAPTCHA_KEY_FILE when unset.
#   CAPTCHA_KEY_FILE   file holding the key. Default: ~/.2captcha_key
#   SITEKEY            override the discovered Turnstile site key (rarely needed)
#
# Tokens file, one account per line, "#" starts a comment:
#   TOKEN|label                 plain panel
#   TOKEN|USER_ID|label         panel that requires the New-Api-User header
# The USER_ID form is detected automatically, so either layout works.
#
# Only run this against panels you own or are explicitly authorised to use.
set -o pipefail

PANEL="${PANEL:-}"
if [ -z "$PANEL" ]; then
  echo "ERR: set PANEL to the panel origin, e.g. PANEL=https://panel.example" >&2
  exit 2
fi
PANEL="${PANEL%/}"
HOST="${PANEL#*://}"; HOST="${HOST%%/*}"

ZELINE_HOME="${ZELINE_HOME:-$HOME/.zeline}"
TOKENS="${TOKENS:-$ZELINE_HOME/scripts/${HOST}_tokens.txt}"
UA="Mozilla/5.0"

# The key never lives in this file. One rotated key in a script is a whole run of
# "captcha failed" messages that look like a panel outage.
CAPTCHA_KEY_FILE="${CAPTCHA_KEY_FILE:-$HOME/.2captcha_key}"
CAPTCHA_KEY="${CAPTCHA_KEY:-}"
if [ -z "$CAPTCHA_KEY" ] && [ -r "$CAPTCHA_KEY_FILE" ]; then
  CAPTCHA_KEY="$(tr -d '\r\n' < "$CAPTCHA_KEY_FILE")"
fi

if [ ! -f "$TOKENS" ]; then
  echo "ERR: tokens file not found: $TOKENS" >&2
  echo "     Create it with one account per line: TOKEN|label" >&2
  exit 2
fi

# ---------------------------------------------------------------- discovery
STATUS="$(curl -sS --max-time 25 -A "$UA" "$PANEL/api/status" 2>/dev/null)"
read -r NEEDS_CAPTCHA DISCOVERED_SITEKEY QPU CURRENCY RATE <<EOF
$(printf '%s' "$STATUS" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin).get("data", {}) or {}
except Exception:
    data = {}
needs = "true" if data.get("turnstile_check") else "false"
key = str(data.get("turnstile_site_key") or "-")
# quota_per_unit is credits per one display unit; every fork ships it.
try:
    qpu = float(data.get("quota_per_unit") or 500000) or 500000
except (TypeError, ValueError):
    qpu = 500000
currency = str(data.get("quota_display_type") or "USD").upper()
try:
    rate = float(data.get("usd_exchange_rate") or 0) or 0
except (TypeError, ValueError):
    rate = 0
print(needs, key, qpu, currency, rate)
' 2>/dev/null)
EOF
NEEDS_CAPTCHA="${NEEDS_CAPTCHA:-true}"
QPU="${QPU:-500000}"
CURRENCY="${CURRENCY:-USD}"
SITEKEY="${SITEKEY:-$DISCOVERED_SITEKEY}"
case "$CURRENCY" in
  CNY) SYM="¥" ;;
  EUR) SYM="€" ;;
  *)   SYM="$" ;;
esac

if [ "$NEEDS_CAPTCHA" = "true" ] && { [ -z "$SITEKEY" ] || [ "$SITEKEY" = "-" ]; }; then
  echo "ERR: $HOST wants a captcha but /api/status exposed no turnstile_site_key." >&2
  echo "     Pass it explicitly with SITEKEY=0x… if you can read it from the page." >&2
  exit 2
fi
if [ "$NEEDS_CAPTCHA" = "true" ] && [ -z "$CAPTCHA_KEY" ]; then
  echo "ERR: $HOST requires a Turnstile solve but no 2Captcha key was found." >&2
  echo "     Set CAPTCHA_KEY, or write the key to $CAPTCHA_KEY_FILE (chmod 600)." >&2
  exit 2
fi

# Fail fast on a dead key instead of reporting it once per account as a captcha
# failure. ERROR_KEY_DOES_NOT_EXIST looks exactly like a broken panel otherwise.
if [ "$NEEDS_CAPTCHA" = "true" ]; then
  BAL="$(curl -sS --max-time 25 "https://2captcha.com/res.php" \
    --data-urlencode "key=$CAPTCHA_KEY" -d "action=getbalance" -d "json=1" 2>/dev/null)"
  if ! printf '%s' "$BAL" | grep -q '"status":1'; then
    echo "ERR: the solver rejected this key: $BAL" >&2
    echo "     A rotated key fails every account; fix the key, not the panel." >&2
    exit 2
  fi
fi

solve_turnstile() {
  local id response attempt poll
  # A worker giving up (ERROR_CAPTCHA_UNSOLVABLE) is normal; retry before
  # declaring failure. The token is submitted immediately after solving because
  # Turnstile tokens expire within minutes.
  for attempt in 1 2 3; do
    id="$(curl -sS --max-time 30 "https://2captcha.com/in.php" \
      --data-urlencode "key=$CAPTCHA_KEY" \
      --data-urlencode "method=turnstile" \
      --data-urlencode "sitekey=$SITEKEY" \
      --data-urlencode "pageurl=$PANEL/" -d "json=1" 2>/dev/null \
      | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
print(d.get("request", "") if d.get("status") == 1 else "")' 2>/dev/null)"
    [ -z "$id" ] && continue
    for poll in $(seq 1 20); do
      sleep 7
      response="$(curl -sS --max-time 25 \
        "https://2captcha.com/res.php?key=$CAPTCHA_KEY&action=get&id=$id&json=1" 2>/dev/null)"
      if printf '%s' "$response" | grep -q '"status":1'; then
        printf '%s' "$response" | python3 -c 'import json,sys; print(json.load(sys.stdin)["request"])'
        return 0
      fi
      printf '%s' "$response" | grep -q 'CAPCHA_NOT_READY' || break
    done
  done
  return 1
}

# Auth shape differs per fork: some require New-Api-User on every request. Probe
# once rather than asking the operator to know which kind of fork this is.
auth_headers() {
  local token="$1" user_id="$2"
  if [ -n "$user_id" ]; then
    printf '%s\n' "-H" "Authorization: Bearer $token" "-H" "New-Api-User: $user_id" "-A" "$UA"
  else
    printf '%s\n' "-H" "Authorization: Bearer $token" "-A" "$UA"
  fi
}

read_field() {
  python3 -c '
import json, sys
path = sys.argv[1].split(".")
try:
    node = json.load(sys.stdin)
except Exception:
    print(""); raise SystemExit
for key in path:
    if isinstance(node, list):
        try:
            node = node[int(key)]
            continue
        except (ValueError, IndexError):
            print(""); raise SystemExit
    if not isinstance(node, dict) or key not in node:
        print(""); raise SystemExit
    node = node[key]
print("" if node is None else node)
' "$1" 2>/dev/null
}

money() { python3 -c "print('%.2f' % (float('${1:-0}') / float('$QPU')))" 2>/dev/null; }

echo "=== $HOST check-in $(date '+%Y-%m-%d %H:%M') ==="
if [ "$NEEDS_CAPTCHA" = "true" ]; then
  echo "    captcha: Turnstile via solver | currency: $CURRENCY | credits per unit: $QPU"
else
  echo "    captcha: not required by this panel | currency: $CURRENCY | credits per unit: $QPU"
fi

DONE=0; OK=0; FAILED=0; SKIPPED=0
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%$'\r'}"
  [ -z "$line" ] && continue
  case "$line" in \#*) continue ;; esac

  TOKEN="${line%%|*}"; REST="${line#*|}"
  USER_ID=""; LABEL="$REST"
  if [ "$REST" != "$line" ] && [ "${REST%%|*}" != "$REST" ]; then
    # Three fields: the middle one is the numeric id some forks require.
    CANDIDATE="${REST%%|*}"
    case "$CANDIDATE" in
      ''|*[!0-9]*) LABEL="$REST" ;;
      *) USER_ID="$CANDIDATE"; LABEL="${REST#*|}" ;;
    esac
  fi
  [ "$LABEL" = "$line" ] && LABEL="?"

  mapfile -t H < <(auth_headers "$TOKEN" "$USER_ID")
  SELF="$(curl -sS --max-time 25 "${H[@]}" "$PANEL/api/user/self" 2>/dev/null)"
  NAME="$(printf '%s' "$SELF" | read_field data.display_name)"
  if [ -z "$NAME" ] && [ -z "$USER_ID" ]; then
    # Retry with the header form: this fork may want New-Api-User after all.
    PROBE_ID="$(printf '%s' "$SELF" | read_field data.id)"
    if printf '%s' "$SELF" | grep -qi 'New-Api-User'; then
      echo "[$LABEL] needs a numeric user id — add it as TOKEN|USER_ID|label"
      SKIPPED=$((SKIPPED + 1)); continue
    fi
    : "${PROBE_ID:=}"
  fi
  if [ -z "$NAME" ]; then
    echo "[$LABEL] SKIP: token or id rejected by $HOST"
    SKIPPED=$((SKIPPED + 1)); continue
  fi

  BEFORE="$(money "$(printf '%s' "$SELF" | read_field data.quota)")"
  STATE="$(curl -sS --max-time 25 "${H[@]}" "$PANEL/api/user/checkin" 2>/dev/null)"
  if printf '%s' "$STATE" | grep -q '"checked_in_today":true'; then
    echo "[$LABEL] $NAME — already checked in today (${SYM}${BEFORE})"
    DONE=$((DONE + 1)); continue
  fi

  QUERY=""
  if [ "$NEEDS_CAPTCHA" = "true" ]; then
    TOKEN_TS="$(solve_turnstile)"
    if [ -z "$TOKEN_TS" ]; then
      echo "[$LABEL] $NAME — solver could not return a Turnstile token"
      FAILED=$((FAILED + 1)); continue
    fi
    # Must be a query parameter; a body or header field is rejected as empty.
    QUERY="?turnstile=$TOKEN_TS"
  fi

  # Read the POST result. Discarding it produces the worst possible output: a
  # cheerful "checked in" next to a balance that never moved, which is how a
  # rejected Turnstile token or an expired session gets reported as success.
  RESULT="$(curl -sS --max-time 30 "${H[@]}" -X POST "$PANEL/api/user/checkin$QUERY" 2>/dev/null)"
  AWARDED="$(printf '%s' "$RESULT" | read_field data.quota_awarded)"
  if ! printf '%s' "$RESULT" | grep -q '"success":true'; then
    REASON="$(printf '%s' "$RESULT" | read_field message)"
    echo "[$LABEL] $NAME — check-in REFUSED by $HOST: ${REASON:-no success flag in response}"
    FAILED=$((FAILED + 1)); continue
  fi

  AFTER="$(money "$(curl -sS --max-time 25 "${H[@]}" "$PANEL/api/user/self" 2>/dev/null | read_field data.quota)")"
  STATE="$(curl -sS --max-time 25 "${H[@]}" "$PANEL/api/user/checkin" 2>/dev/null)"
  STREAK="$(printf '%s' "$STATE" | read_field data.stats.checkin_count)"
  # Confirm from the panel's own state, not from the POST alone.
  if ! printf '%s' "$STATE" | grep -q '"checked_in_today":true'; then
    echo "[$LABEL] $NAME — $HOST accepted the request but still reports NOT checked in"
    FAILED=$((FAILED + 1)); continue
  fi
  REWARD=""
  [ -n "$AWARDED" ] && REWARD=" (+${SYM}$(money "$AWARDED"))"
  echo "[$LABEL] $NAME — checked in${REWARD}: ${SYM}${BEFORE} -> ${SYM}${AFTER}${STREAK:+ streak ${STREAK}}"
  OK=$((OK + 1))
  sleep 2
done < "$TOKENS"

echo "=== $HOST: $OK checked in, $DONE already done, $FAILED failed, $SKIPPED skipped ==="
[ "$FAILED" -eq 0 ]
