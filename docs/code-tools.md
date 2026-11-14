# Calling Zeline tools from Python snippets

The `execute_code` tool makes a temporary `zeline_tools` module available **inside that snippet**. It is not a globally installed Python package and does not require another service.

```python
from zeline_tools import read_file, patch, terminal

page = read_file("example.py", offset=20, limit=40)
if not page["success"]:
    raise RuntimeError(page["output"])
print(page["output"])

change = patch("example.py", "old_value", "new_value")
if not change["success"]:
    raise RuntimeError(change["output"])

result = terminal("python -m pytest tests/test_example.py -q", timeout=120)
print(result["output"])
```

## Helpers

- `read_file(path, offset=1, limit=500, byte_offset=None)` calls the native `read_file` tool. Request explicit windows for useful line-range progress. `limit=0` retains the native open-ended read contract.
- `write_file(path, content)` calls `write_file`, including normal checkpoints and formatting.
- `search_files(pattern='', target='content', path='.', limit=50, offset=0, query=None, file_glob='*', regex=False)` maps the query and file glob to native `search_files`.
- `patch(path, old_string, new_string)` calls native `patch_file`; it is an actual edit, not a preview.
- `terminal(command, timeout=None, background=False)` calls native `run_shell`. A nonzero foreground exit produces `success=False`; inspect the native output for the exit code and diagnostics.

Every helper returns `{"success": bool, "tool": str, "output": str}`. `output` is the native tool result, not generated or summarized data. Check `success` before depending on a previous operation.

## Execution and controls

Inner calls reuse the original session's executor, identity, workspace, registered tools, policies, budgets, audit, checkpoints and cancellation. They do not create a new privileged executor. Disabled or disallowed tools remain unavailable. Arbitrary or recursive `execute_code` dispatch is not exposed by the module.

The code subprocess remains tracked and subject to the enclosing `execute_code` timeout and stop controls. Set the outer timeout high enough for the entire workflow. A short-lived private Unix socket is created for the invocation and removed afterward; no persistent listener is installed. This bridge requires Unix-domain socket support.

This is not an OS sandbox: `execute_code` remains an operator-only capability.

## Progress fidelity

Code progress shows `🐍 Executing code` with a compact preview of the actual Python snippet, not a generic `zeline_tools` label. Code, shell and native file-tool activity share one feed. The first invocation has no count suffix; only an exact rerun of the same tool with the same arguments receives an inline `(x2)`, `(x3)`, and so on. Different snippets or arguments do not inherit a counter merely because their rendered previews look alike. Inner callbacks and heartbeat redraws do not increment the outer code invocation count.

Inner tools emit their own real tool-start callbacks, including filenames and requested line ranges. A range such as `L20-59` represents the requested window, not a claim that every requested line exists. Open-ended reads never invent an end line. Progress remains bounded; it is not a separate bubble or counter for every tool family.

Changes to Python execution or callbacks require the normal guarded activation and graceful restart. Source/candidate tests alone do not mean a running gateway has loaded this feature.
