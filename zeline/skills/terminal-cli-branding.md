# Terminal Cli Branding

> Design, implement, test, and publish polished cross-platform terminal CLI branding.

Use for terminal banners, setup wizards, CLI status screens, and installer identity across Termux, Linux, macOS, Windows terminals, and CI.

## Design goals

- Use a real, legible ASCII/FIGlet wordmark when the user asks for large title text; do not substitute a small framed label. A border containing the plain name is not a “large banner.”
- Treat the wordmark width as a layout constraint. Center subtitle, version, and credit lines only when requested and when the result remains legible; if the subtitle is wider, print it as a normal line below rather than padding or distorting the wordmark.
- Use restrained ANSI cyan-to-blue treatment only when color is supported. Keep a clean monochrome fallback for `NO_COLOR`, `TERM=dumb`, non-TTY output, logs, and CI.
- Keep supporting copy concise. Follow explicit user wording preferences exactly; do not add labels the user rejected.
- Preserve the same identity in the interactive CLI, setup wizard, status/doctor screen, foreground gateway runner, and installer.
- Lock an explicit identity tuple before broad replacement: **parent brand spelling**, **product spelling**, **CLI command**, and **credit line**. Repeat it back verbatim, then change only those mapped concepts. Never infer a new spelling from a typo or casually shorten the parent brand.
- For user-visible rebrands, keep internal import/package aliases only as documented compatibility surfaces; do not let legacy names leak into banners, prompts, gateway replies, default agent names, docs, or commands.

## Implementation workflow

1. Inspect the current command resolution and installed package path before claiming an update is live. Check the shell-resolved command, generated wrapper contents, package metadata, and imported module path; pip user scripts may not be on PATH, while an older wrapper may invoke a stale or empty interpreter path.
2. Lock the identity tuple with the user before global replacement. Classify each old-name occurrence as parent brand, product/command, public copy, data/config namespace, or internal compatibility surface.
3. Select or construct an ASCII wordmark that fits common terminal widths. Render the intended parent/product name exactly. Compare 2–3 candidate FIGlet fonts by width and line count; prefer a 4-line wordmark around 45–55 columns for Termux.
4. Store the wordmark as immutable lines and compute its display width from the longest line.
5. Build a `color_enabled()` gate: disable ANSI for `NO_COLOR` and `TERM=dumb`; permit ANSI for TTY/forced-color contexts.
6. Render color line-by-line, preserving line lengths. Render plain mode from the same source lines. Print ordinary metadata as ordinary text below the wordmark.
7. Model onboarding as an explicit state machine rather than a fixed generic wizard. When the product requires gateway-first onboarding, bare launch must open one arrow-key picker (Telegram/WhatsApp/Webhook/Cancel), configure only the selected gateway, return to the shell, direct the user to model setup, and keep chat locked until both stages are complete.
8. For arrow-key pickers, restore terminal state in `finally`, support wraparound Up/Down + Enter, provide a numbered non-TTY fallback, and verify through a real PTY. Cancel must exit without creating config.
9. Make commands discoverable with `--help`; add only unambiguous convenience aliases and test each alias.
10. Reinstall from the modified checkout and run the actual command resolved by the shell. Do not treat source-level tests as proof that the installed CLI changed.

## Provider-default, migration, and fresh-install safety

- Treat **package freshness**, **data freshness**, and **process freshness** as separate states. Removing a package and data directory is not a clean uninstall if an old CLI/gateway process remains alive and can recreate state.
- Before claiming a clean uninstall, identify and stop exact product processes, verify they exited, then remove package, wrappers, new and legacy data directories, and install caches. Abort rather than continuing when process shutdown cannot be verified.
- After reinstalling, prove the fresh-user boundary with a real isolated or cleaned HOME: no config before setup, no credential-bearing environment override, and a chat attempt must refuse with a setup instruction.
- Persist an explicit `setup_complete: false` default. Only an intentional setup/model command may set it true; the mere presence of a legacy config, API key, or model must never unlock chat on a supposedly fresh install.
- Do not silently inherit a stale provider/model from an unrelated local configuration when no usable API key is configured.
- Fresh or keyless setup should show generic OpenAI-compatible defaults.
- Offer an explicit reset flag that discards stored provider defaults.
- State clearly in the setup UI that nothing is imported automatically.
- During a rebrand, a copied legacy config may legitimately preserve provider, model, API key, gateway token, and allowlists. Do not call the preserved model a new default and do not replace it by guesswork.
- Normalize only known legacy identity defaults (for example, an old framework name used as the default agent name). Preserve custom agent names.
- Migration must be copy-first, never delete the legacy directory, never overwrite an existing new directory, and keep secret-file permissions restrictive.
- Provide a model-only command that updates provider/base URL/API key/model without rerunning gateway setup or changing messaging tokens.

