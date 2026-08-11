# Local Dev Servers

> Run, rebuild, and manage local web dev servers on Termux (Astro, MkDocs, static HTML) for preview across multiple project types.

Start, rebuild, and verify local web development servers on Termux. Handles multiple project types — Astro, MkDocs, Python static server — and checks for uncommitted changes before serving.

## Trigger

User asks to run/start a local dev server on a specific port, or to run multiple servers at once. Common ports: 8081 (UserDocs/Astro), 8089 (ZelineGuide/MkDocs), 8092 (CommunitySite/static).

## Steps

### 0. Resolve the exact target from conversation context

Before inspecting or starting anything, map the user's wording to the most recent explicit ordered list, reply target, port, or project name.

- If the user says **"server ke-3"** after listing three URLs, use the third URL directly; do not ask which project.
- If the user says **"run semuanya"**, start and verify every listed server, not only one.
- If the user replies to a message containing a specific port (for example `8089`), keep all diagnosis and fixes scoped to that port until the user changes target.
- State the resolved target briefly before acting when multiple servers are nearby: `8089 → ZelineGuide`.
- Never report a group of servers as running until each endpoint has returned a successful HTTP response.

### 1. Identify the project directory

```
ls -d ~/user-site ~/docs-site ~/the community-site 2>/dev/null
```

Common project paths:
- `~/user-site` — Astro project (port 8081)
- `~/docs-site` — MkDocs project (port 8089)
- `~/the community-site` — Static HTML (port 8092)

### 2. Check for uncommitted changes

Before serving, check if the project has uncommitted source changes that would make the build outdated:

```bash
cd ~/project-dir && git diff --stat HEAD
```

If there are uncommitted changes, **rebuild before serving** — otherwise the user sees the old version.

### 3. Rebuild if needed

- **Astro**: `cd ~/project && npx astro build`
- **MkDocs**: `cd ~/project && source venv/bin/activate && mkdocs build`
- **Static HTML**: No build step needed — serve directly with Python.

### 4. Start the server (background)

- **Astro (preview static build)**: `cd ~/project && npx astro preview --port <PORT> --host 0.0.0.0`
- **Astro (dev mode)**: `cd ~/project && npx astro dev --port <PORT> --host 0.0.0.0`
- **MkDocs (source mode)**: `cd ~/project && source venv/bin/activate && mkdocs serve -a 0.0.0.0:<PORT>`
  ⚠️ `mkdocs serve` **cleans and rebuilds** the `site/` directory from `docs/` sources. If the project has custom scripts that write directly to `site/` (e.g., translation, expansion, or post-processing scripts), those changes will be **overwritten**.
- **MkDocs (static mode)**: When the project has custom content injected into `site/` (e.g., via Python scripts), serve the static `site/` directory instead of using `mkdocs serve`:
  - `cd ~/project && python3 -m http.server <PORT> --directory site`
  - Or use a project-specific static server (e.g., `no_cache_server.py` in ZelineGuide).
- **Static HTML (casual preview)**: `cd ~/project && python3 -m http.server <PORT>`
- **Static HTML (iterative UI work)**: prefer a project `no_cache_server.py` that adds `Cache-Control: no-store, no-cache, must-revalidate, max-age=0`, `Pragma: no-cache`, and `Expires: 0`. Plain `http.server` may return `304 Not Modified`, making the user’s Android browser appear unchanged even when the file was edited.

Use `background=true` and `timeout=600` for long-running server processes.

**⚠️ Never use shell `&` (background) inside a Zeline-managed background process.** The command passed to `terminal(background=true)` should be a single foreground process — Zeline tracks it by PID. Adding `&` spawns a child that Zeline cannot track, and `process(action="kill")` will only kill the parent shell, leaving the actual server running as a ghost. Examples:

```bash
# BAD — Zeline cannot track the child process
python3 -m http.server 8081 &    # ← shell & creates an untracked child

# GOOD — Zeline manages the process directly
python3 -m http.server 8081       # ← foreground, Zeline tracks by PID
```

### 5. Verify the server is running and serving the intended artifact

First verify transport:

```bash
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:<PORT> --connect-timeout 5
```

Then verify identity/content, not just HTTP 200:

```bash
curl -s http://127.0.0.1:<PORT> | grep -o '<title>[^<]*</title>'
```

For a rebuilt or script-generated site, compare source/build modification times and check one distinctive marker from the intended version. A successful status code can still be an old or wrong build.

During iterative UI work, also inspect response headers and the live server log:

```bash
curl -sSI http://127.0.0.1:<PORT>/ | grep -iE 'HTTP/|cache-control|pragma|expires'
```

If the user says “no change,” their real device is authoritative. Confirm the exact URL/query string they opened appears in the server log, then fetch that same URL and verify a distinctive source marker or checksum. Do not argue from a headless screenshot alone. A cache-busting query helps only after confirming the server is actually serving the edited file.

For interactive WebGL pages, also perform a browser smoke test when Chromium is available:

```bash
chromium-browser --headless --no-sandbox --use-gl=swiftshader \
  --enable-unsafe-swiftshader --virtual-time-budget=5000 \
  --dump-dom http://127.0.0.1:<PORT>
```

