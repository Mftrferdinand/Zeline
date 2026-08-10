# Llama Cpp

> llama.cpp local GGUF inference + HF Hub model discovery.

Use this skill for local GGUF inference, quant selection, or Hugging Face repo discovery for llama.cpp.

## When to use

- Run local models on CPU, Apple Silicon, CUDA, ROCm, or Intel GPUs
- Find the right GGUF for a specific Hugging Face repo
- Build a `llama-server` or `llama-cli` command from the Hub
- Search the Hub for models that already support llama.cpp
- Enumerate available `.gguf` files and sizes for a repo
- Decide between Q4/Q5/Q6/IQ variants for the user's RAM or VRAM

## Model Discovery workflow

Prefer URL workflows before asking for `hf`, Python, or custom scripts.

1. Search for candidate repos on the Hub:
   - Base: `https://huggingface.co/models?apps=llama.cpp&sort=trending`
   - Add `search=<term>` for a model family
   - Add `num_parameters=min:0,max:24B` or similar when the user has size constraints
2. Open the repo with the llama.cpp local-app view:
   - `https://huggingface.co/<repo>?local-app=llama.cpp`
3. Treat the local-app snippet as the source of truth when it is visible:
   - copy the exact `llama-server` or `llama-cli` command
   - report the recommended quant exactly as HF shows it
4. Read the same `?local-app=llama.cpp` URL as page text or HTML and extract the section under `Hardware compatibility`:
   - prefer its exact quant labels and sizes over generic tables
   - keep repo-specific labels such as `UD-Q4_K_M` or `IQ4_NL_XL`
   - if that section is not visible in the fetched page source, say so and fall back to the tree API plus generic quant guidance
5. Query the tree API to confirm what actually exists:
   - `https://huggingface.co/api/models/<repo>/tree/main?recursive=true`
   - keep entries where `type` is `file` and `path` ends with `.gguf`
   - use `path` and `size` as the source of truth for filenames and byte sizes
   - separate quantized checkpoints from `mmproj-*.gguf` projector files and `BF16/` shard files
   - use `https://huggingface.co/<repo>/tree/main` only as a human fallback
6. If the local-app snippet is not text-visible, reconstruct the command from the repo plus the chosen quant:
   - shorthand quant selection: `llama-server -hf <repo>:<QUANT>`
   - exact-file fallback: `llama-server --hf-repo <repo> --hf-file <filename.gguf>`
7. Only suggest conversion from Transformers weights if the repo does not already expose GGUF files.

## Quick start

### Install llama.cpp

```bash
# macOS / Linux (simplest)
brew install llama.cpp
```

```bash
winget install llama.cpp
```

```bash
git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp
cmake -B build
cmake --build build --config Release
```

### Run directly from the Hugging Face Hub

```bash
llama-cli -hf bartowski/Llama-3.2-3B-Instruct-GGUF:Q8_0
```

```bash
llama-server -hf bartowski/Llama-3.2-3B-Instruct-GGUF:Q8_0
```

### Run an exact GGUF file from the Hub

Use this when the tree API shows custom file naming or the exact HF snippet is missing.

```bash
llama-server \
    --hf-repo microsoft/Phi-3-mini-4k-instruct-gguf \
    --hf-file Phi-3-mini-4k-instruct-q4.gguf \
    -c 4096
```

### OpenAI-compatible server check

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "Write a limerick about Python exceptions"}
    ]
  }'
```

## Python bindings (llama-cpp-python)

`pip install llama-cpp-python` (CUDA: `CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python --force-reinstall --no-cache-dir`; Metal: `CMAKE_ARGS="-DGGML_METAL=on" ...`).

### Basic generation

```python
from llama_cpp import Llama

llm = Llama(
    model_path="./model-q4_k_m.gguf",
    n_ctx=4096,
    n_gpu_layers=35,     # 0 for CPU, 99 to offload everything
    n_threads=8,
)

out = llm("What is machine learning?", max_tokens=256, temperature=0.7)
print(out["choices"][0]["text"])
```

### Chat + streaming

```python
llm = Llama(
    model_path="./model-q4_k_m.gguf",
    n_ctx=4096,
    n_gpu_layers=35,
    chat_format="llama-3",   # or "chatml", "mistral", etc.
)

resp = llm.create_chat_completion(
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is Python?"},
    ],
    max_tokens=256,
)
print(resp["choices"][0]["message"]["content"])

# Streaming
for chunk in llm("Explain quantum computing:", max_tokens=256, stream=True):
    print(chunk["choices"][0]["text"], end="", flush=True)
```

### Embeddings

```python
llm = Llama(model_path="./model-q4_k_m.gguf", embedding=True, n_gpu_layers=35)
vec = llm.embed("This is a test sentence.")
print(f"Embedding dimension: {len(vec)}")
```

You can also load a GGUF straight from the Hub:

```python
llm = Llama.from_pretrained(
    repo_id="bartowski/Llama-3.2-3B-Instruct-GGUF",
    filename="*Q4_K_M.gguf",
    n_gpu_layers=35,
)
```

## Choosing a quant

Use the Hub page first, generic heuristics second.

- Prefer the exact quant that HF marks as compatible for the user's hardware profile.
- For general chat, start with `Q4_K_M`.
- For code or technical work, prefer `Q5_K_M` or `Q6_K` if memory allows.
- For very tight RAM budgets, consider `Q3_K_M`, `IQ` variants, or `Q2` variants only if the user explicitly prioritizes fit over quality.
- For multimodal repos, mention `mmproj-*.gguf` separately. The projector is not the main model file.
- Do not normalize repo-native labels. If the page says `UD-Q4_K_M`, report `UD-Q4_K_M`.

## Extracting available GGUFs from a repo

When the user asks what GGUFs exist, return:

- filename
- file size
- quant label
- whether it is a main model or an auxiliary projector

Ignore unless requested:

- README
- BF16 shard files
- imatrix blobs or calibration artifacts

Use the tree API for this step:

- `https://huggingface.co/api/models/<repo>/tree/main?recursive=true`

