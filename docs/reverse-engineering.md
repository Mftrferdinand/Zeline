# Built-in static reverse engineering

The development branch includes `reverse_engineer` as a native Zeline tool. It
is available by default on `workspace` and `full`, with no MCP registration,
REA installation, Node.js, daemon, API key, or external analysis service.
This is a first static engine, not parity with REA's entire tool catalog.
It is not included in previously published release artifacts.

## Operations

| Action | Observations | Boundaries |
| --- | --- | --- |
| `inspect` | Full SHA256, byte size, first 16 bytes, format candidates | Magic/extension candidates are not validated formats |
| `inventory` | Paged immediate directory children or ZIP central-directory members | No recursive traversal, extraction, member decompression or CRC validation |
| `strings` | Printable ASCII, exact byte offsets and original byte lengths | Not UTF-16/Unicode string decoding; displayed values capped at 256 characters |
| `binary` | ELF32/64 headers/sections and section-based symbol tables; PE32/PE32+ headers/sections, normal imports and exports | Paged observations only; no decompilation, runtime provider resolution, delay-import or PDB/COFF symbol recovery |
| `disassemble` | Bounded linear x86/x64/ARM/Thumb/AArch64 instructions, direct branch/call references and page-local control-flow observations | Not a decompiler or full function/program CFG; indirect targets and runtime behavior remain unresolved |
| `source` | Python AST or JavaScript tree-sitter imports/definitions, lines and UTF-8 byte spans | Syntax inventory, not type checking, dependency resolution or a call graph |

Example tool calls:

```json
{"action":"inspect","path":"artifacts/sample.bin"}
{"action":"strings","path":"artifacts/sample.bin","offset":0,"limit":50,"min_length":6}
{"action":"inventory","path":"artifacts/sample.zip","offset":0,"limit":20}
{"action":"binary","path":"artifacts/program"}
{"action":"source","path":"src/main.mjs"}
{"action":"disassemble","path":"artifacts/program","limit":25}
{"action":"disassemble","path":"artifacts/code.raw","architecture":"x86_64","base_address":4096,"limit":25}
```

`source` infers Python from `.py` and JavaScript from `.js`, `.mjs`, `.cjs`, or
`.jsx`. Specify `language` for extensionless files. TypeScript is unsupported.
Python syntax follows the running interpreter; a UTF-8 BOM is accepted and
reported byte offsets still reference the original bytes, including the BOM. JavaScript syntax follows the
pinned grammar, not a JavaScript runtime's semantic validity. CommonJS require
and dynamic imports are not resolved; JS module strings preserve lexical escapes.

Follow `findings.next_offset` for directory, ZIP, and string pages. `null` means
that observation has no further page. For file and ZIP pages, pass the previous
`artifact.sha256` as `expected_sha256`; a byte change is rejected before parser or
decoder work. Directories have no recursive content digest and reject this option,
so keep the directory unchanged between pages: enumeration is not a frozen
filesystem snapshot. The digest binds file bytes only—callers must also preserve
architecture, symbol/target selectors, and other operation-specific inputs.

## Binary table pages

`binary` keeps the existing `metadata` and `sections`, and adds three independent
pages: `findings.imports`, `findings.exports`, and `findings.symbols`. Each has
`entries`, `offset`, `total_entries`, `next_offset`, `exhausted`, and `available`.
The supplied `offset`/`limit` apply separately to every category. To continue a
category, pass its `next_offset` as the next call's `offset`, preserving the file
SHA256 between pages; other categories may already be exhausted.

ELF reads section-backed `SHT_SYMTAB` and `SHT_DYNSYM`, preserving table provenance.
Undefined dynamic symbols are import observations; externally visible, defined
global/weak/GNU_UNIQUE dynamic symbols are export observations. An ordinary symbol-table
global is not automatically a dynamic export. Duplicate names across tables are
not deduplicated or demangled. A missing table means unavailable coverage, not
proof that the program has no imports; sectionless dynamic recovery and versioned
provider resolution are not implemented.

PE reads normal import descriptors and lookup thunks, preserving named and ordinal
imports. Exports include names or ordinal-only entries, and forwarded strings are
reported without resolution. PE `symbols` is unavailable because COFF/PDB symbol
recovery is not implemented. Delay imports, bound runtime addresses, loader search
paths and execution remain unsupported; instruction decoding is a separate action.

Oversized or malformed tables produce an explicit error. Table scans are bounded
and cancellable; output is paginated rather than silently cut at the first page.

## Static instruction pages

