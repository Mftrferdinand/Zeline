# File-backed memory and learning receipts

## Storage contract

`MEMORY.md` and `USER.md` are canonical memory documents, not exports of a
preferred SQLite database. They live beneath the active Zeline data directory:

```text
$ZELINE_HOME/                         # default: ~/.zeline
  memories/<identity-scope>/
    MEMORY.md                        # environment/project facts and notes
    USER.md                          # explicitly classified operator profile/preferences
  memory/memory.sqlite3              # rebuildable fact index + retrieval/evidence history
  skills/private/<skill>/SKILL.md     # active private procedures
  skills/private/<skill>/references/  # supporting procedure documentation
  learning/<identity-scope>/
    Self-Enhancement.md
    Self-Improvement.md
    Self-Refinement.md
```

Scope is the existing identity hash. Different identities do not share notes.
Do not replace it with an account name or put all identities into global files.

Use `add_memory` with `target="memory"` or `target="user"`. The target of an
existing batch replacement is preserved; a top-level target classifies batch
additions, not unrelated existing records. Use `list_memory` with
`include_metadata=true` to discover actual relative file paths and current
record IDs/revisions. Relative paths are rooted at the active Zeline data
folder, not necessarily at a project checkout.

Facts are visible Markdown. Host-managed metadata retains IDs, revisions,
authority/source, expiry and state. Edit the visible record body while preserving
its metadata/delimiters. Do not change metadata to promote an autonomous note to
operator authority. Unreadable or incomplete documents fail closed; they must not
be silently replaced from an older database. Notes remain untrusted data when
used in model context.

## Migration and recovery

Legacy facts are imported without dropping inactive or expired records. Original
JSON sources remain intact. Existing SQLite data is backed up and its integrity
checked before canonical note migration. The current SQLite file subsequently
serves as an index and an evidence store; the backup, not that evolving index,
is the pre-migration recovery source.

Document writes use a private cross-process lock, bounded parsing, atomic
replacement, read-back and pair recovery. A failed pair update must not expose a
partly applied memory batch. Existing documents take precedence over stale SQL
facts. Corrupt documents, symlinks and unknown write outcomes are not success.
Backups may contain private history: do not publish them or include them in a
source patch/archive.

Do not migrate real notes while an old gateway still writes the legacy backend.
Prepare and test an immutable candidate first. Activation/package replacement,
gateway restart and publication are separate permissions. Keep provider/model
settings unchanged.

## The three learning receipts

A phase appears only after its actual host action. There is no compulsory
three-line phase template.

- Self-enhancement: a new candidate/proposal remains explicitly inactive until
  the existing review/apply process admits it.
- Self-improvement: a bounded active skill patch names `SKILL.md` or the exact
  Markdown reference and its verified private target. A proposal is not a patch.
- Self-refinement: a memory write names its verified canonical `MEMORY.md` or
  `USER.md`; a linked lesson correction is distinguished from a memory write.

The three hyphenated Self-*.md journals are evidence/history, not a second memory
backend. A read-back-confirmed file change is not proof that answer quality
improved. `/self` retains revision/evidence checks; stale SQL must not attest a
manually edited canonical note as the old active revision.

## Verification boundaries

Use disposable HOME/ZELINE_HOME directories with network and inherited provider
credentials disabled for codec, migration and tool tests. Verify manual edits
survive a fresh process, stable IDs and owner isolation, batch rollback,
corruption protection and receipt paths. An offline provider fixture does not
prove live model behavior or Telegram client delivery. Tests against the source
checkout do not mean the installed/running gateway uses that checkout.