For a repo like `unsloth/Qwen3.6-35B-A3B-GGUF`, the local-app page can show quant chips such as `UD-Q4_K_M`, `UD-Q5_K_M`, `UD-Q6_K`, and `Q8_0`, while the tree API exposes exact file paths such as `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf` and `Qwen3.6-35B-A3B-Q8_0.gguf` with byte sizes. Use the tree API to turn a quant label into an exact filename.

## Search patterns

Use these URL shapes directly:

```text
https://huggingface.co/models?apps=llama.cpp&sort=trending
https://huggingface.co/models?search=<term>&apps=llama.cpp&sort=trending
https://huggingface.co/models?search=<term>&apps=llama.cpp&num_parameters=min:0,max:24B&sort=trending
https://huggingface.co/<repo>?local-app=llama.cpp
https://huggingface.co/api/models/<repo>/tree/main?recursive=true
https://huggingface.co/<repo>/tree/main
```

## Output format

When answering discovery requests, prefer a compact structured result like:

```text
Repo: <repo>
Recommended quant from HF: <label> (<size>)
llama-server: <command>
Other GGUFs:
- <filename> - <size>
- <filename> - <size>
Source URLs:
- <local-app URL>
- <tree API URL>
```

## References

- **[hub-discovery.md](references/hub-discovery.md)** - URL-only Hugging Face workflows, search patterns, GGUF extraction, and command reconstruction
- **[advanced-usage.md](references/advanced-usage.md)** — speculative decoding, batched inference, grammar-constrained generation, LoRA, multi-GPU, custom builds, benchmark scripts
- **[quantization.md](references/quantization.md)** — quant quality tradeoffs, when to use Q4/Q5/Q6/IQ, model size scaling, imatrix
- **[server.md](references/server.md)** — direct-from-Hub server launch, OpenAI API endpoints, Docker deployment, NGINX load balancing, monitoring
- **[optimization.md](references/optimization.md)** — CPU threading, BLAS, GPU offload heuristics, batch tuning, benchmarks
- **[troubleshooting.md](references/troubleshooting.md)** — install/convert/quantize/inference/server issues, Apple Silicon, debugging

## Resources

- **GitHub**: https://github.com/ggml-org/llama.cpp
- **Hugging Face GGUF + llama.cpp docs**: https://huggingface.co/docs/hub/gguf-llamacpp
- **Hugging Face Local Apps docs**: https://huggingface.co/docs/hub/main/local-apps
- **Hugging Face Local Agents docs**: https://huggingface.co/docs/hub/agents-local
- **Example local-app page**: https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF?local-app=llama.cpp
- **Example tree API**: https://huggingface.co/api/models/unsloth/Qwen3.6-35B-A3B-GGUF/tree/main?recursive=true
- **Example llama.cpp search**: https://huggingface.co/models?num_parameters=min:0,max:24B&apps=llama.cpp&sort=trending
- **License**: MIT


---

## Lampiran: `references/advanced-usage.md`

# GGUF Advanced Usage Guide

## Speculative Decoding

### Draft Model Approach

```bash
# Use smaller model as draft for faster generation
./llama-speculative \
    -m large-model-q4_k_m.gguf \
    -md draft-model-q4_k_m.gguf \
    -p "Write a story about AI" \
    -n 500 \
    --draft 8  # Draft tokens before verification
```

### Self-Speculative Decoding

```bash
# Use same model with different context for speculation
./llama-cli -m model-q4_k_m.gguf \
    --lookup-cache-static lookup.bin \
    --lookup-cache-dynamic lookup-dynamic.bin \
    -p "Hello world"
```

## Batched Inference

### Process Multiple Prompts

```python
from llama_cpp import Llama

llm = Llama(
    model_path="model-q4_k_m.gguf",
    n_ctx=4096,
    n_gpu_layers=35,
    n_batch=512  # Larger batch for parallel processing
)

prompts = [
    "What is Python?",
    "Explain machine learning.",
    "Describe neural networks."
]

# Process in batch (each prompt gets separate context)
for prompt in prompts:
    output = llm(prompt, max_tokens=100)
    print(f"Q: {prompt}")
    print(f"A: {output['choices'][0]['text']}\n")
```

### Server Batching

```bash
# Start server with batching
./llama-server -m model-q4_k_m.gguf \
    --host 0.0.0.0 \
    --port 8080 \
    -ngl 35 \
    -c 4096 \
    --parallel 4        # Concurrent requests
    --cont-batching     # Continuous batching
```

## Custom Model Conversion

### Convert with Vocabulary Modifications

```python
# custom_convert.py
import sys
sys.path.insert(0, './llama.cpp')

from convert_hf_to_gguf import main
from gguf import GGUFWriter

# Custom conversion with modified vocab
def convert_with_custom_vocab(model_path, output_path):
    # Load and modify tokenizer
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(model_path)

    # Add special tokens if needed
    special_tokens = {"additional_special_tokens": ["<|custom|>"]}
    tokenizer.add_special_tokens(special_tokens)
    tokenizer.save_pretrained(model_path)

    # Then run standard conversion
    main([model_path, "--outfile", output_path])
```

### Convert Specific Architecture

```bash
# For Mistral-style models
python convert_hf_to_gguf.py ./mistral-model \
    --outfile mistral-f16.gguf \
    --outtype f16

# For Qwen models
python convert_hf_to_gguf.py ./qwen-model \
    --outfile qwen-f16.gguf \
    --outtype f16

# For Phi models
python convert_hf_to_gguf.py ./phi-model \
    --outfile phi-f16.gguf \
    --outtype f16
```

## Advanced Quantization

### Mixed Quantization

```bash
# Quantize different layer types differently
./llama-quantize model-f16.gguf model-mixed.gguf Q4_K_M \
    --allow-requantize \
    --leave-output-tensor
```

### Quantization with Token Embeddings

```bash
# Keep embeddings at higher precision
./llama-quantize model-f16.gguf model-q4.gguf Q4_K_M \
    --token-embedding-type f16
```

### IQ Quantization (Importance-aware)

```bash
# Ultra-low bit quantization with importance
./llama-quantize --imatrix model.imatrix \
    model-f16.gguf model-iq2_xxs.gguf IQ2_XXS

# Available IQ types: IQ2_XXS, IQ2_XS, IQ2_S, IQ3_XXS, IQ3_XS, IQ3_S, IQ4_XS
```

