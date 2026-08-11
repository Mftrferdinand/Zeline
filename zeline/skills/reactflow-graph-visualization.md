# Reactflow Graph Visualization

> Build interactive node-graph visualizations using ReactFlow (xyflow). Covers creating standalone HTML files (CDN), Vite+React projects, custom node components, edge markers, CSS variable theming, and generating graphs from markdown vaults.

Build interactive node-edge graphs with ReactFlow (@xyflow/react). Supports drag, zoom, Minimap, Controls, and custom markers.

## Two Modes

### 1. Standalone HTML (CDN) — No Build Step

Include from CDN:

```html
<script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
<script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@xyflow/react@12.4.4/dist/umd/index.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@xyflow/react@12.4.4/dist/style.min.css"/>
```

Template structure:

```js
const { useCallback, useRef } = React;
const { ReactFlow, ReactFlowProvider, Background, Controls, MiniMap,
        useNodesState, useEdgesState, MarkerType, Handle, Position } = ReactFlow;

const initialNodes = [
  { id: "A", position: { x: 0, y: 0 }, data: { label: "Node A", category: "Category", isCategory: false } },
];

const initialEdges = [
  { id: "e1", source: "A", target: "B", animated: true, markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 } },
];

function CustomNode({ data, selected }) {
  const color = COLORS[data.category] || "#666";
  return React.createElement('div', { className: "skill-node", style: { borderColor: color } },
    data.label,
    React.createElement(Handle, { type:'source', position:Position.Bottom, style:{ background:color, width:8, height:8 } }),
    React.createElement(Handle, { type:'target', position:Position.Top, style:{ background:color, width:8, height:8 } })
  );
}

const nodeTypes = { default: CustomNode };
```

### 2. Vite + React + TypeScript

Clone template:

```bash
git clone https://github.com/xyflow/vite-react-flow-template.git
cd project
npm install
npm run dev
```

## Edge Markers (Arrows)

```js
markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 }
markerStart: { type: MarkerType.ArrowClosed, orient: 'auto-start-reverse' }
```

Custom marker SVG:

```js
markerEnd: 'custom-marker-id'
```

With SVG `<defs><marker id="custom-marker-id">...</marker>` in the component.

## CSS Variable Theming

Override `:root` or `.react-flow`:

```css
.react-flow {
  --xy-background-color-default: #1a1a2e;
  --xy-node-color-default: #e8e8e8;
  --xy-node-border-default: 2px solid;
  --xy-node-background-color-default: rgba(255,255,255,0.06);
  --xy-node-boxshadow-selected-default: 0 0 0 2px #4ECDC4;
  --xy-edge-stroke-default: #555;
  --xy-edge-stroke-selected-default: #4ECDC4;
  --xy-handle-background-color-default: #ccc;
  --xy-controls-button-background-color-default: #16213e;
  --xy-minimap-background-color-default: #16213e;
  --xy-background-pattern-dots-color-default: #2a2a4a;
}
```

## Color Scheme

Use `colorMode="dark"` or `colorMode="light"` on ReactFlow component.

## Node Styling

```css
.skill-node {
  padding: 6px 14px; border-radius: 8px; text-align: center;
  cursor: pointer; font-weight: 600; font-size: 11px;
  border: 2px solid; background: rgba(255,255,255,0.06);
  max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.skill-node.cat { font-size: 13px; padding: 10px 20px; text-transform: uppercase; }
.skill-node:hover { transform: scale(1.08); filter: brightness(1.3); }
.skill-node.selected { box-shadow: 0 0 0 2px #4ECDC4; }
```

## Interactive Editor Features (Standalone HTML)

When user wants to edit (not just view) the graph — add/delete/rename nodes from browser, no coding:

```html
<div id="toolbar" style="position:absolute;top:12px;left:12px;z-index:1000;display:flex;gap:6px;flex-direction:column">
  <div class="row">
    <button onclick="addSkill()">➕ Skill</button>
    <button onclick="delSkill()">🗑️ Hapus</button>
    <button onclick="exportData()">💾 Export</button>
  </div>
  <div class="row">
    <label>✏️ <input id="renameInput" placeholder="Nama baru..."/></label>
    <button onclick="renameSkill()">Ganti</button>
  </div>
  <div id="info" style="color:#888;font-size:11px">Klik node dulu</div>
</div>
```

### State Bridge Pattern (Critical)

React Flow state lives inside a `useEffect` — toolbar functions outside React need access:

