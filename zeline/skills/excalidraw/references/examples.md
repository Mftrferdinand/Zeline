# Larger Examples

Complete element arrays you can adapt. Each one is a full `.excalidraw` payload —
wrap the `elements` array in the standard envelope before saving:

```json
{ "type": "excalidraw", "version": 2, "source": "zeline", "elements": [ ... ],
  "appState": { "viewBackgroundColor": "#ffffff" } }
```

Three things that break diagrams in practice, worth knowing before you copy anything
below:

- **Every element needs a unique `id`**, and bindings reference those ids. Duplicate
  ids make Excalidraw drop elements silently.
- **An arrow bound with `startBinding`/`endBinding` must also list itself** in each
  bound element's `boundElements`. One direction only, and the arrow does not follow
  when the shape moves.
- **`seed` controls the hand-drawn jitter.** Fixed seeds render identically every
  time; omit them and the same file looks slightly different on each open.

## Three-Tier Architecture

Client → API → database, with a cache beside the API.

```json
[
  { "type": "rectangle", "id": "client", "x": 80, "y": 80, "width": 180, "height": 70,
    "strokeColor": "#1971c2", "backgroundColor": "#a5d8ff", "fillStyle": "solid",
    "strokeWidth": 2, "roughness": 1, "seed": 101, "roundness": { "type": 3 },
    "boundElements": [{ "id": "a1", "type": "arrow" }] },
  { "type": "text", "id": "client-t", "x": 110, "y": 105, "width": 120, "height": 25,
    "text": "Web Client", "fontSize": 20, "fontFamily": 1, "textAlign": "center",
    "strokeColor": "#1971c2", "seed": 102, "containerId": "client" },

  { "type": "rectangle", "id": "api", "x": 380, "y": 80, "width": 180, "height": 70,
    "strokeColor": "#2f9e44", "backgroundColor": "#b2f2bb", "fillStyle": "solid",
    "strokeWidth": 2, "roughness": 1, "seed": 103, "roundness": { "type": 3 },
    "boundElements": [
      { "id": "a1", "type": "arrow" },
      { "id": "a2", "type": "arrow" },
      { "id": "a3", "type": "arrow" }
    ] },
  { "type": "text", "id": "api-t", "x": 425, "y": 105, "width": 90, "height": 25,
    "text": "API", "fontSize": 20, "fontFamily": 1, "textAlign": "center",
    "strokeColor": "#2f9e44", "seed": 104, "containerId": "api" },

  { "type": "rectangle", "id": "db", "x": 680, "y": 80, "width": 180, "height": 70,
    "strokeColor": "#e8590c", "backgroundColor": "#ffd8a8", "fillStyle": "solid",
    "strokeWidth": 2, "roughness": 1, "seed": 105, "roundness": { "type": 3 },
    "boundElements": [{ "id": "a2", "type": "arrow" }] },
  { "type": "text", "id": "db-t", "x": 720, "y": 105, "width": 100, "height": 25,
    "text": "Postgres", "fontSize": 20, "fontFamily": 1, "textAlign": "center",
    "strokeColor": "#e8590c", "seed": 106, "containerId": "db" },

  { "type": "rectangle", "id": "cache", "x": 380, "y": 260, "width": 180, "height": 70,
    "strokeColor": "#9c36b5", "backgroundColor": "#eebefa", "fillStyle": "solid",
    "strokeWidth": 2, "roughness": 1, "seed": 107, "roundness": { "type": 3 },
    "boundElements": [{ "id": "a3", "type": "arrow" }] },
  { "type": "text", "id": "cache-t", "x": 420, "y": 285, "width": 100, "height": 25,
    "text": "Redis", "fontSize": 20, "fontFamily": 1, "textAlign": "center",
    "strokeColor": "#9c36b5", "seed": 108, "containerId": "cache" },

  { "type": "arrow", "id": "a1", "x": 265, "y": 115, "width": 110, "height": 0,
    "points": [[0, 0], [110, 0]], "strokeColor": "#495057", "strokeWidth": 2,
    "roughness": 1, "seed": 201, "endArrowhead": "arrow",
    "startBinding": { "elementId": "client", "focus": 0, "gap": 5 },
    "endBinding": { "elementId": "api", "focus": 0, "gap": 5 } },
  { "type": "arrow", "id": "a2", "x": 565, "y": 115, "width": 110, "height": 0,
    "points": [[0, 0], [110, 0]], "strokeColor": "#495057", "strokeWidth": 2,
    "roughness": 1, "seed": 202, "endArrowhead": "arrow",
    "startBinding": { "elementId": "api", "focus": 0, "gap": 5 },
    "endBinding": { "elementId": "db", "focus": 0, "gap": 5 } },
  { "type": "arrow", "id": "a3", "x": 470, "y": 155, "width": 0, "height": 100,
    "points": [[0, 0], [0, 100]], "strokeColor": "#9c36b5", "strokeWidth": 2,
    "roughness": 1, "seed": 203, "endArrowhead": "arrow", "strokeStyle": "dashed",
    "startBinding": { "elementId": "api", "focus": 0, "gap": 5 },
    "endBinding": { "elementId": "cache", "focus": 0, "gap": 5 } }
]
```