## Memory Optimization

### Memory Mapping

```python
from llama_cpp import Llama

# Use memory mapping for large models
llm = Llama(
    model_path="model-q4_k_m.gguf",
    use_mmap=True,       # Memory map the model
    use_mlock=False,     # Don't lock in RAM
    n_gpu_layers=35
)
```

### Partial GPU Offload

```python
# Calculate layers to offload based on VRAM
import subprocess

def get_free_vram_gb():
    result = subprocess.run(
        ['nvidia-smi', '--query-gpu=memory.free', '--format=csv,nounits,noheader'],
        capture_output=True, text=True
    )
    return int(result.stdout.strip()) / 1024

# Estimate layers based on VRAM (rough: 0.5GB per layer for 7B Q4)
free_vram = get_free_vram_gb()
layers_to_offload = int(free_vram / 0.5)

llm = Llama(
    model_path="model-q4_k_m.gguf",
    n_gpu_layers=min(layers_to_offload, 35)  # Cap at total layers
)
```

### KV Cache Optimization

```python
from llama_cpp import Llama

# Optimize KV cache for long contexts
llm = Llama(
    model_path="model-q4_k_m.gguf",
    n_ctx=8192,          # Large context
    n_gpu_layers=35,
    type_k=1,            # Q8_0 for K cache (1)
    type_v=1,            # Q8_0 for V cache (1)
    # Or use Q4_0 (2) for more compression
)
```

## Context Management

### Context Shifting

```python
from llama_cpp import Llama

llm = Llama(
    model_path="model-q4_k_m.gguf",
    n_ctx=4096,
    n_gpu_layers=35
)

# Handle long conversations with context shifting
conversation = []
max_history = 10

def chat(user_message):
    conversation.append({"role": "user", "content": user_message})

    # Keep only recent history
    if len(conversation) > max_history * 2:
        conversation = conversation[-max_history * 2:]

    response = llm.create_chat_completion(
        messages=conversation,
        max_tokens=256
    )

    assistant_message = response["choices"][0]["message"]["content"]
    conversation.append({"role": "assistant", "content": assistant_message})
    return assistant_message
```

### Save and Load State

```bash
# Save state to file
./llama-cli -m model.gguf \
    -p "Once upon a time" \
    --save-session session.bin \
    -n 100

# Load and continue
./llama-cli -m model.gguf \
    --load-session session.bin \
    -p " and they lived" \
    -n 100
```

## Grammar Constrained Generation

### JSON Output

```python
from llama_cpp import Llama, LlamaGrammar

# Define JSON grammar
json_grammar = LlamaGrammar.from_string('''
root ::= object
object ::= "{" ws pair ("," ws pair)* "}" ws
pair ::= string ":" ws value
value ::= string | number | object | array | "true" | "false" | "null"
array ::= "[" ws value ("," ws value)* "]" ws
string ::= "\\"" [^"\\\\]* "\\""
number ::= [0-9]+
ws ::= [ \\t\\n]*
''')

llm = Llama(model_path="model-q4_k_m.gguf", n_gpu_layers=35)

output = llm(
    "Output a JSON object with name and age:",
    grammar=json_grammar,
    max_tokens=100
)
print(output["choices"][0]["text"])
```

### Custom Grammar

```python
# Grammar for specific format
answer_grammar = LlamaGrammar.from_string('''
root ::= "Answer: " letter "\\n" "Explanation: " explanation
letter ::= [A-D]
explanation ::= [a-zA-Z0-9 .,!?]+
''')

output = llm(
    "Q: What is 2+2? A) 3 B) 4 C) 5 D) 6",
    grammar=answer_grammar,
    max_tokens=100
)
```

## LoRA Integration

### Load LoRA Adapter

```bash
# Apply LoRA at runtime
./llama-cli -m base-model-q4_k_m.gguf \
    --lora lora-adapter.gguf \
    --lora-scale 1.0 \
    -p "Hello!"
```

### Multiple LoRA Adapters

```bash
# Stack multiple adapters
./llama-cli -m base-model.gguf \
    --lora adapter1.gguf --lora-scale 0.5 \
    --lora adapter2.gguf --lora-scale 0.5 \
    -p "Hello!"
```

### Python LoRA Usage

```python
from llama_cpp import Llama

llm = Llama(
    model_path="base-model-q4_k_m.gguf",
    lora_path="lora-adapter.gguf",
    lora_scale=1.0,
    n_gpu_layers=35
)
```

## Embedding Generation

### Extract Embeddings

```python
from llama_cpp import Llama

llm = Llama(
    model_path="model-q4_k_m.gguf",
    embedding=True,      # Enable embedding mode
    n_gpu_layers=35
)

# Get embeddings
embeddings = llm.embed("This is a test sentence.")
print(f"Embedding dimension: {len(embeddings)}")
```

### Batch Embeddings

```python
texts = [
    "Machine learning is fascinating.",
    "Deep learning uses neural networks.",
    "Python is a programming language."
]

embeddings = [llm.embed(text) for text in texts]

# Calculate similarity
import numpy as np

def cosine_similarity(a, b):
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

sim = cosine_similarity(embeddings[0], embeddings[1])
print(f"Similarity: {sim:.4f}")
```

## Performance Tuning

### Benchmark Script

```python
import time
from llama_cpp import Llama

def benchmark(model_path, prompt, n_tokens=100, n_runs=5):
    llm = Llama(
        model_path=model_path,
        n_gpu_layers=35,
        n_ctx=2048,
        verbose=False
    )

    # Warmup
    llm(prompt, max_tokens=10)

    # Benchmark
    times = []
    for _ in range(n_runs):
        start = time.time()
        output = llm(prompt, max_tokens=n_tokens)
        elapsed = time.time() - start
        times.append(elapsed)

    avg_time = sum(times) / len(times)
    tokens_per_sec = n_tokens / avg_time

    print(f"Model: {model_path}")
    print(f"Avg time: {avg_time:.2f}s")
    print(f"Tokens/sec: {tokens_per_sec:.1f}")

    return tokens_per_sec

# Compare quantizations
for quant in ["q4_k_m", "q5_k_m", "q8_0"]:
    benchmark(f"model-{quant}.gguf", "Explain quantum computing:", 100)
```

