# Github Bulk Follow Unfollow

> Bulk unfollow/follow GitHub accounts safely without tripping spam detection — scope guards, non-mutual queue, resumable on-demand batches. Use when asked to mass-unfollow, clean up following list, or automate follow/unfollow.

Mass follow-graph changes are the single easiest way to get an account flagged.
GitHub's Acceptable Use Policy names it explicitly:

> "rank abuse, such as automated **starring or following**"
> — https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies

Read that carefully: the prohibition targets **inflating** rank. **Unfollowing**
removes signal rather than manufacturing it, so cleanup is far lower risk than
mass-following. Never automate mass *following* — that is squarely what the
policy forbids.

## Step 1 — check the token scope FIRST

`gh` tokens usually lack follow permission even with `repo`+`admin:org`.
GitHub returns **404, not 403**, when the scope is missing — so a naive script
logs 1,800 "failures" with no clue why.

```bash
gh api -i user 2>/dev/null | grep -i '^x-oauth-scopes:'
```

Need `user` or `user:follow`. Adding it requires `gh auth refresh`, which is
**interactive** — it prints a one-time device code and blocks. Drive it like
this (a PTY is mandatory; without one it hangs silently):

```
terminal(command='gh auth refresh -h github.com -s user:follow',
         background=True, pty=True, watch_patterns=['one-time code'])
process(action='poll',   session_id=...)          # read the code out
process(action='submit', session_id=..., data='') # answers "Press Enter"
```

Then hand the code to the user and **stop** — they must authorize in a browser
and you cannot do it for them. A `watch_patterns` notification firing means the
code was *printed*, not that auth *succeeded*: always re-check the scope header
before running any batch.

Confirm write access with an **idempotent probe** — PUT on someone already
followed changes nothing:

```bash
gh api -X PUT "user/following/SOMEONE_ALREADY_FOLLOWED" -i 2>&1 | grep -iE '^HTTP|x-accepted-oauth-scopes'
```

`204` = ready. `404` + `X-Accepted-Oauth-Scopes: user, user:follow` = scope missing.

## Step 2 — back up before touching anything

There is no undo for unfollow. These files are the only recovery path.

```bash
gh api user/following --paginate -q '.[].login' > ~/gh-following-backup.txt
gh api user/followers --paginate -q '.[].login' > ~/gh-followers-backup.txt
```

## Step 3 — build the non-mutual queue

Usually the user wants to keep people who follow them back.

```bash
cd ~
sort gh-following-backup.txt > a.txt; sort gh-followers-backup.txt > b.txt
comm -23 a.txt b.txt > gh-nonmutual.txt   # follow them, they don't follow back
comm -12 a.txt b.txt > gh-mutual.txt      # keep these
rm a.txt b.txt
```

## Step 4 — run in resumable batches, ON DEMAND by default

**Do not create a cron job unless the user explicitly asks for one.** Offering
"I'll schedule it daily" reads as the agent taking ownership of the account, and
aes rejected exactly that ("jangan cronjob, pas gua suruh aja"). Default to
running one batch when told to.

The queue file is what makes on-demand batching work with no scheduler at all —
each invocation resumes where the last stopped:

```bash
## Step 4 — run it (prefer the retry loop on mobile)

Two runners ship with this skill:

| Script | Use when |
|---|---|
| `scripts/gh-unfollow-retry-loop.sh` | **Default.** Flaky/mobile network, or you want it to finish unattended. |
| `scripts/gh-unfollow-batch.sh` | Fixed daily quota (e.g. 400/day), stable connection. |

### Why the retry loop exists (learned the hard way)

A single-pass script **silently loses accounts on mobile**. When the connection
blips, `gh` returns no response at all → `http=000`, which is *not* a GitHub
rejection. The naive script logs a "failure" and pops the account off the queue
anyway. Result: queue empty, run reports success, hundreds still followed.
Measured on a real run: **363 lost on pass 1, then 168 more on pass 2.**

The loop fixes this by rebuilding the queue from **live GitHub data every
round** rather than trusting a static file, so network casualties reappear
automatically. Real run converged in **2 rounds**: 167 remaining → 0.

```bash
SLEEP_SECS=6 MAX_ROUNDS=12 bash gh-unfollow-retry-loop.sh
```

Diagnose `http=000` before assuming you're rate limited:

```bash
gh api -X DELETE "user/following/SOMEONE_STILL_FOLLOWED" -i 2>&1 | head -1
curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" https://api.github.com/
```

Both fine → it was transient network, not GitHub. Only 403/429 means back off.

### Cron vs manual

Ask first. This user wants bulk ops **manual on request, not scheduled**. If
scheduling is wanted, use `no_agent=True` with `script=` (pure script, zero
tokens).

## Step 5 — verify independently, then reconcile

Never trust the script's own success log. Re-derive from live data:

```bash
gh api user/following --paginate -q '.[].login' | sort > a.txt
gh api user/followers --paginate -q '.[].login' | sort > b.txt
comm -23 a.txt b.txt | wc -l   # non-mutual left -> want 0
comm -12 a.txt b.txt | wc -l   # mutual preserved
```

Then check no *mutual* was collateral damage by diffing against the original
backup. If one is missing, determine the cause before re-following:

```bash
grep -c "NAME" ~/gh-unfollow-done.txt                     # 0 = your script didn't do it
gh api -i "users/NAME/following/YOUR_LOGIN" | head -1     # 204 = still follows you
```

Real case: `christianalberto` was mutual at backup time but absent at the end.
`done.txt` had 0 hits, so the script was innocent — **they** unfollowed mid-run.
Following them back would recreate a non-mutual entry, so the correct action was
to leave them unfollowed. Check the *current* relationship, not the stale backup,
before "fixing" a discrepancy.


## Step 5 — verify INDEPENDENTLY, never trust the script's own log

A run log saying `ok=20` only proves the script *believed* it succeeded. Ask
GitHub directly, and assert the negative side too:

```bash
# every processed account must now return 404
while read -r ts user; do
  h=$(gh api -i "user/following/$user" 2>/dev/null | head -1 | grep -oE '[0-9]{3}' | head -1)
  [ "$h" = "404" ] || echo "STILL FOLLOWED: $user (http=$h)"