`disassemble` selects a file-backed executable section of a header-validated ELF/PE,
using the executable entrypoint where available or the first executable section.
It does not follow branches, load a target library, resolve a dependency, or
execute instructions. Architecture is inferred from supported machine headers.
Unknown architectures, missing executable sections, overlapping mappings, and
non-file-backed addresses are rejected rather than interpreted as code.
ET_REL addresses are section-relative, not relocated runtime addresses.
Multiple unrelocated code sections with overlapping address namespaces are rejected.
`endianness` names instruction encoding; `data_endianness` preserves ELF data/header
order. AArch64 and ARM BE8 instructions remain little-endian in big-endian ELF.
PE raw alignment padding beyond the declared virtual span is not treated as code.

For raw bytes, `architecture` is required (`x86`, `x86_64`, `arm`, `thumb`, or
`aarch64`); raw decoding defaults to little-endian. Optional `base_address` names file byte zero, default 0. It cannot
relocate an ELF/PE image. An architecture override cannot reinterpret an image as
an incompatible ISA; ARM/Thumb mode selection is the only family override.
Mixed ARM/Thumb code is not reconstructed automatically. Caller-chosen offsets
are not proof of instruction boundaries, and inline data can resemble code.

Use `byte_offset` and follow `findings.next_byte_offset`, not the entry-based
`offset` used by table pages. Keep the full artifact SHA256, architecture and raw
base unchanged between pages. A page consumes up to 4096 file bytes and returns
1–100 decoded instructions (default 50). `bytes_consumed`, `bytes_remaining`,
`stopped_at_byte_offset`, `stop_reason` and `exhausted` distinguish page limits
from undecodable/truncated input. No skip-data mode is used. Undecodable bytes
stop decoding without inventing instructions: `next_byte_offset` may be null
while `exhausted` remains false. No instruction total for the whole file is claimed.

Instructions include exact file offsets, addresses, byte strings, mnemonics,
operands and classified flow observations. `xrefs` preserve observed direct
transfers; indirect or far transfers stay unresolved. `blocks` and their edges
refer only to this decoded page; external destinations are not silently followed.
These are linear-sweep, page-local observations, not recovered functions, a
complete CFG/call graph, runtime reachability, or proof that the bytes are code.
AArch64 AL/NV branches have no normal fallthrough. Fallthrough outside the ISA's
address width is omitted, without inferring address wrapping. x86/x64 `ud2` emits
`flow.kind = "trap"`, an unresolved trap edge and a block boundary, with no normal
fallthrough. Subsequent bytes may still be decoded by the linear sweep; this does
not claim they are reachable. Trap edges are not branch/call `xrefs`.
Other interrupts/exceptions, handlers, privileged effects and non-returning callees
are not fully modeled. A different page can have different inferred block boundaries. Thumb IT-state
is not carried across page boundaries; mode/predication context can be incomplete.

## Navigate to a declared code symbol

Use `{"action":"disassemble","path":"artifacts/program","symbol":"main","limit":25}`.
Names are exact and case-sensitive, without demangling or fuzzy/provider lookup.
Selection requires lossless UTF-8 ELF names or ASCII PE names. Binary table display
may still use replacement decoding; those displayed replacements cannot select
an invalid original byte spelling. A literal U+FFFD encoded as valid UTF-8 is
not an error and remains selectable.
ARM Thumb-bit normalization applies only to STT_FUNC, never arbitrary NOTYPE
values. An explicit ARM/Thumb caller mode is applied before alignment validation;
unaligned untyped addresses are rejected rather than rounded to another byte.
ELF selection scans section-backed FUNC/NOTYPE declarations, including records
beyond the first table page. PE supports named local exports, not COFF/PDB symbols
or imported providers. Undefined/absolute ELF symbols, ELF OBJECT declarations, non-executable data,
compressed code, forwarded and virtual-only targets cannot supply a local code start.
PE export tables do not distinguish function/data types; executable section
flags are an eligibility check, not proof that an export is a function.

A name resolving to distinct code starts is rejected as ambiguous. Duplicate
origins at the same start are combined; `selected_symbol` reports the address,
source count and at most eight provenance records, with explicit truncation.
ELF provenance `entry_file_offset` identifies a symbol-table record, not its code;
`findings.byte_offset` is the actual code cursor. Header/code mapping still
rejects unsupported ISAs, raw padding and overlapping executable regions.

`symbol` is mutually exclusive with `byte_offset` and raw `base_address`. Continue
with `next_byte_offset` **without** the symbol argument, preserving artifact SHA256
and ARM/Thumb mode. Selection is a starting point, not a recovered function extent:
disassembly may cross the declared symbol size and does not establish reachability.
References remain observations from the decoded page, not a whole-file incoming
reference search. Missing/stripped symbols are errors, not guessed function starts.

## Incoming direct references

Use `{"action":"references","path":"artifacts/program","symbol":"main","limit":100}`
or supply `target_address` instead of `symbol`. Exactly one target selector is
required; address zero is valid. Only linked ELF EXEC/DYN and PE are supported.
Raw code, compressed executable sections and unrelocated ELF objects are rejected.
No PLT/IAT/provider resolution, relocation application or indirect-target recovery
is attempted. Declared virtual addresses are not live runtime addresses.