### Optimal Configuration Finder

```python
def find_optimal_config(model_path, target_vram_gb=8):
    """Find optimal n_gpu_layers and n_batch for target VRAM."""
    from llama_cpp import Llama
    import gc

    best_config = None
    best_speed = 0

    for n_gpu_layers in range(0, 50, 5):
        for n_batch in [128, 256, 512, 1024]:
            try:
                gc.collect()
                llm = Llama(
                    model_path=model_path,
                    n_gpu_layers=n_gpu_layers,
                    n_batch=n_batch,
                    n_ctx=2048,
                    verbose=False
                )

                # Quick benchmark
                start = time.time()
                llm("Hello", max_tokens=50)
                speed = 50 / (time.time() - start)

                if speed > best_speed:
                    best_speed = speed
                    best_config = {
                        "n_gpu_layers": n_gpu_layers,
                        "n_batch": n_batch,
                        "speed": speed
                    }

                del llm
                gc.collect()

            except Exception as e:
                print(f"OOM at layers={n_gpu_layers}, batch={n_batch}")
                break

    return best_config
```

## Multi-GPU Setup

### Distribute Across GPUs

```bash
# Split model across multiple GPUs
./llama-cli -m large-model.gguf \
    --tensor-split 0.5,0.5 \
    -ngl 60 \
    -p "Hello!"
```

### Python Multi-GPU

```python
import os
os.environ["CUDA_VISIBLE_DEVICES"] = "0,1"

from llama_cpp import Llama

llm = Llama(
    model_path="large-model-q4_k_m.gguf",
    n_gpu_layers=60,
    tensor_split=[0.5, 0.5]  # Split evenly across 2 GPUs
)
```

## Custom Builds

### Build with All Optimizations

```bash
# Clean build with all CPU optimizations
make clean
LLAMA_OPENBLAS=1 LLAMA_BLAS_VENDOR=OpenBLAS make -j

# With CUDA and cuBLAS
make clean
GGML_CUDA=1 LLAMA_CUBLAS=1 make -j

# With specific CUDA architecture
GGML_CUDA=1 CUDA_DOCKER_ARCH=sm_86 make -j
```

### CMake Build

```bash
mkdir build && cd build
cmake .. -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release
cmake --build . --config Release -j
```



---

## Lampiran: `references/hub-discovery.md`

# Hugging Face URL Workflows for llama.cpp

Use URL-only workflows first. Do not require `hf` or API clients just to find GGUF files, choose a quant, or build a `llama-server` command.

## Core URLs

```text
Search:
https://huggingface.co/models?apps=llama.cpp&sort=trending

Search with text:
https://huggingface.co/models?search=<term>&apps=llama.cpp&sort=trending

Search with size bounds:
https://huggingface.co/models?search=<term>&apps=llama.cpp&num_parameters=min:0,max:24B&sort=trending

Repo local-app view:
https://huggingface.co/<repo>?local-app=llama.cpp

Repo tree API:
https://huggingface.co/api/models/<repo>/tree/main?recursive=true

Repo file tree:
https://huggingface.co/<repo>/tree/main
```

## 1. Search for llama.cpp-compatible models

Start from the models page with `apps=llama.cpp`.

Use:

- `search=<term>` for model family names such as `Qwen`, `Gemma`, `Phi`, or `Mistral`
- `num_parameters=min:0,max:24B` or similar if the user has hardware limits
- `sort=trending` when the user wants popular repos right now

Do not start with random GGUF repos if the user has not chosen a model family yet. Search first, shortlist second.

Example: https://huggingface.co/models?search=Qwen&apps=llama.cpp&num_parameters=min:0,max:24B&sort=trending

## 2. Use the local-app page for the recommended quant

Open:

```text
https://huggingface.co/<repo>?local-app=llama.cpp
```

Extract, in order:

1. The exact `Use this model` snippet, if it is visible as text
2. The `Hardware compatibility` section from the fetched page text or HTML:
   - quant label
   - file size
   - bit-depth grouping
3. Any extra launch flags shown in the snippet, such as `--jinja`

Treat the HF local-app snippet as the source of truth when it is visible.

Do this by reading the URL itself, not by assuming the UI rendered in a browser. If the fetched page source does not expose `Hardware compatibility`, say that the section was not text-visible and fall back to the tree API plus generic guidance from `quantization.md`.

## 3. Confirm exact files from the tree API

Open:

```text
https://huggingface.co/api/models/<repo>/tree/main?recursive=true
```

Treat the JSON response as the source of truth for repo inventory.

Keep entries where:

- `type` is `file`
- `path` ends with `.gguf`

Use these fields:

- `path` for the filename and subdirectory
- `size` for the byte size
- optionally `lfs.size` to confirm the LFS payload size

Separate files into:

- quantized single-file checkpoints, for example `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`
- projector weights, usually `mmproj-*.gguf`
- BF16 shard files, usually under `BF16/`
- everything else

Ignore unless the user asks:

- `README.md`
- imatrix or calibration blobs

Use `https://huggingface.co/<repo>/tree/main` only as a human fallback if the API endpoint fails or the user wants the web view.

## 4. Build the command

Preferred order:

1. Copy the exact HF snippet from the local-app page
2. If the page gives a clean quant label, use shorthand selection:

```bash
llama-server -hf <repo>:<QUANT>
```

3. If you need an exact file from the tree API, use the file-specific form:

```bash
llama-server --hf-repo <repo> --hf-file <filename.gguf>
```

4. For CLI usage instead of a server, use:

```bash
llama-cli -hf <repo>:<QUANT>
```

Use the exact-file form when the repo uses custom labels or nonstandard naming that could make `:<QUANT>` ambiguous.

## 5. Example: `unsloth/Qwen3.6-35B-A3B-GGUF`

Use these URLs:

```text
https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF?local-app=llama.cpp
https://huggingface.co/api/models/unsloth/Qwen3.6-35B-A3B-GGUF/tree/main?recursive=true
https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/tree/main
```