Text is attached with `containerId`, which is what keeps a label centred inside its
box when the box moves. A free-floating text element at the same coordinates looks
identical until someone drags the shape.

## Flowchart With a Decision

Diamond branches to two outcomes, arrows labelled yes/no.

```json
[
  { "type": "ellipse", "id": "start", "x": 100, "y": 40, "width": 160, "height": 60,
    "strokeColor": "#1971c2", "backgroundColor": "#a5d8ff", "fillStyle": "solid",
    "strokeWidth": 2, "roughness": 1, "seed": 301,
    "boundElements": [{ "id": "f1", "type": "arrow" }] },
  { "type": "text", "id": "start-t", "x": 140, "y": 60, "width": 80, "height": 25,
    "text": "Start", "fontSize": 20, "fontFamily": 1, "textAlign": "center",
    "strokeColor": "#1971c2", "seed": 302, "containerId": "start" },

  { "type": "diamond", "id": "check", "x": 80, "y": 180, "width": 200, "height": 110,
    "strokeColor": "#f08c00", "backgroundColor": "#ffec99", "fillStyle": "solid",
    "strokeWidth": 2, "roughness": 1, "seed": 303,
    "boundElements": [
      { "id": "f1", "type": "arrow" },
      { "id": "f2", "type": "arrow" },
      { "id": "f3", "type": "arrow" }
    ] },
  { "type": "text", "id": "check-t", "x": 110, "y": 222, "width": 140, "height": 25,
    "text": "Valid?", "fontSize": 18, "fontFamily": 1, "textAlign": "center",
    "strokeColor": "#f08c00", "seed": 304, "containerId": "check" },

  { "type": "rectangle", "id": "ok", "x": 380, "y": 195, "width": 170, "height": 70,
    "strokeColor": "#2f9e44", "backgroundColor": "#b2f2bb", "fillStyle": "solid",
    "strokeWidth": 2, "roughness": 1, "seed": 305, "roundness": { "type": 3 },
    "boundElements": [{ "id": "f2", "type": "arrow" }] },
  { "type": "text", "id": "ok-t", "x": 415, "y": 220, "width": 100, "height": 25,
    "text": "Process", "fontSize": 18, "fontFamily": 1, "textAlign": "center",
    "strokeColor": "#2f9e44", "seed": 306, "containerId": "ok" },

  { "type": "rectangle", "id": "fail", "x": 80, "y": 380, "width": 200, "height": 70,
    "strokeColor": "#c92a2a", "backgroundColor": "#ffc9c9", "fillStyle": "solid",
    "strokeWidth": 2, "roughness": 1, "seed": 307, "roundness": { "type": 3 },
    "boundElements": [{ "id": "f3", "type": "arrow" }] },
  { "type": "text", "id": "fail-t", "x": 110, "y": 405, "width": 140, "height": 25,
    "text": "Reject", "fontSize": 18, "fontFamily": 1, "textAlign": "center",
    "strokeColor": "#c92a2a", "seed": 308, "containerId": "fail" },

  { "type": "arrow", "id": "f1", "x": 180, "y": 105, "width": 0, "height": 70,
    "points": [[0, 0], [0, 70]], "strokeColor": "#495057", "strokeWidth": 2,
    "roughness": 1, "seed": 401, "endArrowhead": "arrow",
    "startBinding": { "elementId": "start", "focus": 0, "gap": 5 },
    "endBinding": { "elementId": "check", "focus": 0, "gap": 5 } },
  { "type": "arrow", "id": "f2", "x": 285, "y": 232, "width": 90, "height": 0,
    "points": [[0, 0], [90, 0]], "strokeColor": "#2f9e44", "strokeWidth": 2,
    "roughness": 1, "seed": 402, "endArrowhead": "arrow", "label": { "text": "yes" },
    "startBinding": { "elementId": "check", "focus": 0, "gap": 5 },
    "endBinding": { "elementId": "ok", "focus": 0, "gap": 5 } },
  { "type": "arrow", "id": "f3", "x": 180, "y": 295, "width": 0, "height": 80,
    "points": [[0, 0], [0, 80]], "strokeColor": "#c92a2a", "strokeWidth": 2,
    "roughness": 1, "seed": 403, "endArrowhead": "arrow", "label": { "text": "no" },
    "startBinding": { "elementId": "check", "focus": 0, "gap": 5 },
    "endBinding": { "elementId": "fail", "focus": 0, "gap": 5 } }
]
```