Assert that the loading state finishes and a `<canvas>` is created. Headless GPU crashes are not proof that the page fails on Android Chrome; include a lightweight CSS/static fallback and a loading timeout so unsupported WebGL or CDN failures never leave a blank spinner.

Note: Use `127.0.0.1` instead of `localhost` — Termux may resolve localhost differently in some cases.

If the server doesn't respond, check the process log:
```bash
process(action="log", session_id="<proc_id>", limit=30)
```

## Supporting references

- `references/the user-project-servers.md` — stable port/project mapping and ZelineGuide script-generated workflow.
- `references/mobile-liquid-glass-music-player.md` — static liquid-glass tuning, pale modal themes, CSS cascade checks, cover replacement, and verification for mobile music players.
- `references/mobile-liquid-glass-iteration.md` — static Apple-like glass treatment, full-bleed app icons with count badges, iMessage-style message modals, and iterative visual verification.
- `references/iterative-mobile-ui-polish.md` — bounded visual interpolation, static glass edge treatment, CSS cascade checks, synchronized counts, and multi-bubble message rendering.

## Iterative visual UI refinement

For repeated mobile UI adjustments, treat each user correction as a visual specification and make the smallest targeted CSS/HTML patch rather than rewriting the page. Preserve the existing interaction model and verify after every patch. For liquid-glass interfaces, prefer static layered treatment over animated sheen unless the user explicitly asks for motion: combine a translucent gradient, `backdrop-filter`, `inset` light/dark edge highlights, and restrained internal shadows; avoid stacking large outer shadows on nested glass controls because it makes them look like floating glass-on-glass. Keep related controls aligned to the same container width and use `margin-inline:auto` plus equal horizontal gutters to prevent off-center cards. When a user specifies exact visual placement, encode it literally (for example, header name centered, divider, then counter, then message bubble).

For image-based UI assets, copy the supplied image into the project asset directory with a stable descriptive filename, add a cache-busting query string, disable drag/long-press on decorative images, and verify both the page and asset return HTTP 200. If vision inspection fails, still inspect dimensions/mode with a local image tool and proceed without inventing visual details.

## Pitfalls

