# Bounded HTTP response previews

`http_request` and namespaced OpenAPI tools display text previews, not downloads.
Their display limit is 8,000 characters. That is separate from transport limits:

- Read in chunks of at most 4,096 bytes.
- Retain at most 32,768 decoded bytes plus one truncation lookahead byte.
- Read at most 65,536 wire bytes plus one lookahead byte.
- Ignore Content-Length as an authority for these limits.
- Decode identity, gzip, zlib-wrapped deflate, and raw deflate with bounded output.
- Advertise gzip/deflate by default. Unsupported or stacked content codings fail
  closed; they are not handed to an unrestricted automatic decoder.
- Close the response on success, truncation, blocked redirects, and read errors.

These are response-body bounds, not an overall process-memory quota. Headers,
TLS/HTTP library state, configured credentials, and other tool paths have separate
costs. Truncated compressed previews do not certify whole-stream CRC integrity.

## Redirects

Only same-origin HTTP(S) redirects are followed, up to five hops. Native HTTP
normalizes explicit/default ports. Cross-origin redirects are rejected to avoid
forwarding request credentials or payloads to another origin. This intentionally
tightens the native tool's former automatic cross-origin behavior; request the
new endpoint explicitly when appropriate.

A response hook discards redirect bodies before Requests prepares its `next`
request. This matters because Requests may otherwise consume a redirect body
**even when `allow_redirects=False` and `stream=True`**. The hook closes the raw
stream and caches an intentionally empty discarded body. A local-server regression
with a huge declared redirect body tests this library interaction.

Native HTTP preserves same-origin response cookies and standard method changes:
303/302 change non-HEAD methods to GET; 301 changes POST to GET; 307/308 preserve
the method and payload. OpenAPI preserves HEAD on 303 and PUT on 301. Both
integrations re-extract original response headers into their chain cookie jar;
`Max-Age=0` and expired `Expires` cookies remove matching domain/path entries.
Merging only `response.cookies` is insufficient because that jar omits expiry
tombstones. Standard Requests response/raw header metadata is required for this
expiry extraction; nonstandard custom adapters need equivalent cookie semantics.

Every followed destination is rechecked. Mixed public/private DNS answers and
non-global shared-address-space destinations are blocked. These checks do not pin
DNS to the eventual socket, constrain operator-configured proxies, or implement
an OS network sandbox. DNS rebinding and all other egress paths require separate
security work; do not claim universal SSRF resistance.

## OpenAPI credentials

Configured literal secret ranges are masked before applying the display limit.
When a byte cap cuts a response, a conservative secret-length tail is masked too.
Ranges are unioned in the original text so neither overlapping credentials nor
that tail boundary can create a newly exposed partial credential. Truncation
remains explicit. Literal configured secrets are also masked in displayed OpenAPI
reason/Content-Type metadata. Native HTTP and OpenAPI request failures report the
exception class but omit raw exception text, which can contain escaped header or
query credentials. This is not global redaction of arbitrary encoded credential
representations, all transport metadata, or unrelated tools.

## Verification scope

Tests use synthetic bounded streams, compression fixtures, mocked DNS and a
short-lived loopback HTTP server. They do not create live jobs or call providers.
The patch does not change the configured model, tool profile, CLI branding, App
layout, installed package, or running gateway.

Cross-platform CI, independent source review, integration with other pending
patches, release, and deployment are separate gates. This document is a contract,
not a claim those later gates have already passed.