Arrow labels use `label: { text }` on the arrow itself, not a separate text element —
that way the label tracks the arrow when either endpoint moves.

## Grouped Subsystem With a Frame

A labelled container around several boxes. Use a `frame` when the grouping is
structural; a plain rectangle behind the children works but does not move with them.

```json
[
  { "type": "frame", "id": "vpc", "x": 60, "y": 60, "width": 560, "height": 260,
    "strokeColor": "#868e96", "backgroundColor": "transparent", "strokeWidth": 1,
    "roughness": 0, "seed": 501, "name": "VPC · private subnet",
    "children": ["svc-a", "svc-b"] },

  { "type": "rectangle", "id": "svc-a", "x": 110, "y": 130, "width": 190, "height": 80,
    "strokeColor": "#1971c2", "backgroundColor": "#a5d8ff", "fillStyle": "solid",
    "strokeWidth": 2, "roughness": 1, "seed": 502, "roundness": { "type": 3 },
    "frameId": "vpc", "boundElements": [{ "id": "g1", "type": "arrow" }] },
  { "type": "text", "id": "svc-a-t", "x": 140, "y": 160, "width": 130, "height": 25,
    "text": "auth-service", "fontSize": 16, "fontFamily": 1, "textAlign": "center",
    "strokeColor": "#1971c2", "seed": 503, "containerId": "svc-a" },

  { "type": "rectangle", "id": "svc-b", "x": 380, "y": 130, "width": 190, "height": 80,
    "strokeColor": "#2f9e44", "backgroundColor": "#b2f2bb", "fillStyle": "solid",
    "strokeWidth": 2, "roughness": 1, "seed": 504, "roundness": { "type": 3 },
    "frameId": "vpc", "boundElements": [{ "id": "g1", "type": "arrow" }] },
  { "type": "text", "id": "svc-b-t", "x": 405, "y": 160, "width": 140, "height": 25,
    "text": "billing-service", "fontSize": 16, "fontFamily": 1, "textAlign": "center",
    "strokeColor": "#2f9e44", "seed": 505, "containerId": "svc-b" },

  { "type": "arrow", "id": "g1", "x": 305, "y": 170, "width": 70, "height": 0,
    "points": [[0, 0], [70, 0]], "strokeColor": "#495057", "strokeWidth": 2,
    "roughness": 1, "seed": 506, "endArrowhead": "arrow", "frameId": "vpc",
    "startBinding": { "elementId": "svc-a", "focus": 0, "gap": 5 },
    "endBinding": { "elementId": "svc-b", "focus": 0, "gap": 5 } }
]
```

