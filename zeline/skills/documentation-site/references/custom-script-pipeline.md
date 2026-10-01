# Custom Script Pipeline for MkDocs Sites

When a MkDocs project uses **custom Python scripts** that write directly to `site/` (bypassing `mkdocs build`), running `mkdocs serve` will **destroy** those changes because it cleans `site/` and rebuilds from `docs/`.

## Detecting the Pattern

Look for these signs in the project directory:

- Scripts like `expand_docs_v32.py`, `translate_id_v32.py`, `add_authoritative_v33.py`, `add_parity_v34.py`
- A `no_cache_server.py` that serves `site/` directly
- `site/` files that differ significantly from what `mkdocs build` would produce from `docs/`
- `site/` file timestamps newer than `docs/` files

## The Problem

```bash
mkdocs serve -a 0.0.0.0:8089
# ↑ This CLEANS site/ and rebuilds from docs/ → custom script changes are lost
```

MkDocs serve output shows:
```
INFO    -  Cleaning site directory
INFO    -  Documentation built in 1.82 seconds
```

After this, the server serves the OLD version from `docs/`, not the user's improved version.

## The Fix

### 1. Kill the mkdocs serve process

```bash
kill <PID>  # Find PID with: ps aux | grep 8089
```

### 2. Use the custom static server instead

```python
# no_cache_server.py
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from functools import partial

class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

if __name__ == '__main__':
    handler = partial(NoCacheHandler, directory='/path/to/zeline-guide/site')
    ThreadingHTTPServer(('127.0.0.1', 8089), handler).serve_forever()
```

### 3. Start in background

```bash
cd ~/zeline-guide && python3 no_cache_server.py
```

### 4. Verify

```bash
curl -s http://127.0.0.1:8089 | head -c 200
# Should show the custom-generated version, not mkdocs output
```

## Re-running the Custom Scripts

If `site/` was already cleaned by mkdocs, re-run the scripts in order:

```bash
cd ~/zeline-guide
source venv/bin/activate
python3 expand_docs_v32.py        # Generate EN pages
python3 translate_id_v32.py       # Translate to ID
python3 add_authoritative_v33.py  # Add authoritative content
python3 add_parity_v34.py         # Add parity/feature content
```

## Health Check

| Check | Command | Expected |
|-------|---------|----------|
| Server running | `curl -sI http://localhost:8089` | `200 OK` |
| Custom version | `curl -s http://localhost:8089 \| grep '<title>'` | Custom title, not "ZELINE Guide" |
| ID version | `curl -s http://localhost:8089/id/ \| head -c 200` | `<html lang="id">` |
| No mkdocs leftover | `ps aux \| grep mkdocs` | Empty (no running mkdocs process) |