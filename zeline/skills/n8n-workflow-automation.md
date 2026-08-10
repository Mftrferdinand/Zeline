# N8N Workflow Automation

> Create, import, and manage n8n workflows. Covers workflow structure (nodes, connections, credentials), integration patterns (Telegram, PostgreSQL, Ollama, Google Sheets), and troubleshooting for self-hosted and cloud n8n.

n8n is a workflow automation platform (self-hosted or cloud). Workflows are JSON files with nodes and connections.

## Workflow Structure

A workflow JSON has two top-level keys:

- `"nodes"`: array of node objects
- `"connections"`: maps each source node to its targets

### Node Structure

```json
{
  "id": "unique-id",
  "name": "Display Name",
  "type": "n8n-nodes-base.webhook",
  "typeVersion": 1,
  "position": [250, 300],
  "parameters": {
    "path": "my-webhook",
    "options": {}
  },
  "credentials": {
    "telegramApi": "{{ $credentials.telegramApi }}"
  }
}
```

### Connection Structure

```json
{
  "Webhook Receive": {
    "main": [[ { "node": "Next Node", "type": "main", "index": 0 } ]]
  }
}
```

To split output to multiple targets:

```json
{
  "Parse Node": {
    "main": [[ { "node": "Target A", "type": "main", "index": 0 }, { "node": "Target B", "type": "main", "index": 1 } ]]
  }
}
```

For switch nodes, use separate arrays:

```json
{
  "Route by Action": {
    "main": [
      [ { "node": "Case 0", "type": "main", "index": 0 } ],
      [ { "node": "Case 1", "type": "main", "index": 0 } ],
      [ { "node": "Case 2", "type": "main", "index": 0 } ]
    ]
  }
}
```

## Common Node Types

| Type | Usage |
|---|---|
| `n8n-nodes-base.webhook` | Receive external HTTP requests |
| `n8n-nodes-base.code` | JavaScript transformation |
| `n8n-nodes-base.switch` | Route by value |
| `n8n-nodes-base.httpRequest` | Call external APIs |
| `n8n-nodes-base.telegram` | Send Telegram messages |
| `n8n-nodes-base.postgres` | Database operations |
| `n8n-nodes-base.googleSheets` | Spreadsheet operations |

## Common Integration Patterns

### Telegram Message Format

```javascript
const data = $input.first().json;
const msg = `📊 *${data.asset} Analysis*\n\n` +
  `💰 Price: $${data.price}\n` +
  `📈 Trend: ${data.trend}\n\n` +
  `⚠️ PROTECT YOUR CAPITAL, MANAGE YOUR RISK, USE SL`;
return { message: msg };
```

`parse_mode: "Markdown"` in additionalFields.

### Ollama AI (Local)

```json
{
  "method": "POST",
  "url": "http://localhost:11434/api/generate",
  "sendBody": true,
  "bodyParameters": {
    "parameters": [
      { "name": "model", "value": "llama3.2" },
      { "name": "prompt", "value": "={{ $json.text }}" },
      { "name": "stream", "value": false }
    ]
  }
}
```

### PostgreSQL Insert

```json
{
  "operation": "insert",
  "schema": "public",
  "table": "trades",
  "columns": {
    "id": "={{ $json.id }}",
    "asset": "={{ $json.asset }}"
  }
}
```

## JavaScript Expression Syntax

- Access input: `$input.first().json` (single) or `$input.all()` (array)
- Expressions in parameters: `={{ $json.asset }}`
- Current time: `$now.toISOString()`
- Conditional: `$json.result === 'TP1' ? 'win' : 'lose'`

## Import Workflow

1. Open n8n (cloud or local)
2. Workflows → Import from File
3. Select `.json` file

Or via API (requires API key):

```bash
curl -X POST "https://your-instance.n8n.cloud/api/v1/workflows" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d @workflow.json
```

## Credentials Setup

After import, each node may need credentials:
- **Telegram**: Bot token from @BotFather
- **PostgreSQL**: DB host, port, user, password, database
- **Google Sheets**: OAuth login
- **n8n API Key**: Settings → API Keys → Create

## Troubleshooting

- Workflow not firing → check **Active** toggle
- Webhook not receiving → check URL path, POST method
- Node errors → check credential binding, data format in expressions
- `$json` undefined → the previous node didn't send the expected structure

## Triggers