`children` on the frame and `frameId` on each child must agree. Setting only one side
leaves elements that look grouped but do not move together.

## Sequence Diagram

Lifelines as dashed verticals, messages as horizontal arrows. Excalidraw has no
native sequence type, so this is a convention rather than a feature.

```json
[
  { "type": "rectangle", "id": "l1", "x": 100, "y": 40, "width": 140, "height": 50,
    "strokeColor": "#1971c2", "backgroundColor": "#a5d8ff", "fillStyle": "solid",
    "strokeWidth": 2, "roughness": 1, "seed": 601, "roundness": { "type": 3 } },
  { "type": "text", "id": "l1-t", "x": 130, "y": 57, "width": 80, "height": 25,
    "text": "Client", "fontSize": 16, "fontFamily": 1, "textAlign": "center",
    "strokeColor": "#1971c2", "seed": 602, "containerId": "l1" },
  { "type": "line", "id": "l1-life", "x": 170, "y": 95, "width": 0, "height": 300,
    "points": [[0, 0], [0, 300]], "strokeColor": "#adb5bd", "strokeWidth": 1,
    "strokeStyle": "dashed", "roughness": 0, "seed": 603 },

  { "type": "rectangle", "id": "l2", "x": 420, "y": 40, "width": 140, "height": 50,
    "strokeColor": "#2f9e44", "backgroundColor": "#b2f2bb", "fillStyle": "solid",
    "strokeWidth": 2, "roughness": 1, "seed": 604, "roundness": { "type": 3 } },
  { "type": "text", "id": "l2-t", "x": 455, "y": 57, "width": 70, "height": 25,
    "text": "Server", "fontSize": 16, "fontFamily": 1, "textAlign": "center",
    "strokeColor": "#2f9e44", "seed": 605, "containerId": "l2" },
  { "type": "line", "id": "l2-life", "x": 490, "y": 95, "width": 0, "height": 300,
    "points": [[0, 0], [0, 300]], "strokeColor": "#adb5bd", "strokeWidth": 1,
    "strokeStyle": "dashed", "roughness": 0, "seed": 606 },

  { "type": "arrow", "id": "m1", "x": 175, "y": 150, "width": 310, "height": 0,
    "points": [[0, 0], [310, 0]], "strokeColor": "#495057", "strokeWidth": 2,
    "roughness": 1, "seed": 607, "endArrowhead": "arrow",
    "label": { "text": "POST /login" } },
  { "type": "arrow", "id": "m2", "x": 485, "y": 230, "width": -310, "height": 0,
    "points": [[0, 0], [-310, 0]], "strokeColor": "#2f9e44", "strokeWidth": 2,
    "roughness": 1, "seed": 608, "endArrowhead": "arrow", "strokeStyle": "dashed",
    "label": { "text": "200 + token" } }
]
```

Lifelines are deliberately unbound: binding them to the header boxes makes
Excalidraw reroute them around the shape, which destroys the vertical alignment the
diagram depends on.

## Layout Arithmetic

Spacing is manual, so compute it rather than eyeballing coordinates:

| Quantity | Workable value |
|---|---|
| Box size | 180×70 (fits ~14 characters at `fontSize` 20) |
| Horizontal gap between boxes | 120 |
| Vertical gap between rows | 110 |
| Arrow `gap` to a bound shape | 5 |
| Centre of a box | `x + width / 2`, `y + height / 2` |
| Arrow between horizontal neighbours | `x: leftBox.x + leftBox.width + 5`, length `gap - 10` |

Text height is ~25 px at `fontSize` 20; to centre a label manually use
`y + (height - 25) / 2`. Prefer `containerId` and let Excalidraw do it.

`fontFamily`: `1` = hand-drawn (Virgil), `2` = normal (Helvetica), `3` = code (Cascadia).
Keep one family per diagram — mixing reads as an accident.
