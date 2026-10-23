"""Config value expansion and secret hygiene for MCP server specs.

Operators write MCP config like ``{"headers": {"Authorization": "Bearer
${GITHUB_TOKEN}"}}`` or pass env through ``{"env": {"KEY": "${KEY}"}}``.
Without expansion, a literal ``${...}`` string reaches the server — which
fails confusingly and tempts people to paste real tokens into a file they
might share. OpenCode-style ``${VAR}`` expansion closes that: the secret
lives in the environment, the config stays shareable.

Two rules keep this safe rather than clever:

- **Known variables only.** A reference to a variable that is not set is an
  error naming the variable, not silent empty text — a config pointing at a
  missing secret must fail loudly at startup, not produce a broken request
  at every call.
- **URLs never interpolate ``${}`` silently either.** The same expansion
  applies to ``url``, ``command``, ``headers`` and ``env`` uniformly, so a
  self-hosted gateway URL can also come from the environment.
"""
from __future__ import annotations

import os
import re

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigReferenceError(ValueError):
    """A config value references an environment variable that is not set."""


def expand(value: str, environ: dict[str, str] | None = None) -> str:
    """Expand ``${VAR}`` references from the environment.

    Raises ConfigReferenceError when a referenced variable is unset or empty,
    so a missing secret fails at config load with its name in the message.
    """
    source = os.environ if environ is None else environ
    def replace(match: re.Match) -> str:
        name = match.group(1)
        resolved = source.get(name, "")
        if not resolved:
            raise ConfigReferenceError(
                f"config references ${{{name}}}, but it is not set in the environment")
        return resolved
    return _VAR.sub(replace, value)


def expand_spec(spec: dict, environ: dict[str, str] | None = None) -> dict:
    """Expand ``${VAR}`` in a server spec's url/command/headers/env fields.

    Unknown keys are returned untouched. A spec with no references is
    returned as an equal copy, so callers can keep their original.
    """
    if not isinstance(spec, dict):
        return spec
    expanded: dict = {}
    for key, value in spec.items():
        if key in ("url", "command") and isinstance(value, str) and "${" in value:
            expanded[key] = expand(value, environ)
        elif key in ("headers", "env") and isinstance(value, dict):
            expanded[key] = {
                str(name): expand(item, environ) if isinstance(item, str) and "${" in item else item
                for name, item in value.items()
            }
        else:
            expanded[key] = value
    return expanded
