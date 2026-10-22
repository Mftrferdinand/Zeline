# Terminal presentation

Interactive commands share blue panels. The large identity is shown only by
`zeline`, `zeline status`, and `zeline gateway run`, not `zeline gateway`
or the other command lists/help pages. The original
shadow-block ZELINE and AGENTIC AI marks remain stacked with one vertical
white-to-blue gradient. Glyph rows retain their fixed origin.

- `zeline status` and `zeline doctor`: Runtime, Workspace & configuration,
  Gateways, and Diagnostics cards. A running process is not reported as proof
  of a connected platform.
- `zeline gateway run`: foreground runtime card and timestamped adapter events.
  Read-only 9Router discovery checks a loopback HTML title, following at most
  two relative same-origin redirects under one one-second deadline. It does
  not send credentials, log in, start a router, or change the model provider.
  A low-refresh animated footer tracks STARTING/RUNNING/STOPPING/STOPPED.
  RUNNING means adapter threads are alive, not a claim that every platform or
  model provider is connected. Non-colour terminals use static state lines.
  The footer stops and restores its line on exit and errors.
  Startup, locking, drain, stop and signal handling are unchanged.
- Setup, model selection, lists, tools, sessions, skills, memory, cron, plugins,
  version, updater and help share the command presentation layer.
- Native input and arrow menus close the current panel before taking control.
- WhatsApp bridge QR output and installer subprocess output retain native layout.

## Compatibility

Redirected/non-TTY output remains plain and keeps existing exit codes. `TERM=dumb`
disables interactive presentation; `NO_COLOR` disables ANSI colour. Legacy
encodings use ASCII borders and text identity fallbacks.

Configuration JSON, path-only commands, patch/diff output, and SkillForge JSON
stay machine-readable. Presentation never redirects process-wide stdout or
patches Python builtins. Only CLI-owned messages enter panels; trusted menu
cursor operations use an explicit raw path. Untrusted messages are sanitised.

## Verification

`tests/test_command_ui.py` covers command registration, structured diagnostics,
raw output, nested invocations, menu/input safety, exception cleanup, terminal
widths, and a stubbed foreground gateway with adapter-thread logging. Gateway
previews must never start a second real poller or restart an existing service.