User asks about: n8n, workflow automation, importing workflow, setting up Telegram bot, auto-trade recording, market analysis workflow.



---

## Lampiran: `templates/n8n-zeline.json`

```json
{
  "name": "Zeline AI Agent",
  "nodes": [
    {
      "id": "webhook-3",
      "name": "Incoming Request",
      "type": "n8n-nodes-base.webhook",
      "typeVersion": 1,
      "position": [250, 300],
      "parameters": { "path": "zeline-ai", "options": {} }
    },
    {
      "id": "switch-3",
      "name": "Route by Action",
      "type": "n8n-nodes-base.switch",
      "typeVersion": 2,
      "position": [450, 300],
      "parameters": {
        "dataType": "string",
        "value1": "={{ $json.action }}",
        "rules": [{ "value2": "analyze", "output": 0 }, { "value2": "track", "output": 1 }, { "value2": "summarize", "output": 2 }]
      }
    },
    {
      "id": "http-3a",
      "name": "Ollama (Analyze)",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4,
      "position": [650, 200],
      "parameters": {
        "method": "POST",
        "url": "http://localhost:11434/api/generate",
        "authentication": "none",
        "sendBody": true,
        "bodyParameters": {
          "parameters": [
            { "name": "model", "value": "llama3.2" },
            { "name": "prompt", "value": "={{ `Analyze this market: ${JSON.stringify($json)}` }}" },
            { "name": "stream", "value": false }
          ]
        }
      }
    },
    {
      "id": "http-3b",
      "name": "Ollama (Summarize)",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4,
      "position": [650, 500],
      "parameters": {
        "method": "POST",
        "url": "http://localhost:11434/api/generate",
        "authentication": "none",
        "sendBody": true,
        "bodyParameters": {
          "parameters": [
            { "name": "model", "value": "llama3.2" },
            { "name": "prompt", "value": "={{ `Summarize in 3 bullets: ${$json.text}` }}" },
            { "name": "stream", "value": false }
          ]
        }
      }
    },
    {
      "id": "code-4",
      "name": "Parse Response",
      "type": "n8n-nodes-base.code",
      "typeVersion": 2,
      "position": [850, 300],
      "parameters": {
        "language": "javascript",
        "code": "const raw = $input.first().json;\nreturn { summary: (raw.response || '').slice(0, 500), timestamp: $now.toISOString() };"
      }
    },
    {
      "id": "telegram-3",
      "name": "Send Result",
      "type": "n8n-nodes-base.telegram",
      "typeVersion": 1,
      "position": [1050, 300],
      "parameters": {
        "resource": "message",
        "chatId": "={{ $json.chatId || '<OWNER_CHAT_ID>' }}",
        "text": "={{ $json.summary }}",
        "additionalFields": { "parse_mode": "Markdown" }
      },
      "credentials": { "telegramApi": "{{ $credentials.telegramApi }}" }
    }
  ],
  "connections": {
    "Incoming Request": { "main": [[ { "node": "Route by Action", "type": "main", "index": 0 } ]] },
    "Route by Action": {
      "main": [
        [ { "node": "Ollama (Analyze)", "type": "main", "index": 0 } ],
        [ { "node": "Parse Response", "type": "main", "index": 0 } ],
        [ { "node": "Ollama (Summarize)", "type": "main", "index": 0 } ]
      ]
    },
    "Ollama (Analyze)": { "main": [[ { "node": "Parse Response", "type": "main", "index": 0 } ]] },
    "Ollama (Summarize)": { "main": [[ { "node": "Parse Response", "type": "main", "index": 0 } ]] },
    "Parse Response": { "main": [[ { "node": "Send Result", "type": "main", "index": 0 } ]] }
  }
}

```


---

## Lampiran: `templates/n8n-market-analysis.json`