`limit` bounds **decoded instructions**, not the number of hits: each page decodes
at most 100 instructions from at most 4096 bytes using one isolated worker. A page
with zero `matches` is still useful coverage and is not evidence of absence.
`unresolved_calls_jumps` counts indirect/unresolved calls and jumps on that page.
The default scan cursor is the first declared file-backed executable section in
file order, not the entrypoint or target symbol. Symbol declaration mode validates
the target only; `architecture` controls the source scan. For an untyped target
whose mode cannot be inferred, use its explicit declared `target_address`.

Pass `next_byte_offset` as `byte_offset` and preserve the full artifact SHA256,
source ISA/mode and target address. You can switch from a symbol to the returned
address to avoid repeated symbol-table lookup. Non-executable gaps are excluded.
Undecodable bytes stop the page without auto-skip; `next_region_byte_offset` is
only a diagnostic for an explicit new scan, not implicit coverage of skipped bytes.

Coverage is **this page only**: byte range, decoded bytes/instructions, eligible
bytes before/after it and declared region counts. `exhausted` means no further
eligible bytes after the page, never that preceding bytes were scanned. Even a
final page does not prove complete/reachable program coverage. Inline data,
mixed ARM/Thumb modes, missing prior Thumb IT state and alternate instruction
boundaries remain limitations; `whole_program_complete` is always false.

## Static PE import-slot references

Use `{"action":"references","path":"artifacts/program.exe","import_iat_rva":4352,"limit":50}`
after reading the standard import table with `binary`. Select the table entry's
`iat_rva`, not its lookup thunk RVA, raw file offset, import name, or a loaded
function pointer. Exactly one of `symbol`, `target_address`, or `import_iat_rva`
is required. This selector supports PE x86/x64 only.

The bounded import scan validates the whole standard import table, even after
finding a matching slot, and retains at most two candidates to reject duplicate
slot declarations. Selection works beyond the first display page. Missing,
malformed, ambiguous, cancelled, and over-budget tables produce no partial
success. A bound IAT without an original lookup table is rejected. Delay imports
and provider lookup are unsupported. Normal table scans retain their existing
256-descriptor/16,384-thunk/string budgets; no input or worker cap is increased.

Only decoded pointer-width near `CALL`/`JMP` memory operands addressing that
static IAT slot match: x86 absolute, and x64 RIP-relative or absolute forms with
no segment/index/address-size override. Far transfers, register/dataflow-based
calls, direct branches into the slot, and other addressing forms are not import
matches. The slot VA is computed from the **preferred** image base, not runtime
relocations. `target.address_role` is `iat_slot_preferred_va`; `target.import`
preserves the declared DLL/name or ordinal and table byte/RVA provenance.
`matches[].static_import_iat_rva` links each observation to that declaration.

**Static identity is not runtime resolution.** `matches[].target`,
`target.runtime_target`, and `target.runtime_provider` remain `null`. These
indirect calls/jumps still count in `unresolved_calls_jumps`, even when their
static declaration matches. A patched/hooked IAT or forwarder may use a different
runtime provider; no destination is inferred from on-disk IAT values.

Follow `next_byte_offset` using the same `import_iat_rva`, full SHA256 and source
ISA. Do **not** switch to `target_address`: that selects direct instruction
immediates and is a different query. Instruction/byte budgets, undecodable stops,
page-local coverage, and `whole_program_complete: false` remain unchanged.

## Real-fixture regression corpus

`tests/test_reverse_real_fixtures.py` builds temporary, non-executed ELF fixtures with
`clang` and `ld.lld`, then compares `llvm-objdump` instruction words against the
engine's raw file bytes, symbol starts, direct branch targets, incoming references,
and artifact SHA256. The corpus currently covers linked x86-64 and AArch64 ELF,
including an OBJECT symbol that must not be accepted as a code start. The worker
invocation is also checked to receive captured bytes rather than a target path.

AArch64 output intentionally distinguishes display order from file order: for
example, `llvm-objdump` may display `d65f03c0` while a little-endian ELF stores
`c0035fd6`. The known-answer assertions use both representations so an endian
regression cannot pass by comparing only formatted disassembly text. These tests
are skipped when the local clang/lld/LLVM toolchain is unavailable; the existing
synthetic decoder and resource-boundary corpus remains independent of that skip.

## Evidence and limits

Every successful operation returns `schema_version`, `engine`, `operation`,
`artifact`, `findings`, `limitations`, and a deterministic `zev_...` evidence ID.
The ID hashes canonical JSON of the record before the ID is added. File artifacts
carry a SHA256 of the entire captured file, never an unlabeled prefix hash.
Directories have `sha256: null`; the evidence describes only the observed page.
IDs prove reproducibility of recorded observations, not authenticity or safety.

