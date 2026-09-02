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
A stdio server is a local process launched by Zeline, so the same
`workspace`/`full` restriction applies.

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
