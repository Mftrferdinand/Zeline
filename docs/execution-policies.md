# Required execution policies

Required policies are a separate, opt-in tool-dispatch boundary. Ordinary
`plugins/*.py` observers retain their existing best-effort behavior.
Installing this build does not activate a policy or change model configuration.

## Operator configuration

Create `$ZELINE_HOME/policies.json` (default home: `~/.zeline`):

```json
{"required": ["protect.py"]}
```

Create `$ZELINE_HOME/policies/protect.py` with a synchronous callback:

```python
def authorize(context):
    return context["tool"] in {"read_file", "runtime_info"}
```

This deliberately small example permits only those names. Do not install an
example without adapting the allowlist to the intended workload. Keep the state
and policy directories private to the operator; never install untrusted code.
No live policy is installed automatically by these changes.

`context` contains `tool`, an isolated deep copy of `args`, `profile`, `identity`
and `workspace` (a string or null). Rules run in registry order; every rule must
return the literal boolean `True`. False, None, numbers, dictionaries, missing
callbacks, incompatible signatures, async callbacks, import errors, exceptions
and SystemExit all deny dispatch. Exception messages and raw arguments are not
included in the denial. Callback-local mutations cannot rewrite tool arguments.

## Fail-closed behavior

- Missing/unreadable declared source, malformed registry, duplicate JSON keys,
  duplicate names and unsupported fields deny dispatch.
- Only simple `.py` basenames are accepted. Registry/source symlinks and sources
  resolving outside the policy directory are rejected.
- Registry input is capped at 16 KiB, at most 16 declared policies, and source
  input at 64 KiB per file. Limits are enforced before compilation.
- Policies use current source bytes on every dispatch, not timestamp-based
  bytecode caches. The temporary module is removed after evaluation.
- Effective arguments are checked **after** legacy observer rewrites and
  **before** native/custom/MCP/OpenAPI dispatch and internal reflection actions.
- A policy denial cannot be rewritten into success by an output observer. It
  stays an error for the normal outcome/audit/learning path.
- Policies apply to all tool profiles. `tools.plugins=false` only disables
  legacy observers; it cannot bypass a declared required policy.

With no registry, a fresh executor is unconfigured and behaves as before.
Once an executor observes a registry, disappearance is an error for that executor.
An explicit `{"required": []}` disables the rules without an ambiguous missing
file. A newly started process cannot distinguish an absent, never-configured
registry from one deliberately removed before startup; protect host
configuration and backups accordingly.

## Boundary and limitations

Policies are **trusted, owner-installed Python**, including in the safe tool
profile. They may execute arbitrary host code and must be kept short and
nonblocking. There is no in-process deadline or Python sandbox for a policy.
This is not protection against a compromised operator, writable host config or
a malicious local plugin; such code already runs with host privileges.

The gate governs `ToolExecutor` dispatch, not arbitrary Python execution,
recurring cron script processes, shell grandchildren, or actions occurring
inside an external MCP server. Allowing a tool does not grant capabilities that
its profile or native handler otherwise forbids. OS isolation, network egress
controls, semantic retrieval, complete privacy purge and GEPA are separate work.

## Verification

`tests/test_execution_policy.py` exercises real blocked/permitted file writes,
required-source loss, malformed decisions, private exception handling,
source refresh, argument isolation, effective-argument rewriting, mandatory
rules across profiles, dispatch routing and denial-preserving observers.
Legacy observer tests remain separately applicable. Built-wheel probes must
exercise the actual installed dispatcher, not just a synthetic decision object.
