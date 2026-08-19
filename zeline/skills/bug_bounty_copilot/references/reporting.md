# Reference: Reporting, Severity & Triage (v2)

> **AUTHORIZED USE ONLY.** Apply this only to assets you own or that are explicitly in-scope for a bug bounty / pentest program you are enrolled in. Verify scope before acting; out-of-scope = stop. No destructive actions or DoS against production without written program permission.

> The report is the final product. A great bug with a weak report = rejected/underpaid.
> A clear report with credible impact = accepted and paid properly.

## Standard Report Structure
1. **Title** — concise & specific: `[Class] in [component] allowing [impact]`.
   Example: "IDOR in /api/v2/invoices allows cross-tenant invoice access".
2. **Summary** — 2–3 sentences: what the bug is, where, why it matters.
3. **Severity** — rating + CVSS vector + impact argument (see below).
4. **Steps to Reproduce** — numbered, precise, reproducible without guessing.
5. **Proof of Concept** — request/response, screenshots, or script (Web3 fork test).
6. **Impact** — real business/user impact. Explain realistic attack scenario.
   If there's chained impact not executed, explain narratively.
7. **Remediation** — actionable fix recommendation for dev team.
8. **Supporting material** — references, notes, environment.

### Traits of Accepted Reports
- 100% reproducible from written steps.
- Impact explained from business perspective, not just technical.
- Neutral & professional — no hype, no threats, no "give big bounty pls".
- One bug per report (unless interdependent chain).
- Sensitive data redacted.

---

## Severity & CVSS (v3.1 / v4.0)
CVSS provides a number; **impact argument** provides credibility. Include both.

**Base metrics (v3.1) quick reference:**
- **AV** Attack Vector: Network (N) / Adjacent (A) / Local (L) / Physical (P)
- **AC** Attack Complexity: Low (L) / High (H)
- **PR** Privileges Required: None (N) / Low (L) / High (H)
- **UI** User Interaction: None (N) / Required (R)
- **S** Scope: Unchanged (U) / Changed (C) — impact jumps to another component
- **C/I/A** Confidentiality/Integrity/Availability impact: None (N) / Low (L) / High (H)

**Score ranges:** 0.0 None · 0.1–3.9 Low · 4.0–6.9 Medium · 7.0–8.9 High · 9.0–10.0 Critical

**Honest scoring:**
- Choose metrics based on actual evidence, not worst-case imagination.
- If PR/UI makes attack more or less realistic, reflect that.
- For Web3: many platforms (Immunefi) use their own impact scale (Critical/High/...)
  based on **funds at risk / protocol damage** — follow program rubrics.
- Always include full **vector string** for triager verification.

**Example argument (not just numbers):**
> "High (CVSS 8.1 / AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:N). An attacker with a basic account
> can read and modify invoice data of other tenants without victim interaction, impacting
> confidentiality and integrity of financial data for all customers."

---

## Deduplication (before submitting)

Duplicates = no payout. Check first:
- Is this already documented? (changelogs, public security advisories).
- Is this a variant of your old report or known pattern for this asset?
- For contests (Code4rena/Sherlock): assume "obvious" bugs will have many reports —
  winners usually have the most precise root cause + best write-up.
If unsure about uniqueness: report anyway with clear angle/impact — let triager judge.

---

## Triage & Follow-up

After submission, you interact with the triager. Approach: cooperative, data-driven.

- **Request for more info** → provide clearer steps/artifacts, don't get defensive.
- **Severity dispute** → argue with concrete impact scenarios + CVSS vector, not emotion.
  If they're right, accept. If you're right, show proof.
- **Marked duplicate/informative** → ask for clarification politely; present new impact
  angles if uncovered.
- **Need deeper PoC** → coordinate; offer controlled demo/safe environment.
- **Timeline & disclosure**: follow coordinated disclosure. Don't publish before authorized/patched.

---

## Template Report

```
# [Class] in [component] — [impact summary]

## Summary
[2–3 sentences]

## Severity
[Rating] — CVSS:3.1/AV:_/AC:_/PR:_/UI:_/S:_/C:_/I:_/A:_ (score _)
[1–2 sentence impact argument]

## Steps to Reproduce
Prerequisites: [setup details]
1. ...
2. ...
3. ...

## Proof of Concept
[request/response/screenshot/script output]

## Impact
[realistic attack scenario + business impact; chaining = narrative description]

## Remediation
[actionable fix suggestion]

## Notes
[environment, timestamp, references]
```
