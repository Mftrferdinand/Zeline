# REA Reverse Engineering

> Investigate owned or authorized local software with REA (Reverse Engineer Anything), using evidence-backed static analysis first and explicit prerequisites for native or runtime analysis.

## When to use

Use when the operator asks to inspect an application's implementation, binaries,
JavaScript/Electron modules, managed assemblies, APK structure, or recorded behavior.
This workflow is optional: it does not install or start REA by loading this skill.
Reference: https://github.com/morluto/rea (MIT); integration: docs/extending.md,
"Reverse engineering with REA". The documented package pin is rea-agents 6.7.0.

## Readiness

1. Identify the target path, the question, and the operator's authorized scope.
   Do not assume permission to run an unknown executable or attach to a process.
2. Use `tool_search` to find the relevant `mcp__rea__` tools and read the actual
   current schema. Do not invent tool names or arguments from this skill.
3. If absent, report that the owner must register the local MCP server and use
   a workspace/full session. Never weaken a public gateway's safe profile.
   The CLI check is `zeline mcp test rea`; inspect its tools and errors rather
   than interpreting exit zero as proof of readiness.
4. The first cold REA start may take longer than Zeline's six-second discovery
   budget. With late-discovery recovery, a subsequent tool lookup can see the
   completed catalog in the same session; do not repeatedly spawn servers.
   Older Zeline builds without recovery may cache an empty catalog until a new
   session. Use the integration's real discovery verification before relying on it.
5. An MCP connection proves neither a working decompiler nor runtime capture.
   Check the required engine and host for the specific operation. Termux is not
   desktop Linux. Do not install Ghidra, IDA, Hopper, Frida or a browser implicitly.

## Investigation

1. Prefer static analysis of a small, trusted, copied fixture before a real target.
   For JavaScript, discover `analyze_javascript_application` and provide an
   absolute `input_path`. Start with `detail: "summary"` and a narrowly scoped
   set of source paths when the discovered schema supports these options.
2. Retain the returned evidence ID, artifact digest, source locations, coverage,
   and limitations. Separate observed facts from derived hypotheses and unknowns.
   Static relationships do not establish runtime execution or behavioral equivalence.
3. Inspect selected findings or analysis views using the current tool schemas.
   Request the minimum source and output needed; avoid dumping a full application
   or entire catalog into model context. Redact credentials from explanations.
4. Runtime capture, process attachment, file extraction and rewriting have effects
   beyond static reads. Confirm target/scope before those operations; use a
   disposable workspace and a restricted OS account for untrusted software.
5. Reconstruct behavior in the operator's project only after enough evidence is
   available. Write regression tests first, implement incrementally, and exercise
   the result. Do not claim REA's pseudocode itself is a verified replacement.

## Reporting and cleanup

Report the target/digest, the exact operations exercised, the evidence IDs,
findings, limitations and untested capabilities. A missing engine is a concrete
prerequisite, not a successful analysis. Analysis runs locally, but tool results
seen by the model reach the configured model provider.

Close temporary MCP clients after checks. Preserve investigation artifacts unless
asked to remove them. Do not restart gateways, alter model/provider settings,
change permissions, publish source, or upgrade packages merely to use this skill.
