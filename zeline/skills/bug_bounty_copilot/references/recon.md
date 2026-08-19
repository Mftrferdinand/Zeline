# Reference: Reconnaissance Methodology (v2)

> **AUTHORIZED USE ONLY.** Apply this only to assets you own or that are explicitly in-scope for a bug bounty / pentest program you are enrolled in. Verify scope before acting; out-of-scope = stop. No destructive actions or DoS against production without written program permission.

> Reconnaissance methodology for authorized scope. Map the in-scope attack surface thoroughly.

## Principles
Recon = mapping the **full attack surface**. Goal: know everything exposed, what's interesting,
where bugs are most likely. Order: **passive → active → targeted**. Passive doesn't touch
target directly; active probes directly. Go as deep as needed.

---

## 1. Passive Recon (no direct target contact)

- **Certificate Transparency**: crt.sh, censys — discover subdomains from certificates.
- **DNS & WHOIS history**: passive DNS, DNS archives for old/forgotten assets.
- **Search engine dorking**: `site:`, `inurl:`, `filetype:` operators for exposed files.
- **Wayback / archives**: historical endpoints & parameters that may still be alive.
- **Public code repos**: GitHub/GitLab for org repos, leaked secrets, TODO comments, configs.
- **Passive tech fingerprint**: BuiltWith, public headers, favicon hashes.
- **ASN & IP ranges**: map IP blocks owned by org from public data.

**Output**: candidate subdomain list, suspected technologies, historical assets, info leaks.

---

## 2. Active Enumeration

- **Subdomain enumeration**: brute force + resolve. Verify which are alive.
- **Port & service discovery**: open ports + service/version banners.
- **Content discovery**: hidden directories/endpoints via wordlists. Start small, scale up.
- **Parameter discovery**: hidden parameters on endpoints (arjun, param-miner).
- **Crawling / spidering**: map application flow, forms, JS endpoints, API calls from frontend.
- **JS analysis**: parse JavaScript for endpoints, frontend API keys, feature flags, logic.
- **Source map extraction**: unpack `.js.map` files to recover original source.
- **Swagger/GraphQL discovery**: `/swagger.json`, `/api-docs`, introspection queries.
- **.git / .env hunting**: exposed git repos, environment files, backup files.

---

## 3. Attack Surface Mapping

| Aspect | What to record |
|---|---|
| Entry points | All endpoints/params accepting user input |
| Auth surface | Login, password reset, SSO, tokens, session management |
| Roles & tenancy | Different access levels, multi-tenant boundaries |
| Sensitive data | Where money/PII/secrets flow |
| Tech stack | Frameworks, versions, dependencies (for known CVEs) |
| Trust boundaries | Where data crosses trust zones |

---

## 4. Prioritization

Rank by:
1. **High-value functions**: payments, auth, uploads, admin panels, internal APIs, integrations.
2. **Complexity**: new/complex features more fragile than mature ones.
3. **Trust boundaries**: role/tenant/user transitions = IDOR/authz bug goldmine.
4. **Forgotten assets**: old subdomains, leaked staging, deprecated API versions.

**Output**: ranked test candidates, each with vulnerability class hypothesis.

---

## Anti-patterns
- Collecting 10,000 subdomains but never analyzing any deeply.
- Running every tool at once without reading output.
- Active scanning wildcards that include out-of-scope assets.
- Skipping passive recon and going straight aggressive (loses context + noisy).