On the local-app page, the hardware compatibility section can expose entries such as:

- `UD-IQ4_XS` - 17.7 GB
- `UD-Q4_K_S` - 20.9 GB
- `UD-Q4_K_M` - 22.1 GB
- `UD-Q5_K_M` - 26.5 GB
- `UD-Q6_K` - 29.3 GB
- `Q8_0` - 36.9 GB

On the tree API, you can confirm exact filenames such as:

- `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`
- `Qwen3.6-35B-A3B-UD-Q5_K_M.gguf`
- `Qwen3.6-35B-A3B-UD-Q6_K.gguf`
- `Qwen3.6-35B-A3B-Q8_0.gguf`
- `mmproj-F16.gguf`

Good final output for this repo:

```text
Repo: unsloth/Qwen3.6-35B-A3B-GGUF
Recommended quant from HF: UD-Q4_K_M (22.1 GB)
llama-server: llama-server --hf-repo unsloth/Qwen3.6-35B-A3B-GGUF --hf-file Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
Other GGUFs:
- Qwen3.6-35B-A3B-UD-Q5_K_M.gguf - 26.5 GB
- Qwen3.6-35B-A3B-UD-Q6_K.gguf - 29.3 GB
- Qwen3.6-35B-A3B-Q8_0.gguf - 36.9 GB
Projector:
- mmproj-F16.gguf - 899 MB
```

## Notes

- Repo-specific quant labels matter. Do not rewrite `UD-Q4_K_M` to `Q4_K_M` unless the page itself does.
- `mmproj` files are projector weights for multimodal models, not the main language model checkpoint.
- If the HF hardware compatibility panel is missing because the user has no hardware profile configured, or because the fetched page source did not expose it, still use the tree API plus generic quant guidance from `quantization.md`.
- If the repo already has GGUFs, do not jump straight to conversion workflows.



---

## Lampiran: `references/optimization.md`

# Performance Optimization Guide

Maximize llama.cpp inference speed and efficiency.

## CPU Optimization

### Thread tuning
```bash
# Set threads (default: physical cores)
./llama-cli -m model.gguf -t 8

# For AMD Ryzen 9 7950X (16 cores, 32 threads)
-t 16  # Best: physical cores

# Avoid hyperthreading (slower for matrix ops)
```

### BLAS acceleration
```bash
# OpenBLAS (faster matrix ops)
make LLAMA_OPENBLAS=1

# BLAS gives 2-3× speedup
```

## GPU Offloading

### Layer offloading
```bash
# Offload 35 layers to GPU (hybrid mode)
./llama-cli -m model.gguf -ngl 35

# Offload all layers
./llama-cli -m model.gguf -ngl 999

# Find optimal value:
# Start with -ngl 999
# If OOM, reduce by 5 until fits
```

### Memory usage
```bash
# Check VRAM usage
nvidia-smi dmon

# Reduce context if needed
./llama-cli -m model.gguf -c 2048  # 2K context instead of 4K
```

## Batch Processing

```bash
# Increase batch size for throughput
./llama-cli -m model.gguf -b 512  # Default: 512

# Physical batch (GPU)
--ubatch 128  # Process 128 tokens at once
```

## Context Management

```bash
# Default context (512 tokens)
-c 512

# Longer context (slower, more memory)
-c 4096

# Very long context (if model supports)
-c 32768
```

## Benchmarks

### CPU Performance (Llama 2-7B Q4_K_M)

| Setup | Speed | Notes |
|-------|-------|-------|
| Apple M3 Max | 50 tok/s | Metal acceleration |
| AMD 7950X (16c) | 35 tok/s | OpenBLAS |
| Intel i9-13900K | 30 tok/s | AVX2 |

### GPU Offloading (RTX 4090)

| Layers GPU | Speed | VRAM |
|------------|-------|------|
| 0 (CPU only) | 30 tok/s | 0 GB |
| 20 (hybrid) | 80 tok/s | 8 GB |
| 35 (all) | 120 tok/s | 12 GB |



---

## Lampiran: `references/quantization.md`

# GGUF Quantization Guide

Complete guide to GGUF quantization formats and model conversion.

## Hub-first quant selection

Before using generic tables, open the model repo with:

```text
https://huggingface.co/<repo>?local-app=llama.cpp
```

Prefer the exact quant labels and sizes shown in the `Hardware compatibility` section of the fetched `?local-app=llama.cpp` page text or HTML. Then confirm the matching filenames in:

```text
https://huggingface.co/api/models/<repo>/tree/main?recursive=true
```

Use the Hub page first, and only fall back to the generic heuristics below when the repo page does not expose a clear recommendation.

## Quantization Overview

**GGUF** (GPT-Generated Unified Format) - Standard format for llama.cpp models.

### Format Comparison

| Format | Perplexity | Size (7B) | Tokens/sec | Notes |
|--------|------------|-----------|------------|-------|
| FP16 | 5.9565 (baseline) | 13.0 GB | 15 tok/s | Original quality |
| Q8_0 | 5.9584 (+0.03%) | 7.0 GB | 25 tok/s | Nearly lossless |
| **Q6_K** | 5.9642 (+0.13%) | 5.5 GB | 30 tok/s | Best quality/size |
| **Q5_K_M** | 5.9796 (+0.39%) | 4.8 GB | 35 tok/s | Balanced |
| **Q4_K_M** | 6.0565 (+1.68%) | 4.1 GB | 40 tok/s | **Recommended** |
| Q4_K_S | 6.1125 (+2.62%) | 3.9 GB | 42 tok/s | Faster, lower quality |
| Q3_K_M | 6.3184 (+6.07%) | 3.3 GB | 45 tok/s | Small models only |
| Q2_K | 6.8673 (+15.3%) | 2.7 GB | 50 tok/s | Not recommended |

**Recommendation**: Use **Q4_K_M** for best balance of quality and speed.

## Converting Models

### Hugging Face to GGUF

