# MCP Server Development Guide

> Build high-quality MCP (Model Context Protocol) servers that let LLMs interact with external services through well-designed tools, in Python (FastMCP) or Node/TypeScript (MCP SDK).

Use this skill when building MCP servers to integrate external APIs or services. An MCP server provides tools that allow LLMs to access external services and APIs. Quality is measured by how well it enables LLMs to accomplish real-world tasks with the tools provided.

## High-Level Workflow

Four phases: research/planning → implementation → review → evaluation.

### Phase 1: Deep Research and Planning

#### 1.1 Agent-Centric Design Principles

**Build for workflows, not just API endpoints:**
- Don't simply wrap existing endpoints — build thoughtful, high-impact workflow tools
- Consolidate related operations (e.g. `schedule_event` that both checks availability and creates the event)
- Focus on tools that enable complete tasks, not individual API calls

**Optimize for limited context:**
- Agents have constrained context windows — make every token count
- Return high-signal information, not exhaustive data dumps
- Provide "concise" vs "detailed" response format options
- Default to human-readable identifiers over technical codes (names over IDs)

**Design actionable error messages:**
- Guide agents toward correct usage: "Try using filter='active_only' to reduce results"
- Make errors educational, not just diagnostic

**Follow natural task subdivisions:**
- Tool names should reflect how humans think about tasks
- Group related tools with consistent prefixes for discoverability

**Use evaluation-driven development:**
- Create realistic evaluation scenarios early
- Let agent feedback drive tool improvements; prototype quickly and iterate

#### 1.2 Study the MCP protocol

Fetch the latest MCP protocol documentation with your web-fetch tool: `https://modelcontextprotocol.io/llms-full.txt` — the complete MCP specification and guidelines.

#### 1.3 Study the SDK you'll use

- **Python SDK:** `https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/main/README.md`
- **TypeScript SDK:** `https://raw.githubusercontent.com/modelcontextprotocol/typescript-sdk/main/README.md`

#### 1.4 Exhaustively study the target API

Read through ALL available API docs before integrating: reference docs, auth requirements, rate limiting, pagination patterns, error responses/status codes, endpoints and parameters, data models. Use web search + fetch as needed.

#### 1.5 Create a comprehensive implementation plan

- **Tool selection:** list the most valuable operations; prioritize common/important use cases; consider which tools combine into workflows
- **Shared utilities:** API request helpers, pagination helpers, filtering/formatting utilities, error-handling strategy
- **Input/output design:** validation models (Pydantic for Python, Zod for TypeScript), consistent response formats (JSON or Markdown) with configurable detail levels, character limits and truncation (e.g. 25,000 tokens)
- **Error handling:** graceful failure modes, clear actionable natural-language messages, rate-limit/timeout handling, auth errors

### Phase 2: Implementation

#### 2.1 Project structure

- **Python:** a single `.py` file, or modules if complex; use the MCP Python SDK for tool registration; define Pydantic models for input validation
- **Node/TypeScript:** proper project structure, `package.json` + `tsconfig.json`, MCP TypeScript SDK, Zod schemas for validation

#### 2.2 Core infrastructure first

Build shared utilities before tools: API request helpers, error-handling utilities, response formatters (JSON + Markdown), pagination helpers, auth/token management.

#### 2.3 Implement tools systematically

For each tool:

- **Input schema:** Pydantic (Python) or Zod (TypeScript) with proper constraints (min/max length, regex, ranges); clear field descriptions with examples
- **Docstrings/descriptions:** one-line summary; detailed purpose; explicit parameter types with examples; complete return schema; when-to-use / when-not-to-use; error-handling guidance
- **Tool logic:** reuse shared utilities; async/await for all I/O; proper error handling; support multiple response formats; respect pagination; enforce character limits
- **Annotations:** `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint` as appropriate

#### 2.4 Language-specific practices

- **Python:** MCP Python SDK with proper tool registration; Pydantic v2 with `model_config`; type hints throughout; async/await for I/O; module-level constants (`CHARACTER_LIMIT`, `API_BASE_URL`)
- **Node/TypeScript:** `server.registerTool`; Zod schemas with `.strict()`; TypeScript strict mode; no `any` types; explicit `Promise<T>` return types; `npm run build` configured

### Phase 3: Review and Refine

**Code-quality review:** DRY (no duplicated code), composability (shared logic in functions), consistency (similar operations → similar formats), error handling on all external calls, full type coverage, comprehensive docstrings.

**Test and build (IMPORTANT):** MCP servers are long-running processes waiting for requests over stdio or http. Running them directly in your main process will hang it. Safe ways to test:
- Run the server in tmux to keep it outside your main process
- Use a timeout: `timeout 5s python server.py`
- Python syntax check: `python -m py_compile your_server.py`
- Node: `npm run build` and verify `dist/index.js` is created

### Phase 4: Create Evaluations

Evaluations test whether LLMs can effectively use your server to answer realistic, complex questions.

Process: (1) inspect available tools, (2) explore data with read-only operations, (3) generate 10 complex realistic questions, (4) solve each yourself to verify.

Each question must be: **independent**, **read-only**, **complex** (multiple tool calls), **realistic**, **verifiable** (single clear answer), and **stable** (answer won't change over time).

Output format:

```xml
<evaluation>
  <qa_pair>
    <question>Find discussions about AI model launches with animal codenames. One model needed a safety designation in the format ASL-X. What number X was determined for the model named after a spotted wild cat?</question>
    <answer>3</answer>
  </qa_pair>
  <!-- More qa_pairs... -->
</evaluation>
```

## Reference URLs

- MCP protocol spec: `https://modelcontextprotocol.io/llms-full.txt`
- Python SDK: `https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/main/README.md`
- TypeScript SDK: `https://raw.githubusercontent.com/modelcontextprotocol/typescript-sdk/main/README.md`
