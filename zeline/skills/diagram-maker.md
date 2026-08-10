# Diagram Maker

> Create SVG/HTML or Excalidraw diagrams for concepts, architecture, flows, and whiteboards.

Create diagrams as artifacts, not prose. Choose one output mode:

- `clean-svg`: educational concepts, physical systems, processes, lifecycle, simple data flow.
- `architecture-svg`: software/cloud/infra topology, services, databases, queues, trust zones.
- `excalidraw`: editable hand-drawn whiteboard, flowchart, sequence, architecture sketch.

Routing

- User wants editable/collaborative: choose Excalidraw.
- User wants polished standalone browser output: choose SVG/HTML.
- Software architecture with infra components: choose architecture SVG.
- Science, product, process, concept map, physical object: choose clean SVG.
- Unsure: ask one short question only if output format matters; otherwise choose clean SVG.

Workflow

1. Extract nodes, groups, labels, and directed relationships.
2. Pick layout first: left-to-right, top-down, hub-spoke, swimlanes, layered stack, sequence.
3. Keep labels short. Prefer 5-9 main elements over dense diagrams.
4. Generate the file at the requested path, or `./diagram.html` / `./diagram.excalidraw`.
5. Verify syntax by opening/parsing when feasible.

SVG/HTML rules

- Single standalone `.html` file with inline CSS and inline SVG.
- No external fonts, JS, images, gradients, glows, decorative blobs, or remote assets.
- Use semantic colors, not rainbow sequences: neutral, input, process, storage, external, risk.
- Draw connectors before nodes so arrows sit behind boxes.
- Every connector path has `fill="none"` and a marker arrow when directed.
- Leave 24px text padding inside boxes; do not let text touch borders.
- Legend only when symbols/colors are not obvious.

SVG template

Use `references/svg-template.md` as the wrapper and replace `<!-- SVG -->`.

Excalidraw rules

- Save `.excalidraw` JSON with `type`, `version`, `source`, `elements`, and `appState`.
- Use bound text for shape labels. Do not use a nonstandard `label` property.
- Keep bound text immediately after its container in the elements array.
- Minimum labeled shape: 120x60. Minimum body text: 16px.
- Use roughness `1`, `fontFamily: 1`, and simple fills.

For exact Excalidraw element snippets, read `references/excalidraw-patterns.md`.


---

## Lampiran: `references/excalidraw-patterns.md`

# Excalidraw Patterns

Envelope:

```json
{
  "type": "excalidraw",
  "version": 2,
  "source": "zeline/diagram-maker",
  "elements": [],
  "appState": { "viewBackgroundColor": "#ffffff" }
}
```

Labeled rounded rectangle:

```json
{
  "type": "rectangle",
  "id": "svc",
  "x": 100,
  "y": 100,
  "width": 180,
  "height": 72,
  "roundness": { "type": 3 },
  "backgroundColor": "#a5d8ff",
  "fillStyle": "solid",
  "strokeWidth": 2,
  "roughness": 1,
  "opacity": 100,
  "boundElements": [{ "id": "svc_text", "type": "text" }]
}
```

Bound text:

```json
{
  "type": "text",
  "id": "svc_text",
  "x": 112,
  "y": 124,
  "width": 156,
  "height": 24,
  "text": "API service",
  "originalText": "API service",
  "fontSize": 20,
  "fontFamily": 1,
  "strokeColor": "#1e1e1e",
  "textAlign": "center",
  "verticalAlign": "middle",
  "containerId": "svc",
  "autoResize": true
}
```

Bound arrow:

```json
{
  "type": "arrow",
  "id": "a1",
  "x": 280,
  "y": 136,
  "width": 140,
  "height": 0,
  "points": [
    [0, 0],
    [140, 0]
  ],
  "endArrowhead": "arrow",
  "startBinding": { "elementId": "svc", "fixedPoint": [1, 0.5] },
  "endBinding": { "elementId": "db", "fixedPoint": [0, 0.5] }
}
```

Palette:

- Primary/input: `#a5d8ff`
- Process: `#d0bfff`
- Success/output: `#b2f2bb`
- Storage/data: `#c3fae8`
- External/warning: `#ffd8a8`
- Error/risk: `#ffc9c9`
- Note/decision: `#fff3bf`



---

## Lampiran: `references/svg-template.md`

# SVG HTML Template

Copy this to a `.html` file and replace `<!-- SVG -->`.

```html
<!doctype html>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Diagram</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #f8fafc;
    --fg: #172033;
    --muted: #5b6475;
    --line: #64748b;
    --neutral: #e2e8f0;
    --input: #bfdbfe;
    --process: #c7d2fe;
    --storage: #99f6e4;
    --external: #fde68a;
    --risk: #fecaca;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0f172a;
      --fg: #e5e7eb;
      --muted: #a3adbd;
      --line: #94a3b8;
      --neutral: #334155;
      --input: #1d4ed8;
      --process: #4338ca;
      --storage: #0f766e;
      --external: #92400e;
      --risk: #991b1b;
    }
  }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--fg);
    font:
      14px/1.4 ui-sans-serif,
      system-ui,
      -apple-system,
      BlinkMacSystemFont,
      "Segoe UI",
      sans-serif;
  }
  main {
    max-width: 980px;
    margin: 32px auto;
    padding: 0 20px;
  }
  svg {
    width: 100%;
    height: auto;
    display: block;
  }
  .title {
    font-size: 20px;
    font-weight: 650;
    fill: var(--fg);
  }
  .label {
    font-size: 14px;
    font-weight: 600;
    fill: var(--fg);
  }
  .small {
    font-size: 12px;
    fill: var(--muted);
  }
  .node {
    stroke: var(--line);
    stroke-width: 1;
  }
  .neutral {
    fill: var(--neutral);
  }
  .input {
    fill: var(--input);
  }
  .process {
    fill: var(--process);
  }
  .storage {
    fill: var(--storage);
  }
  .external {
    fill: var(--external);
  }
  .risk {
    fill: var(--risk);
  }
  .edge {
    stroke: var(--line);
    stroke-width: 1.5;
    fill: none;
  }
  .zone {
    fill: none;
    stroke: var(--line);
    stroke-width: 1;
    stroke-dasharray: 6 5;
    opacity: 0.8;
  }
</style>
<main>
  <!-- SVG -->
</main>
```