done < ~/gh-unfollow-done.txt

# mutuals must be UNTOUCHED — spot-check 5, expect 204 each
head -5 ~/gh-mutual.txt | while read -r u; do
  printf '%-24s %s\n' "$u" "$(gh api -i "user/following/$u" 2>/dev/null | head -1)"
done
```

Report the ratio (`20/20 confirmed 404`) plus the mutual spot-check plus
`gh api rate_limit -q '.resources.core'`. That is what makes "it worked"
checkable rather than a claim.

## Step 6 — batch sizing and progress reporting

Run a small probe batch first (`DAILY_LIMIT=20`), verify per Step 5, then let
the user pick the size for the rest. For long runs use
`terminal(background=True, notify_on_complete=True)` so completion surfaces on
its own.

**When the user says "lanjut" / "continue" mid-run, check whether it is still
running before doing anything:**

```bash
pgrep -f gh-unfollow-batch.sh >/dev/null && echo "still running" || echo "stopped"
```

If alive, just report progress — launching a second process would race on
`sed -i '1d'` and double-consume the queue. Say plainly that it never stopped,
so the user knows no prompting is needed.

Progress snapshot worth reporting each time:

```bash
cd ~
echo "remaining : $(wc -l < gh-unfollow-queue.txt)"
echo "done      : $(wc -l < gh-unfollow-done.txt)"
echo "failed    : $([ -f gh-unfollow-failed.txt ] && wc -l < gh-unfollow-failed.txt || echo 0)"
echo "following : $(gh api user -q '.following')"
```

## Answering "will my account get banned?"

Do not hand-wave. Fetch the actual policy and quote it, then show the arithmetic
against the published limits. The distinction that settles it: the AUP forbids
**rank abuse** — *inflating* metrics. Unfollowing removes signal, so cleanup sits
on the safe side of the same sentence that bans mass-following. Present the
per-run point math (below) so the margin is visible, and name the safeguards
(halt on 403/429, no concurrency, backup file) rather than just asserting safety.

## Step 7 — `http=000` is a network dropout, NOT a GitHub rejection

The single most misleading outcome. On a phone/Termux connection, long runs
accumulate entries where `gh` returned **no response at all**, so the captured
status code is empty and defaults to `000`.

A 1,800-account run produced `ok=1437 failed=363` — and **all 363 were `000`**,
clustered into one hour (319 of them). Not one was a real refusal. Diagnose
before reporting anything to the user:

```bash
# distribution of failure codes — if it is all 000, GitHub never said no
awk '{print $NF}' ~/gh-unfollow-failed.txt | sort | uniq -c | sort -rn
# when the failures happened — a tight cluster means connectivity, not policy
awk '{print $2}' ~/gh-unfollow-failed.txt | cut -d: -f1 | sort | uniq -c

# retry ONE by hand; a clean 204 proves the account is fine
gh api -X DELETE "user/following/SOME_FAILED_USER" -i 2>&1 | head -1
curl -s -o /dev/null -w "api.github.com http=%{http_code} time=%{time_total}s\n" https://api.github.com/
```

Never report `000` as "GitHub blocked us" or as evidence of rate limiting. Say
plainly it was a local connectivity drop and show the successful manual retry.

**Consequence: those accounts were never unfollowed.** Rebuild the queue from
**live GitHub data**, not from the failure log — the log only records what the
script *attempted*, while live data is ground truth and also absorbs accounts
that changed state on their own:

```bash
cd ~
gh api user/following --paginate -q '.[].login' > ing.raw
echo "exit=$? lines=$(wc -l < ing.raw)"
gh api user/followers --paginate -q '.[].login' > ers.raw
echo "exit=$? lines=$(wc -l < ers.raw)"
# MUST match the authoritative counts before trusting these files:
gh api user -q '.following, .followers'

