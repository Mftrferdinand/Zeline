# Obsidian Graph Generator

> Bulk-generate 100-1000+ interconnected markdown files for Obsidian Graph View — names, backlinks, category distribution.

Generate massive interconnected Obsidian vaults for Graph View visualization. Targets 520+ nodes with random wikilinks.

## When to Use

- User wants to populate an Obsidian Graph View for demo/testing
- Creating a rich knowledge base from scratch
- Testing Obsidian plugins on a dense graph

## Strategy: Terminal Python, NOT write_file

`write_file` is capped at ~50 calls per `execute_code` block. For 500+ files, use `terminal()` with Python directly:

```python
import os, random
vault = '/storage/emulated/0/Documents/YourVault'

# Get existing files to avoid duplicates
existing = set()
for f in os.listdir(vault):
    if f.endswith('.md'):
        existing.add(f[:-3])
```

## Name Generation

Use 3-part word pools (prefix + root + suffix):

| Pattern | Example |
|---|---|
| Prefix+Root | `AstroDash`, `HyperCore` |
| Root+Suffix | `FlowManager`, `HubScanner` |
| Prefix+Root+Suffix | `QuantumNodeWatcher` |

**Prefixes:** Ultra, Super, Mega, Hyper, Nano, Quantum, Turbo, Neo, Proto, Retro, Crypto, Cyber, Bio, Geo, Astro, Techno, Neuro, Chrono, Holo, Velo, Auto, Multi, Cross, Omni, Poly, Tri, Dual, Mono, Macro, Micro

**Roots:** Core, Stack, Flow, Node, Grid, Net, Mesh, Chain, Loop, Pulse, Wave, Beam, Path, Track, Frame, Base, Hub, Port, Bridge, Gate, Synth, Forge, Craft, Lab, Studio, Kit, Pack, Set, Suite, Blend, Flex, Shift, Sync, Spark, Glide, Boost, Rush, Dash, Jump, Leap

**Suffixes:** Manager, Engine, Processor, Controller, Handler, Builder, Runner, Driver, Router, Filter, Loader, Dumper, Parser, Writer, Reader, Watcher, Poller, Spawner, Tracker, Tracer, Profiler, Scanner, Monitor, Logger, Mapper, Extractor, Collector, Server, Client, Agent, Daemon, Service, Plugin, Bridge, Worker, Scheduler, Oracle, Validator

## File Content Template

Each file has:
1. A `# Title` header
2. Brief description mentioning the category
3. `Bagian dari [[{CategoryFile}|{CategoryDisplay}]].`
4. `## Related` with 4 random backlinks
5. `Kembali ke [[{CategoryFile}|{CategoryDisplay}]].`

## Backlink Strategy

- Each file → its **category file** (parent)
- Each file → **3-5 random siblings** from the existing set
- Home page → ALL category files
- Total edges ≈ files × 5

## Category Distribution

Rotate through 60+ categories evenly:

```python
categories = ["Blockchain-Crypto", "Trading-Strategies", "DevOps-Infra", ...]
for i in range(needed):
    cat = categories[i % len(categories)]
```

Create each `{Category}.md` file with: `# {Display}` + description + link to home.

## Performance

| Count | Time (terminal Python) |
|---|---|
| 100 | ~1s |
| 500 | ~3-4s |
| 1000 | ~8-12s |

Must use `terminal()` not `execute_code()` + `write_file()` for anything over 50 files.

## Pitfalls

1. **Don't use write_file for bulk** — capped at ~50 calls per execute_code.
2. **Seed for reproducibility**: `random.seed(...)` 
3. **Filename length cap**: keep under 40 chars.
4. **Deduplicate**: maintain `used_names` set, check before writing.
5. **Category files must exist first** — Obsidian shows broken links otherwise.
6. **Description language**: match user's preferred language (Indonesian for the user).
7. **Device path**: Android vault path is under `/storage/emulated/0/`, not home dir.

## User Preference: Curated Over Bulk