```bash
# 1. Download Hugging Face model
hf download meta-llama/Llama-2-7b-chat-hf \
    --local-dir models/llama-2-7b-chat/

# 2. Convert to FP16 GGUF
python convert_hf_to_gguf.py \
    models/llama-2-7b-chat/ \
    --outtype f16 \
    --outfile models/llama-2-7b-chat-f16.gguf

# 3. Quantize to Q4_K_M
./llama-quantize \
    models/llama-2-7b-chat-f16.gguf \
    models/llama-2-7b-chat-Q4_K_M.gguf \
    Q4_K_M
```

### Batch quantization

```bash
# Quantize to multiple formats
for quant in Q4_K_M Q5_K_M Q6_K Q8_0; do
    ./llama-quantize \
        model-f16.gguf \
        model-${quant}.gguf \
        $quant
done
```

## K-Quantization Methods

**K-quants** use mixed precision for better quality:
- Attention weights: Higher precision
- Feed-forward weights: Lower precision

**Variants**:
- `_S` (Small): Faster, lower quality
- `_M` (Medium): Balanced (recommended)
- `_L` (Large): Better quality, larger size

**Example**: `Q4_K_M`
- `Q4`: 4-bit quantization
- `K`: Mixed precision method
- `M`: Medium quality

## Quality Testing

```bash
# Calculate perplexity (quality metric)
./llama-perplexity \
    -m model.gguf \
    -f wikitext-2-raw/wiki.test.raw \
    -c 512

# Lower perplexity = better quality
# Baseline (FP16): ~5.96
# Q4_K_M: ~6.06 (+1.7%)
# Q2_K: ~6.87 (+15.3% - too much degradation)
```

## Use Case Guide

### General purpose (chatbots, assistants)
```
Q4_K_M - Best balance
Q5_K_M - If you have extra RAM
```

### Code generation
```
Q5_K_M or Q6_K - Higher precision helps with code
```

### Creative writing
```
Q4_K_M - Sufficient quality
Q3_K_M - Acceptable for draft generation
```

### Technical/medical
```
Q6_K or Q8_0 - Maximum accuracy
```

### Edge devices (Raspberry Pi)
```
Q2_K or Q3_K_S - Fit in limited RAM
```

## Model Size Scaling

### 7B parameter models

| Format | Size | RAM needed |
|--------|------|------------|
| Q2_K | 2.7 GB | 5 GB |
| Q3_K_M | 3.3 GB | 6 GB |
| Q4_K_M | 4.1 GB | 7 GB |
| Q5_K_M | 4.8 GB | 8 GB |
| Q6_K | 5.5 GB | 9 GB |
| Q8_0 | 7.0 GB | 11 GB |

### 13B parameter models

| Format | Size | RAM needed |
|--------|------|------------|
| Q2_K | 5.1 GB | 8 GB |
| Q3_K_M | 6.2 GB | 10 GB |
| Q4_K_M | 7.9 GB | 12 GB |
| Q5_K_M | 9.2 GB | 14 GB |
| Q6_K | 10.7 GB | 16 GB |

### 70B parameter models

| Format | Size | RAM needed |
|--------|------|------------|
| Q2_K | 26 GB | 32 GB |
| Q3_K_M | 32 GB | 40 GB |
| Q4_K_M | 41 GB | 48 GB |
| Q4_K_S | 39 GB | 46 GB |
| Q5_K_M | 48 GB | 56 GB |

**Recommendation for 70B**: Use Q3_K_M or Q4_K_S to fit in consumer hardware.

## Finding Pre-Quantized Models

Use the Hub search with the llama.cpp app filter:

```text
https://huggingface.co/models?apps=llama.cpp&sort=trending
https://huggingface.co/models?search=<term>&apps=llama.cpp&sort=trending
https://huggingface.co/models?search=<term>&apps=llama.cpp&num_parameters=min:0,max:24B&sort=trending
```

For a specific repo, open:

```text
https://huggingface.co/<repo>?local-app=llama.cpp
https://huggingface.co/api/models/<repo>/tree/main?recursive=true
```

Then launch directly from the Hub without extra Hub tooling:

```bash
llama-cli -hf <repo>:Q4_K_M
llama-server -hf <repo>:Q4_K_M
```

If you need the exact file name from the tree API:

```bash
llama-server --hf-repo <repo> --hf-file <filename.gguf>
```

## Importance Matrices (imatrix)

**What**: Calibration data to improve quantization quality.

**Benefits**:
- 10-20% perplexity improvement with Q4
- Essential for Q3 and below

**Usage**:
```bash
# 1. Generate importance matrix
./llama-imatrix \
    -m model-f16.gguf \
    -f calibration-data.txt \
    -o model.imatrix

# 2. Quantize with imatrix
./llama-quantize \
    --imatrix model.imatrix \
    model-f16.gguf \
    model-Q4_K_M.gguf \
    Q4_K_M
```

**Calibration data**:
- Use domain-specific text (e.g., code for code models)
- ~100MB of representative text
- Higher quality data = better quantization

## Troubleshooting

**Model outputs gibberish**:
- Quantization too aggressive (Q2_K)
- Try Q4_K_M or Q5_K_M
- Verify model converted correctly

**Out of memory**:
- Use lower quantization (Q4_K_S instead of Q5_K_M)
- Offload fewer layers to GPU (`-ngl`)
- Use smaller context (`-c 2048`)

**Slow inference**:
- Higher quantization uses more compute
- Q8_0 much slower than Q4_K_M
- Consider speed vs quality trade-off



---

## Lampiran: `references/server.md`

# Server Deployment Guide

Production deployment of llama.cpp server with OpenAI-compatible API.

## Direct from Hugging Face Hub

Prefer the model repo's local-app page first:

```text
https://huggingface.co/<repo>?local-app=llama.cpp
```

If the page shows an exact snippet, copy it. If not, use one of these forms:

```bash
# Choose a quant label directly from the Hub repo
llama-server -hf bartowski/Llama-3.2-3B-Instruct-GGUF:Q8_0
```

```bash
# Pin an exact GGUF file from the repo tree
llama-server \
    --hf-repo microsoft/Phi-3-mini-4k-instruct-gguf \
    --hf-file Phi-3-mini-4k-instruct-q4.gguf \
    -c 4096
```

Use the file-specific form when the repo has custom naming or when you already extracted the exact filename from the tree API.

## Server Modes

### llama-server