```json
{
  "name": "Market Analysis → Telegram",
  "nodes": [
    {
      "id": "webhook-1",
      "name": "Webhook Receive",
      "type": "n8n-nodes-base.webhook",
      "typeVersion": 1,
      "position": [250, 300],
      "parameters": { "path": "market-analysis", "options": {} }
    },
    {
      "id": "code-1",
      "name": "Format Analysis",
      "type": "n8n-nodes-base.code",
      "typeVersion": 2,
      "position": [450, 300],
      "parameters": {
        "language": "javascript",
        "code": "const data = $input.first().json;\nconst asset = data.asset || 'BTC';\nconst price = data.price || 'N/A';\nconst trend = data.trend || 'Neutral';\nconst entry = data.entry || '-';\nconst tp = data.takeProfit || '-';\nconst sl = data.stopLoss || '-';\nconst fundamental = data.fundamental || '-';\nconst msg = `📊 *${asset} Analysis*\\n\\n💰 Price: $${price}\\n📈 Trend: ${trend}\\n\\n🔵 *Entry:* ${entry}\\n🟢 TP: ${tp}\\n🔴 SL: ${sl}\\n\\n📰 *Fundamental:*\\n${fundamental}\\n\\n⚠️ PROTECT YOUR CAPITAL, MANAGE YOUR RISK, USE SL`;\nreturn { message: msg, asset, price, trend };"
      }
    },
    {
      "id": "telegram-1",
      "name": "Send Telegram",
      "type": "n8n-nodes-base.telegram",
      "typeVersion": 1,
      "position": [650, 300],
      "parameters": {
        "resource": "message",
        "chatId": "={{ $json.chatId || '-1002052178219' }}",
        "text": "={{ $json.message }}",
        "additionalFields": { "parse_mode": "Markdown" }
      },
      "credentials": { "telegramApi": "{{ $credentials.telegramApi }}" }
    }
  ],
  "connections": {
    "Webhook Receive": { "main": [[ { "node": "Format Analysis", "type": "main", "index": 0 } ]] },
    "Format Analysis": { "main": [[ { "node": "Send Telegram", "type": "main", "index": 0 } ]] }
  }
}

```


---

## Lampiran: `templates/n8n-trade-tracker.json`

```json
{
  "name": "Trade Data Tracker",
  "nodes": [
    {
      "id": "webhook-2",
      "name": "Trade Signal Webhook",
      "type": "n8n-nodes-base.webhook",
      "typeVersion": 1,
      "position": [250, 300],
      "parameters": { "path": "trade-signal", "options": {} }
    },
    {
      "id": "code-2",
      "name": "Parse & Validate",
      "type": "n8n-nodes-base.code",
      "typeVersion": 2,
      "position": [450, 300],
      "parameters": {
        "language": "javascript",
        "code": "const data = $input.first().json;\nconst trade = {\n  id: $json.id || Date.now().toString(),\n  date: $json.date || $now.toISOString().split('T')[0],\n  asset: ($json.asset || 'XAUUSD').toUpperCase(),\n  direction: ($json.direction || 'BUY').toUpperCase(),\n  entry: parseFloat($json.entry) || 0,\n  tp1: parseFloat($json.tp1) || 0,\n  tp2: parseFloat($json.tp2) || 0,\n  sl: parseFloat($json.sl) || 0,\n  lotSize: parseFloat($json.lotSize) || 0.01,\n  result: ($json.result || 'PENDING').toUpperCase(),\n  profit: parseFloat($json.profit) || 0,\n  notes: $json.notes || '',\n  timestamp: $now.toISOString()\n};\nif (trade.entry && trade.sl && trade.tp1) {\n  const risk = Math.abs(trade.entry - trade.sl);\n  const reward = Math.abs(trade.tp1 - trade.entry);\n  trade.rrRatio = risk > 0 ? (reward / risk).toFixed(2) : 0;\n}\nreturn trade;"
      }
    },
    {
      "id": "telegram-2",
      "name": "Trade Notif",
      "type": "n8n-nodes-base.telegram",
      "typeVersion": 1,
      "position": [650, 300],
      "parameters": {
        "resource": "message",
        "chatId": "={{ $json.chatId || '<OWNER_CHAT_ID>' }}",
        "text": "={{ `🛒 *Trade ${$json.direction} ${$json.asset}*\\nEntry: $${$json.entry}\\nTP1: $${$json.tp1} | TP2: $${$json.tp2}\\nSL: $${$json.sl}\\nLot: ${$json.lotSize} | R:R ${$json.rrRatio}\\nStatus: ${$json.result}` }}",
        "additionalFields": { "parse_mode": "Markdown" }
      },
      "credentials": { "telegramApi": "{{ $credentials.telegramApi }}" }
    }
  ],
  "connections": {
    "Trade Signal Webhook": { "main": [[ { "node": "Parse & Validate", "type": "main", "index": 0 } ]] },
    "Parse & Validate": { "main": [[ { "node": "Trade Notif", "type": "main", "index": 0 } ]] }
  }
}

```