- **Shell `&` creates ghost processes Zeline can't kill**: If you accidentally used `&` in a background command, `process(action="kill")` only kills the parent shell — the child server lives on. To kill a ghost server, use `pkill -f "python3 -m http.server <PORT>"` or `kill $(lsof -ti :<PORT> 2>/dev/null)` in a terminal command, then verify the port is free with `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:<PORT> || echo "free"`.
- **`process(kill)` does not kill `nohup` or `&` children**: Zeline tracks only the top-level PID of the background session. Any process spawned with `&`, `nohup`, `disown`, or `setsid` becomes a child of init and survives the Zeline kill. Always use `pkill -f` or `kill` with the right PID to clean up.
- **Process alive but port dead = wedged accept loop, not EADDRINUSE**: Recurring on `~/SampleProject` `no_cache_server.py` (port 8082) — happened two nights in a row. Symptom: `ps aux | grep no_cache` shows the process running, but `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8082/` returns `HTTP 000` and the port is absent from `/proc/net/tcp` (or the old server bound the port but its accept loop died). The process is wedged, not bound, not crashed. Fix: `kill -9 <pid>` (plain `kill` may leave it wedged), restart via `terminal(background=true)`, then verify `HTTP 200` + a distinctive content marker (`curl -s ... | grep -c 'music-head'`). Never trust `ps` alone — a live PID does not mean the port is served; always curl. Check both `ps` and the port before declaring "server jalan".
- **Foreground commands with `nohup ... &` are rejected by the Zeline terminal tool**: Shell-level background wrappers (`nohup`, `disown`, `setsid`, trailing `&`) in a foreground `terminal()` call fail with exit -1 and an explicit error telling you to use `background=true`. Split the work: kill/cleanup in a foreground call, then start the server with `terminal(background=true)` as a plain foreground command (no wrapper), then verify readiness in a separate call.
- **`mkdocs serve` overwrites custom `site/` content**: If the project has Python scripts that write directly to `site/` (e.g., `expand_docs_v32.py`, `translate_id_v32.py`, `add_*.py` in ZelineGuide), **do NOT use `mkdocs serve`** — it cleans the `site/` directory and rebuilds from `docs/`, destroying all custom content. Use a static file server (Python http.server or project-specific server like `no_cache_server.py`) instead.
- **Stale dist/ serving old content**: After `astro build` or `mkdocs build`, the preview server should pick up the new files automatically. If it doesn't, kill and restart the process.
- **kill %1 doesn't work on Zeline-managed bg processes**: Use `process(action="kill", session_id="...")` instead of shell job control.
- **localhost vs 127.0.0.1**: On Termux, `localhost` may resolve differently than `127.0.0.1`. Always verify with `127.0.0.1` if `localhost` fails.
- **Do not assume `/tmp` exists on Termux during verification**: A server can return HTTP 200 while `curl -o /tmp/check.html` exits with code 23 because Android/Termux does not use the conventional Linux `/tmp` path. Prefer inspecting the response directly in memory (for example, Python `urllib.request`) or write under `${TMPDIR:-$PREFIX/tmp}`. Report transport status separately from artifact-inspection status so a local file-write failure is not misdiagnosed as a server failure.
- **Verify inline JS after editing static HTML**: after patching `<script>` blocks, extract them and run `node --check` (write the temp file under `~`, not `/tmp`): `python3 -c "import re;s=open('index.html',encoding='utf-8').read();open('check.js','w').write('\n'.join(re.findall(r'<script>(.*?)</script>',s,re.S)))"` then `node --check check.js && rm check.js`. A syntax error in inline JS = blank page on the device while HTML still serves HTTP 200 — curl alone won't catch it.
- **Quote discipline when patching JS string arrays**: strings containing apostrophes (`I'm`, `It's`, `You're`) MUST use double quotes. Single-quoted `'It's you'` silently truncates the string and breaks the whole script (real incident this session — patch applied, page dead, node --check caught it). After any patch touching JS, run node --check.
- **Synchronize hardcoded counters when editing playlist/array data**: removing an item from a JS array (e.g. `MUSIC_SONGS`) requires also updating hardcoded HTML counters — notification badge (`4`→`3`) and static labels like `1 of 4`→`1 of 3`. JS re-renders the in-app counter only after it runs; the initial HTML shows the stale value.
- **Permission denied for netstat/ss**: Termux doesn't allow raw socket access. Use `curl` to check if a port is listening instead.
- **Multiple servers on the same port**: If a port is already in use, the new server will fail silently. Check if the port is responding first.
- **NEVER pkill an unrelated service to free a port.** When asked to "run server on port N" and startup fails with `EADDRINUSE`/`Address already in use`, identify EXACTLY which process owns **port N** before killing anything. Do NOT `pkill -f` a broad pattern that also matches other running services. Example failure: asked to run on `8082` (already held by a static `http.server`), running `pkill -f custom-server.js` instead killed a running **9Router** on port `20128` — a completely unrelated service — which broke model routing. Correct sequence: (1) `ps aux | grep <PORT>` to see who owns the target port, (2) if it's the SAME app that should be restarted, kill only that PID, (3) if it's a different service, either pick a free port or ask — never kill it. `ss`/`netstat` need root on Termux and fail; rely on `ps aux | grep <PORT>` + `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:<PORT>` instead.
- **Answer the literal request; don't silently repurpose the port.** "run server 8082" means start whatever was last running on 8082 (here: the `~/SampleProject` static site), not repoint another app there. Resolve the target from conversation context first (step 0), then serve.
- **cloudflared on Termux needs `--edge-ip-version 4`**: Termux has no IPv6 route, so QUIC-over-IPv6 links throw recurring `ERR ... network is unreachable` (UDP to 2606:4700:*) and may flap connections. Start with `cloudflared tunnel --edge-ip-version 4 --config ~/.cloudflared/config.yml run <tunnel-id>`. Verify the public domain with `curl -s -o /dev/null -w "%{http_code}" https://example.domain/` (expect 200; 530 = tunnel down after a Termux restart). The MyStore backend on 8899 is gated behind this tunnel.

---

## Lampiran: `references/the user-project-servers.md`

# the user's Local Dev Servers

Three local servers commonly run together on Termux:

| Port | Project | Directory | Type | Server Command |
|------|---------|-----------|------|----------------|
| 8081 | UserDocs | `~/user-site` | Astro | `npx astro preview --port 8081 --host 0.0.0.0` |
| 8089 | ZelineGuide | `~/docs-site` | MkDocs (custom) | `python3 no_cache_server.py` (serves static `site/` — **do NOT use `mkdocs serve`**, it overwrites custom content) |
| 8092 | CommunitySite | `~/the community-site` | Static HTML | `python3 -m http.server 8092` |

## Checking for uncommitted changes

Before serving Astro/MkDocs projects, check git:

```bash
cd ~/project && git diff --stat HEAD
```

If there are uncommitted changes (780+ insertions, 60+ deletions typical for a redesign), rebuild:

- **Astro**: `npx astro build`
- **MkDocs**: `source venv/bin/activate && mkdocs build`

## ZelineGuide Custom Scripts Workflow

ZelineGuide (`~/docs-site`) has custom Python scripts that write directly to `site/`:

| Script | Purpose |
|--------|---------|
| `expand_docs_v32.py` | Generates expanded EN documentation pages into `site/` |
| `translate_id_v32.py` | Translates EN pages to ID in `site/id/` |
| `add_authoritative_v33.py` | Appends authoritative content sections |
| `add_parity_v34.py` | Appends parity/feature content sections |

**Workflow:**
1. Run each script in order: `python3 expand_docs_v32.py && python3 translate_id_v32.py && python3 add_authoritative_v33.py && python3 add_parity_v34.py`
2. Serve with `python3 no_cache_server.py` (not `mkdocs serve`!)

