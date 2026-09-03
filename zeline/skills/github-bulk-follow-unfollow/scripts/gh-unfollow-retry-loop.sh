#!/usr/bin/env bash
#
# gh-unfollow-retry-loop.sh — resilient bulk unfollow for flaky mobile networks.
#
# THE PROBLEM THIS SOLVES:
# A plain one-pass unfollow script silently loses accounts on mobile. When the
# connection drops for a moment, `gh` returns NO response at all — the script
# sees http=000 (not a GitHub rejection), logs a "failure", and pops the account
# off the queue anyway. Those accounts are still followed but the queue is empty,
# so the run reports success while hundreds remain. Observed twice: 363 lost on
# the first pass, then 168 more on the retry.
#
# THE FIX:
# Rebuild the queue from LIVE GitHub data every round instead of trusting a
# static file. Network casualties automatically reappear next round, so nothing
# can escape permanently. Converges in 1-2 rounds in practice.
#
# Stops on its own when: non-mutual hits 0, a round makes no progress (stuck),
# or GitHub returns 403/429 (rate limit — never fight it).

set -uo pipefail
cd "${HOME:?HOME must be set}" || exit 1

SLEEP_SECS="${SLEEP_SECS:-6}"      # GitHub docs require >=1s between mutations
MAX_ROUNDS="${MAX_ROUNDS:-12}"
LOG="$HOME/gh-unfollow-overnight.log"

log() { printf '%s %s\n' "$(date '+%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }

log "=== START retry loop (sleep ${SLEEP_SECS}s, max $MAX_ROUNDS rounds) ==="

prev_remaining=-1

for round in $(seq 1 "$MAX_ROUNDS"); do
  # Live data. If paginate fails mid-way (network), wait and redo the round.
  if ! gh api user/following --paginate -q '.[].login' > ing.tmp 2>/dev/null; then
    log "round $round: failed to fetch following (network). Sleeping 60s."
    rm -f ing.tmp; sleep 60; continue
  fi
  if ! gh api user/followers --paginate -q '.[].login' > ers.tmp 2>/dev/null; then
    log "round $round: failed to fetch followers (network). Sleeping 60s."
    rm -f ing.tmp ers.tmp; sleep 60; continue
  fi

  sort ing.tmp -o ing.tmp; sort ers.tmp -o ers.tmp
  comm -23 ing.tmp ers.tmp > queue.tmp        # following but not followed back
  remaining=$(wc -l < queue.tmp | tr -d ' ')
  following=$(wc -l < ing.tmp | tr -d ' ')
  mutual=$(comm -12 ing.tmp ers.tmp | wc -l | tr -d ' ')
  rm -f ing.tmp ers.tmp

  log "round $round: following=$following non-mutual=$remaining mutual=$mutual"

  if [ "$remaining" -eq 0 ]; then
    log "DONE — no non-mutual left. Following is $following (mutual only)."
    rm -f queue.tmp
    break
  fi

  # No progress vs previous round -> stop wasting requests. Leftovers are
  # usually special cases (orgs, suspended accounts).
  if [ "$remaining" -ge "$prev_remaining" ] && [ "$prev_remaining" -ne -1 ]; then
    log "STUCK at $remaining accounts (no progress). Stopping."
    rm -f queue.tmp
    break
  fi
  prev_remaining="$remaining"

  ok=0; net_fail=0; halted=""
  while read -r user; do
    [ -z "$user" ] && continue
    http="$(gh api -X DELETE "user/following/$user" -i 2>/dev/null | head -1 | grep -oE '[0-9]{3}' | head -1)"
    case "${http:-000}" in
      204) ok=$((ok+1)) ;;
      403|429)
        log "  STOP: HTTP $http on '$user' — rate limit signal."
        halted="rate-$http"; break ;;
      000)
        # Connection dropped. Give the network time to recover.
        net_fail=$((net_fail+1)); sleep 15 ;;
      *) : ;;   # 404 etc: account gone/renamed, ignore
    esac
    sleep "$SLEEP_SECS"
  done < queue.tmp
  rm -f queue.tmp

  log "  round $round result: ok=$ok network-fail=$net_fail"

  if [ -n "$halted" ]; then
    log "HALTED ($halted). Re-run later; the queue rebuilds itself."
    break
  fi

  sleep 30
done

FINAL_ING=$(gh api user -q '.following' 2>/dev/null || echo '?')
FINAL_ERS=$(gh api user -q '.followers' 2>/dev/null || echo '?')
log "=== CLOSE. following=$FINAL_ING followers=$FINAL_ERS ==="
echo "FINAL following=$FINAL_ING followers=$FINAL_ERS"