```js
// Inside Flow component:
useEffect(() => {
  window.__setNodes = setNodes;
  window.__setEdges = setEdges;
  window.__selected = selected;
}, [nodes, edges, selected]);

// Also track selected node ID:
const [selected, setSelected] = useState(null);
const onNodeClick = useCallback((_, node) => {
  setSelected(node.id);
  document.getElementById("info").textContent = "Selected: " + node.data.label;
  document.getElementById("renameInput").value = node.data.label;
}, []);
```

### Toolbar Functions (Global Scope)

```js
let counter = 100;

function addSkill(){
  const id = "skill-" + (counter++);
  const pos = {x: 100 + Math.random()*200, y: 100 + Math.random()*200};
  window.__setNodes(prev => [...prev, {
    id, position: pos,
    data: {label: "New Skill", cat: "Uncategorized", isCategory: false}
  }]);
}

function delSkill(){
  if(!window.__selected) return;
  window.__setNodes(prev => prev.filter(n => n.id !== window.__selected));
  window.__setEdges(prev => prev.filter(e => e.source !== window.__selected && e.target !== window.__selected));
}

function renameSkill(){
  const val = document.getElementById('renameInput').value.trim();
  if(!val || !window.__selected) return;
  window.__setNodes(prev => prev.map(n => n.id === window.__selected ?
    {...n, data: {...n.data, label: val}} : n));
}

function exportData(){
  // Capture from state via a ref or global bridge
  // Create blob: URL and click <a> to download
}
```

Add `deleteKeyCode="Delete"` to ReactFlow for keyboard delete of selected nodes.

### Important: onConnect for Edge Drawing

```js
const onConnect = useCallback((p) =>
  setEdges((eds) => addEdge({
    ...p,
    markerEnd: {type: MarkerType.ArrowClosed, width: 16, height: 16},
    style: {stroke: "#888", strokeWidth: 2}
  }, eds)), [setEdges]);
```

### Delivery: HTTP Server Required

```bash
# Start server in Download folder
terminal(background=true, command="cd /storage/emulated/0/Download && python3 -m http.server 8080")

# Open in browser
am start -a android.intent.action.VIEW -d "http://127.0.0.1:8080/Graph.html"
```

**Do NOT use `termux-open`** — may fail to find handler. Always prefer `am start` + HTTP server.

### Preference: Interactive Editor Over Coding

**Key lesson:** prefer editing via a browser UI (drag/rename/add/delete) over code editing through terminal/nano — the default deliverable is a browser-based editor.

### Decision Flow for Graph Requests

```
User asks for graph/map/visualization
├── "liat" / "coba liat" / "buatin grafik" → Static ReactFlow HTML (inline data, no fetch)
├── "geser" / "ganti nama" / "edit" / "gamau codding" → Single-file HTML editor with toolbar
└── "coding" / "project" / "template" / "vite" → Vite + TS template
```

For an editable graph (add/delete/rename nodes, add edges via drag):

- Expose `window.__setNodes` and `window.__setEdges` from inside React Flow
- Track `selected` state via `onNodeClick`
- Add toolbar buttons that call the exposed functions
- Use `deleteKeyCode="Delete"` on ReactFlow to enable keyboard delete
- Use `onConnect` callback to add edges by dragging between handles

```js
const onConnect = useCallback((p) =>
  setEdges((eds) => addEdge({ ...p, markerEnd: { type: MarkerType.ArrowClosed } }, eds)), [setEdges]);
```

## Generating from Markdown Vault

Extract nodes from `.md` files by:
1. Reading filenames → node IDs
2. Reading content for `[[wikilinks]]` → edges
3. Assigning positions (grid layout or force-directed)
4. Determining category from `Kembali ke [[Category]]` patterns

## Controls Overrides

```css
.react-flow__controls button { background: #16213e !important; border-color: #0f3460 !important; }
.react-flow__controls svg { fill: #ccc; }
```

## MiniMap Styling

```js
<MiniMap
  nodeColor={(n) => COLORS[n.data?.category] || '#666'}
  maskColor="#1a1a2e"
  style={{ borderRadius: 8 }}
/>
```

## Background

```js
<Background variant="dots" gap={30} size={1.5} />
```

Variants: `dots`, `lines`, `cross`.

## Triggers

User asks: "reactflow", "buat graph", "visualisasi skill", "node graph", "network diagram", "interactive graph", "skill map", "peta skill".