## Secret-input UX

For API keys, bot tokens, and other secrets, hidden input with no visible feedback is confusing. On interactive terminals, use a masked secret reader that shows exactly one `*` per entered character, supports paste, backspace, Enter, and Ctrl-C, and never prints the actual secret. Keep a secure no-echo fallback only for redirected or limited stdin.

Prompt wording should make the behavior obvious, for example:

```text
Telegram bot token [* per character]: ****************
✓ Saved securely. The value stays hidden.
```

Test both that the captured secret is not written to output and that the masked-input helper is used by setup flows.

## Test and verification checklist

- Add a regression test before changing behavior.
- Test plain output with `NO_COLOR=1` and assert it contains no ANSI escape sequence.
- Test ANSI output separately when supported.
- Assert subtitle/credit text, product name, and precise width alignment.
- Run CLI parser/help smoke tests, shell syntax checks for installers, and the full test suite.
- Reinstall from the actual project source and invoke the installed command to verify the deployed package, not only the checkout.

## GitHub publication

For protected repositories, commit branding changes on a feature branch, create a PR, wait for required checks, and merge only after success. See `references/github-branding-publication.md` for the asset workflow.

For the full rename/migration/Termux verification playbook, see `references/rebrand-migration-and-termux-cli.md`.

For uninstall/reinstall isolation and the setup-completion guard, see `references/clean-uninstall-fresh-install-boundary.md`.

For provider protocol/model discovery, visible API-key masking, model verification gates, native Anthropic transport, and safe runtime self-analysis, see `references/provider-setup-fresh-install-and-introspection.md`.

For the gateway-first onboarding state machine, arrow-key picker, strict stage guards, and Cancel semantics, see `references/gateway-first-provider-onboarding.md`.


---

## Lampiran: `references/clean-uninstall-fresh-install-boundary.md`

# Clean uninstall and fresh-install boundary

## Durable lesson

A CLI uninstall has three independent dimensions:

1. **Package freshness** — distribution and generated scripts are removed.
2. **Data freshness** — current and legacy config/data directories are absent.
3. **Process freshness** — no old CLI, daemon, gateway, watcher, or child process remains able to recreate state.

Deleting package + data while a process remains alive can produce a false fresh install: an old process may recreate provider credentials, model selection, gateway tokens, or memory immediately after reinstall.

## Safe clean-uninstall sequence

1. Resolve the live command and inspect exact process identities.
2. Stop through the supported lifecycle command.
3. If lifecycle state is stale, match only the exact expected executable/module command line; never broad-kill unrelated Python processes.
4. Verify every matched process exited. If not, abort cleanup and report the blocker.
5. Remove package distribution, generated command wrappers, current data directory, legacy data directory, and installer source caches.
6. Verify command resolution, package metadata, and data paths are absent.

## Fresh-install proof

Use an isolated HOME or truly cleaned HOME and verify:

- importing/seeding bundled assets does not create `config.json`;
- API key is empty and no credential environment override is present;
- chat before setup exits nonzero and says to run setup;
- chat before setup does not create/import config;
- setup writes an explicit completion marker;
- only setup or an intentional model/provider command can enable chat.

## Regression-test contract

A useful contract is:

```text
setup_complete defaults false
chat + stale key/model + setup_complete false => reject
setup => setup_complete true
model/provider command => setup_complete true
```

This prevents an accidentally restored legacy config from silently authenticating a nominally fresh installation.



---

## Lampiran: `references/gateway-first-provider-onboarding.md`

# Gateway-first onboarding, provider discovery, and safe introspection

## Final onboarding state machine

Use explicit persisted states rather than inferring readiness from the presence of a config/key:

```text
fresh install
  -> bare command opens gateway picker
  -> configure exactly one selected gateway
  -> return to shell and direct user to model setup
  -> detect protocol + fetch models + require explicit model selection
  -> unlock local chat only when gateway and model are both ready
```

Recommended fields:

```json
{
  "gateway_setup_complete": false,
  "setup_complete": false,
  "provider": {
    "protocol": "openai",
    "model_verified": false
  }
}
```

Guards:

- No gateway: bare command opens picker; model setup is blocked.
- Gateway ready, model not ready: direct to the model command; never open chat.
- Both ready: open chat.
- Cancel: exit without creating config.
- Configure only the selected gateway; never ask Telegram, WhatsApp, and Webhook sequentially.

## Arrow-key picker

TTY UX:

```text
Telegram
WhatsApp
Webhook
Cancel
```