**Critical lesson from session 2026-07-03:** the user (icibos) explicitly rejected 500+ generic randomly-generated nodes and asked to "hapus aja semuanya, buat data asli aja" — meaning restore to the original 92 Zeline skill files only.

### Behavior Rules
1. **Default to original skill set (92 files)** unless user explicitly asks for bulk generation.
2. If asked for 500 themed skills, use real-sounding sub-skill names (Web3 + Daily Work) not random prefix+root+suffix.
3. User will likely say "ganti web3 dan daily aja" — meaning KEEP the originals (92) and ADD themed ones, not replace.
4. If user says "hapus" — clean up generics but keep ALL original Zeline skills.

## User Preference: Interactive Over Code

**Critical lesson (2026-07-03):** the user (icibos) rejected code editing via terminal/nano — wants drag/rename/add/delete from browser UI only. Default deliverable for any graph request:

### Decision Flow

```
User asks for graph/map/visualization
├── "liat" / "coba liat" / "buatin grafik" → Static ReactFlow HTML (inline data, no fetch)
├── "geser" / "ganti nama" / "edit" / "gamau codding" → Single-file HTML editor with toolbar
└── "coding" / "project" / "template" / "vite" → Vite + TS template
```

### Why Single-File HTML Editor Wins on Android

1. User stays in browser — no Termux, no nano/vim, no TypeScript compilation.
2. Zero dependencies besides CDN — works offline once loaded.
3. Instant feedback: click button → node moves/renames/appears/deletes immediately.
4. On Android, file watchers don't work in Termux — manual refresh every edit is painful.
5. Export as JSON to persist changes — import later or share.

### Single-File HTML Editor Architecture

```
Single HTML file
├── React 18 (CDN UMD: unpkg.com/react@18/umd/react.production.min.js)
├── ReactDOM (CDN UMD: unpkg.com/react-dom@18/umd/react-dom.production.min.js)
├── @xyflow/react 12.4.4 (CDN UMD: cdn.jsdelivr.net/npm/@xyflow/react@12.4.4/dist/umd/index.min.js)
├── CSS stylesheet (CDN: cdn.jsdelivr.net/npm/@xyflow/react@12.4.4/dist/style.min.css)
├── Inline node/edge data array (NOT fetch — file:// blocks CORS)
├── Custom node component (createElement, NOT JSX)
├── Toolbar div with buttons (position: absolute, z-index: 1000)
│   ├── ➕ Skill → addSkill()
│   ├── 🗑️ Hapus → delSkill()
│   ├── 💾 Export → exportData()
│   ├── ✏️ [input box] + [Ganti button] → renameSkill()
│   └── Info bar showing selected node name
└── Stats overlay (node count, edge count)
```

### Critical: Inline Data, Never Fetch

**Chrome/Android browsers BLOCK `fetch()` from file:// URLs.** Always inline node/edge arrays directly in HTML:

```javascript
// WRONG — blank white page on file://
fetch('./data.json').then(r => r.json()).then(data => setNodes(data.nodes));

// RIGHT
const DATA = {"nodes": [...], "edges": [...]};
useEffect(() => { setNodes(DATA.nodes); setEdges(DATA.edges.map(e => ({...e, markerEnd: {...}}))); }, []);
```

### UMD Mode Requirements

1. **No JSX** — use `React.createElement('tag', {props}, children)`.
2. **State bridge** — expose setNodes/setEdges via `window.__setNodes` and `window.__setEdges` in a `useEffect` so toolbar functions can access React state.
3. **Matching Handles** — every custom node component MUST render both `<Handle type="source">` and `<Handle type="target">` even if not connectable.
4. **Script load order**: React → ReactDOM → @xyflow/react — must load sequentially.
5. **Emoji in JS**: template literals containing emoji (`🧠`) can break parsing. Use `&#x1F9E0;` or concatenation instead of backtick strings when emoji is present.
6. **Connection line style**: set `connectionLineStyle={{stroke: "#4ECDC4", strokeWidth: 2}}` on ReactFlow for visual feedback when dragging.
7. **Delete key**: pass `deleteKeyCode="Delete"` to ReactFlow to allow keyboard deletion of selected nodes.

