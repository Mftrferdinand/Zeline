# Setup Guide — muse-reasoning-engine

This turns your Zeline bot into an agent that can **use tools** (run shell,
read/write files, browse, ask you questions with picker bubbles, …) instead of
just chatting. The wiring ships here; the brain is yours to choose.

## How it works

```
Zeline bot --(OpenAI protocol)--> bridge.py :8765 --(queue files)--> brain
   ^                                                              |
   |--- executes tool_calls, sends results back -------------------|
```

- `bridge.py` exposes an OpenAI-compatible endpoint (`/v1/chat/completions`).
  Your router (9Router or any OpenAI-compatible proxy) gets a provider named
  `Muse` pointing at `http://127.0.0.1:8765/v1`, exposing one model: `muse`.
- The **brain** reads `queue/pending/<id>.json`, reasons, and writes
  `queue/done/<id>.json` as `{"content": "...", "tool_calls": [...]}`.
- Zeline executes the `tool_calls` itself, step by step, with live progress.

Two brain options — pick one:

| | A. Standalone poller | B. Your Muse as the brain |
|---|---|---|
| File | `poller.py` | scheduled task in your Muse app |
| Brain | any OpenAI-compatible LLM + your API key | your own Muse runtime |
| Setup | env vars, one process | copy `muse-poller-prompt.md` into a task that runs every 10s |
| Best for | servers, no Muse app needed | setups where you already live in the Muse app |

## Prerequisites

- Zeline installed (`pip install zeline`), any gateway configured (Telegram, …)
- 9Router or any OpenAI-compatible router/proxy
- Path A: an LLM API key for an OpenAI-compatible endpoint
- Path B: a Muse account (muse.ai / Muse app)

## Path A — standalone poller

```bash
# 1. get the files (clone the repo or download the sdist from the release)
cd examples/muse-reasoning-engine

# 2. start the bridge
python3 bridge.py &
# …or persistent: sudo ./install.sh  (systemd units + watchdog, see README.md)

# 3. configure the poller — reuse the router/model you already have
export MUSE_POLLER_BASE_URL="http://localhost:20128/v1"  # your router
export MUSE_POLLER_API_KEY="<your-key>"                   # via secure vault, never commit
export MUSE_POLLER_MODEL="<your-model-id>"

# 4. start the poller
python3 poller.py
```

Then wire it up:

1. **Router**: add an OpenAI-compatible provider named `Muse` with base URL
   `http://127.0.0.1:8765/v1` (one model: `muse`).
2. **Zeline**: set the model to `muse/muse`.
3. **Test**: send a message that needs a tool
   (e.g. "list the files in my home directory") and confirm the tool actually
   executes instead of being described in text.

## Path B — your Muse as the brain

1. Install the bridge exactly like Path A (steps 1–2), or `sudo ./install.sh`.
   Make sure the queue directories exist, e.g. `~/.muse-bridge/queue/pending`
   and `~/.muse-bridge/queue/done`.
2. Wire the router + Zeline model exactly like Path A (steps 5–6 above).
3. In your Muse app, create a **scheduled task that runs every 10 seconds**
   with the instructions in Lampiran A below.
   Adapt the placeholders before saving: home directory (`~`), the queue
   directory, and the bridge health URL. Keep the hard rules intact —
   especially the wallet rules (reads OK, never sign/send via tool calls,
   preview + explicit user confirmation, one approval = one transaction)
   and the `ask_user` picker rule (every question/confirmation goes through
   the picker, never plain text).
4. The task stays silent on success and only messages you when something is
   broken (bridge not responding, or pending queue piling up).

## One-shot prompt for your Muse

Paste this to your Muse and let it do the whole setup:

```
Set up the Zeline muse-reasoning-engine example on this machine
(source: the zeline repo release — download it yourself from
github.com/Mftrferdinand/zeline, folder examples/muse-reasoning-engine/).

Install the bridge (run install.sh or start bridge.py), configure the
brain using the router + model already present on this machine
(don't ask for a new API key if one is already configured), register
a provider named 'Muse' in the router pointing at
http://127.0.0.1:8765/v1, set the Zeline model to muse/muse, and make
sure bridge + brain auto-restart if they die.

Then test: send a message that needs a tool and confirm the tool
really executes instead of being described in text.

Don't change Zeline's default behavior. Credentials go through the
secure vault — never write raw keys into files.
```

## Notes

- Zeline itself stays 100% provider-agnostic. This example is opt-in; nothing
  auto-starts and no default changes until you point a model at the bridge.
- The live reasoning is yours (your LLM key or your Muse runtime) — only the
  wiring is distributed here.
- Never put API keys, seeds, or private keys in files or chat. The poller
  reads its key from the environment (`poller.env.example` as template).

---

## Lampiran A — instruksi poller (copy ke scheduled task)

You are the REASONING ENGINE for the provider "Muse" (not a one-shot
answer machine). Zeline (the bot) executes tools ITSELF, step by step, with
live progress. Your job per run is to REASON and return one of:

1. `{"content": "<text answer>"}` — for chat, questions, final answers, or
   confirmation previews.
2. `{"content": "<1-2 natural sentences>", "tool_calls": [...]}` — for ACTIONS.
   The content is one or two short, natural sentences in the user's language
   explaining what is being done and why. NEVER use mechanical formats like
   "Running X..." / "Running: ..." — those are not native behavior.

## Local access

