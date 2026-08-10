# Reactflow Graph

> Interactive node-edge graph visualizations with ReactFlow — self-contained, draggable, themeable.

Generate **self-contained interactive node-edge graphs** using ReactFlow (@xyflow/react). Output is a single HTML file with all data inlined — no external fetch, no build step, just open in a browser.

## When to use

User asks for:
- "Bikin grafik interaktif skill/task/relationship"
- "Visualisasi pake node yang bisa digeser"
- "Graph pake ReactFlow kayak di dokumentasi"
- "Yang ada panah, warna, bisa klik-geser"
- "Buat editor graph — tambah/hapus/ganti nama node langsung dari browser"

## Core Architecture

```
Self-contained HTML
├── CDN React 18 (umd)
├── CDN @xyflow/react 12 (umd)
├── Data nodes + edges (inline JS object)
├── Custom node component (SkillNode)
└── CSS Variables theming (--xy-*)
```

## Essential: Serve via HTTP, Not file://

**CRITICAL:** Browsers block `file://` for CDN scripts. Always serve via HTTP:

```bash
# Terminal 1: Start HTTP server
cd /path/to/folder
python3 -m http.server 8080

# Terminal 2: Open in browser
am start -a android.intent.action.VIEW -d "http://127.0.0.1:8080/graph.html"
```

## Template Structure

### Minimal Graph (read-only, draggable)

```html
<script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
<script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@xyflow/react@12.4.4/dist/umd/index.min.js"></script>
```

### Data Format (nodes)

```javascript
const initialNodes = [
  { id: "node-1", position: { x: 0, y: 0 }, data: { label: "Name", category: "Group", isCategory: false, isCat: false } },
  { id: "cat-1", position: { x: 100, y: 100 }, data: { label: "Category", category: "Group", isCategory: true, isCat: true } },
];
```

### Data Format (edges)

```javascript
const initialEdges = [
  { id: "e1", source: "cat-1", target: "node-1", animated: true,
    markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
    style: { stroke: "#888", strokeWidth: 2 } },
];
```

### Custom Node Component

```javascript
function SkillNode({ data, selected }) {
  const color = COLORS[data.cat] || "#666";
  const cls = "skill-node" + (data.isCat ? " cat" : "") + (selected ? " selected" : "");
  return React.createElement('div', { className: cls, style: {
    borderColor: color,
    background: data.isCat ? color+'30' : color+'15',
    color: data.isCat ? '#fff' : '#e0e0e0',
  }}, data.label,
    React.createElement(Handle, { type:'source', position:Position.Bottom, style:{background:color,width:8,height:8,border:'2px solid #1a1a2e'} }),
    React.createElement(Handle, { type:'target', position:Position.Top, style:{background:color,width:8,height:8,border:'2px solid #1a1a2e'} })
  );
}
const nodeTypes = { default: SkillNode };
```

### CSS Variables Theming (matching ReactFlow docs)

```css
.react-flow {
  --xy-background-color-default: #1a1a2e;
  --xy-node-color-default: #e8e8e8;
  --xy-node-border-default: 2px solid;
  --xy-node-background-color-default: rgba(255,255,255,0.06);
  --xy-node-boxshadow-hover-default: 0 0 0 1px rgba(255,255,255,0.15);
  --xy-node-boxshadow-selected-default: 0 0 0 2px #4ECDC4;
  --xy-edge-stroke-default: #555;
  --xy-edge-stroke-width-default: 1.5;
  --xy-edge-stroke-selected-default: #4ECDC4;
  --xy-handle-background-color-default: #ccc;
  --xy-handle-border-color-default: #1a1a2e;
  --xy-controls-button-background-color-default: #16213e;
  --xy-controls-button-background-color-hover-default: #0f3460;
  --xy-controls-button-color-default: #ccc;
  --xy-controls-button-color-hover-default: #fff;
  --xy-controls-button-border-color-default: #0f3460;
  --xy-minimap-background-color-default: #16213e;
  --xy-background-pattern-dots-color-default: #2a2a4a;
}
```

## Interactive Editor Features

For a full editor (add/delete/rename nodes), expose these globals:

```javascript
// Expose setNodes/setEdges globally
useEffect(() => {
  window.__setNodes = setNodes;
  window.__setEdges = setEdges;
  window.__nodes = nodes;
  window.__selected = selected;
}, [nodes, edges]);

// Toolbar functions
function addSkill() {
  const newId = "node-" + counter++;
  const newNode = { id: newId, position: { x: Math.random()*200, y: Math.random()*200 },
    data: { label: "New", cat: "Uncategorized", isCategory: false, isCat: false } };
  window.__setNodes([...window.__nodes, newNode]);
}
function delSkill() {
  if (!window.__selected) return;
  window.__setNodes(window.__nodes.filter(n => n.id !== window.__selected));
  window.__setEdges(window.__edges.filter(e => e.source !== window.__selected && e.target !== window.__selected));
}
function renameSkill(newName) {
  if (!window.__selected) return;
  window.__setNodes(window.__nodes.map(n =>
    n.id === window.__selected ? { ...n, data: { ...n.data, label: newName } } : n
  ));
}
function exportData() {
  const data = { nodes: window.__nodes, edges: window.__edges };
  const blob = new Blob([JSON.stringify(data, null, 2)], {type:"application/json"});
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "graph.json"; a.click();
}
```

## Complete HTML Template

Load the full interactive editor template:

```
load_skill(name="reactflow-graph", file_path="templates/interactive-editor.html")
```

## User Preferences (Tserriednich/the user)

- Always use DARK theme (`#1a1a2e` background)
- Category colors: semantic and distinct per group
- Nodes must be draggable
- Include MiniMap + Controls + Background (dots variant)
- Edge arrows with `MarkerType.ArrowClosed`
- Category nodes = larger, uppercase, centered
- If the user says "putih" or "full putih", switch to light theme immediately
- Prefer inlined data over fetch — file:// protocol breaks CDN scripts on Android
- Always serve via `python3 -m http.server 8080` and open via `am start -a android.intent.action.VIEW`


---

## Lampiran: `references/serving-tips.md`

# Serving ReactFlow HTML on Android (Termux)

## The Problem

Browsers block `file://` protocol when loading CDN scripts (`<script src="https://unpkg.com/...">`). A self-contained HTML file opened via `file://` shows an empty white page with no errors.

## The Fix: HTTP Server

```bash
# Start server
cd /path/to/folder
python3 -m http.server 8080

# Open in browser
am start -a android.intent.action.VIEW -d "http://127.0.0.1:8080/graph.html"
```

## Kill Old Server

```bash
# Find and kill the python HTTP server
ps aux | grep "http.server"
kill <PID>
```

## AM Start Cheatsheet

```bash
# URL
am start -a android.intent.action.VIEW -d "http://127.0.0.1:8080/file.html"

# File (avoid — breaks CDN scripts)
am start -a android.intent.action.VIEW -d "file:///storage/emulated/0/Download/file.html" -t "text/html"
```

---

## Catatan adaptasi Zeline
- Tool luar diganti ke padanan Zeline: skill_view.
- File pendukung tidak di-inline (terlalu besar/biner): templates/interactive-editor.html.