**CRITICAL:** `mkdocs serve` will **clean** `site/` and rebuild from `docs/`, destroying all script-generated content. Always use the static server.

## Kill ghost processes (stuck on port)

If a previous server is still holding the port (common when `&` was used inside a Zeline `terminal(background=true)` command, the `process(kill)` only kills the parent shell — the child process survives):

```bash
# Find and kill any process on the port
pkill -f "python3 -m http.server <PORT>" 2>/dev/null
# Or use lsof if available
kill $(lsof -ti :<PORT> 2>/dev/null) 2>/dev/null
# Verify it's free
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:<PORT> 2>/dev/null
echo " → 000 = free, 200 = still alive"
```

After killing, always verify with curl before starting a new server.

## Rebuild + serve workflow

1. Kill old server process if port is stuck (see ghost-kill section above)
2. For Astro: `npx astro build`; for ZelineGuide: run the custom scripts (see above)
3. Start server in background (no `&` — let Zeline manage the PID directly)
4. Verify with `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:<PORT>`

## Verification

Always use `127.0.0.1` not `localhost` — Termux may route `localhost` differently.


---

## Lampiran: `references/interactive-3d-preview.md`

# Interactive 3D Preview on Termux

Use this pattern for lightweight Three.js landing/coming-soon pages served as static HTML.

## Performance budget for Android

- Cap device pixel ratio around `1.25–1.5` on mobile and below `2` on desktop.
- Reduce particle/star counts on narrow screens rather than shipping one maximal scene.
- Prefer one `Points` object with buffer attributes over hundreds of independent meshes.
- Use `depthWrite: false` and additive blending for glow/particle layers.
- Keep geometry segments modest; visual density should come from particles, gradients, and composition.
- Honor `prefers-reduced-motion` and stop unnecessary motion when requested.

## Interaction defaults

- Touch drag rotates; pinch zooms; disable pan.
- Keep damping enabled and rotation speed restrained.
- Pause auto-rotation on interaction and resume after roughly 3–5 seconds.
- Preserve text as a DOM overlay so it remains sharp and readable independently of WebGL.

## Resilient loading

A CDN import or WebGL initialization can fail independently of the static page. Avoid permanent black screens:

1. Put a visually coherent CSS gradient/star-field fallback behind the transparent canvas.
2. Create the renderer with `alpha: true` and transparent clear color.
3. Remove loading state after first render, but also add a short timeout fallback.
4. Keep the headline and essential status visible even without WebGL.

## Verification ladder

1. HTTP endpoint returns 200.
2. Served `<title>` or distinctive marker matches the intended version.
3. Extract module JS and run `node --check` for syntax.
4. Run Chromium `--dump-dom` with SwiftShader and a virtual-time budget; assert loading is done and `<canvas>` exists.
5. Capture a mobile screenshot when possible. Treat Termux headless GPU-process crashes as a limitation of the preview environment, not automatic proof of Android Chrome failure.

Example smoke command:

```bash
chromium-browser --headless --no-sandbox \
  --use-gl=swiftshader --enable-unsafe-swiftshader \
  --virtual-time-budget=5000 --dump-dom \
  http://127.0.0.1:8092/
```

## Version safety during iterative UI work

Before replacing a version the user may request again, save a named snapshot such as `index.v1-3d.backup.html`. Do not use an ambiguous `index.html.bak` if it predates the requested design state. When the user says “balikin versi 1,” restore the named snapshot and verify its title/content marker before reporting success.



---

## Lampiran: `references/iterative-mobile-ui-polish.md`

# Iterative mobile UI polish for static gift/music pages

Use this reference when refining a compact mobile-first static page through repeated visual corrections.

## Preserve the user's visual intent

- Treat phrases such as “sedikit,” “setengah dari tadi,” and “selaraskan” as bounded interpolation requests. Adjust only the relevant opacity/brightness values; do not redesign adjacent elements.
- When the user asks for Apple-like liquid glass but explicitly rejects animation, use **static optical depth**: a bright top/left inner edge, a slightly darker bottom/right inner edge, restrained translucency, and no moving sheen.
- Avoid stacked-glass artifacts. Controls inside a glass panel should not also carry a strong external drop shadow. Prefer one subtle border plus inset highlights/shadows.
- For paired controls around a centered label, use a three-column grid with equal fixed outer columns and a flexible center column. This keeps the label mathematically centered even when one control is disabled.
- Full-bleed app icons should use `inset:0`, `width/height:100%`, and `border-radius:inherit`; do not leave an accidental inner gutter between the image and glass overlay.
- Notification badges should inherit the app icon’s visual rotation unless the user explicitly asks them to remain upright. Keep count badges synchronized with the actual item count.

## CSS cascade verification

After changing icon colors or state styling, search for every matching selector later in the stylesheet. A later generic rule can silently override an earlier state-specific fix.

Example: play/pause colors require changing both the base triangle and the playing-state bars at their final declarations, not merely adding an earlier override.

## Message/chat UI

