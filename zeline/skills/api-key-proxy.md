# Api Key Proxy

> Build lightweight local API proxies with auto-rotating API keys for LLM endpoints. http.server + httpx, zero external deps beyond httpx. Key rotation on 429/401/403, health endpoints, revive exhausted keys, Termux launcher with auto-restart.

Build a lightweight local proxy that manages multiple API keys for any OpenAI-compatible LLM endpoint. Auto-rotates when keys hit rate limits or credit exhaustion.

## Architecture

```
Client (curl/app) → localhost:PORT → Proxy (http.server + httpx) → Upstream API
                                        ├── Key rotation (round-robin)
                                        ├── Cooldown on 429
                                        ├── Mark dead on 401/403
                                        ├── /status endpoint
                                        └── /revive endpoint
```

## Why http.server + httpx

- **http.server** (stdlib) — avoids aiohttp content-type conflicts when forwarding requests. No framework dependency.
- **httpx** — reliable upstream client, handles streaming and timeouts well. Already commonly installed.
- No FastAPI/Flask needed for a simple transparent proxy.

## Key Rotation Logic

1. **Round-robin** through available keys
2. **429 (rate limit)** → cooldown N seconds, try next key
3. **401/403 (credit exhausted)** → mark key dead permanently
4. **Timeout (httpx.TimeoutException)** → cooldown N seconds, try next key. Timeout means quota likely exhausted for that model on that key.
5. **200 (success)** → revive key (reset cooldown, unmark dead)
6. **All keys dead/timed out** → return 503 with error message
7. **/revive** endpoint — reset all keys (for monthly quota resets)

## Project Structure

```
~/proxy/
├── config.json       # API keys, model, port, rotation settings
├── proxy.py          # The proxy server
└── run.sh            # Launcher (start/stop/status/restart/watch)
```

## Config Template (`config.json`)

```json
{
  "api_keys": ["key1", "key2"],
  "default_model": "model-name",
  "listen_host": "127.0.0.1",
  "listen_port": 18080,
  "nvidia_base_url": "https://integrate.api.nvidia.com/v1",
  "rotation": {
    "cooldown_seconds": 60,
    "credit_exhausted_codes": [401, 403],
    "max_retries": 2,
    "rate_limit_codes": [429]
  }
}
```

## Pitfalls

- **Double /v1 prefix**: If `nvidia_base_url` already has `/v1`, strip `/v1` from incoming request paths before forwarding.
- **Content-Type conflicts**: aiohttp's `ClientSession` with `data=bytes` + manual `Content-Type` header causes "passing both Content-Type header and content_type or charset params is forbidden". Use httpx instead.
- **Port already in use**: Old process may linger. Use `fuser -k PORT/tcp` before restarting.
- **Background process**: Use `run.sh start` (nohup) or `run.sh watch` (foreground with auto-restart).
- **Model not found**: Default model in config may not exist on the upstream provider. Always verify available models via `/v1/models`.

## Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/status` | GET | Key states: available count, cooldown remaining, exhausted status |
| `/revive` | POST | Reset all exhausted/cooldown keys |
| `/*` | ANY | Forwarded to upstream with key rotation |

## Launcher (`run.sh`)

```bash
./run.sh start      # Background with nohup
./run.sh stop       # Kill by PID
./run.sh status     # Check if running + /status
./run.sh restart    # Stop + start
./run.sh watch      # Foreground with auto-restart on crash
```

## Verification

```bash
# Health check
curl http://localhost:PORT/status

# Chat completion
curl -X POST http://localhost:PORT/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"hi"}],"model":"model-name"}'

# Revive exhausted keys
curl -X POST http://localhost:PORT/revive
```


---

## Lampiran: `references/nvidia-nim-proxy.md`

# NVIDIA NIM Proxy — Session Reference

Built for Termux (Android 14) using Python 3.13 stdlib + httpx.

## Config

- **Port**: 18080
- **Keys**: 2 NVIDIA NIM API keys
- **Default model**: `z-ai/glm-5.2` (listed in NVIDIA catalog but often times out — quota exhausted per-key, not model unavailable)
- **Timeout handling**: httpx timeout (60s total, 15s connect) triggers cooldown + rotation to next key, same as 429
- **Available models on NVIDIA NIM** (partial list, 121 total):
  - `meta/llama-3.1-8b-instruct`
  - `meta/llama-3.1-70b-instruct`
  - `meta/llama-3.3-70b-instruct`
  - `meta/llama-4-maverick-17b-128e-instruct`
  - `nvidia/llama-3.3-nemotron-super-49b-v1`
  - `nvidia/llama-3.3-nemotron-super-49b-v1.5`
  - `nvidia/llama-3.1-nemotron-70b-instruct`
  - `nvidia/llama-3.1-nemotron-ultra-253b-v1`
  - `mistralai/mistral-nemotron`
  - `z-ai/glm-5.2` (listed, but times out when quota exhausted)

## Key Rotation Behavior

| Status | Action |
|---|---|
| 200 | Revive key (reset cooldown + unmark exhausted) |
| 429 | Cooldown 60s, try next key |
| 401/403 | Mark exhausted permanently |
| Timeout | Cooldown 60s, try next key (same as 429) |
| All dead/timed out | Return 503 |

## Debugging Journey

1. **aiohttp → httpx**: aiohttp's `ClientSession.request()` with `data=bytes` + manual `Content-Type` header throws "passing both Content-Type header and content_type or charset params is forbidden". Switched to http.server (stdlib) + httpx upstream.

2. **Double /v1 prefix**: NVIDIA base URL `https://integrate.api.nvidia.com/v1` already has `/v1`. Client sends `/v1/chat/completions`. Without stripping, target becomes `.../v1/v1/chat/completions` → 404. Fix: strip `/v1` from incoming path before forwarding.

3. **Old process lingering**: After killing proxy, `fuser -k 18080/tcp` sometimes doesn't work due to `/proc` permissions on Termux. Use `pkill -f proxy.py` or `kill -9 $(pgrep -f proxy.py)`.

4. **http.server 404 confusion**: When NVIDIA returns 404 (e.g., wrong path or auth issue), the response includes both the proxy's `Server: BaseHTTP/0.6 Python/3.13.13` header AND NVIDIA's headers (`vary: Origin`, `x-content-type-options: nosniff`). This can make it look like the proxy itself is returning 404.

5. **Timeout = quota exhaustion**: On NVIDIA NIM free tier, hitting monthly quota makes the model timeout rather than return a proper HTTP error. Handle `httpx.TimeoutException` as a rotation signal (cooldown + next key), not a hard failure. Use shorter timeouts (60s total, 15s connect) so rotation happens fast.

## File Locations

```
~/nvidia-proxy/
├── config.json
├── proxy.py          # 224 lines
└── run.sh            # Launcher script
```

## Usage

```bash
cd ~/nvidia-proxy
./run.sh start        # Background
./run.sh status       # Check + curl /status
./run.sh watch        # Foreground + auto-restart
```