Use Up/Down + Enter, wrap selection, restore terminal state in `finally`, and offer a numbered fallback for redirected/non-TTY stdin. Test through a real PTY, not only mocked key events. Verify Cancel creates no config.

## Provider discovery

After Base URL and API key:

1. Probe `GET {base_url}/models` with OpenAI Bearer auth.
2. If unsuccessful, probe with Anthropic `x-api-key` and `anthropic-version` headers.
3. Parse unique non-empty model IDs and show a numbered picker.
4. If listing is unavailable, require an explicitly typed model ID; do not accept a placeholder/default via Enter.
5. Persist `protocol` and `model_verified`.
6. Runtime transport must match detection: OpenAI `/chat/completions`; Anthropic `/messages` with native message/tool schemas.

A model placeholder is not proof that the provider supports that model. Chat must reject unverified models.

## Secret UX

On a TTY, show exactly one `*` per entered/pasted character; support backspace, Enter, and Ctrl-C; restore terminal settings in `finally`. For non-TTY input use secure no-echo fallback. PTY verification should prove the star count and absence of plaintext.

## Safe self-analysis

Expose non-secret runtime facts through a tool such as `runtime_info` and a bundled self-analysis skill:

- framework/lab identity
- model ID
- provider base URL
- protocol
- tool profile and available tools

Never include API keys, bot tokens, webhook tokens, private keys, or credentials. Inject a concise non-secret runtime summary into the system prompt so the model can answer basic self-identity questions even when it does not call the tool.

## Fresh-install boundary pitfall

Package freshness, data freshness, and process freshness are separate. Before a clean reinstall, stop and verify exact old CLI/gateway processes, then remove package/wrappers/data. After install, prove in a clean HOME that chat cannot run or recreate/import config before onboarding completes.



---

## Lampiran: `references/github-branding-publication.md`

# GitHub branding asset workflow

Use supplied official artwork as the README logo asset. Preserve its native aspect ratio and do not stretch, redraw, or replace it with CSS.

For a GitHub social preview, create a 1280×640 image:

1. Resize the supplied artwork proportionally so it has safe margins.
2. Center it on the canvas.
3. If the source aspect ratio does not fill 2:1, create a dark/blurred backdrop derived from the source rather than stretching the foreground.
4. Inspect the output visually for clipped letters, readability, and balance.

Repository publication:

- Replace `assets/<product>-logo.png` with the supplied official asset.
- Save the social composition as `assets/<product>-social-preview.png`.
- Commit both assets on a feature branch.
- Open a PR when main is protected, wait for tests/CodeQL/dependency review, and squash merge.
- Verify the merge commit and the remote `main` SHA.

Do not assume the GitHub REST social-image endpoint is available for every token. Verify it explicitly before saying the repository-level Open Graph image was changed.



---

## Lampiran: `references/provider-setup-fresh-install-and-introspection.md`

# Provider Setup, Fresh-Install Gates, and Safe Runtime Introspection

Use this reference when a branded CLI configures an LLM provider, migrates legacy data, or must prove a genuinely fresh first-run experience.

## Identity and first-run contract

Lock four values before broad edits:

- parent/lab spelling;
- product spelling;
- CLI command;
- credit line.

Do not infer spelling from a typo. Assert the exact tuple in tests and scan for near-miss spellings.

A package install is not equivalent to completed setup. Persist an explicit `setup_complete: false` default and allow only deliberate setup/model configuration to set it true. Chat must fail closed until setup is complete.

If an old placeholder model may survive migration, track model confirmation separately (for example `model_verified`). Do not permit chat merely because an API key and a model-shaped string exist.

## Provider discovery

After collecting base URL and API key:

1. Probe `GET <base_url>/models` as OpenAI-compatible using `Authorization: Bearer <key>`.
2. If that does not return model IDs, probe as Anthropic using `x-api-key` and `anthropic-version`.
3. Parse `data[].id`, deduplicate, sort, and show a numbered picker.
4. Persist both selected model and protocol (`openai` or `anthropic`).
5. If model listing is unavailable, require an explicit non-empty model ID. Never accept a placeholder through Enter/default.
6. Keep provider discovery timeout bounded and never print response bodies that may contain sensitive details.

Detection UI must correspond to real runtime support. OpenAI-compatible uses `/chat/completions`; native Anthropic uses `/messages`, Anthropic headers, a separate system field, Anthropic tool schemas, and normalized tool-use/tool-result blocks.

## Visible secret masking

No-echo prompts can look frozen or broken. On a real TTY:

- render exactly one `*` per entered character, including pasted characters;
- support backspace by erasing one star;
- restore terminal settings in `finally`;
- handle Enter and Ctrl-C;
- never echo or log plaintext.

