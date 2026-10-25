# Extending Zeline

Four ways to give the agent new capabilities without forking this repository.
Pick by what you are trying to do:

| You want to | Use |
| --- | --- |
| add a capability written in Python | [custom tools](#custom-tools) |
| audit, rewrite, or block existing tool calls | [plugin hooks](#plugin-hooks) |
| call an HTTP API you already have a spec for | [OpenAPI tools](#openapi-tools) |
| reuse a tool server someone else wrote | [MCP servers](#mcp-servers) |

All four load **only on the `workspace` and `full` profiles**. They run arbitrary
local Python or reach local processes, so a public messaging gateway on the
default `safe` profile never sees them. Check where you are:

```bash
zeline tools profile          # with no argument: prints the current profile
zeline tools list             # every native tool, its state, and the profile
```

---

## Custom tools

A Python file in `~/.zeline/tools/` turns every public function into a tool named
`custom_<function>`.

```bash
zeline tools custom-init my_tools.py   # writes a working starter file
zeline tools custom-path               # print the directory
zeline tools custom                    # list what actually loaded
```

```python
# ~/.zeline/tools/my_tools.py

def jira_issue(key: str, verbose: bool = False) -> str:
    """Fetch a Jira issue by key.

    key: issue key such as PROJ-123
    verbose: include the full description
    """
    ...
    return summary
```

That becomes `custom_jira_issue`. The JSON schema comes from the signature:
annotations give types, defaults decide what is optional, and the docstring
supplies both the tool description (first line) and the per-argument
descriptions (the `name: text` lines). There is no manifest to keep in sync,
because a manifest that can drift from the signature is a bug waiting to happen.

Annotations accepted: `str`, `int`, `float`, `bool`, `dict`, `list`. Anything else
is rejected with a clear message rather than guessed at — a wrong schema makes the
model send arguments your function cannot accept.

Four behaviours worth knowing:

- **One bad file cannot take down the agent.** Import errors, syntax errors, and
  unsupported signatures are collected per file and reported; every other file
  still loads.
- **Names are prefixed and cannot shadow a native tool.** `custom_` makes the
  origin obvious in transcripts, and guarantees a local file never silently
  replaces `write_file`.
- **Return a string.** The provider protocol requires one, so returns are coerced
  and exceptions become `ERROR ...` text instead of escaping into the turn.
- **Export a subset** with `ZELINE_TOOLS = ["jira_issue"]` at module level when a
  file also holds helpers you do not want the model to call.

## Plugin hooks

Custom tools *add* capabilities. Hooks *govern* the ones that already exist — a
different job, so a different mechanism. A file in `~/.zeline/plugins/`:

```bash
zeline plugins init 10-audit.py   # starter file
zeline plugins list               # loaded hooks, in run order
zeline plugins path
```

```python
# ~/.zeline/plugins/10-policy.py
from zeline.plugins import deny


def on_tool_before(name, args):
    if name == "run_shell" and "rm -rf /" in str(args.get("command", "")):
        return deny("blocked by local policy")
    return None


def on_tool_after(name, args, result):
    token = os.environ.get("COMPANY_TOKEN")
    return result.replace(token, "[redacted]") if token else None
```

This is the only place you get an audit trail of every tool call, argument
rewriting (inject a default, clamp a limit), and redaction of tool output
*before* it enters the model's context.

A hook sits on the path of every tool call, so a careless one is more damaging
than a careless custom tool. Hence:

- **A broken hook never breaks the tool call.** Exceptions are captured, the hook
  is skipped, the call proceeds.
- **Blocking is explicit.** Only a `deny(...)` sentinel stops a call. `None`, a
  wrong type, or no return at all means "no opinion", so a hook cannot block by
  accident — a silent, baffling failure mode.
- **Rewrites must be type-correct or they are ignored.** `on_tool_before` returns
  a dict to change arguments; `on_tool_after` returns a string to change output.
- **Order is deterministic:** sorted filename order, so `10-audit.py` runs before
  `20-redact.py` and you control the pipeline.

## OpenAPI tools

If the API you want already has an OpenAPI 3 document, you do not need to write
wrappers for it:

```bash
zeline tools openapi-add ./petstore.yaml       # copies it into ~/.zeline/openapi/
zeline tools openapi                           # list the tools it produced
zeline tools openapi-path
```

Each operation becomes one `api_<file>_<operationId>` tool with parameters
derived from the document. `.yaml`, `.yml`, and `.json` are supported, along with
local `#/...` references.

**Credentials never appear in a tool schema.** They are read from `~/.zeline/.env`
under a name derived from the file and the security scheme:

```
ZELINE_OPENAPI_<FILE>_<SCHEME>
```

So `petstore.yaml` with a security scheme named `apiKey` reads
`ZELINE_OPENAPI_PETSTORE_APIKEY`. If a required credential is missing, the tool
says which variable to set instead of sending an unauthenticated request. When a
document lists several security alternatives, the first one whose credentials are
actually present is used.

A broken document is reported without hiding the tools from every other file.

## MCP servers

Model Context Protocol servers expose their tools automatically — stdio for a
local command, streamable HTTP for a URL:

```bash
zeline mcp add filesystem --command "npx -y @modelcontextprotocol/server-filesystem ~/"
zeline mcp add openconnector --url http://localhost:3000/mcp
zeline mcp test filesystem      # connect and list the tools it offers
zeline mcp list
zeline mcp remove filesystem
```

`zeline mcp test` before relying on a server: it proves the transport works and
shows exactly which tools arrive, rather than leaving you to find out mid-turn.
Discovery has a shared deadline; a stdio probe that does not produce a complete
JSON-RPC line is timed out and its server process is retired before discovery
continues. A stdio server is a local process launched by Zeline, so the same
`workspace`/`full` restriction applies.

### Reverse engineering with REA

[REA (Reverse Engineer Anything)](https://github.com/morluto/rea) adds optional
local analysis tools through the existing MCP client. No Python dependency,
new daemon, model change, or native-tool replacement is required. This recipe
pins the published `rea-agents@6.7.0` package, not the moving `main` branch.

Prerequisites: Node.js 22.x >=22.19, 24.x >=24.11, or 26+, with npm. Install or
warm the pinned package **before** starting Zeline: a first-time package download
can exceed the shared six-second MCP discovery deadline. Review installation
scripts and optional native dependencies before installing on a shared host.
For a minimal static-analysis installation, use a dedicated directory:

```bash
npm install --prefix "$HOME/.local/share/zeline-integrations/rea" \
  --ignore-scripts --omit=optional --no-audit --no-fund --save-exact rea-agents@6.7.0
```

This deliberately skips lifecycle scripts and optional Frida support. It does
not install Ghidra, IDA, Hopper, browsers, JADX, or other external engines.

For the portable package-runner route, merge this entry into `mcp.servers`
(`zeline config path` prints the file location), preserving all existing fields:

```json
{
  "mcp": {
    "servers": {
      "rea": {
        "enabled": true,
        "transport": "stdio",
        "command": "npx -y rea-agents@6.7.0 mcp",
        "env": {
          "REA_MCP_INPUT_SCHEMA_PROFILE": "compact",
          "REA_MCP_OUTPUT_SCHEMA_PROFILE": "off"
        }
      }
    }
  }
}
```

The output-schema setting omits advertised output schemas that Zeline does not
consume; REA still validates results against its canonical contracts.

On Termux, measured cold discovery took about 15 seconds even with these profiles.
The first lookup can return no REA tools at the six-second deadline. With the
late-discovery recovery in this integration, a later lookup in the same session
sees the completed catalog without restarting or downloading again. Older
versions may cache the empty result permanently until a new session.

For the dedicated installation above, avoid package downloads at agent startup:
replace `command` with the absolute Node executable and the installed absolute
`node_modules/rea-agents/scripts/rea.mjs` path, followed by `mcp`. Quote paths
containing spaces. Do not put `$HOME` or `~` in that command: stdio commands use
argument splitting, not shell expansion. On Windows the package-runner route
needs `cmd.exe /d /c npx -y rea-agents@6.7.0 mcp`; absolute `node.exe` plus the
entrypoint avoids that wrapper. Do not copy POSIX installation paths to Windows.

Run `zeline mcp test rea` and inspect the reported tools and errors; exit zero
alone is not a readiness verdict. Tools are named `mcp__rea__<upstream_name>`.
Use `tool_search` for the relevant discovered tool instead of injecting the
whole catalog into a prompt. Load the bundled `rea-reverse-engineering` skill
for the investigation workflow. Start a new session with an existing
`workspace` or `full` profile; do not change gateway permissions just to expose
REA. No server is registered or activated merely by installing this Zeline package.

Start with a trusted local JavaScript fixture and static analysis. An MCP
handshake does **not** prove native decompilation, browser automation, APK
analysis, Frida, or runtime capture works. Each requires its own compatible
host and tools. Termux is not equivalent to desktop Linux: verify each desired
operation; Linux process workflows may need a procps-compatible `ps`.

REA exposes runtime and write-capable operations as well as static reads;
registration is not a sandbox or an authorization to execute an unknown target.
Use only owned or authorized targets. Tool output can contain private source,
paths, and credentials: results sent to the agent reach its configured model.
Keep evidence and limitations with every conclusion. Remove the entry with
`zeline mcp remove rea` and start a new session to stop exposing it. The local
installation and investigation artifacts remain until separately removed.

### Local code lookup with Sem

Install [Sem](https://github.com/Ataraxy-Labs/sem) on the machine running Zeline
and check `sem --version` (tested with 0.27.0). Merge this optional entry into
`mcp.servers` in your config, preserving existing settings:

```json
{
  "mcp": {
    "servers": {
      "sem": {
        "enabled": true,
        "transport": "stdio",
        "command": "sem mcp",
        "env": {"SEM_REPO": "/absolute/path/to/checkout"}
      }
    }
  }
}
```

Replace the path with a trusted checkout visible to Zeline, then run
`zeline mcp test sem`. Confirm `mcp__sem__sem_find` and `mcp__sem__sem_grep`
are listed without connection errors. Use a new chat session with an existing
`workspace` or `full` profile and your usual model configuration. Ask it to
look up a known symbol with `sem_find`, for example `parse_config`.

Zeline exposes all discovered server tools, not just search: Sem also has
validation capabilities that can execute commands. `SEM_REPO` selects a
repository and is not a sandbox. Use a restricted OS account for untrusted
checkouts. Sem may write a local index, and source returned to an agent goes
to its configured model. This does not replace Zeline's native tools.
Remove the entry with `zeline mcp remove sem` and start a new session to disable it.

### Parallel web search and page fetching

[Parallel Search MCP](https://docs.parallel.ai/integrations/mcp/search-mcp)
adds web search and page extraction without a Parallel account or API key.
Anonymous searches use Fast mode; the free endpoint has lower rate limits.

Add the following `parallel` server to `mcp.servers` in your Zeline config
(`zeline config path` prints its location). Merge the entry into your existing
configuration, keeping your other settings and servers:

```json
{
  "mcp": {
    "servers": {
      "parallel": {
        "enabled": true,
        "transport": "http",
        "url": "https://search.parallel.ai/mcp",
        "headers": {
          "User-Agent": "Zeline (Parallel Search MCP)"
        }
      }
    }
  }
}
```

Check the connection:

```bash
zeline mcp test parallel
```

Look for `mcp__parallel__web_search` and `mcp__parallel__web_fetch` in the
output. A zero exit status alone does not prove discovery succeeded: check
that both tools are listed and there is no connection error.

MCP tools require the `workspace` or `full` profile. Keep your current profile
if it already supports MCP. If your local CLI uses `safe` and you want to enable
extensions, run `zeline tools profile workspace`; this changes only the local
CLI profile, not gateway profiles. Start a new `zeline chat` session using your
existing model configuration, then try:

> Use Parallel to find the latest Python release on python.org, fetch the
> relevant release page, and summarize the changes with source links.

This adds separate MCP tools; it does not replace the native `web_search` or
`web_fetch` tools or change their providers. Requests send the search text and
fetched URLs to Parallel. To remove the server, run `zeline mcp remove parallel`
and start a new session.

---

## Choosing between them

Reach for a **custom tool** when the logic is yours and small — a lookup, a
calculation, a call to an internal service. Reach for **OpenAPI** when a spec
already exists; hand-writing wrappers for a documented API only creates drift.
Reach for **MCP** when someone has already built and maintained the integration.
Reach for a **hook** when the capability exists and what you need is control over
it.

## Changing Zeline itself

Adding a *native* tool — one that ships in the package and appears on the `safe`
profile — is a change to this repository, and it is not a one-file change: the
`ToolDef` in `zeline/tools.py`, its handler, a title in the Telegram and app
gateway progress renderers, and an entry in the compaction artifact map all have
to agree. See [CONTRIBUTING.md](../CONTRIBUTING.md).

## Native static artifact analysis

The development branch includes the default `reverse_engineer` tool on
workspace/full profiles. It does not require the optional REA MCP setup above.
See [built-in reverse engineering](reverse-engineering.md) for operations,
evidence format, parser dependencies, and safety/resource limits.