- For iMessage-like incoming messages, use left-aligned gray bubbles; outgoing messages use right-aligned blue bubbles.
- If one logical message must contain several bubbles, model it as an array and render one element per item rather than inserting line breaks into one bubble.
- Keep message totals, counters, navigation bounds, dots, and icon badges derived from the same array length. Removing a message must update all of them.
- If each bubble must remain one line, use `white-space:nowrap` with a responsive font size and compact horizontal padding. Verify the longest requested line still fits the target mobile width.
- Header order for this user’s compact message UI: centered sender name, horizontal divider, message counter below the divider, then bubbles.

## Verification loop

1. Confirm the live endpoint returns HTTP 200.
2. Confirm the exact new selector/data marker is present in the served HTML.
3. Check for stale or later duplicate selectors that override the intended rule.
4. Confirm asset URLs return HTTP 200 and increment cache-busting query strings after replacements.
5. For layout work, capture a real mobile-size screenshot when possible; source-marker checks alone do not prove visual alignment.



---

## Lampiran: `references/liquid-glass-media-wallpapers.md`

# Song-specific wallpaper treatment for liquid-glass media cards

Use this pattern when a static music player needs a different wallpaper treatment for one song while retaining one shared player component.

## Implementation pattern

1. Copy the supplied artwork into the project assets directory with a stable descriptive name. Inspect its dimensions and mode first.
2. Keep one player DOM. In the song-switching function, toggle a deterministic state class such as `is-song1` or, preferably, a semantic class derived from the song ID.
3. Render the wallpaper on a dedicated pseudo-element behind the glass content:
   - player: `position: relative; isolation: isolate; overflow: hidden`
   - wallpaper layer: `::after`, negative z-index, `background: center/cover no-repeat`
   - glass highlight: separate `::before` layer
4. Scope wallpaper and glass tint to the active song class. Scope typography and controls either to that song or to the shared player depending on the user's requested consistency. If the user later asks every song to use the same light theme, promote title, artist, lyrics, timestamps, close/skip/play icons, progress track, and thumb rules from `.is-song1` to `.music-player` rather than duplicating selectors.
5. Add a cache-busting query to every newly supplied wallpaper.

## Legibility tuning

For a light liquid-glass inversion over a colorful wallpaper:

- Preserve the image instead of turning the card into an opaque white panel.
- Use a translucent white gradient over the image, moderate desaturation, and near-black text.
- Tune together: image-layer opacity, white-overlay alpha, player background alpha, saturation, and brightness.
- Keep controls consistent with the inversion: title, artist, lyrics, timestamps, close/skip/play icons, progress fill, and thumb all need dark variants.
- A white blurred backdrop changes the contrast context outside the card too. Restyle message-modal close, Previous/Next, disabled states, and page dots with translucent white fills plus muted dark/maroon borders and text; white-on-white glass controls become effectively invisible.
- Tune the inner wallpaper overlay and outer modal blur as one visual system. If the backdrop is lightened, raise the inner white wash modestly rather than jumping to opaque white; avoid a dark card floating over a bright blur or vice versa.
- Verify bold lyric sections remain visibly distinct after changing the foreground palette.

## User-specific iteration rule

When the user requests `sedikit`, `setengah dari tadi`, or asks to move between two recent visual states, interpolate the actual numeric CSS values between those states. Do not jump back to either endpoint. Change only the variables responsible for the requested effect and preserve layout, typography, and behavior.

Example midpoint calculation between overlay alphas `.34/.50` and `.68/.82`:

```css
/* Midpoint, rounded to two decimals */
background-image: linear-gradient(
  180deg,
  rgba(255,255,255,.51),
  rgba(255,255,255,.66)
), url('assets/wallpaper.png?v=1');
```

## Static optical-glass treatment (not animated)

When the user asks for glass that feels `hidup`, first distinguish **optical depth** from motion. His preferred interpretation is usually static glass with believable edge lighting, not animated sheen or 3D movement.

- Do not add moving gradients, sweeping highlights, floating cards, or live 3D unless he explicitly asks for animation.
- Build depth using fixed layers: a bright top/left rim, a darker bottom/right rim, restrained inset shadows, a soft radial highlight near one corner, and a subtle opposing shadow.
- Keep the center transparent enough to preserve the wallpaper/backdrop; concentrate contrast at the perimeter so the component reads as real glass rather than a white card.
- Apply the same optical language to related controls (close, Previous/Next, play/skip), while preserving their shape and legibility.
- If an animated version was added due to ambiguity and the user corrects it, remove both the animation declarations and now-unused keyframes/reduced-motion rules; do not merely pause the animation.

Illustrative static layering:

```css
.glass {
  background:
    radial-gradient(120% 75% at 12% 0%, rgba(255,255,255,.5), transparent 46%),
    radial-gradient(90% 70% at 100% 100%, rgba(70,42,59,.12), transparent 54%);
  box-shadow:
    inset 1px 1px 0 rgba(255,255,255,.72),
    inset -1px -1px 0 rgba(64,38,54,.12),
    0 16px 36px rgba(42,20,31,.18);
}
```

## Asset protection expectations