Use secure `getpass` fallback for non-TTY input. Verify masking in an actual PTY, not only mocked unit tests.

## Safe self-analysis

Model ID, provider base URL, protocol, framework identity, tool profile, and available tool names are non-secret runtime facts. API keys, bot tokens, webhook secrets, wallet secrets, and credentials remain secret.

Provide a read-only `runtime_info` tool available to safe profiles. It should return only non-secret facts and an explicit note that secrets are omitted. Inject a short non-secret runtime summary into the system prompt so the model can answer basic identity questions even if it does not call the tool. Add a bundled self-analysis skill that directs the model to use `runtime_info` rather than guess.

## Clean uninstall and POV verification

Before claiming a fresh-user POV:

1. Stop and verify every exact product CLI/gateway process, not only the PID-file path.
2. If graceful stop fails verification, investigate the exact command line before removing state.
3. Uninstall package and remove generated commands.
4. Remove both current and legacy data directories only within the confirmed scope.
5. Confirm no matching process remains; a surviving process can recreate deleted state.
6. Reproduce in an isolated empty HOME: package install/skill seeding must not create provider config.
7. Before setup, assert chat exits nonzero, points to setup, and does not create/import config.
8. After source changes, reinstall and invoke the shell-resolved command; source tests alone do not prove the deployed CLI changed.

## Verification checklist

- [ ] Exact identity tuple asserted
- [ ] `setup_complete` defaults false
- [ ] stale/unverified model cannot chat
- [ ] OpenAI and Anthropic discovery contracts tested
- [ ] numbered picker tested
- [ ] manual fallback requires explicit model
- [ ] PTY star masking verified without plaintext
- [ ] native runtime transport matches detected protocol
- [ ] runtime introspection omits every secret
- [ ] isolated-HOME fresh install cannot chat before setup
- [ ] full suite, installer syntax, package build, and installed command pass



---

## Lampiran: `references/rebrand-migration-and-termux-cli.md`

# Rebrand, migration, and Termux CLI notes

Use this reference when a Python CLI is renamed while preserving existing installations.

## Identity lock

Before broad edits, write and confirm four exact strings:

```text
Parent brand: <EXACT SPELLING>
Product/framework: <EXACT SPELLING>
CLI command: <exact-command>
Credit: <Exact credit line>
```

Do not infer spelling from later typos. Classify old-name matches before replacement:

- Parent/lab brand
- Product/framework and command
- Public runtime text
- User data/env namespace
- Internal import/package compatibility

## Large banner acceptance

A framed plain-text label is not a large wordmark. For narrow terminals, compare FIGlet candidates and choose a true 4-line ASCII rendering around 45–55 columns. Render metadata normally beneath it:

```text
<LARGE ASCII WORDMARK>
PRODUCT · CATEGORY · vX.Y.Z · BY AUTHOR
```

Keep ANSI and monochrome output generated from the same immutable lines. Regression tests should assert the exact lines, no border fallback, no ANSI under `NO_COLOR`, and correct subtitle/credit.

## Legacy config migration

A safe rename migration is copy-first:

1. If the new data directory exists, do nothing.
2. If only the legacy directory exists, copy it recursively.
3. Never delete the legacy directory.
4. Preserve provider URL, model, API key, gateway tokens, allowlists, memory, and skills.
5. Normalize only a known legacy *default* identity name; never replace a custom persona name.
6. Keep config permissions restrictive (0600 where supported).
7. Report preserved models as migrated state—not as a newly chosen default.
8. Let users change provider/model through a dedicated model command that does not rerun gateway setup.

Test both first migration and an already-migrated config that still contains the legacy default name.

## Termux command verification

After `pip install --user`, scripts commonly land in `~/.local/bin`, which may not be on Termux PATH. Verify all layers:

```bash
command -v <command>
<command> --version
python -m pip show <package>
python -c 'import package, package.cli; print(package.__file__); print(package.cli.__file__)'
```

Inspect any existing `$PREFIX/bin/<command>` wrapper. A generated wrapper can be present but invalid if its interpreter path was expanded to an empty value. Prefer a wrapper with a verified absolute Python path, or ensure `~/.local/bin` is in PATH. Then run the installed command itself (`doctor`, banner, or help), not just `python -m` from the checkout.

## Publication sequence

1. Regression tests RED → implementation GREEN.
2. Full suite, installer syntax, diff check, wheel build.
3. Reinstall from checkout and invoke the shell-resolved command.
4. Rename remote repo only after the identity tuple is final.
5. Update remote and every live URL.
6. Commit source only; exclude unrelated staging directories.
7. PR → CI green → merge only with explicit merge authorization.
