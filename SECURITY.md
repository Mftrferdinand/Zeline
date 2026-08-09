# Security policy

## Supported versions

Security fixes are applied to the latest release on the `main` branch.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's private vulnerability reporting feature for this repository. Include a clear reproduction, affected version, impact, and any suggested mitigation.

If private reporting is unavailable, contact the repository owner through GitHub and avoid including credentials, access tokens, or personal data.

## Safe deployment

Keep Zeline's data directory private. Use `safe` tool profiles for public messaging gateways, keep webhooks on loopback unless protected by a reverse proxy, and rotate any credential you accidentally disclose.