CSS can suppress casual saving gestures but cannot make a browser-delivered image impossible to download. For the user's static previews, apply `user-select:none`, `-webkit-user-drag:none`, `-webkit-touch-callout:none`, and disable image pointer interaction. Describe this accurately as blocking casual long-press/drag, not absolute download prevention.

## Verification

1. Confirm the page and wallpaper both return HTTP 200.
2. Confirm the active song receives the expected state class and other songs do not.
3. Open the player at a mobile viewport and inspect the actual card, not only the landing screen.
4. Check text/control contrast, wallpaper visibility, clipping, and that switching songs restores the default theme.
5. During iterative UI work, keep the no-cache server running and verify the exact live URL.



---

## Lampiran: `references/mobile-liquid-glass-iteration.md`

# Mobile liquid-glass UI iteration patterns

Use this reference for small static mobile experiences with glass modals, app-style icons, music players, and iterative visual corrections.

## Interpret “alive” glass correctly

“Liquid glass that feels alive” does not always mean animation. Confirm from context whether the user wants motion or a static optical treatment. For the user, prefer **static glass depth** unless motion is explicitly requested:

- bright top/left edge highlight;
- slightly darker bottom/right inner edge;
- restrained translucent fill;
- backdrop blur and mild saturation;
- no sweeping sheen, moving gradient, floating 3D transform, or oversized exterior shadow.

A convincing static recipe combines a subtle radial/linear highlight with paired inset shadows. Avoid stacking several strong radial fills and an outer shadow: it makes buttons look like multiple glass layers sitting on top of each other.

## Keep related glass surfaces visually consistent

When a modal card and its blurred backdrop both use a white treatment, tune them together. A common failure is a bright card over a dim or colored blur, making the card look pasted on.

Recommended sequence:

1. Set the modal backdrop tint and `backdrop-filter` brightness.
2. Tune the card fill/overlay to approximately the same white family.
3. Increase or reduce the wallpaper’s white veil in small increments only.
4. Keep enough wallpaper contrast that the image remains identifiable.
5. Reduce exterior card shadow if the transition between backdrop and card feels abrupt.

For multiple sibling modals, apply the same backdrop treatment globally unless the user explicitly wants one state to differ.

## CSS cascade checks for icon state

Music controls often have duplicate selectors in different parts of a stylesheet. A later base rule can silently override an earlier state-specific fix. Before reporting success:

1. Search every occurrence of the selector (for example `.music-toggle span`).
2. Inspect source order and specificity.
3. Change the final/base declaration, not only an earlier override.
4. Verify both states: play icon and pause icon.
5. Search for the old color value and confirm it is absent from relevant declarations.

This specifically prevents the bug where play is black but the two pause bars remain white while audio is playing.

## App-style icons replacing envelope art

When an envelope/button becomes an app icon:

- Keep its original position and click handler.
- Use a square aspect ratio with a consistent rounded radius.
- Make the supplied image full bleed (`inset:0; width:100%; height:100%`), not padded inside a second glass frame.
- Put the static glass highlight directly over the image with a pseudo-element.
- Disable drag, selection, and touch callout on supplied images.
- Add an iOS-style notification badge outside the top-right corner.
- The badge should share the icon’s rotation naturally. Do not counter-rotate it unless the user explicitly wants the badge upright relative to the viewport.
- Badge value should reflect the real item count (for example, 7 messages or 4 songs).

## iMessage-style message modal

For a seven-page message experience redesigned as iMessage:

- use a cool white/blue blurred backdrop;
- use a rounded white chat surface rather than paper texture;
- show a centered sender name with no profile photo when requested;
- place `Message N of M` directly below the header divider, then place the message bubble below the counter;
- when the user says the message is from the named sender, render it as a left-aligned incoming iMessage-style gray bubble (`#E5E5EA`) with dark text and a small left tail; do not assume blue/right outgoing bubbles;
- retain page navigation and map its counter to `Message N of M`;
- remove decorative paper folds, floral line art, and parchment textures;
- keep Previous/Next and close controls legible against the light blur.

## Visual verification

An HTTP 200 and a source marker prove delivery, not appearance. For modal or interaction states:

- verify the supplied asset itself returns 200;
- verify the HTML contains the intended class/state mapping;
- inspect all relevant duplicate CSS selectors;
- when possible, open the actual modal/state in a browser before taking a screenshot;
- treat the user’s device screenshot as authoritative for subtle opacity, shadow, and contrast judgments.

Use cache-busting query strings whenever replacing an asset at the same path.


---

## Lampiran: `references/mobile-liquid-glass-music-player.md`

# Mobile liquid-glass music player iteration

Use this note for static, mobile-first music-player modals with album covers, lyrics, and an Android preview loop.

## Visual construction