### Toolbar Functions (Global Scope)

```javascript
// Global counter for new node IDs
let counter = 100;

function addSkill() {
  const id = "skill-" + (counter++);
  const pos = {x: 100 + Math.random() * 200, y: 100 + Math.random() * 200};
  window.__setNodes(prev => [...prev, {
    id, position: pos,
    data: {label: "New Skill", cat: "Uncategorized", isCategory: false}
  }]);
}

function delSkill() {
  if (!window.__selected) return;
  window.__setNodes(prev => prev.filter(n => n.id !== window.__selected));
  window.__setEdges(prev => prev.filter(e => e.source !== window.__selected && e.target !== window.__selected));
}

function renameSkill() {
  const val = document.getElementById('renameInput').value.trim();
  if (!val || !window.__selected) return;
  window.__setNodes(prev => prev.map(n => n.id === window.__selected ? {...n, data: {...n.data, label: val}} : n));
}

function exportData() {
  // Capture current nodes + edges from React state
  // Convert to JSON blob and trigger download via <a href="blob:..." download>
}
```

### Common Render Failures (Blank Page)

| Symptom | Cause | Fix |
|---|---|---|
| Blank page | `fetch()` on file:// | Inline data |
| Blank page | Script load order wrong | React → ReactDOM → @xyflow/react |
| Blank page | Missing Handle | Add Handle inside every node component |
| Blank page | Emoji in template literal | Remove emoji from backtick strings |
| Blank page | Hook error | Ensure no conditional hooks |
| Nodes but no edges | Edge data mismatch | Check source/target IDs match node IDs |
| Graph invisible | CSS missing | Import `@xyflow/react/dist/style.min.css` |

## React Flow Graph Export (Obsidian → HTML)

After building a vault, export it as an interactive React Flow HTML graph.

### Steps
1. Read all `.md` files from vault using `os.listdir()`.
2. For each file, determine category by scanning content for `[[CategoryName]]` or `Kembali ke [[CategoryName]]`.
3. Extract wikilinks via regex: `re.findall(r'\[\[([^\]]+)\]\]', content)` — strip the `|display` part if present.
4. **Positioning**: place category nodes in a grid (row 1 = AI/Data/GitHub etc), skill nodes offset around their parent category. Use `random.seed()` for reproducibility.
5. Generate JSON: `{"nodes": [...], "edges": [...]}`. Each node has `{id, position, data: {label, category, isCat}}`.
6. **Inline the JSON directly in HTML** — DO NOT use `fetch('./file.json')` for local files. File:// protocol blocks CORS fetch. The data must be embedded as a JS variable assignment.
7. Use React Flow v12 from CDN:
   ```html
   <script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
   <script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
   <script src="https://cdn.jsdelivr.net/npm/@xyflow/react@12.4.4/dist/umd/index.min.js"></script>
   <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@xyflow/react@12.4.4/dist/style.min.css"/>
   ```
8. Use `createElement()` not JSX since we're in UMD mode.
9. Custom node component via `nodeTypes = { default: SkillNode }`.
10. Edge markers: `markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15 }`.
11. Add MiniMap, Controls, Background, `colorMode='dark'`.
12. Fit view: `setTimeout(() => inst.fitView({ padding: 0.15 }), 200)`.

### Theming (React Flow CSS Variables)

Override the built-in theme via CSS variables on `.react-flow`:

```css
.react-flow {
  --xy-node-background-color-default: rgba(255,255,255,0.05);
  --xy-node-border-default: 2px solid;
  --xy-node-color-default: #e0e0e0;
  --xy-edge-stroke-default: #666;
  --xy-edge-stroke-width-default: 1.5;
  --xy-handle-background-color-default: #fff;
  --xy-controls-button-background-color-default: #16213e;
  --xy-minimap-background-color-default: #16213e;
  --xy-background-pattern-dots-color-default: #2a2a4a;
}
```

