# Serving Llms Vllm

> vLLM: high-throughput LLM serving, OpenAI API, quantization.

## When to use

Use when deploying production LLM APIs, optimizing inference latency/throughput, or serving models with limited GPU memory. Supports OpenAI-compatible endpoints, quantization (GPTQ/AWQ/FP8), and tensor parallelism.

## Quick start

vLLM achieves 24x higher throughput than standard transformers through PagedAttention (block-based KV cache) and continuous batching (mixing prefill/decode requests).

**Installation**:
```bash
pip install vllm
```

**Basic offline inference**:
```python
from vllm import LLM, SamplingParams

llm = LLM(model="meta-llama/Llama-3-8B-Instruct")
sampling = SamplingParams(temperature=0.7, max_tokens=256)

outputs = llm.generate(["Explain quantum computing"], sampling)
print(outputs[0].outputs[0].text)
```

**OpenAI-compatible server**:
```bash
vllm serve meta-llama/Llama-3-8B-Instruct

# Query with OpenAI SDK
python -c "
from openai import OpenAI
client = OpenAI(base_url='http://localhost:8000/v1', api_key='EMPTY')
print(client.chat.completions.create(
    model='meta-llama/Llama-3-8B-Instruct',
    messages=[{'role': 'user', 'content': 'Hello!'}]
).choices[0].message.content)
"
```

## Common workflows

### Workflow 1: Production API deployment

Copy this checklist and track progress:

```
Deployment Progress:
- [ ] Step 1: Configure server settings
- [ ] Step 2: Test with limited traffic
- [ ] Step 3: Enable monitoring
- [ ] Step 4: Deploy to production
- [ ] Step 5: Verify performance metrics
```

**Step 1: Configure server settings**

Choose configuration based on your model size:

```bash
# For 7B-13B models on single GPU
vllm serve meta-llama/Llama-3-8B-Instruct \
  --gpu-memory-utilization 0.9 \
  --max-model-len 8192 \
  --port 8000

# For 30B-70B models with tensor parallelism
vllm serve meta-llama/Llama-2-70b-hf \
  --tensor-parallel-size 4 \
  --gpu-memory-utilization 0.9 \
  --quantization awq \
  --port 8000

# For production with caching and metrics
vllm serve meta-llama/Llama-3-8B-Instruct \
  --gpu-memory-utilization 0.9 \
  --enable-prefix-caching \
  --enable-metrics \
  --metrics-port 9090 \
  --port 8000 \
  --host 0.0.0.0
```

**Step 2: Test with limited traffic**

Run load test before production:

```bash
# Install load testing tool
pip install locust

# Create test_load.py with sample requests
# Run: locust -f test_load.py --host http://localhost:8000
```

Verify TTFT (time to first token) < 500ms and throughput > 100 req/sec.

**Step 3: Enable monitoring**

vLLM exposes Prometheus metrics on port 9090:

```bash
curl http://localhost:9090/metrics | grep vllm
```

Key metrics to monitor:
- `vllm:time_to_first_token_seconds` - Latency
- `vllm:num_requests_running` - Active requests
- `vllm:gpu_cache_usage_perc` - KV cache utilization

**Step 4: Deploy to production**

Use Docker for consistent deployment:

```bash
# Run vLLM in Docker
docker run --gpus all -p 8000:8000 \
  vllm/vllm-openai:latest \
  --model meta-llama/Llama-3-8B-Instruct \
  --gpu-memory-utilization 0.9 \
  --enable-prefix-caching
```

**Step 5: Verify performance metrics**

Check that deployment meets targets:
- TTFT < 500ms (for short prompts)
- Throughput > target req/sec
- GPU utilization > 80%
- No OOM errors in logs

### Workflow 2: Offline batch inference

For processing large datasets without server overhead.

Copy this checklist:

```
Batch Processing:
- [ ] Step 1: Prepare input data
- [ ] Step 2: Configure LLM engine
- [ ] Step 3: Run batch inference
- [ ] Step 4: Process results
```

**Step 1: Prepare input data**

```python
# Load prompts from file
prompts = []
with open("prompts.txt") as f:
    prompts = [line.strip() for line in f]

print(f"Loaded {len(prompts)} prompts")
```

**Step 2: Configure LLM engine**

```python
from vllm import LLM, SamplingParams

llm = LLM(
    model="meta-llama/Llama-3-8B-Instruct",
    tensor_parallel_size=2,  # Use 2 GPUs
    gpu_memory_utilization=0.9,
    max_model_len=4096
)

sampling = SamplingParams(
    temperature=0.7,
    top_p=0.95,
    max_tokens=512,
    stop=["</s>", "\n\n"]
)
```

**Step 3: Run batch inference**

vLLM automatically batches requests for efficiency:

```python
# Process all prompts in one call
outputs = llm.generate(prompts, sampling)

# vLLM handles batching internally
# No need to manually chunk prompts
```