- File input: at most 8 MiB, read in bounded chunks; observed file changes are rejected.
- Source input: at most 1 MiB, 50,000 visited nodes, 1,000 combined findings.
  Traversal/output caps are reported in limitations. The node cap is not a memory
  guard. Parsing occurs only in a temporary trusted Python worker, serialized to
  one active worker per Zeline process, with a 5-second total deadline including
  queue/startup. Cancellation kills and reaps that owned worker and closes pipes.
  POSIX enforces a 128 MiB writable-data limit and an address-space limit (accounting
  for existing `/proc` virtual reservations where available). Windows uses a
  128 MiB per-process committed-memory Job Object limit. These are allocation
  controls, not a universal 128 MiB RSS ceiling or an OS security sandbox.
  Limit setup failure rejects source analysis; there is no in-process fallback.
  Termux is exercised locally; Windows/macOS limit behavior still needs CI before
  a public release. Large pre-mapped runtimes without `/proc` may fail closed.
- Disassembly: 4096-byte decoder input, at most 100 instructions per call. Its
  own temporary trusted worker has a 5-second queue/startup/parse deadline and
  the same 128 MiB allocation controls described above. Source and disassembly
  serialize separately; one helper of each kind can coexist. Neither helper is
  an OS security sandbox, and neither executes the target.
- ELF/PE: at most 256 sections; ELF extended numbering is unsupported.
- ELF symbols: at most 100,000 entries per table, 200,000 across tables; symbol
  names are at most 512 bytes and decode as UTF-8 with replacement. `file_offset`
  locates the symbol-table entry, not the function's code. Reserved unnamed/null
  entries remain in `symbols`; imports exclude empty names.
- PE: at most 256 descriptors and 16,384 thunks (including terminators), 8,192
  export functions/names; strings are at most 511 bytes, ASCII with replacement.
  Export `offset` may be `null` for a valid virtual-only zero-fill data target;
  this never permits table structures to be read from non-file-backed memory.
- ZIP: at most 4,096 declared entries and 512 KiB central directory; ZIP64 and
  multi-volume indexes are rejected. Member sizes are unverified declarations.
- Pages: 1–100 entries/matches, default 50; offset is zero-based.
- Oversize/unsupported/malformed artifacts produce explicit errors, not invented
  findings or silently truncated input.

## Installation and security

The normal package dependencies include `tree-sitter==0.26.0`,
`tree-sitter-javascript==0.25.0` and `capstone==5.0.9`. JavaScript does not use
regex or a Node process. Capstone is the bundled instruction-decoding dependency
(BSD-style license), not an external REA server or a target library.
Supported wheels are installed normally by pip. Source builds need a C toolchain
and Python headers (Termux: `pkg install clang make`; iSH/Alpine: `build-base python3-dev`).
There is no package download on a tool invocation or normal startup. Source
analysis and instruction decoding launch only their bounded first-party Python
helpers, not the inspected program. `inspect`, `inventory`, `strings` and `binary`
remain in-process byte inspection without spawning subprocesses. This is not a persistent service. Parser
imports are lazy. Broken/missing dependencies affect that action, not startup.

Only regular files and immediate directory indexes inside the executor workspace
are accepted. POSIX uses descriptor-relative no-follow traversal where supported;
symlink paths, traversal, and special files are rejected. The fallback checks
observed symlinks but is **not** a race-proof Windows filesystem sandbox. Use a
restricted OS account or isolated workspace for hostile artifacts.

Targets are never imported, executed, extracted, or uploaded by this engine.
Artifact-derived strings and source are untrusted data, never agent instructions.
Tool results can nevertheless enter the configured model provider's context:
local analysis does not mean the entire agent workflow is offline.

The public `safe` profile gains no local-file access. Owner tool disabling and
execution policies still apply. No read-only operation is recorded as creating
an artifact. Advanced REA MCP remains separate and optional; it is not required
by the native engine. This feature makes no percentage improvement claim without
an agent-level benchmark using the same model, tasks, and budget.

### Worker memory accounting

Workers are resource-limited, not OS sandboxes. Linux/Android keep a 128 MiB
absolute data/heap limit, with the virtual-address limit adjusted for interpreter
startup mappings. Windows uses a 128 MiB per-process memory Job. On Darwin, XNU
checks **both** `RLIMIT_DATA` and `RLIMIT_AS` against the VM map size, including
large startup mappings. The worker measures its own baseline using Apple's
`PROC_PIDTASKINFO` ABI and sets both kernel limits to that baseline plus 128 MiB.
This is a **new virtual-memory growth budget**, not a 128 MiB absolute RSS claim.
Missing or incomplete baseline data fails closed. Native macOS CI also verifies
that a bounded trusted allocation probe cannot allocate 256 MiB under that budget.
