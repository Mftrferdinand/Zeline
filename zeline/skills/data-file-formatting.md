# Data File Formatting

> Format and deliver data files (account lists, link collections, CSV exports) in clean human-readable format per user preference.

Format and deliver text data files (account lists, link collections, credential dumps) for human reading — NOT machine parsing.

## Format Options

Two formats are supported. Choose based on the data shape:

### Option A: Vertical Block (━━━) — multiple accounts, many links per account

Use when an account has 3+ links or properties. Clean visual layout:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Account #1
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Email    : user@example.com

Link 1   : https://...

Link 2   : https://...
```

- Use `━━━` separator lines between accounts
- Labels with colon + spaces: `Email    :`, `Link 1   :`
- Each link on its own line, not concatenated
- Account numbering: `Account #1`, `Account #2`, etc.
- Blank line between sections for readability

### Option B: Horizontal Table (Markdown pipe table) — simple key-value pairs

Use when each account has few columns (Email | Service | Link). Standard Markdown table:

```
| Email | Netflix Jailbreak |
|-------|-----------------|
| user@domain.com | [Service Name](https://...) |
```

- Header row with column names, separator row, then data rows
- Links are Markdown inline links: `[label](url)` — clickable
- One row per account
- This IS a Markdown table (structured formatting), NOT raw CSV data

## File Extension

Use `.md` extension for text data files (e.g. `netflix-accounts.md`), not `.txt`.

## Delivery

Always deliver the actual file via MEDIA: path — do NOT just show the content in chat. The user wants the file itself.

```
MEDIA:/absolute/path/to/filename.md
```

## Edge Cases & Pitfalls

- **Markdown tables vs raw CSV**: Markdown pipe tables (`| col | col |`) are structured formatting and are fine for simple account listings. Avoid raw CSV (unquoted values, no headers, no formatting) — that's machine-only.
- **Choosing between formats**: Default to Option B (horizontal table) for 1-2 accounts with few columns. Default to Option A (vertical block) for 3+ accounts or 3+ links per account. **If the requester states a preference, it wins over this heuristic** — record it and apply it consistently for the rest of the task; see `references/iterative-file-workflow.md`.
- **User iterates**: The user will ask to see the file, then refine column names or format. Expect 2-3 rounds. Just apply each change, don't ask for confirmation between every tweak. This iteration pattern applies broadly — not just data files but also in-chat tables, project lists, any structured output. See `skill: project-list-tracking` for the same iterative-refinement pattern applied to a persistent project registry.
- **Link accuracy**: The user may ask you to verify a link is correct. Use `diff` or character-by-character comparison — do not visually scan.
- **Long URLs**: They make the file look messy in chat display, but in the actual file they're fine — the user can open the file separately. Still deliver the file.
- **Multiple accounts**: Option A: each account gets its own `━━━`-bordered block, numbered sequentially. Option B: each account is its own table row.
- **File format**: If the user asks for CSV/pipe format (for machine use), keep a separate raw file. But the delivered/displayed version should always be one of the two Markdown formats.

## Reference Files

- `references/iterative-file-workflow.md` — the user's preferred iterative file-creation workflow (write → show → refine → verify).
- `references/batch-recording-pattern.md` — recording multiple email+link pairs into a single file as the user sends them.


---

## Lampiran: `references/batch-recording-pattern.md`

# Batch Recording Pattern (the user)

When the user sends multiple email+link pairs one at a time to be recorded into a single file.

## Flow

1. **User sends email** — `user@domain.com`
2. **User sends link** — a long URL (OAuth payment link, etc.)
3. **Agent appends** — add a new row to the existing Markdown table with `patch`
4. **Repeat** — user sends next email+link pair, agent appends again
5. **Deliver** — send the updated file via MEDIA: after each addition

## Rules

- **Do NOT rewrite the whole file** — use `patch` to append the new row after the last existing row. Find the last row in the file and set `old_string` to it, then `new_string` to the last row + the new row.
- **Link accuracy is critical** — the user may ask to verify a link matches what they sent. Use `diff` / character-level comparison. Do not visually scan.
- **Bulk verification**: After adding 5+ entries, the user may ask "pada aman kan?" — run a bulk check: extract all URLs, verify they're all unique (no duplicates), and optionally show lengths. Use `grep -oP '\]\(https://[^)]+'` to extract, then `sort | uniq` to check uniqueness.
- **Each link is unique** — even when they look similar, every OAuth link has unique parameters (netAuthId, referenceAgreementId, authRequestId, signature, trackId). Never assume two links are the same.
- **Delete-and-re-add**: If the user says "hapus X" then later sends "X" again with a new link, the new link is a completely different OAuth session — treat it as a new entry, not a restoration of the old one.
- **File naming** — user specifies the filename (e.g. `Netflix-aez.md`). Respect their naming exactly. User may rename multiple times — use `mv` in terminal.
- **Format** — Markdown pipe table: `| Email | Service Name |` with clickable Markdown inline links `[label](url)`.
- **User iterates on column headers** — they may change column names multiple times. Just apply each change, don't ask for confirmation between tweaks.
- **User may ask to see the file** — use `read_file` to show it.



---

## Lampiran: `references/iterative-file-workflow.md`

# Iterative File Creation Workflow (the user)

The user's preferred workflow for creating account/link data files.

1. **User provides data** — raw link + email. No format specified yet.
2. **Agent writes file** — use Option B (horizontal Markdown table) as default for simple data.
3. **User reviews** — asks "mana liat file md nya" to see content.
4. **User refines** — may change column names (Netflix 30D-Free → Netflix Jailbreak), table layout, or column count. Expect 2-3 rounds of tweaks.
5. **Agent applies changes** — use `patch` for single header changes, `write_file` for full rewrites.
6. **User verifies accuracy** — may ask to compare link with original. Use `diff` / character-level comparison. Do not visually scan long URLs.
7. **Deliver via MEDIA:** path.

Key rules:
- Do NOT ask for confirmation between tweaks — just apply each change.
- Links must be clickable Markdown inline links: `[label](url)`.
- Default format: `| Email | Service Name |`
- The file path is `~/<service-name>.md` (e.g. `netflix-dana.md`).
- **Batch recording**: When user sends email+link pairs one at a time, append each new row to the existing table with `patch`. Never rewrite the whole file. See `references/batch-recording-pattern.md`.
- **A stated preference overrides the account-count heuristic.** If the requester asks for horizontal tables, keep using Markdown pipe tables even at 4+ accounts instead of switching to Option A.
- **Bulk verification**: When asked "pada aman kan?", run a bulk uniqueness check on all URLs in the file.
- **Delete-and-re-add**: "hapus X" then re-sending X with a new link = new OAuth session, not a restoration. Treat as a fresh entry.