Full CSS variable table from reactflow.dev docs:
- `--xy-edge-stroke-selected-default` → `#555`
- `--xy-selection-background-color-default` → `rgba(78,205,196,0.08)`
- `--xy-node-boxshadow-hover-default` → `0 0 0 0.5px #555`
- `--xy-node-boxshadow-selected-default` → `0 0 0 2px #4ECDC4`

### Delivery on Android

For Termux users, deliver the graph file via local HTTP server (bypasses CORS):

```bash
# Start server
terminal(background=true, command="cd /storage/emulated/0/Download && python3 -m http.server 8080")

# Open in browser
am start -a android.intent.action.VIEW -d "http://127.0.0.1:8080/Graph.html"
```

**Do NOT use `termux-open` for HTML files** — it may not find a handler. Always prefer `am start` + HTTP server.

## React Flow Theming Reference

See `references/react-flow-theming.md` for the full CSS variables table (default + dark mode), MarkerType usage, and CDN import paths for React Flow v12.

## Vite + React Flow Template Integration

See `references/vite-react-flow-template-integration.md` for cloning the official template, file structure, running on Termux, custom node patterns, and TypeScript config fixes.

## Themed Sub-Skill Generation (Web3 + Daily Work)

When user asks for themed skills (not random generic), use real-sounding sub-skill names per category. This differs from the generic prefix+root+suffix approach.

### Strategy

Build a `skill_template` dict per category with 10-15 real-sounding sub-skill names, then a `cat_map` dict mapping each sub-skill → its category. Write them with the same template format.

### Themed Category Pool

**Web3:**
- OnChain-Analysis: Whale-Tracking, Exchange-Flow-Analysis, MVRV-Analysis, SOPR-Trends, NUPL-Index, Realized-Cap-Tracking, Coin-Days-Destroyed, Top-Holder-Concentration
- DeFi-Lending: Aave-Strategies, Compound-Guide, Morpho-Optimizer, Liquidation-Price-Calc, Health-Factor-Monitoring, Cross-Chain-Lending
- DeFi-DEX: Uniswap-V3-Concentrated, Curve-StableSwap, Balancer-Weighted-Pool, Impermanent-Loss-Calc, DEX-Aggregator-Comparison
- Smart-Contract: Solidity-Patterns, Foundry-Dev-Env, Hardhat-Deployment, Proxy-Pattern-UUPS, Gas-Optimization-Tips
- Smart-Contract-Audit: Slither-Static-Analysis, Mythril-Automation, Foundry-Invariant-Test, Reentrancy-Detection, Flash-Loan-Attack-Check
- Tokenomics: Supply-Schedule-Design, Vesting-Schedule-Structuring, Emission-Rate-Calc, Token-Burn-Mechanics
- Wallet-Hardware/Software/Multisig: Ledger-Setup-Guide, MetaMask-Advanced, Phantom-Solana-Guide, Gnosis-Safe-Deploy
- Layer2-Rollup/ZK: Arbitrum-Airdrop-Farm, zkSync-Era-Wallet, L2-Gas-Optimization
- Bridging: Wormhole-Token-Transfer, LayerZero-OFT-Standard, Stargate-Liquidity-Bridge
- MEV: Mempool-Transaction-Trace, Searcher-Bot-Build, PBS-Architecture, Sandwich-Attack-Detect
- DAO: Voting-Power-Calc, Proposal-Template, Snapshot-Voting-Setup
- Trading-Spot/Futures/Arbitrage: DCA-Automation-Bot, Perpetual-Funding-Rate, CEX-DEX-Arbitrage-Bot
- DeFi-Security: Honeypot-Detection, Rug-Pull-Checklist, Token-Approval-Revoke
- Airdrop-Farming: Sybil-Resistance-Tool, Layer2-Airdrop-Farm, Testnet-Participation
- Crypto-Tax: Koinly-Import-Wallet, Capital-Gain-Calculation, Tax-Report-Generation
- DePIN: Helium-Hotspot-Setup, Render-Token-Node, Filecoin-Provider
- RWA: Tokenized-T-Bill, Real-Estate-Tokenization, On-Chain-KYC-Integration