sort ing.raw -o ing.raw; sort ers.raw -o ers.raw
comm -23 ing.raw ers.raw > gh-unfollow-queue.txt   # still-followed non-mutuals
comm -12 ing.raw ers.raw > gh-mutual-now.txt
# arithmetic check: queue + mutual MUST equal following
rm -f ing.raw ers.raw
```

Then re-run with a wider gap (`SLEEP_SECS=4`) to ride out flaky connectivity.

**For anything over ~200 accounts, skip the single-pass script entirely and use
`scripts/gh-unfollow-retry-loop.sh`.** It rebuilds the queue from live GitHub
data on *every* round, so accounts lost to `000` are automatically retried
instead of silently dropped — it cannot leak work the way a single pass does:

```bash
SLEEP_SECS=6 MAX_ROUNDS=12 bash ~/.zeline/scripts/gh-unfollow-retry-loop.sh
```

It halts on its own at 0 non-mutuals, on a no-progress round (orgs/suspended
accounts that can never clear), or on 403/429. Launch it with
`terminal(background=True, notify_on_complete=True)`.

**Run each `--paginate` fetch as its own command and echo its line count.**
Chaining both fetches plus `comm` in one compound command silently produced an
empty queue file: a mid-pagination network stall truncated the output, `comm`
then diffed nothing, and the queue was clobbered to 0 lines with no error. Split
the steps and assert the counts against `gh api user` before consuming them.

## Rate limit facts (verified from docs)

| Limit | Value |
|---|---|
| Primary, authenticated | 5,000 req/hour |
| REST write (POST/PATCH/PUT/DELETE) point cost | 5 points each |
| Secondary: points per minute, single endpoint | 900 max |
| Secondary: content-generating requests | 80/min, 500/hour |
| Concurrent requests | 100 max |

400 unfollows/day at 3s spacing = 2,000 points spread over 20 minutes ≈ 100
points/min against a 900 cap. Comfortable margin.

## Real-run results (reference)

2,273 → 456 following (1,647 unfollowed) over ~3.5h on Termux/Android:

| Phase | Unfollowed | Lost to network | Note |
|---|---|---|---|
| Test batch (20) | 20 | 0 | verified 20/20 independently |
| Main pass (1,800) | 1,437 | 363 | all `http=000`, connection blips |
| Retry pass (358) | 190 | 168 | still bleeding on one-pass design |
| Retry **loop** (167) | 167 | 0 | converged in 2 rounds |

Zero 403/429 across the entire run. Rate limit never dropped below 4,499/5,000.
Mutual set stayed intact at 456. Conclusion: GitHub does not push back on
unfollow at this pace — **the only real adversary was the mobile connection.**

## Pitfalls

- **`http=000` is NOT rate limiting.** It means `gh` got no response — network
  dropped. Do not back off for an hour; retry the account. Only 403/429 warrants
  stopping.
- **A one-pass queue silently drops network casualties.** Rebuild from live data
  or you will under-deliver and report success. This is the single biggest trap.
- **`/tmp` does not exist on Termux.** Write temp files to `$HOME` or use
  `mktemp`. `> /tmp/f1` fails with "No such file or directory".
- **Long-running Termux jobs need `termux-wake-lock`** or Android kills them when
  the screen sleeps.
- **Bare `(` inside `$(...)` in a double-quoted echo is a bash syntax error.**
  `echo "$([ -f x ] && echo YA || echo NO (ok))"` breaks. Drop the parens.
- **Don't inject shell vars into a heredoc'd `python3 -c`** — `json.loads('''$var''')`
  explodes on quotes/newlines in the JSON. Pipe the data via stdin and read it
  with `sys.stdin.read()` instead.
- **`following` drifting down on its own is normal** — accounts get renamed,
  deleted, or suspended. Diff live vs backup and check whether the account still
  resolves: `gh api users/NAME -i | head -1`. A 200 on the user but 404 on
  `user/following/NAME` means *they* changed something, not you.
- **404 on DELETE is not fatal** — deleted/renamed account, or already
  unfollowed. Log and continue.
- Do **not** parallelize. Concurrency is what abuse detection keys on.
  cap and dies mid-wait.** Poll a bounded number of times per call (e.g. 8 x 20s)
  and re-issue, or rely on `notify_on_complete=True` instead of babysitting.
- **Reaching `queue=0` does not mean the process exited** — the final account is
  still in its `sleep`, and the summary line lands seconds later. Confirm via the
  run log's `Selesai.`/`STATUS=` line, not the queue count alone.
- **`pgrep -f <script>` matches the very command asking the question** when the
  script name appears in your own shell invocation. Prefer `pgrep -f` on a
  distinct pattern, or ignore the self-match, before reporting "still running".
- **Long-running batches need a wake lock on Android.** Termux processes get
  killed when the screen sleeps, silently truncating a 90-minute run. Take
  `termux-wake-lock` before launching, and expect to release it afterwards.
- Do **not** parallelize. Concurrency is what abuse detection keys on.

## Scripts

- `scripts/gh-unfollow-batch.sh` — single pass, resumable queue, hard limit per
  invocation. Good for probe batches (`DAILY_LIMIT=20`) and small cleanups.
- `scripts/gh-unfollow-retry-loop.sh` — self-healing multi-round loop that
  rebuilds its queue from live data each round. **Use this for large batches**;
  it is immune to the `http=000` work-leak described in Step 7.