```bash
# Basic server
./llama-server \
    -m models/llama-2-7b-chat.Q4_K_M.gguf \
    --host 0.0.0.0 \
    --port 8080 \
    -c 4096  # Context size

# With GPU acceleration
./llama-server \
    -m models/llama-2-70b.Q4_K_M.gguf \
    -ngl 40  # Offload 40 layers to GPU
```

## OpenAI-Compatible API

### Chat completions
```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama-2",
    "messages": [
      {"role": "system", "content": "You are helpful"},
      {"role": "user", "content": "Hello"}
    ],
    "temperature": 0.7,
    "max_tokens": 100
  }'
```

### Streaming
```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama-2",
    "messages": [{"role": "user", "content": "Count to 10"}],
    "stream": true
  }'
```

## Docker Deployment

**Dockerfile**:
```dockerfile
FROM ubuntu:22.04
RUN apt-get update && apt-get install -y git build-essential
RUN git clone https://github.com/ggerganov/llama.cpp
WORKDIR /llama.cpp
RUN make LLAMA_CUDA=1
COPY models/ /models/
EXPOSE 8080
CMD ["./llama-server", "-m", "/models/model.gguf", "--host", "0.0.0.0", "--port", "8080"]
```

**Run**:
```bash
docker run --gpus all -p 8080:8080 llama-cpp:latest
```

## Monitoring

```bash
# Server metrics endpoint
curl http://localhost:8080/metrics

# Health check
curl http://localhost:8080/health
```

**Metrics**:
- requests_total
- tokens_generated
- prompt_tokens
- completion_tokens
- kv_cache_tokens

## Load Balancing

**NGINX**:
```nginx
upstream llama_cpp {
    server llama1:8080;
    server llama2:8080;
}

server {
    location / {
        proxy_pass http://llama_cpp;
        proxy_read_timeout 300s;
    }
}
```

## Performance Tuning

**Parallel requests**:
```bash
./llama-server \
    -m model.gguf \
    -np 4  # 4 parallel slots
```

**Continuous batching**:
```bash
./llama-server \
    -m model.gguf \
    --cont-batching  # Enable continuous batching
```

**Context caching**:
```bash
./llama-server \
    -m model.gguf \
    --cache-prompt  # Cache processed prompts
```



---

## Lampiran: `references/troubleshooting.md`

# GGUF Troubleshooting Guide

## Installation Issues

### Build Fails

**Error**: `make: *** No targets specified and no makefile found`

**Fix**:
```bash
# Ensure you're in llama.cpp directory
cd llama.cpp
make
```

**Error**: `fatal error: cuda_runtime.h: No such file or directory`

**Fix**:
```bash
# Install CUDA toolkit
# Ubuntu
sudo apt install nvidia-cuda-toolkit

# Or set CUDA path
export CUDA_PATH=/usr/local/cuda
export PATH=$CUDA_PATH/bin:$PATH
make GGML_CUDA=1
```

### Python Bindings Issues

**Error**: `ERROR: Failed building wheel for llama-cpp-python`

**Fix**:
```bash
# Install build dependencies
pip install cmake scikit-build-core

# For CUDA support
CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python --force-reinstall --no-cache-dir

# For Metal (macOS)
CMAKE_ARGS="-DGGML_METAL=on" pip install llama-cpp-python --force-reinstall --no-cache-dir
```

**Error**: `ImportError: libcudart.so.XX: cannot open shared object file`

**Fix**:
```bash
# Add CUDA libraries to path
export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH

# Or reinstall with correct CUDA version
pip uninstall llama-cpp-python
CUDACXX=/usr/local/cuda/bin/nvcc CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python
```

## Conversion Issues

### Model Not Supported

**Error**: `KeyError: 'model.embed_tokens.weight'`

**Fix**:
```bash
# Check model architecture
python -c "from transformers import AutoConfig; print(AutoConfig.from_pretrained('./model').architectures)"

# Use appropriate conversion script
# For most models:
python convert_hf_to_gguf.py ./model --outfile model.gguf

# For older models, check if legacy script needed
```

### Vocabulary Mismatch

**Error**: `RuntimeError: Vocabulary size mismatch`

**Fix**:
```python
# Ensure tokenizer matches model
from transformers import AutoTokenizer, AutoModelForCausalLM

tokenizer = AutoTokenizer.from_pretrained("./model")
model = AutoModelForCausalLM.from_pretrained("./model")

print(f"Tokenizer vocab size: {len(tokenizer)}")
print(f"Model vocab size: {model.config.vocab_size}")

# If mismatch, resize embeddings before conversion
model.resize_token_embeddings(len(tokenizer))
model.save_pretrained("./model-fixed")
```

### Out of Memory During Conversion

**Error**: `torch.cuda.OutOfMemoryError` during conversion

**Fix**:
```bash
# Use CPU for conversion
CUDA_VISIBLE_DEVICES="" python convert_hf_to_gguf.py ./model --outfile model.gguf

# Or use low memory mode
python convert_hf_to_gguf.py ./model --outfile model.gguf --outtype f16
```

## Quantization Issues

### Wrong Output File Size

**Problem**: Quantized file is larger than expected

**Check**:
```bash
# Verify quantization type
./llama-cli -m model.gguf --verbose

# Expected sizes for 7B model:
# Q4_K_M: ~4.1 GB
# Q5_K_M: ~4.8 GB
# Q8_0: ~7.2 GB
# F16: ~13.5 GB
```

### Quantization Crashes

**Error**: `Segmentation fault` during quantization

**Fix**:
```bash
# Increase stack size
ulimit -s unlimited

# Or use less threads
./llama-quantize -t 4 model-f16.gguf model-q4.gguf Q4_K_M
```

### Poor Quality After Quantization

**Problem**: Model outputs gibberish after quantization

**Solutions**:

1. **Use importance matrix**:
```bash
# Generate imatrix with good calibration data
./llama-imatrix -m model-f16.gguf \
    -f wiki_sample.txt \
    --chunk 512 \
    -o model.imatrix

# Quantize with imatrix
./llama-quantize --imatrix model.imatrix \
    model-f16.gguf model-q4_k_m.gguf Q4_K_M
```