**Daily Work:**
- Note-Taking: Obsidian-Setup-Vault, Logseq-Journal-Workflow, Zettelkasten-Method
- Task-Management: Todoist-Project-Setup, GTD-Method-Weekly, Kanban-Board-Setup
- Email-Workflow: Inbox-Zero-Practice, Email-Template-Quick, Follow-Up-Schedule
- Daily-Planning: Morning-Routine-Efficient, Time-Block-Schedule, Deep-Work-Session
- Terminal-Workflow: Tmux-Session-Manager, Zsh-Plugin-Omz, Fzf-Fuzzy-Find
- Git-Workflow: Feature-Branch-Strategy, Rebase-vs-Merge, Squash-Commit-Clean
- Testing: Unit-Test-Jest, Integration-Test-Setup, E2E-Test-Cypress
- CI-CD: GitHub-Actions-Workflow, GitLab-CI-Pipeline, Deploy-Strategy-Rolling
- Cloud-Setup: VPS-Initial-Setup, SSL-Cert-Let-Encrypt, Server-Hardening
- Learning-System: Spaced-Repetition-Anki, Active-Recall-Method, Feynman-Technique
- Data-Analysis: Excel-Pivot-Master, Pandas-Data-Wrangle, SQL-Query-Window
- Spreadsheet-Org: Named-Ranges-Formula, Pivot-Table-Refresh, Conditional-Format-Rule
- Password-Manager: Bitwarden-Vault-Org, Pass-GPG-Setup, 2FA-TOTP-Setup
- Backup-Strategy: 3-2-1-Rule-Implement, Restic-Encrypted, Restore-Test-Quarterly
- Time-Tracking: Toggl-Project-Time, Pomodoro-Track-Int, Clockify-Report-Week
- Health: Posture-Correction-Habit, Strength-Training-3x, Sleep-Hygiene-Routine
- Finance: Budget-Category-Track, Index-Fund-DCA-VOO, Tax-Loss-Harvest

### Fallback: AI-Enhanced Name Generation

When you need more names than mapped, generate from: `{category_prefix}-{middle}-{suffix}` where middle ∈ [Advanced, Practical, Essential, Modern, Automated, Professional, Daily] and suffix ∈ [Strategies, Guide, Workflow, Basics, Deep-Dive, Setup, Hacks, 101, Checklist, Tools].

### Pitfalls

- Theme-specific nouns beat random prefix+root. User sees through generic filler.
- Map every sub-skill to its category with `cat_map` dict for backlink generation.
- Generate category files FIRST so sub-skill backlinks don't break.

## Cleanup Procedure

When user says "hapus" or "reset":

```python
import os
vault = '/storage/emulated/0/Documents/Tserriednich'

# Define keep-set of 92 original files
keep = {
    'Tserriednich', 'user', 'Deprecated',
    'MarketAnalysis', 'TradeDataTracker',
    'Zeline-Agent', 'Zeline-Custom-Provider-Setup',
    # ... all 92
}

# Batch delete
deleted = 0
for fname in os.listdir(vault):
    if not fname.endswith('.md'): continue
    name = fname[:-3]
    if name not in keep:
        os.remove(os.path.join(vault, fname))
        deleted += 1
```

Then update the home page `Tserriednich.md` to reflect the current count and content.

### Keep-Set (92 Original Files)