**Step 4: Process results**

```python
# Extract generated text
results = []
for output in outputs:
    prompt = output.prompt
    generated = output.outputs[0].text
    results.append({
        "prompt": prompt,
        "generated": generated,
        "tokens": len(output.outputs[0].token_ids)
    })

# Save to file
import json
with open("results.jsonl", "w") as f:
    for result in results:
        f.write(json.dumps(result) + "\n")

print(f"Processed {len(results)} prompts")
```

### Workflow 3: Quantized model serving

Fit large models in limited GPU memory.

```
Quantization Setup:
- [ ] Step 1: Choose quantization method
- [ ] Step 2: Find or create quantized model
- [ ] Step 3: Launch with quantization flag
- [ ] Step 4: Verify accuracy
```

**Step 1: Choose quantization method**

- **AWQ**: Best for 70B models, minimal accuracy loss
- **GPTQ**: Wide model support, good compression
- **FP8**: Fastest on H100 GPUs

**Step 2: Find or create quantized model**

Use pre-quantized models from HuggingFace:

```bash
# Search for AWQ models
# Example: TheBloke/Llama-2-70B-AWQ
```

**Step 3: Launch with quantization flag**

```bash
# Using pre-quantized model
vllm serve TheBloke/Llama-2-70B-AWQ \
  --quantization awq \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.95

# Results: 70B model in ~40GB VRAM
```

**Step 4: Verify accuracy**

Test outputs match expected quality:

```python
# Compare quantized vs non-quantized responses
# Verify task-specific performance unchanged
```

## When to use vs alternatives

**Use vLLM when:**
- Deploying production LLM APIs (100+ req/sec)
- Serving OpenAI-compatible endpoints
- Limited GPU memory but need large models
- Multi-user applications (chatbots, assistants)
- Need low latency with high throughput

**Use alternatives instead:**
- **llama.cpp**: CPU/edge inference, single-user
- **HuggingFace transformers**: Research, prototyping, one-off generation
- **TensorRT-LLM**: NVIDIA-only, need absolute maximum performance
- **Text-Generation-Inference**: Already in HuggingFace ecosystem

## Common issues

**Issue: Out of memory during model loading**

Reduce memory usage:
```bash
vllm serve MODEL \
  --gpu-memory-utilization 0.7 \
  --max-model-len 4096
```

Or use quantization:
```bash
vllm serve MODEL --quantization awq
```

**Issue: Slow first token (TTFT > 1 second)**

Enable prefix caching for repeated prompts:
```bash
vllm serve MODEL --enable-prefix-caching
```

For long prompts, enable chunked prefill:
```bash
vllm serve MODEL --enable-chunked-prefill
```

**Issue: Model not found error**

Use `--trust-remote-code` for custom models:
```bash
vllm serve MODEL --trust-remote-code
```

**Issue: Low throughput (<50 req/sec)**

Increase concurrent sequences:
```bash
vllm serve MODEL --max-num-seqs 512
```

Check GPU utilization with `nvidia-smi` - should be >80%.

**Issue: Inference slower than expected**

Verify tensor parallelism uses power of 2 GPUs:
```bash
vllm serve MODEL --tensor-parallel-size 4  # Not 3
```

Enable speculative decoding for faster generation:
```bash
vllm serve MODEL --speculative-model DRAFT_MODEL
```

## Advanced topics

**Server deployment patterns**: See `references/server-deployment.md` for Docker, Kubernetes, and load balancing configurations.

**Performance optimization**: See `references/optimization.md` for PagedAttention tuning, continuous batching details, and benchmark results.

**Quantization guide**: See `references/quantization.md` for AWQ/GPTQ/FP8 setup, model preparation, and accuracy comparisons.

**Troubleshooting**: See `references/troubleshooting.md` for detailed error messages, debugging steps, and performance diagnostics.

## Hardware requirements

- **Small models (7B-13B)**: 1x A10 (24GB) or A100 (40GB)
- **Medium models (30B-40B)**: 2x A100 (40GB) with tensor parallelism
- **Large models (70B+)**: 4x A100 (40GB) or 2x A100 (80GB), use AWQ/GPTQ

Supported platforms: NVIDIA (primary), AMD ROCm, Intel GPUs, TPUs

## Resources

- Official docs: https://docs.vllm.ai
- GitHub: https://github.com/vllm-project/vllm
- Paper: "Efficient Memory Management for Large Language Model Serving with PagedAttention" (SOSP 2023)
- Community: https://discuss.vllm.ai





---

## Lampiran: `references/optimization.md`

# Performance Optimization

## Contents
- PagedAttention explained
- Continuous batching mechanics
- Prefix caching strategies
- Speculative decoding setup
- Benchmark results and comparisons
- Performance tuning guide

## PagedAttention explained

