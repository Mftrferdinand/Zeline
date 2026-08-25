---
name: conversation-runtime-state
description: "Keep a streaming chat UI's history intact across room switches, reloads, and mid-stream navigation. Use when messages vanish on room re-entry, a room paints blank on open, or a live 'thinking' indicator disappears then reappears as an answer or cancellation."
version: 1.0.0
---

# Conversation Runtime State

Prevent the three classic streaming-chat regressions: history that suddenly
disappears, a room that opens blank, and a live "thinking" state that vanishes
when you leave and re-enter mid-stream. All three share one root cause —
**global mutable message state that a later room load can replace out from under
an in-flight stream** — plus a renderer that hides not-yet-text placeholders.

## When to use

- Opening a chat room shows nothing for a moment, or stays blank on failure.
- Leaving a room while the agent is answering, then returning, shows no
  messages and no thinking indicator; the answer or a "cancelled" state pops in
  later.
- Message history "flashes empty" after a background refresh.

## Root causes

1. **Streaming placeholders are skipped by the renderer.** An assistant message
   that is still `streaming` and has no text yet gets filtered out, so the
   thinking indicator is never painted on re-entry.
2. **The stream mutates shared global state.** The stream updates one global
   `messages` array. After the user leaves and re-enters, that array is replaced
   by a fresh cache/fetch, so the old stream keeps writing to an object that is
   no longer rendered.
3. **Room open waits on a slow sync before painting.** The room renders only
   after an agent/session sync completes; if it is slow or fails, the room looks
   blank.
4. **A non-empty-but-partial server response overwrites the cache.** A partial
   fetch replaces optimistic turns that were not yet persisted.
5. **cancelled/error only touched the DOM.** State and cache were not updated,
   so leaving and re-entering lost the terminal state.

## Fix contract

- **Cache-first paint.** On room open, render the last *verified* local snapshot
  immediately, then reconcile with the network in the background. Never block the
  first paint on a remote sync.
- **Sync-independent room.** If the agent/session sync is slow or fails, the room
  and its cached history must still be visible. Render the cache *before* the
  sync call.
- **Session-owned stream snapshot.** The active stream holds its own reference to
  the message list for its session id — not the shared global. A different room
  replacing the global array must not affect an in-flight stream, and the
  stream's deltas must still land in its own room on re-entry.
- **Empty never overwrites non-empty.** A refresh returning an empty history must
  not replace a cache that has messages. Retain the cache on failed/transient
  empty responses; only a confirmed authoritative empty state (verified delete)
  invalidates it.
- **Reconcile, don't clobber.** Merge a partial server response with optimistic
  turns that are not yet persisted. Drop an optimistic turn only when its
  authoritative counterpart actually arrives.
- **Persist terminal states.** On `cancelled`/`error`, update both the message
  state and the cache, not just the DOM. Store a sanitized status (e.g.
  `cancelled`, and a short marker like "_(dibatalkan)_" when there is no partial
  answer) so it survives leave → re-entry.
- **Centralize cache mutation.** Update the cache on create/rename/delete and on
  every stream event through one helper, so no code path forgets it.

## Change workflow

1. Read the renderer, the stream handler, the room-open path, and the cache
   read/write helpers. Confirm which state is global vs session-scoped.
2. Write a RED regression first for each of: leave-and-re-enter mid-stream keeps
   the user message + thinking indicator; empty GET does not wipe a non-empty
   cache; room paints cache before sync; cancelled/error persists across
   re-entry.
3. Implement cache-first paint, a session-owned stream snapshot, empty-guard
   reconciliation, and terminal-state persistence.
4. Bump any changed static asset version strings.
5. Run the full frontend suite and JavaScript syntax checks to green.
6. Verify with a real mobile browser (Android Chromium DPR2), not unit tests
   alone: seed history, start a stream, leave the room, return, and assert the
   user message and thinking indicator remain, deltas continue to land, no blank
   frame appears, and the final answer is saved. Force a failing/slow sync and a
   seeded-then-empty refresh and assert the room stays populated. Confirm console
   errors are zero.

## Pitfalls

- Do not treat a slow sync as a reason to blank the room — paint cache first.
- Do not let the renderer skip `streaming` placeholders; they carry the thinking
  state.
- Do not persist raw provider exception text into visible state; store a
  sanitized status only.
- Unit tests cannot prove a re-entry race is fixed. Reproduce it in a browser.