2. **Try higher precision**:
```bash
# Use Q5_K_M or Q6_K instead of Q4
./llama-quantize model-f16.gguf model-q5_k_m.gguf Q5_K_M
```

3. **Check original model**:
```bash
# Test FP16 version first
./llama-cli -m model-f16.gguf -p "Hello, how are you?" -n 50
```

## Inference Issues

### Slow Generation

**Problem**: Generation is slower than expected

**Solutions**:

1. **Enable GPU offload**:
```bash
./llama-cli -m model.gguf -ngl 35 -p "Hello"
```

2. **Optimize batch size**:
```python
llm = Llama(
    model_path="model.gguf",
    n_batch=512,        # Increase for faster prompt processing
    n_gpu_layers=35
)
```

3. **Use appropriate threads**:
```bash
# Match physical cores, not logical
./llama-cli -m model.gguf -t 8 -p "Hello"
```

4. **Enable Flash Attention** (if supported):
```bash
./llama-cli -m model.gguf -ngl 35 --flash-attn -p "Hello"
```

### Out of Memory

**Error**: `CUDA out of memory` or system freeze

**Solutions**:

1. **Reduce GPU layers**:
```python
# Start low and increase
llm = Llama(model_path="model.gguf", n_gpu_layers=10)
```

2. **Use smaller quantization**:
```bash
./llama-quantize model-f16.gguf model-q3_k_m.gguf Q3_K_M
```

3. **Reduce context length**:
```python
llm = Llama(
    model_path="model.gguf",
    n_ctx=2048,  # Reduce from 4096
    n_gpu_layers=35
)
```

4. **Quantize KV cache**:
```python
llm = Llama(
    model_path="model.gguf",
    type_k=2,    # Q4_0 for K cache
    type_v=2,    # Q4_0 for V cache
    n_gpu_layers=35
)
```

### Garbage Output

**Problem**: Model outputs random characters or nonsense

**Diagnose**:
```python
# Check model loading
llm = Llama(model_path="model.gguf", verbose=True)

# Test with simple prompt
output = llm("1+1=", max_tokens=5, temperature=0)
print(output)
```

**Solutions**:

1. **Check model integrity**:
```bash
# Verify GGUF file
./llama-cli -m model.gguf --verbose 2>&1 | head -50
```

2. **Use correct chat format**:
```python
llm = Llama(
    model_path="model.gguf",
    chat_format="llama-3"  # Match your model: chatml, mistral, etc.
)
```

3. **Check temperature**:
```python
# Use lower temperature for deterministic output
output = llm("Hello", max_tokens=50, temperature=0.1)
```

### Token Issues

**Error**: `RuntimeError: unknown token` or encoding errors

**Fix**:
```python
# Ensure UTF-8 encoding
prompt = "Hello, world!".encode('utf-8').decode('utf-8')
output = llm(prompt, max_tokens=50)
```

## Server Issues

### Connection Refused

**Error**: `Connection refused` when accessing server

**Fix**:
```bash
# Bind to all interfaces
./llama-server -m model.gguf --host 0.0.0.0 --port 8080

# Check if port is in use
lsof -i :8080
```

### Server Crashes Under Load

**Problem**: Server crashes with multiple concurrent requests

**Solutions**:

1. **Limit parallelism**:
```bash
./llama-server -m model.gguf \
    --parallel 2 \
    -c 4096 \
    --cont-batching
```

2. **Add request timeout**:
```bash
./llama-server -m model.gguf --timeout 300
```

3. **Monitor memory**:
```bash
watch -n 1 nvidia-smi  # For GPU
watch -n 1 free -h     # For RAM
```

### API Compatibility Issues

**Problem**: OpenAI client not working with server

**Fix**:
```python
from openai import OpenAI

# Use correct base URL format
client = OpenAI(
    base_url="http://localhost:8080/v1",  # Include /v1
    api_key="not-needed"
)

# Use correct model name
response = client.chat.completions.create(
    model="local",  # Or the actual model name
    messages=[{"role": "user", "content": "Hello"}]
)
```

## Apple Silicon Issues

### Metal Not Working

**Problem**: Metal acceleration not enabled

**Check**:
```bash
# Verify Metal support
./llama-cli -m model.gguf --verbose 2>&1 | grep -i metal
```

**Fix**:
```bash
# Rebuild with Metal
make clean
make GGML_METAL=1

# Python bindings
CMAKE_ARGS="-DGGML_METAL=on" pip install llama-cpp-python --force-reinstall
```

### Incorrect Memory Usage on M1/M2

**Problem**: Model uses too much unified memory

**Fix**:
```python
# Offload all layers for Metal
llm = Llama(
    model_path="model.gguf",
    n_gpu_layers=99,    # Offload everything
    n_threads=1         # Metal handles parallelism
)
```

## Debugging

### Enable Verbose Output

```bash
# CLI verbose mode
./llama-cli -m model.gguf --verbose -p "Hello" -n 50

# Python verbose
llm = Llama(model_path="model.gguf", verbose=True)
```

### Check Model Metadata

```bash
# View GGUF metadata
./llama-cli -m model.gguf --verbose 2>&1 | head -100
```

### Validate GGUF File

```python
import struct

def validate_gguf(filepath):
    with open(filepath, 'rb') as f:
        magic = f.read(4)
        if magic != b'GGUF':
            print(f"Invalid magic: {magic}")
            return False

        version = struct.unpack('<I', f.read(4))[0]
        print(f"GGUF version: {version}")

        tensor_count = struct.unpack('<Q', f.read(8))[0]
        metadata_count = struct.unpack('<Q', f.read(8))[0]
        print(f"Tensors: {tensor_count}, Metadata: {metadata_count}")

        return True

validate_gguf("model.gguf")
```

## Getting Help

1. **GitHub Issues**: https://github.com/ggml-org/llama.cpp/issues
2. **Discussions**: https://github.com/ggml-org/llama.cpp/discussions
3. **Reddit**: r/LocalLLaMA

### Reporting Issues

Include:
- llama.cpp version/commit hash
- Build command used
- Model name and quantization
- Full error message/stack trace
- Hardware: CPU/GPU model, RAM, VRAM
- OS version
- Minimal reproduction steps