**Traditional attention problem**:
- KV cache stored in contiguous memory
- Wastes ~50% GPU memory due to fragmentation
- Cannot dynamically reallocate for varying sequence lengths

**PagedAttention solution**:
- Divides KV cache into fixed-size blocks (like OS virtual memory)
- Dynamic allocation from free block queue
- Shares blocks across sequences (for prefix caching)

**Memory savings example**:
```
Traditional: 70B model needs 160GB KV cache → OOM on 8x A100
PagedAttention: 70B model needs 80GB KV cache → Fits on 4x A100
```

**Configuration**:
```bash
# Block size (default: 16 tokens)
vllm serve MODEL --block-size 16

# Number of GPU blocks (auto-calculated)
# Controlled by --gpu-memory-utilization
vllm serve MODEL --gpu-memory-utilization 0.9
```

## Continuous batching mechanics

**Traditional batching**:
- Wait for all sequences in batch to finish
- GPU idle while waiting for longest sequence
- Low GPU utilization (~40-60%)

**Continuous batching**:
- Add new requests as slots become available
- Mix prefill (new requests) and decode (ongoing) in same batch
- High GPU utilization (>90%)

**Throughput improvement**:
```
Traditional batching: 50 req/sec @ 50% GPU util
Continuous batching: 200 req/sec @ 90% GPU util
= 4x throughput improvement
```

**Tuning parameters**:
```bash
# Max concurrent sequences (higher = more batching)
vllm serve MODEL --max-num-seqs 256

# Prefill/decode schedule (auto-balanced by default)
# No manual tuning needed
```

## Prefix caching strategies

Reuse computed KV cache for common prompt prefixes.

**Use cases**:
- System prompts repeated across requests
- Few-shot examples in every prompt
- RAG contexts with overlapping chunks

**Example savings**:
```
Prompt: [System: 500 tokens] + [User: 100 tokens]

Without caching: Compute 600 tokens every request
With caching: Compute 500 tokens once, then 100 tokens/request
= 83% faster TTFT
```

**Enable prefix caching**:
```bash
vllm serve MODEL --enable-prefix-caching
```

**Automatic prefix detection**:
- vLLM detects common prefixes automatically
- No code changes required
- Works with OpenAI-compatible API

**Cache hit rate monitoring**:
```bash
curl http://localhost:9090/metrics | grep cache_hit
# vllm_cache_hit_rate: 0.75  (75% hit rate)
```

## Speculative decoding setup

Use smaller "draft" model to propose tokens, larger model to verify.

**Speed improvement**:
```
Standard: Generate 1 token per forward pass
Speculative: Generate 3-5 tokens per forward pass
= 2-3x faster generation
```

**How it works**:
1. Draft model proposes K tokens (fast)
2. Target model verifies all K tokens in parallel (one pass)
3. Accept verified tokens, restart from first rejection

**Setup with separate draft model**:
```bash
vllm serve meta-llama/Llama-3-70B-Instruct \
  --speculative-model TinyLlama/TinyLlama-1.1B-Chat-v1.0 \
  --num-speculative-tokens 5
```

**Setup with n-gram draft** (no separate model):
```bash
vllm serve MODEL \
  --speculative-method ngram \
  --num-speculative-tokens 3
```

**When to use**:
- Output length > 100 tokens
- Draft model 5-10x smaller than target
- Acceptable 2-3% accuracy trade-off

## Benchmark results

**vLLM vs HuggingFace Transformers** (Llama 3 8B, A100):
```
Metric                  | HF Transformers | vLLM   | Improvement
------------------------|-----------------|--------|------------
Throughput (req/sec)    | 12              | 280    | 23x
TTFT (ms)              | 850             | 120    | 7x
Tokens/sec             | 45              | 2,100  | 47x
GPU Memory (GB)        | 28              | 16     | 1.75x less
```

**vLLM vs TensorRT-LLM** (Llama 2 70B, 4x A100):
```
Metric                  | TensorRT-LLM | vLLM   | Notes
------------------------|--------------|--------|------------------
Throughput (req/sec)    | 320          | 285    | TRT 12% faster
Setup complexity        | High         | Low    | vLLM much easier
NVIDIA-only            | Yes          | No     | vLLM multi-platform
Quantization support    | FP8, INT8    | AWQ/GPTQ/FP8 | vLLM more options
```

## Performance tuning guide

**Step 1: Measure baseline**

```bash
# Install benchmarking tool
pip install locust

# Run baseline benchmark
vllm bench throughput \
  --model MODEL \
  --input-tokens 128 \
  --output-tokens 256 \
  --num-prompts 1000

# Record: throughput, TTFT, tokens/sec
```

**Step 2: Tune memory utilization**

```bash
# Try different values: 0.7, 0.85, 0.9, 0.95
vllm serve MODEL --gpu-memory-utilization 0.9
```