- Treat “Apple-like liquid glass” as **static optical depth by default**, not moving shine: translucent fill, backdrop blur, a thin bright edge on the top/left, and a faint darker inner edge on the bottom/right.
- Avoid combining a strong radial fill, multiple inset shadows, and an outer shadow on small controls. It reads as stacked glass. For close/previous/next controls, prefer one translucent linear fill plus two subtle inset 1px edges and no external shadow.
- When a card uses a wallpaper while the modal backdrop is pale, tune them together. Raise the wallpaper’s white overlay in small increments (roughly 0.03–0.05 alpha), then compare card and surrounding blur so neither looks detached.
- Keep wallpaper visible but secondary: a pale overlay, slightly reduced saturation, and modest brightness are usually enough. Do not solve readability by washing the image out completely.
- If the user asks for “alive” glass but clarifies it is not live/3D, remove all keyframe sheen. Use asymmetric static highlights and inner edge shadows instead.

## Theme consistency

- When the modal backdrop changes from dark to white/pale blur, update every dependent foreground color together: title, artist, lyric, counter, times, close, skip, play/pause, progress track, thumb, and pagination dots.
- Do not scope text/icon color only to the first song if the user wants all songs to share the pale theme.
- Keep disabled navigation legible through reduced opacity, not white-on-white styling.

## CSS cascade pitfall

A later base selector can override an earlier themed selector when specificity ties. This happened with play/pause icons: an earlier black rule was overwritten later by `border-left: ... #fff` and `border-right: ... #fff`.

After changing a stateful CSS icon:

1. Search every occurrence of the selector/property.
2. Edit the final/base declaration or increase specificity intentionally.
3. Verify both states separately (play and pause), including both pause bars.
4. Confirm the old color literal no longer exists in the relevant declarations.

## Cover replacement and download deterrence

- Copy each supplied square asset to a stable descriptive filename and increment its query version (`?v=2`, `?v=3`) after replacement.
- Use `-webkit-user-drag:none`, `-webkit-touch-callout:none`, `user-select:none`, and disable image pointer interaction to deter long-press/drag saving on Android.
- This is UI deterrence, not true access control: any image served to a browser can still be fetched by a technically capable user. Do not claim absolute download prevention.

## Verification loop

- Verify the page and each changed asset return HTTP 200.
- Check distinctive source markers for the new CSS/data and updated cache version.
- For CSS cascade bugs, search the full file rather than assuming the newly inserted rule wins.
- Keep the existing no-cache server running during iterative Android testing.



---

## Lampiran: `references/supplied-image-ui-preview.md`

# Supplied image assets in static UI previews

Use this workflow when the user provides an image and asks it to replace existing decoration in a static page:

1. Inspect image dimensions, color mode, and whether the background is baked in.
2. Copy it into the project's asset directory with a stable descriptive filename.
3. If a near-white studio background must blend into the page, create a transparent derivative using a conservative whiteness/chroma threshold so pale subject details remain intact.
4. Replace or hide the previous CSS/SVG placeholder. Never render both the old decoration and supplied asset.
5. Let the supplied image drive the nearby palette, spacing, and focal hierarchy. Keep text outside the image's occupied region at mobile and desktop sizes.
6. Add a cache-busting query string when iterating.
7. Verify the page and asset both return HTTP 200. Produce a real mobile screenshot, confirm it is nonblank, and check that the subject and text are visible without overlap.

User-specific preference: the user expects the exact supplied visual asset to replace generic decoration, with polished mobile-first integration and verification on the existing requested port.

## Supplied image as a state-specific liquid-glass background

When an image is used as wallpaper inside a player/card rather than as a normal `<img>`:

1. Copy the exact asset into the project and render it on a dedicated pseudo-element behind the card content (`isolation:isolate`, negative z-index, `overflow:hidden`). Do not replace the card's glass treatment with an opaque image.
2. Activate it through an explicit state class such as `.is-song1`, applied by the same renderer that updates title, audio, cover, and lyrics. Toggle it off when switching items so the wallpaper never leaks into other songs.
3. Build legibility with two coordinated layers: a restrained gradient over the wallpaper, plus a matching tint/blur/brightness on the modal backdrop. Tune them together; changing only one makes the card look detached or "jomplang".
4. For the user's light variant, keep the wallpaper softly visible rather than near-white. A practical starting range is a white gradient around `.50–.70`, wallpaper opacity around `.75–.82`, restrained saturation, and near-black text around `#171216`. The real device decides the final values.
5. If the outer modal changes from dark blur to white blur, update every dependent contrast token: title, artist, kicker/count, lyrics, timestamps, close, previous/next, play/pause, skip controls, range track/thumb, and active/inactive indicators.
6. On a white blurred letter/message modal, keep glass controls visible with translucent white fill, a subtle dark-tinted border/shadow, and muted maroon/dark text. Pure-white controls and dots disappear against white blur.
7. Preserve requested lyric/message line breaks exactly with `<br>` or newline-aware rendering. Treat bold boundaries separately from line breaks; do not reflow wording unless requested.
8. Mobile anti-download is only deterrence: disable selection, image dragging, touch callout, pointer interaction, and context menus where appropriate, but never claim a publicly served asset is impossible to download.

### Verification

- Verify the page and wallpaper asset both return HTTP 200.
- Verify the state class is applied by the item renderer and removed on item switch.
- Test the actual hidden modal/player state, not merely the landing page. Automate opening the relevant envelope and selecting each item, or temporarily force the state for a local audit screenshot.
- Check every light-background control for contrast, including disabled navigation and inactive dots.
- Treat the user's Android display as authoritative for final opacity and brightness tuning.