**Zeline Skills (74):** MarketAnalysis, TradeDataTracker, Zeline-Agent, Zeline-Custom-Provider-Setup, Zeline-Agent-Skill-Authoring, Zeline-Voice-Config, Claude-Code, Codex, OpenCode, 9router-Management, Group-Security-Lockdown, Telegram-Gateway-Setup, News-Scraper, Riset-Airdrop, Arxiv, Blogwatcher, LLM-Wiki, Polymarket, Research-Paper-Writing, Excalidraw, ASCII-Art, ASCII-Video, Humanizer, Manim-Video, P5js, Popular-Web-Designs, Pretext, Sketch, Claude-Design, Design-MD, ComfyUI, Baoyu-Infographic, Touchdesigner-MCP, Songwriting-and-AI-Music, Architecture-Diagram, Systematic-Debugging, Test-Driven-Development, Plan, Spike, Simplify-Code, Requesting-Code-Review, Python-Debugpy, Node-Inspect-Debugger, GitHub-Auth, GitHub-Code-Review, GitHub-Issues, GitHub-PR-Workflow, GitHub-Repo-Audit, GitHub-Repo-Management, Codebase-Inspection, Himalaya, Jupyter-Live-Kernel, Obsidian, GIF-Search, Heartmula, Songsee, YouTube-Content, Yuanbao, Audiocraft-Audio-Generation, Evaluating-LLMs-Harness, HuggingFace-Hub, Llama-CPP, Segment-Anything-Model, Serving-LLMs-vLLM, Weights-and-Biases, Airtable, Document-Templater, Google-Workspace, Invoice-Generator, Maps, Nano-PDF, Notion, OCR-and-Documents, Petdex, Powerpoint, Status-Report-Generator, Teams-Meeting-Pipeline, Market-Research, OpenHue

**Category Files (15):** AI-Agents, Creative-Tools, Data-Science, Email, GitHub, Media, Messaging, MLOps, Note-Taking, Productivity, Research, Router, Security, Smart-Home, Software-Development

**Home/Personal (3):** Tserriednich, user, Deprecated


---

## Lampiran: `references/react-flow-theming.md`

# React Flow Theming Reference

Source: https://reactflow.dev/learn/customization/theming (July 2026)

## CSS Variables (Default Values)

Override these on `.react-flow` selector:

```css
.react-flow {
  --xy-edge-stroke-default: #b1b1b7;
  --xy-edge-stroke-width-default: 1;
  --xy-edge-stroke-selected-default: #555;
  --xy-connectionline-stroke-default: #b1b1b7;
  --xy-connectionline-stroke-width-default: 1;
  --xy-attribution-background-color-default: rgba(255, 255, 255, 0.5);
  --xy-minimap-background-color-default: #fff;
  --xy-background-pattern-dots-color-default: #91919a;
  --xy-background-pattern-lines-color-default: #eee;
  --xy-background-pattern-cross-color-default: #e2e2e2;
  --xy-node-color-default: inherit;
  --xy-node-border-default: 1px solid #1a192b;
  --xy-node-background-color-default: #fff;
  --xy-node-group-background-color-default: rgba(240, 240, 240, 0.25);
  --xy-node-boxshadow-hover-default: 0 1px 4px 1px rgba(0, 0, 0, 0.08);
  --xy-node-boxshadow-selected-default: 0 0 0 0.5px #1a192b;
  --xy-handle-background-color-default: #1a192b;
  --xy-handle-border-color-default: #fff;
  --xy-selection-background-color-default: rgba(0, 89, 220, 0.08);
  --xy-selection-border-default: 1px dotted rgba(0, 89, 220, 0.8);
  --xy-controls-button-background-color-default: #fefefe;
  --xy-controls-button-background-color-hover-default: #f4f4f4;
  --xy-controls-button-color-default: inherit;
  --xy-controls-button-color-hover-default: inherit;
  --xy-controls-button-border-color-default: #eee;
  --xy-controls-box-shadow-default: 0 0 2px 1px rgba(0, 0, 0, 0.08);
  --xy-resize-background-color-default: #3367d9;
}
```

## Dark Mode Preset

Quick dark theme via `colorMode="dark"` prop + CSS vars override:

```css
.react-flow {
  --xy-background-color-default: #1a1a2e;
  --xy-node-color-default: #e8e8e8;
  --xy-node-border-default: 2px solid;
  --xy-node-background-color-default: rgba(255,255,255,0.06);
  --xy-node-boxshadow-hover-default: 0 0 0 1px rgba(255,255,255,0.15);
  --xy-node-boxshadow-selected-default: 0 0 0 2px #4ECDC4;
  --xy-edge-stroke-default: #555;
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
  --xy-attribution-background-color-default: rgba(0,0,0,0.3);
}
```

## Edge Markers Reference

```ts
// Import
import { MarkerType } from '@xyflow/react';

// Usage on individual edge
{ id: 'a->b', source: 'a', target: 'b', 
  markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
  markerStart: { type: MarkerType.ArrowClosed, orient: 'auto-start-reverse' }
}

// Default for all edges via prop
<ReactFlow defaultEdgeOptions={{
  markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
  style: { stroke: '#777', strokeWidth: 1.5 },
}}>
```

Marker types: `Arrow` (open), `ArrowClosed` (filled). Custom SVG markers via `defs` + marker id reference.



---

## Lampiran: `references/vite-react-flow-template-integration.md`

# Vite React Flow Template Integration

Source: https://github.com/xyflow/vite-react-flow-template

## Cloning

```bash
git clone https://github.com/xyflow/vite-react-flow-template.git AppName
cd AppName
npm install  # takes 2-4 min in Termux
```

## File Structure After Setup

```
AppName/
├── index.html          # Entry point, update <title>
├── package.json        # @xyflow/react ^12.x, react ^18.x, vite ^5.x
├── tsconfig.json
├── vite.config.ts
└── src/
    ├── main.tsx        # ReactDOM.createRoot — unchanged
    ├── index.css       # Put theming CSS here
    ├── App.tsx         # Main ReactFlow component
    ├── nodes/
    │   ├── index.ts    # initialNodes array + nodeTypes export
    │   ├── types.ts    # Your custom node type
    │   └── SkillNode.tsx  # Custom node component
    └── edges/
        └── index.ts    # initialEdges array + edgeTypes export
```

## Running

```bash
npm run dev  # starts on localhost:5173 by default
```

On Termux Android, start in background:
```bash
cd /path/to/App && npx vite --host 127.0.0.1 --port 5173
```
Then open: `http://127.0.0.1:5173`

On Termux, `npm install` can take 2-4 minutes. Use `terminal(background=true)` + `process(wait)`.

## Custom Node Pattern

```tsx
// src/nodes/types.ts
export type SkillNodeData = {
  label: string;
  category: string;
  isCategory: boolean;
};

// src/nodes/SkillNode.tsx
import { Handle, Position, type NodeProps } from '@xyflow/react';

export function SkillNode({ data, selected }: NodeProps<SkillNodeData>) {
  return (
    <div className={`skill-node${data.isCat ? ' cat' : ''}${selected ? ' selected' : ''}`}>
      {data.label}
      <Handle type="source" position={Position.Bottom} />
      <Handle type="target" position={Position.Top} />
    </div>
  );
}
```

## Category Color Map

Keep as a `Record<string, string>` constant in App.tsx or SkillNode.tsx:
```tsx
const COLORS: Record<string, string> = {
  "AI-Agents":"#FF6B6B", "Creative-Tools":"#4ECDC4",
  // ... 15+ categories
};
```

## Pitfalls

1. **tsconfig strict mode errors**: Set `"strict": false, "noUnusedLocals": false, "noUnusedParameters": false` in tsconfig to avoid TS compilation errors on express-style code.
2. **npm install timeout in Termux**: Use `terminal(background=true, notify_on_complete=true)` with `process(action='wait', timeout=300)`.
3. **Port already in use**: Port 5173 is default. Use `--port 5174` to override. Kill old process with `pkill -f "vite"` if needed.
4. **No hot reload on Android**: Termux doesn't support file watchers well. Edit files, save, and manually refresh browser.
5. **TypeScript LSP errors in Zeline**: `Cannot find module '@xyflow/react'` shows until `npm install` completes. Safe to ignore.