Higher = more batch capacity = higher throughput, but risk OOM.

**Step 3: Tune concurrency**

```bash
# Try values: 128, 256, 512, 1024
vllm serve MODEL --max-num-seqs 256
```

Higher = more batching opportunity, but may increase latency.

**Step 4: Enable optimizations**

```bash
vllm serve MODEL \
  --enable-prefix-caching \     # For repeated prompts
  --enable-chunked-prefill \    # For long prompts
  --gpu-memory-utilization 0.9 \
  --max-num-seqs 512
```

**Step 5: Re-benchmark and compare**

Target improvements:
- Throughput: +30-100%
- TTFT: -20-50%
- GPU utilization: >85%

**Common performance issues**:

**Low throughput (<50 req/sec)**:
- Increase `--max-num-seqs`
- Enable `--enable-prefix-caching`
- Check GPU utilization (should be >80%)

**High TTFT (>1 second)**:
- Enable `--enable-chunked-prefill`
- Reduce `--max-model-len` if possible
- Check if model is too large for GPU

**OOM errors**:
- Reduce `--gpu-memory-utilization` to 0.7
- Reduce `--max-model-len`
- Use quantization (`--quantization awq`)



---

## Lampiran: `references/quantization.md`

# Quantization Guide

## Contents
- Quantization methods comparison
- AWQ setup and usage
- GPTQ setup and usage
- FP8 quantization (H100)
- Model preparation
- Accuracy vs compression trade-offs

## Quantization methods comparison

| Method | Compression | Accuracy Loss | Speed | Best For |
|--------|-------------|---------------|-------|----------|
| **AWQ** | 4-bit (75%) | <1% | Fast | 70B models, production |
| **GPTQ** | 4-bit (75%) | 1-2% | Fast | Wide model support |
| **FP8** | 8-bit (50%) | <0.5% | Fastest | H100 GPUs only |
| **SqueezeLLM** | 3-4 bit (75-80%) | 2-3% | Medium | Extreme compression |

**Recommendation**:
- **Production**: Use AWQ for 70B models
- **H100 GPUs**: Use FP8 for best speed
- **Maximum compatibility**: Use GPTQ
- **Extreme compression**: Use SqueezeLLM

## AWQ setup and usage

**AWQ** (Activation-aware Weight Quantization) achieves best accuracy at 4-bit.

**Step 1: Find pre-quantized model**

Search HuggingFace for AWQ models:
```bash
# Example: TheBloke/Llama-2-70B-AWQ
# Example: TheBloke/Mixtral-8x7B-Instruct-v0.1-AWQ
```

**Step 2: Launch with AWQ**

```bash
vllm serve TheBloke/Llama-2-70B-AWQ \
  --quantization awq \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.95
```

**Memory savings**:
```
Llama 2 70B fp16: 140GB VRAM (4x A100 needed)
Llama 2 70B AWQ: 35GB VRAM (1x A100 40GB)
= 4x memory reduction
```

**Step 3: Verify performance**

Test that outputs are acceptable:
```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="EMPTY")

# Test complex reasoning
response = client.chat.completions.create(
    model="TheBloke/Llama-2-70B-AWQ",
    messages=[{"role": "user", "content": "Explain quantum entanglement"}]
)

print(response.choices[0].message.content)
# Verify quality matches your requirements
```

**Quantize your own model** (requires GPU with 80GB+ VRAM):

```python
from awq import AutoAWQForCausalLM
from transformers import AutoTokenizer

model_path = "meta-llama/Llama-2-70b-hf"
quant_path = "llama-2-70b-awq"

# Load model
model = AutoAWQForCausalLM.from_pretrained(model_path)
tokenizer = AutoTokenizer.from_pretrained(model_path)

# Quantize
quant_config = {"zero_point": True, "q_group_size": 128, "w_bit": 4}
model.quantize(tokenizer, quant_config=quant_config)

# Save
model.save_quantized(quant_path)
tokenizer.save_pretrained(quant_path)
```

## GPTQ setup and usage

**GPTQ** has widest model support and good compression.

**Step 1: Find GPTQ model**

```bash
# Example: TheBloke/Llama-2-13B-GPTQ
# Example: TheBloke/CodeLlama-34B-GPTQ
```

**Step 2: Launch with GPTQ**

```bash
vllm serve TheBloke/Llama-2-13B-GPTQ \
  --quantization gptq \
  --dtype float16
```

**GPTQ configuration options**:
```bash
# Specify GPTQ parameters if needed
vllm serve MODEL \
  --quantization gptq \
  --gptq-act-order \  # Activation ordering
  --dtype float16
```

**Quantize your own model**:

```python
from auto_gptq import AutoGPTQForCausalLM, BaseQuantizeConfig
from transformers import AutoTokenizer

model_name = "meta-llama/Llama-2-13b-hf"
quantized_name = "llama-2-13b-gptq"

# Load model
tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoGPTQForCausalLM.from_pretrained(model_name, quantize_config)

# Prepare calibration data
calib_data = [...]  # List of sample texts

# Quantize
quantize_config = BaseQuantizeConfig(
    bits=4,
    group_size=128,
    desc_act=True
)
model.quantize(calib_data)

# Save
model.save_quantized(quantized_name)
```

## FP8 quantization (H100)

**FP8** (8-bit floating point) offers best speed on H100 GPUs with minimal accuracy loss.

**Requirements**:
- H100 or H800 GPU
- CUDA 12.3+ (12.8 recommended)
- Hopper architecture support

**Step 1: Enable FP8**

```bash
vllm serve meta-llama/Llama-3-70B-Instruct \
  --quantization fp8 \
  --tensor-parallel-size 2
```

**Performance gains on H100**:
```
fp16: 180 tokens/sec
FP8: 320 tokens/sec
= 1.8x speedup
```

**Step 2: Verify accuracy**

FP8 typically has <0.5% accuracy degradation:
```python
# Run evaluation suite
# Compare FP8 vs FP16 on your tasks
# Verify acceptable accuracy
```

**Dynamic FP8 quantization** (no pre-quantized model needed):

```bash
# vLLM automatically quantizes at runtime
vllm serve MODEL --quantization fp8
# No model preparation required
```

## Model preparation

**Pre-quantized models (easiest)**:

1. Search HuggingFace: `[model name] AWQ` or `[model name] GPTQ`
2. Download or use directly: `TheBloke/[Model]-AWQ`
3. Launch with appropriate `--quantization` flag

**Quantize your own model**:

**AWQ**:
```bash
# Install AutoAWQ
pip install autoawq

# Run quantization script
python quantize_awq.py --model MODEL --output OUTPUT
```

**GPTQ**:
```bash
# Install AutoGPTQ
pip install auto-gptq

# Run quantization script
python quantize_gptq.py --model MODEL --output OUTPUT
```

**Calibration data**:
- Use 128-512 diverse examples from target domain
- Representative of production inputs
- Higher quality calibration = better accuracy

## Accuracy vs compression trade-offs

**Empirical results** (Llama 2 70B on MMLU benchmark):

| Quantization | Accuracy | Memory | Speed | Production-Ready |
|--------------|----------|--------|-------|------------------|
| FP16 (baseline) | 100% | 140GB | 1.0x | ✅ (if memory available) |
| FP8 | 99.5% | 70GB | 1.8x | ✅ (H100 only) |
| AWQ 4-bit | 99.0% | 35GB | 1.5x | ✅ (best for 70B) |
| GPTQ 4-bit | 98.5% | 35GB | 1.5x | ✅ (good compatibility) |
| SqueezeLLM 3-bit | 96.0% | 26GB | 1.3x | ⚠️ (check accuracy) |

**When to use each**:

**No quantization (FP16)**:
- Have sufficient GPU memory
- Need absolute best accuracy
- Model <13B parameters

**FP8**:
- Using H100/H800 GPUs
- Need best speed with minimal accuracy loss
- Production deployment

**AWQ 4-bit**:
- Need to fit 70B model in 40GB GPU
- Production deployment
- <1% accuracy loss acceptable

**GPTQ 4-bit**:
- Wide model support needed
- Not on H100 (use FP8 instead)
- 1-2% accuracy loss acceptable

**Testing strategy**:

1. **Baseline**: Measure FP16 accuracy on your evaluation set
2. **Quantize**: Create quantized version
3. **Evaluate**: Compare quantized vs baseline on same tasks
4. **Decide**: Accept if degradation < threshold (typically 1-2%)

**Example evaluation**:
```python
from evaluate import load_evaluation_suite

# Run on FP16 baseline
baseline_score = evaluate(model_fp16, eval_suite)

# Run on quantized
quant_score = evaluate(model_awq, eval_suite)

# Compare
degradation = (baseline_score - quant_score) / baseline_score * 100
print(f"Accuracy degradation: {degradation:.2f}%")

# Decision
if degradation < 1.0:
    print("✅ Quantization acceptable for production")
else:
    print("⚠️ Review accuracy loss")
```



---

## Lampiran: `references/server-deployment.md`

# Server Deployment Patterns

## Contents
- Docker deployment
- Kubernetes deployment
- Load balancing with Nginx
- Multi-node distributed serving
- Production configuration examples
- Health checks and monitoring

## Docker deployment

**Basic Dockerfile**:
```dockerfile
FROM nvidia/cuda:12.1.0-devel-ubuntu22.04

RUN apt-get update && apt-get install -y python3-pip
RUN pip install vllm

EXPOSE 8000

CMD ["vllm", "serve", "meta-llama/Llama-3-8B-Instruct", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--gpu-memory-utilization", "0.9"]
```