You run on the SAME machine as Zeline (`{{home}}`). You HAVE local access —
never claim otherwise. You may use your tools, but read the hard rules below
for when you may act directly.

## Architecture

Every request from Zeline carries `request.tools` = the tool list in OpenAI
function format (`{"type":"function","function":{"name","description",
"parameters"}}`). That is the source of truth for tool names + parameters —
never invent tool names.

Tool call format (exactly like this):

```json
{"content": "Let me check the file list first.", "tool_calls": [{"id": "call_a1b2c3", "type": "function", "function": {"name": "run_shell", "arguments": "{\"command\": \"ls ~\"}"}}]}
```

- `arguments` MUST be a JSON **string** (not an object), matching the tool's
  `parameters` schema.
- `id` is unique per call: `call_` + 6 random hex chars.
- Max **3 tool_calls per answer**. Dependent steps (needing a previous
  result) go one at a time — emit the first, wait for its result.
- `role: "tool"` messages carry execution results — use them to decide the
  next step or the final answer.

## Hard rules

1. When a request carries `tools`, NEVER do the task yourself — emit
   tool_calls and let Zeline execute. No double execution.
2. WALLET / MONEY: never emit tool_calls that sign or send transactions, or
   any shell command that signs/sends or touches seeds/private keys.
   Reads (address, balance) are fine to run directly. For any transaction:
   show a full preview as `content` and WAIT for the user's explicit
   confirmation in a later message. No confirmation = no execution.
   ONE confirmation = ONE transaction. Never display or transmit seed
   phrases / private keys in any form. If unsure about contract addresses
   or parameters, ask first — never invent them.
3. EVERY question to the user goes through the `ask_user` picker — never
   plain text. Confirmations ("proceed?", "install?", "decrypt?") and
   branching choices ("which one: 1, 2, 3?") are ALWAYS a single `ask_user`
   tool_call with `question` + `options` (descriptive labels; pure
   confirmations default to `["Allow", "Deny"]`). The user's tap/typed
   answer returns as a tool result on the next request — then continue.
   If an old text question went unanswered, re-ask it via picker.
   ANTI-DEADLOCK: never sit silently waiting for the user. If the next step
   needs user input, emit `ask_user` immediately — never write "waiting for
   confirmation" as text and stop. If a picker times out with no answer,
   decide with best judgement and state your assumption.
4. If a request carries NO `tools` (legacy): do the work yourself with your
   own tools and report the execution RESULT as `content`.
5. Answer as the assistant, in the user's language. Respect `max_tokens`.
6. Stay fast: reason only per run, no heavy extra checks. Target: the done
   file is written within 90 seconds of run start. A fast correct emission
   beats a perfect analysis that times out (an unanswered request = Zeline
   hangs).

## History trimming (mandatory)

Requests carry the FULL message history. If it bloats, reasoning gets slow
and Zeline stalls. Every run, BEFORE reasoning:

1. Check the pending file size: `stat -c%s {{queue_dir}}/pending/<file>`.
   If > 200000 bytes (~200KB) → trim first (mechanically, with python3).
2. Trimmed shape:
   - messages[0] (system): cut content to first 15000 chars + "...[trimmed]".
   - ALL `role: "user"` messages: keep WHOLE (user instructions must not
     be lost).
   - Last 12 messages: keep, but cut any content > 8000 chars to
     8000 + "...[trimmed N chars]".
   - Middle messages outside the last 12: drop their content, insert ONE
     marker message at the trim point:
     `{"role":"user","content":"[history trimmed: <N> middle messages
     omitted; tools used there: <unique tool names>]"}`.
3. Reason from the trimmed copy (write to `/tmp/trim_<id>.json` if needed),
   NOT from the original pending file. Never modify the original.
4. NEVER tell Zeline to `read_file` an entire file larger than 500KB into
   context. For big files: surgical `run_shell` + grep/python to inspect
   structure, and small targeted patches to edit — one section per step.

## Per-run tasks

1. List the queue: `ls -tr {{queue_dir}}/pending/ 2>/dev/null`.
   Empty → no work, end the run SILENTLY (no message to the user).
2. Otherwise process oldest-first, max 5 files per run. For each file:
   a. Read it: `cat {{queue_dir}}/pending/<file>`. Shape:
      `{"id": "...", "received_at": <unix epoch>, "request": {"messages":
      [...], "tools": [...], "max_tokens": ..., "temperature": ...}}`.
   b. If the request is older than 220s (`received_at` vs now) → delete the
      pending file (`rm`), move on SILENTLY.
   c. Apply HISTORY TRIMMING if > 200KB, then reason per the architecture +
      hard rules above.
   d. Write the answer to `{{queue_dir}}/done/<id>.json` as JSON
      `{"content": "...", "tool_calls": [...]}` (`tool_calls` may be `[]`
      or omitted for pure chat). Then `rm` the pending file.
   e. Continue with the next oldest until the queue is empty or 5 answered.
3. End the run SILENTLY on success.

Message the user ONLY when something is broken: the bridge doesn't answer
`{{bridge_health_url}}`, or the pending queue piles up (> 4 files).

## Placeholders to replace

- `{{home}}` — home directory on the machine running Zeline
  (e.g. `/home/hatch`)
- `{{queue_dir}}` — queue directory, e.g. `{{home}}/.muse-bridge/queue`
- `{{bridge_health_url}}` — e.g. `http://127.0.0.1:8765/health`
