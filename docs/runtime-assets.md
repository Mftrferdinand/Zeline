# Packaged runtime UI assets

The public runtime UI was imported byte-for-byte from the approved installed
`zeline/runtime_assets` directory. No installed files were changed. This is a
composed distribution, not a reproducible upstream build checkout.

## Explicit import allowlist

Paths below are relative to `zeline/runtime_assets/`:

- `index.html`
- `runtime.css`
- `runtime.js`
- `vendor/motion/LICENSE.md`
- `vendor/number-flow/LICENSE.md`
- `vendor/runtime-motion-manifest.json`
- `useanimations/wallet-provenance.json`
- `useanimations/responsive-icons-provenance.json`

Only the first three files are browser entry assets. The other five preserve
third-party notices and installed provenance. Do not copy the entire installed
asset directory: it contains a backup that must never ship. Standalone motion,
number-flow, navigation, chrome and icon authoring intermediates and separate
animation JSON files are deliberately excluded: the current HTML loads only
`/runtime.css` and `/runtime.js`, and their dependencies are embedded. In
particular, the standalone motion entry imports files absent from the installed
snapshot (`vendor/number-flow/dist/index.mjs` and `useanimations/server.json`);
those unused authoring sources are not an alternative serving entry point.

## Third-party notices and provenance

Keep the files and embedded comments intact when distributing:

| Embedded component | Installed identification | Retained notice |
| --- | --- | --- |
| Motion | 14.9.0, MIT | `vendor/motion/LICENSE.md` (Motion B.V., 2024) |
| NumberFlow | 0.6.2, MIT | `vendor/number-flow/LICENSE.md` (Maxwell Barvian, 2024) |
| Lottie-web | 5.13.0, MIT | Full MIT notice inside `runtime.js` (Bodymovin, 2015) |
| useAnimations artwork | MIT; no exact upstream revision recorded | Full MIT notice inside `runtime.js` (useAnimations, 2018–2026); visible attribution link retained in HTML |
| Inter Latin variable font | SIL Open Font License 1.1 | Full OFL and Inter Project Authors copyright (2016) inside `runtime.js`; font bytes embedded there |

The two animation provenance JSON files document project-authored wallet artwork
and changes to bundled icon geometry/playback. Their wording is historical
installed provenance, not independently reconstructed upstream source history.
The installed manifest is retained unchanged. Its `motion.sha256` value is not
the hash of the current composed `runtime.js` or standalone motion vendor file;
do not use that historical field as a verification checksum for this import.
The installed snapshot provides no lockfile, original Lottie/useAnimations source
archive, or reproducible bundle recipe. This limits exact upstream provenance,
but the inspected distribution contains the applicable MIT and OFL notices.

## Serving and packaging requirements

Source and installed `runtime_dashboard.py` were byte-identical at inspection.
The existing exact `_STATIC` allowlist already serves all required public paths:

- `/` and `/index.html`: `text/html; charset=utf-8`
- `/runtime.css`: `text/css; charset=utf-8`
- `/runtime.js`: `text/javascript; charset=utf-8`

**No backend asset-route changes are needed for this composed UI.** Do not add a
generic directory-serving route or expose provenance, backups, or installed data.
The existing authenticated `/api/runtime` and `/api/events` endpoints supply live
state; the UI has no additional API endpoint requirement. Retain loopback/Host/
Origin/token boundaries, no-store, nosniff, referrer policy and current CSP. Font
bytes use `FontFace` with an ArrayBuffer, images use data URLs, animations are
embedded, and NumberFlow uses adopted shadow stylesheets; no CDN/font route or
CSP relaxation is required. Attribution hyperlinks are not automatic requests.

The package builder must include these eight files, including nested licence and
provenance paths, in wheel and source distributions. Package configuration is
outside this import's scope; verify archive contents before release. Packaging
only HTML/CSS/JS would lose the Motion/NumberFlow notices.

## Verification

Import verification compared SHA-256 digests against installed originals. The
three composed browser entry digests were:

```text
3effe169583dd4c148b3ef2c71d041ee60b29c894b66655f7c01865e31b060f0  index.html
7da827c865e8bfa92ff0ad2826280616c82ff57de2caea5530e8d596856352fa  runtime.css
fbbfa1e7455f4ac657a50a351aa94107c36e3660b47131ef02d106ed6040d822  runtime.js
```

Static inspection confirmed the Zeline Runtime title and task agent summaries
rendering before the hidden Agents-list guard. The import contains no backups,
symlinks, local absolute paths, email addresses or recognizable API/bot token
patterns in the inspected text. This is a scoped content audit, not a claim of
independent upstream licence or exhaustive secret verification.

`node --check` validated the composed JavaScript; JSON provenance/manifests were
parsed. Serving checks should use a disposable server rather than restarting the
installed dashboard. No live model/API writes are needed to verify these assets.
Primary browser entry bytes are intentionally not rewritten during import.

These checks establish no observed privacy or licence-notice blocker; exact
upstream rebuild provenance remains incomplete as described above.