**Build and run**:
```bash
docker build -t vllm-server .
docker run --gpus all -p 8000:8000 vllm-server
```

**Docker Compose** (with metrics):
```yaml
version: '3.8'
services:
  vllm:
    image: vllm/vllm-openai:latest
    command: >
      --model meta-llama/Llama-3-8B-Instruct
      --gpu-memory-utilization 0.9
      --enable-metrics
      --metrics-port 9090
    ports:
      - "8000:8000"
      - "9090:9090"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

## Kubernetes deployment

**Deployment manifest**:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-server
spec:
  replicas: 2
  selector:
    matchLabels:
      app: vllm
  template:
    metadata:
      labels:
        app: vllm
    spec:
      containers:
      - name: vllm
        image: vllm/vllm-openai:latest
        args:
          - "--model=meta-llama/Llama-3-8B-Instruct"
          - "--gpu-memory-utilization=0.9"
          - "--enable-prefix-caching"
        resources:
          limits:
            nvidia.com/gpu: 1
        ports:
        - containerPort: 8000
          name: http
        - containerPort: 9090
          name: metrics
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 60
          periodSeconds: 30
---
apiVersion: v1
kind: Service
metadata:
  name: vllm-service
spec:
  selector:
    app: vllm
  ports:
  - port: 8000
    targetPort: 8000
    name: http
  - port: 9090
    targetPort: 9090
    name: metrics
  type: LoadBalancer
```

## Load balancing with Nginx

**Nginx configuration**:
```nginx
upstream vllm_backend {
    least_conn;  # Route to least-loaded server
    server localhost:8001;
    server localhost:8002;
    server localhost:8003;
}

server {
    listen 80;

    location / {
        proxy_pass http://vllm_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;

        # Timeouts for long-running inference
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }

    # Metrics endpoint
    location /metrics {
        proxy_pass http://localhost:9090/metrics;
    }
}
```

**Start multiple vLLM instances**:
```bash
# Terminal 1
vllm serve MODEL --port 8001 --tensor-parallel-size 1

# Terminal 2
vllm serve MODEL --port 8002 --tensor-parallel-size 1

# Terminal 3
vllm serve MODEL --port 8003 --tensor-parallel-size 1

# Start Nginx
nginx -c /path/to/nginx.conf
```

## Multi-node distributed serving

For models too large for single node:

**Node 1** (master):
```bash
export MASTER_ADDR=192.168.1.10
export MASTER_PORT=29500
export RANK=0
export WORLD_SIZE=2

vllm serve meta-llama/Llama-2-70b-hf \
  --tensor-parallel-size 8 \
  --pipeline-parallel-size 2
```

**Node 2** (worker):
```bash
export MASTER_ADDR=192.168.1.10
export MASTER_PORT=29500
export RANK=1
export WORLD_SIZE=2

vllm serve meta-llama/Llama-2-70b-hf \
  --tensor-parallel-size 8 \
  --pipeline-parallel-size 2
```

## Production configuration examples

**High throughput** (batch-heavy workload):
```bash
vllm serve MODEL \
  --max-num-seqs 512 \
  --gpu-memory-utilization 0.95 \
  --enable-prefix-caching \
  --trust-remote-code
```

**Low latency** (interactive workload):
```bash
vllm serve MODEL \
  --max-num-seqs 64 \
  --gpu-memory-utilization 0.85 \
  --enable-chunked-prefill
```

**Memory-constrained** (40GB GPU for 70B model):
```bash
vllm serve TheBloke/Llama-2-70B-AWQ \
  --quantization awq \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.95 \
  --max-model-len 4096
```

## Health checks and monitoring

**Health check endpoint**:
```bash
curl http://localhost:8000/health
# Returns: {"status": "ok"}
```

**Readiness check** (wait for model loaded):
```bash
#!/bin/bash
until curl -f http://localhost:8000/health; do
    echo "Waiting for vLLM to be ready..."
    sleep 5
done
echo "vLLM is ready!"
```

**Prometheus scraping**:
```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'vllm'
    static_configs:
      - targets: ['localhost:9090']
    metrics_path: '/metrics'
    scrape_interval: 15s
```

**Grafana dashboard** (key metrics):
- Requests per second: `rate(vllm_request_success_total[5m])`
- TTFT p50: `histogram_quantile(0.5, vllm_time_to_first_token_seconds_bucket)`
- TTFT p99: `histogram_quantile(0.99, vllm_time_to_first_token_seconds_bucket)`
- GPU cache usage: `vllm_gpu_cache_usage_perc`
- Active requests: `vllm_num_requests_running`



---

## Lampiran: `references/troubleshooting.md`

# Troubleshooting Guide

## Contents
- Out of memory (OOM) errors
- Performance issues
- Model loading errors
- Network and connection issues
- Quantization problems
- Distributed serving issues
- Debugging tools and commands