---

## Lampiran: `references/SampleProject-music-player.md`

# SampleProject gift page — music player editing (`~/SampleProject/index.html`, port 8082)

The "For Puji" gift page has 3 envelopes (`.flower-letter`): #1 = 7-page letter, #2 = empty, #3 = liquid-glass music player. Songs live in the `MUSIC_SONGS` JS array near the bottom of `index.html`. Serve with `python3 no_cache_server.py` (never `mkdocs`/plain http.server — see main SKILL pitfalls).

## Adding a song

1. Copy assets into `assets/` with stable descriptive names:
   - cover → `assets/<slug>-cover.jpg`
   - audio → `assets/<slug>.mp3`
2. Get real duration for the `fallback` field:
   `ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 assets/<slug>.mp3`
3. Append an object to `MUSIC_SONGS`:
   ```js
   {title:'…',artist:'…',cover:'assets/<slug>-cover.jpg?v=1',src:'assets/<slug>.mp3?v=1',fallback:39.73,lyric:`…`}
   ```
   The player auto-updates the "N of M" counter from `MUSIC_SONGS.length` (via `applySong`), BUT the static `<span class="music-count">1 of N</span>` in the HTML must be bumped by hand to match the new total.
4. If audio isn't ready yet, set `src:''` and build the card first; drop the mp3 in later and set `src` + bump `?v=`.
5. Verify: HTTP 200 on `/`, on the cover jpg, and on the mp3; grep the served HTML for the new title + `N of N`.

## Lyric placement — VERBATIM, this is what the user iterates on most

- the user pastes lyrics with EXACT line breaks he wants. Map his text literally:
  - single newline → `<br>`
  - blank line → `<br><br>`
  - the emphasized couplet he marks → wrap in `<b>…</b>`
- Do NOT re-flow, re-punctuate, or "tidy" line breaks. If he splits `But baby, I` onto its own line, keep it on its own line. If he wants `'Cause you're amazing just the way you are,` to sit on the SAME line as the following bold phrase, use a trailing space before `<b>` (no `<br>`); if on its own line, use `<br>` before `<b>`.
- He frequently asks to tweak exactly where a line wraps and where bold starts/ends. Treat each paste as the new source of truth and re-diff only the `lyric` template string. When he says "balikin kaya sebelumnya" revert to the immediately prior placement.

## Editing the paper letter (envelope #1)

Letter pages live in `const messages=[{title,copy}]`. `copy` is plain text rendered with
CSS `white-space:pre-line`, so use literal `\n` for line breaks and `\n\n` for a blank
line (NOT `<br>` — that's only for the music-player `lyric` field). Reproduce the user's
wording verbatim, same as lyrics.

## Protect cover images from download/save

the user asked that song covers not be downloadable/saveable. Applied on `.music-cover img`:
```css
-webkit-user-select:none;user-select:none;-webkit-user-drag:none;
-webkit-touch-callout:none;pointer-events:none
```
`touch-callout` kills the Android/iOS long-press "save image" menu; `user-drag` +
`pointer-events` block drag-to-save. Matches the user's general "blok long-press" preference.

## Verify served content (no /tmp on Termux)

```python
import urllib.request
with urllib.request.urlopen('http://127.0.0.1:8082/',timeout=6) as r:
    t=r.read().decode('utf-8','replace')
print(r.status, '<distinctive lyric substring>' in t)
```



---

## Lampiran: `references/user-supplied-visual-assets.md`

# User-supplied visual asset iteration

Use this checklist when replacing decorative assets in an already-running mobile web page.

## Workflow

1. Inspect the live HTML/CSS and the supplied asset dimensions/background before editing.
2. Preserve the original asset. Save transparent and cropped derivatives as separate files so iterations remain reversible.
3. Explicitly hide or remove the old visual system when adding the replacement; otherwise old CSS flowers/decorations can remain visible underneath.
4. Keep a bottom-anchored subject subordinate to the copy. Start around 80–92vw on mobile, then validate with a real screenshot.
5. Use only 3–4 falling elements, with staggered timing and varied sizes, so motion does not cover the message.
6. A bitmap rotated with `rotateY()` still looks like paper. To suggest volume, assemble separate rounded petals with varied `translateZ`, `rotateX`, radial gradients, highlights, inner shadows, and a raised center. Rotate the assembled flower.
7. Avoid large tilted circles/rings behind centered copy unless explicitly requested; they can read as a plate. Prefer vertical depth glows, subtle light shafts, or restrained parallax.
8. Add navigation cues in the target language as a separate low-z visual layer and reserve room for them at the viewport edge.

## Verification

- Check the page and every new asset returns HTTP 200.
- Capture a mobile screenshot at the target viewport.
- Use a virtual-time delay long enough to capture a mid-animation frame, not only the initial state.
- Verify no blank render, overflow, text occlusion, or stale decorative elements.

---

## Catatan adaptasi Zeline
- Tool berikut TIDAK tersedia di Zeline, abaikan instruksinya: process(.