## Out of memory (OOM) errors

### Symptom: `torch.cuda.OutOfMemoryError` during model loading

**Cause**: Model + KV cache exceeds available VRAM

**Solutions (try in order)**:

1. **Reduce GPU memory utilization**:
```bash
vllm serve MODEL --gpu-memory-utilization 0.7  # Try 0.7, 0.75, 0.8
```

2. **Reduce max sequence length**:
```bash
vllm serve MODEL --max-model-len 4096  # Instead of 8192
```

3. **Enable quantization**:
```bash
vllm serve MODEL --quantization awq  # 4x memory reduction
```

4. **Use tensor parallelism** (multiple GPUs):
```bash
vllm serve MODEL --tensor-parallel-size 2  # Split across 2 GPUs
```

5. **Reduce max concurrent sequences**:
```bash
vllm serve MODEL --max-num-seqs 128  # Default is 256
```

### Symptom: OOM during inference (not model loading)

**Cause**: KV cache fills up during generation

**Solutions**:

```bash
# Reduce KV cache allocation
vllm serve MODEL --gpu-memory-utilization 0.85

# Reduce batch size
vllm serve MODEL --max-num-seqs 64

# Reduce max tokens per request
# Set in client request: max_tokens=512
```

### Symptom: OOM with quantized model

**Cause**: Quantization overhead or incorrect configuration

**Solution**:
```bash
# Ensure quantization flag matches model
vllm serve TheBloke/Llama-2-70B-AWQ --quantization awq  # Must specify

# Try different dtype
vllm serve MODEL --quantization awq --dtype float16
```

## Performance issues

### Symptom: Low throughput (<50 req/sec expected >100)

**Diagnostic steps**:

1. **Check GPU utilization**:
```bash
watch -n 1 nvidia-smi
# GPU utilization should be >80%
```

If <80%, increase concurrent requests:
```bash
vllm serve MODEL --max-num-seqs 512  # Increase from 256
```

2. **Check if memory-bound**:
```bash
# If memory at 100% but GPU <80%, reduce sequence length
vllm serve MODEL --max-model-len 4096
```

3. **Enable optimizations**:
```bash
vllm serve MODEL \
  --enable-prefix-caching \
  --enable-chunked-prefill \
  --max-num-seqs 512
```

4. **Check tensor parallelism settings**:
```bash
# Must use power-of-2 GPUs
vllm serve MODEL --tensor-parallel-size 4  # Not 3 or 5
```

### Symptom: High TTFT (time to first token >1 second)

**Causes and solutions**:

**Long prompts**:
```bash
vllm serve MODEL --enable-chunked-prefill
```

**No prefix caching**:
```bash
vllm serve MODEL --enable-prefix-caching  # For repeated prompts
```

**Too many concurrent requests**:
```bash
vllm serve MODEL --max-num-seqs 64  # Reduce to prioritize latency
```

**Model too large for single GPU**:
```bash
vllm serve MODEL --tensor-parallel-size 2  # Parallelize prefill
```

### Symptom: Slow token generation (low tokens/sec)

**Diagnostic**:
```bash
# Check if model is correct size
vllm serve MODEL  # Should see model size in logs

# Check speculative decoding
vllm serve MODEL --speculative-model DRAFT_MODEL
```

**For H100 GPUs**, enable FP8:
```bash
vllm serve MODEL --quantization fp8
```

## Model loading errors

### Symptom: `OSError: MODEL not found`

**Causes**:

1. **Model name typo**:
```bash
# Check exact model name on HuggingFace
vllm serve meta-llama/Llama-3-8B-Instruct  # Correct capitalization
```

2. **Private/gated model**:
```bash
# Login to HuggingFace first
huggingface-cli login
# Then run vLLM
vllm serve meta-llama/Llama-3-70B-Instruct
```

3. **Custom model needs trust flag**:
```bash
vllm serve MODEL --trust-remote-code
```

### Symptom: `ValueError: Tokenizer not found`

**Solution**:
```bash
# Download model manually first
python -c "from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('MODEL')"

# Then launch vLLM
vllm serve MODEL
```

### Symptom: `ImportError: No module named 'flash_attn'`

**Solution**:
```bash
# Install flash attention
pip install flash-attn --no-build-isolation

# Or disable flash attention
vllm serve MODEL --disable-flash-attn
```

## Network and connection issues

### Symptom: `Connection refused` when querying server

**Diagnostic**:

1. **Check server is running**:
```bash
curl http://localhost:8000/health
```

2. **Check port binding**:
```bash
# Bind to all interfaces for remote access
vllm serve MODEL --host 0.0.0.0 --port 8000

# Check if port is in use
lsof -i :8000
```

3. **Check firewall**:
```bash
# Allow port through firewall
sudo ufw allow 8000
```

### Symptom: Slow response times over network

**Solutions**:

1. **Increase timeout**:
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="EMPTY",
    timeout=300.0  # 5 minute timeout
)
```

2. **Check network latency**:
```bash
ping SERVER_IP  # Should be <10ms for local network
```

3. **Use connection pooling**:
```python
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

session = requests.Session()
retries = Retry(total=3, backoff_factor=1)
session.mount('http://', HTTPAdapter(max_retries=retries))
```

## Quantization problems

### Symptom: `RuntimeError: Quantization format not supported`

**Solution**:
```bash
# Ensure correct quantization method
vllm serve MODEL --quantization awq  # For AWQ models
vllm serve MODEL --quantization gptq  # For GPTQ models

# Check model card for quantization type
```

### Symptom: Poor quality outputs after quantization

**Diagnostic**:

1. **Verify model is correctly quantized**:
```bash
# Check model config.json for quantization_config
cat ~/.cache/huggingface/hub/models--MODEL/config.json
```

2. **Try different quantization method**:
```bash
# If AWQ quality issues, try FP8 (H100 only)
vllm serve MODEL --quantization fp8

# Or use less aggressive quantization
vllm serve MODEL  # No quantization
```

3. **Increase temperature for better diversity**:
```python
sampling_params = SamplingParams(temperature=0.8, top_p=0.95)
```

## Distributed serving issues

### Symptom: `RuntimeError: Distributed init failed`

**Diagnostic**:

1. **Check environment variables**:
```bash
# On all nodes
echo $MASTER_ADDR  # Should be same
echo $MASTER_PORT  # Should be same
echo $RANK  # Should be unique per node (0, 1, 2, ...)
echo $WORLD_SIZE  # Should be same (total nodes)
```

2. **Check network connectivity**:
```bash
# From node 1 to node 2
ping NODE2_IP
nc -zv NODE2_IP 29500  # Check port accessibility
```

3. **Check NCCL settings**:
```bash
export NCCL_DEBUG=INFO
export NCCL_SOCKET_IFNAME=eth0  # Or your network interface
vllm serve MODEL --tensor-parallel-size 8
```

### Symptom: `NCCL error: unhandled cuda error`

**Solutions**:

```bash
# Set NCCL to use correct network interface
export NCCL_SOCKET_IFNAME=eth0  # Replace with your interface

# Increase timeout
export NCCL_TIMEOUT=1800  # 30 minutes

# Force P2P for debugging
export NCCL_P2P_DISABLE=1
```

## Debugging tools and commands

### Enable debug logging

```bash
export VLLM_LOGGING_LEVEL=DEBUG
vllm serve MODEL
```

### Monitor GPU usage

```bash
# Real-time GPU monitoring
watch -n 1 nvidia-smi

# Memory breakdown
nvidia-smi --query-gpu=memory.used,memory.free --format=csv -l 1
```

### Profile performance

```bash
# Built-in benchmarking
vllm bench throughput \
  --model MODEL \
  --input-tokens 128 \
  --output-tokens 256 \
  --num-prompts 100

vllm bench latency \
  --model MODEL \
  --input-tokens 128 \
  --output-tokens 256 \
  --batch-size 8
```

### Check metrics

```bash
# Prometheus metrics
curl http://localhost:9090/metrics

# Filter for specific metrics
curl http://localhost:9090/metrics | grep vllm_time_to_first_token

# Key metrics to monitor:
# - vllm_time_to_first_token_seconds
# - vllm_time_per_output_token_seconds
# - vllm_num_requests_running
# - vllm_gpu_cache_usage_perc
# - vllm_request_success_total
```

### Test server health

```bash
# Health check
curl http://localhost:8000/health

# Model info
curl http://localhost:8000/v1/models

# Test completion
curl http://localhost:8000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "MODEL",
    "prompt": "Hello",
    "max_tokens": 10
  }'
```

### Common environment variables

```bash
# CUDA settings
export CUDA_VISIBLE_DEVICES=0,1,2,3  # Limit to specific GPUs

# vLLM settings
export VLLM_LOGGING_LEVEL=DEBUG
export VLLM_TRACE_FUNCTION=1  # Profile functions
export VLLM_USE_V1=1  # Use v1.0 engine (faster)

# NCCL settings (distributed)
export NCCL_DEBUG=INFO
export NCCL_SOCKET_IFNAME=eth0
export NCCL_IB_DISABLE=0  # Enable InfiniBand
```

### Collect diagnostic info for bug reports

```bash
# System info
nvidia-smi
python --version
pip show vllm

# vLLM version and config
vllm --version
python -c "import vllm; print(vllm.__version__)"

# Run with debug logging
export VLLM_LOGGING_LEVEL=DEBUG
vllm serve MODEL 2>&1 | tee vllm_debug.log

# Include in bug report:
# - vllm_debug.log
# - nvidia-smi output
# - Full command used
# - Expected vs actual behavior
```
