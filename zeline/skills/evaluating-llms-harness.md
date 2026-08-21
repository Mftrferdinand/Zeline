# Evaluating Llms Harness

> lm-eval-harness: benchmark LLMs (MMLU, GSM8K, etc.).

## What's inside

Evaluates LLMs across 60+ academic benchmarks (MMLU, HumanEval, GSM8K, TruthfulQA, HellaSwag). Use when benchmarking model quality, comparing models, reporting academic results, or tracking training progress. Industry standard used by EleutherAI, HuggingFace, and major labs. Supports HuggingFace, vLLM, APIs.

## Quick start

lm-evaluation-harness evaluates LLMs across 60+ academic benchmarks using standardized prompts and metrics.

**Installation**:
```bash
pip install lm-eval
```

**Evaluate any HuggingFace model**:
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks mmlu,gsm8k,hellaswag \
  --device cuda:0 \
  --batch_size 8
```

**View available tasks**:
```bash
lm_eval --tasks list
```

## Common workflows

### Workflow 1: Standard benchmark evaluation

Evaluate model on core benchmarks (MMLU, GSM8K, HumanEval).

Copy this checklist:

```
Benchmark Evaluation:
- [ ] Step 1: Choose benchmark suite
- [ ] Step 2: Configure model
- [ ] Step 3: Run evaluation
- [ ] Step 4: Analyze results
```

**Step 1: Choose benchmark suite**

**Core reasoning benchmarks**:
- **MMLU** (Massive Multitask Language Understanding) - 57 subjects, multiple choice
- **GSM8K** - Grade school math word problems
- **HellaSwag** - Common sense reasoning
- **TruthfulQA** - Truthfulness and factuality
- **ARC** (AI2 Reasoning Challenge) - Science questions

**Code benchmarks**:
- **HumanEval** - Python code generation (164 problems)
- **MBPP** (Mostly Basic Python Problems) - Python coding

**Standard suite** (recommended for model releases):
```bash
--tasks mmlu,gsm8k,hellaswag,truthfulqa,arc_challenge
```

**Step 2: Configure model**

**HuggingFace model**:
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf,dtype=bfloat16 \
  --tasks mmlu \
  --device cuda:0 \
  --batch_size auto  # Auto-detect optimal batch size
```

**Quantized model (4-bit/8-bit)**:
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf,load_in_4bit=True \
  --tasks mmlu \
  --device cuda:0
```

**Custom checkpoint**:
```bash
lm_eval --model hf \
  --model_args pretrained=/path/to/my-model,tokenizer=/path/to/tokenizer \
  --tasks mmlu \
  --device cuda:0
```

**Step 3: Run evaluation**

```bash
# Full MMLU evaluation (57 subjects)
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks mmlu \
  --num_fewshot 5 \  # 5-shot evaluation (standard)
  --batch_size 8 \
  --output_path results/ \
  --log_samples  # Save individual predictions

# Multiple benchmarks at once
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks mmlu,gsm8k,hellaswag,truthfulqa,arc_challenge \
  --num_fewshot 5 \
  --batch_size 8 \
  --output_path results/llama2-7b-eval.json
```

**Step 4: Analyze results**

Results saved to `results/llama2-7b-eval.json`:

```json
{
  "results": {
    "mmlu": {
      "acc": 0.459,
      "acc_stderr": 0.004
    },
    "gsm8k": {
      "exact_match": 0.142,
      "exact_match_stderr": 0.006
    },
    "hellaswag": {
      "acc_norm": 0.765,
      "acc_norm_stderr": 0.004
    }
  },
  "config": {
    "model": "hf",
    "model_args": "pretrained=meta-llama/Llama-2-7b-hf",
    "num_fewshot": 5
  }
}
```

### Workflow 2: Track training progress

Evaluate checkpoints during training.

```
Training Progress Tracking:
- [ ] Step 1: Set up periodic evaluation
- [ ] Step 2: Choose quick benchmarks
- [ ] Step 3: Automate evaluation
- [ ] Step 4: Plot learning curves
```

**Step 1: Set up periodic evaluation**

Evaluate every N training steps:

```bash
#!/bin/bash
# eval_checkpoint.sh

CHECKPOINT_DIR=$1
STEP=$2

lm_eval --model hf \
  --model_args pretrained=$CHECKPOINT_DIR/checkpoint-$STEP \
  --tasks gsm8k,hellaswag \
  --num_fewshot 0 \  # 0-shot for speed
  --batch_size 16 \
  --output_path results/step-$STEP.json
```

**Step 2: Choose quick benchmarks**

Fast benchmarks for frequent evaluation:
- **HellaSwag**: ~10 minutes on 1 GPU
- **GSM8K**: ~5 minutes
- **PIQA**: ~2 minutes

Avoid for frequent eval (too slow):
- **MMLU**: ~2 hours (57 subjects)
- **HumanEval**: Requires code execution

**Step 3: Automate evaluation**

Integrate with training script:

```python
# In training loop
if step % eval_interval == 0:
    model.save_pretrained(f"checkpoints/step-{step}")

    # Run evaluation
    os.system(f"./eval_checkpoint.sh checkpoints step-{step}")
```

Or use PyTorch Lightning callbacks:

```python
from pytorch_lightning import Callback

class EvalHarnessCallback(Callback):
    def on_validation_epoch_end(self, trainer, pl_module):
        step = trainer.global_step
        checkpoint_path = f"checkpoints/step-{step}"

        # Save checkpoint
        trainer.save_checkpoint(checkpoint_path)

        # Run lm-eval
        os.system(f"lm_eval --model hf --model_args pretrained={checkpoint_path} ...")
```

**Step 4: Plot learning curves**

```python
import json
import matplotlib.pyplot as plt

# Load all results
steps = []
mmlu_scores = []

for file in sorted(glob.glob("results/step-*.json")):
    with open(file) as f:
        data = json.load(f)
        step = int(file.split("-")[1].split(".")[0])
        steps.append(step)
        mmlu_scores.append(data["results"]["mmlu"]["acc"])

# Plot
plt.plot(steps, mmlu_scores)
plt.xlabel("Training Step")
plt.ylabel("MMLU Accuracy")
plt.title("Training Progress")
plt.savefig("training_curve.png")
```

### Workflow 3: Compare multiple models

Benchmark suite for model comparison.

```
Model Comparison:
- [ ] Step 1: Define model list
- [ ] Step 2: Run evaluations
- [ ] Step 3: Generate comparison table
```

**Step 1: Define model list**

```bash
# models.txt
meta-llama/Llama-2-7b-hf
meta-llama/Llama-2-13b-hf
mistralai/Mistral-7B-v0.1
microsoft/phi-2
```

**Step 2: Run evaluations**

```bash
#!/bin/bash
# eval_all_models.sh

TASKS="mmlu,gsm8k,hellaswag,truthfulqa"

while read model; do
    echo "Evaluating $model"

    # Extract model name for output file
    model_name=$(echo $model | sed 's/\//-/g')

    lm_eval --model hf \
      --model_args pretrained=$model,dtype=bfloat16 \
      --tasks $TASKS \
      --num_fewshot 5 \
      --batch_size auto \
      --output_path results/$model_name.json

done < models.txt
```

**Step 3: Generate comparison table**

```python
import json
import pandas as pd

models = [
    "meta-llama-Llama-2-7b-hf",
    "meta-llama-Llama-2-13b-hf",
    "mistralai-Mistral-7B-v0.1",
    "microsoft-phi-2"
]

tasks = ["mmlu", "gsm8k", "hellaswag", "truthfulqa"]

results = []
for model in models:
    with open(f"results/{model}.json") as f:
        data = json.load(f)
        row = {"Model": model.replace("-", "/")}
        for task in tasks:
            # Get primary metric for each task
            metrics = data["results"][task]
            if "acc" in metrics:
                row[task.upper()] = f"{metrics['acc']:.3f}"
            elif "exact_match" in metrics:
                row[task.upper()] = f"{metrics['exact_match']:.3f}"
        results.append(row)

df = pd.DataFrame(results)
print(df.to_markdown(index=False))
```

Output:
```
| Model                  | MMLU  | GSM8K | HELLASWAG | TRUTHFULQA |
|------------------------|-------|-------|-----------|------------|
| meta-llama/Llama-2-7b  | 0.459 | 0.142 | 0.765     | 0.391      |
| meta-llama/Llama-2-13b | 0.549 | 0.287 | 0.801     | 0.430      |
| mistralai/Mistral-7B   | 0.626 | 0.395 | 0.812     | 0.428      |
| microsoft/phi-2        | 0.560 | 0.613 | 0.682     | 0.447      |
```

### Workflow 4: Evaluate with vLLM (faster inference)

Use vLLM backend for 5-10x faster evaluation.

```
vLLM Evaluation:
- [ ] Step 1: Install vLLM
- [ ] Step 2: Configure vLLM backend
- [ ] Step 3: Run evaluation
```

**Step 1: Install vLLM**

```bash
pip install vllm
```

**Step 2: Configure vLLM backend**

```bash
lm_eval --model vllm \
  --model_args pretrained=meta-llama/Llama-2-7b-hf,tensor_parallel_size=1,dtype=auto,gpu_memory_utilization=0.8 \
  --tasks mmlu \
  --batch_size auto
```

**Step 3: Run evaluation**

vLLM is 5-10× faster than standard HuggingFace:

```bash
# Standard HF: ~2 hours for MMLU on 7B model
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks mmlu \
  --batch_size 8

# vLLM: ~15-20 minutes for MMLU on 7B model
lm_eval --model vllm \
  --model_args pretrained=meta-llama/Llama-2-7b-hf,tensor_parallel_size=2 \
  --tasks mmlu \
  --batch_size auto
```

## When to use vs alternatives

**Use lm-evaluation-harness when:**
- Benchmarking models for academic papers
- Comparing model quality across standard tasks
- Tracking training progress
- Reporting standardized metrics (everyone uses same prompts)
- Need reproducible evaluation

**Use alternatives instead:**
- **HELM** (Stanford): Broader evaluation (fairness, efficiency, calibration)
- **AlpacaEval**: Instruction-following evaluation with LLM judges
- **MT-Bench**: Conversational multi-turn evaluation
- **Custom scripts**: Domain-specific evaluation

## Common issues

**Issue: Evaluation too slow**

Use vLLM backend:
```bash
lm_eval --model vllm \
  --model_args pretrained=model-name,tensor_parallel_size=2
```

Or reduce fewshot examples:
```bash
--num_fewshot 0  # Instead of 5
```

Or evaluate subset of MMLU:
```bash
--tasks mmlu_stem  # Only STEM subjects
```

**Issue: Out of memory**

Reduce batch size:
```bash
--batch_size 1  # Or --batch_size auto
```

Use quantization:
```bash
--model_args pretrained=model-name,load_in_8bit=True
```

Enable CPU offloading:
```bash
--model_args pretrained=model-name,device_map=auto,offload_folder=offload
```

**Issue: Different results than reported**

Check fewshot count:
```bash
--num_fewshot 5  # Most papers use 5-shot
```

Check exact task name:
```bash
--tasks mmlu  # Not mmlu_direct or mmlu_fewshot
```

Verify model and tokenizer match:
```bash
--model_args pretrained=model-name,tokenizer=same-model-name
```

**Issue: HumanEval not executing code**

Install execution dependencies:
```bash
pip install human-eval
```

Enable code execution:
```bash
lm_eval --model hf \
  --model_args pretrained=model-name \
  --tasks humaneval \
  --allow_code_execution  # Required for HumanEval
```

## Advanced topics

**Benchmark descriptions**: See `references/benchmark-guide.md` for detailed description of all 60+ tasks, what they measure, and interpretation.

**Custom tasks**: See `references/custom-tasks.md` for creating domain-specific evaluation tasks.

**API evaluation**: See `references/api-evaluation.md` for evaluating OpenAI, Anthropic, and other API models.

**Multi-GPU strategies**: See `references/distributed-eval.md` for data parallel and tensor parallel evaluation.

## Hardware requirements

- **GPU**: NVIDIA (CUDA 11.8+), works on CPU (very slow)
- **VRAM**:
  - 7B model: 16GB (bf16) or 8GB (8-bit)
  - 13B model: 28GB (bf16) or 14GB (8-bit)
  - 70B model: Requires multi-GPU or quantization
- **Time** (7B model, single A100):
  - HellaSwag: 10 minutes
  - GSM8K: 5 minutes
  - MMLU (full): 2 hours
  - HumanEval: 20 minutes

## Resources

- GitHub: https://github.com/EleutherAI/lm-evaluation-harness
- Docs: https://github.com/EleutherAI/lm-evaluation-harness/tree/main/docs
- Task library: 60+ tasks including MMLU, GSM8K, HumanEval, TruthfulQA, HellaSwag, ARC, WinoGrande, etc.
- Leaderboard: https://huggingface.co/spaces/HuggingFaceH4/open_llm_leaderboard (uses this harness)





---

## Lampiran: `references/api-evaluation.md`

# API Evaluation

Guide to evaluating OpenAI, Anthropic, and other API-based language models.

## Overview

The lm-evaluation-harness supports evaluating API-based models through a unified `TemplateAPI` interface. This allows benchmarking of:
- OpenAI models (GPT-4, GPT-3.5, etc.)
- Anthropic models (Claude 3, Claude 2, etc.)
- Local OpenAI-compatible APIs
- Custom API endpoints

**Why evaluate API models**:
- Benchmark closed-source models
- Compare API models to open models
- Validate API performance
- Track model updates over time

## Supported API Models

| Provider | Model Type | Request Types | Logprobs |
|----------|------------|---------------|----------|
| OpenAI (completions) | `openai-completions` | All | ✅ Yes |
| OpenAI (chat) | `openai-chat-completions` | `generate_until` only | ❌ No |
| Anthropic (completions) | `anthropic-completions` | All | ❌ No |
| Anthropic (chat) | `anthropic-chat` | `generate_until` only | ❌ No |
| Local (OpenAI-compatible) | `local-completions` | Depends on server | Varies |

**Note**: Models without logprobs can only be evaluated on generation tasks, not perplexity or loglikelihood tasks.

## OpenAI Models

### Setup

```bash
export OPENAI_API_KEY=sk-...
```

### Completion Models (Legacy)

**Available models**: `davinci-002`, `babbage-002`

```bash
lm_eval --model openai-completions \
  --model_args model=davinci-002 \
  --tasks lambada_openai,hellaswag \
  --batch_size auto
```

**Supports**:
- `generate_until`: ✅
- `loglikelihood`: ✅
- `loglikelihood_rolling`: ✅

### Chat Models

**Available models**: `gpt-4`, `gpt-4-turbo`, `gpt-3.5-turbo`

```bash
lm_eval --model openai-chat-completions \
  --model_args model=gpt-4-turbo \
  --tasks mmlu,gsm8k,humaneval \
  --num_fewshot 5 \
  --batch_size auto
```

**Supports**:
- `generate_until`: ✅
- `loglikelihood`: ❌ (no logprobs)
- `loglikelihood_rolling`: ❌

**Important**: Chat models don't provide logprobs, so they can only be used with generation tasks (MMLU, GSM8K, HumanEval), not perplexity tasks.

### Configuration Options

```bash
lm_eval --model openai-chat-completions \
  --model_args \
    model=gpt-4-turbo,\
    base_url=https://api.openai.com/v1,\
    num_concurrent=5,\
    max_retries=3,\
    timeout=60,\
    batch_size=auto
```

**Parameters**:
- `model`: Model identifier (required)
- `base_url`: API endpoint (default: OpenAI)
- `num_concurrent`: Concurrent requests (default: 5)
- `max_retries`: Retry failed requests (default: 3)
- `timeout`: Request timeout in seconds (default: 60)
- `tokenizer`: Tokenizer to use (default: matches model)
- `tokenizer_backend`: `"tiktoken"` or `"huggingface"`

### Cost Management

OpenAI charges per token. Estimate costs before running:

```python
# Rough estimate
num_samples = 1000
avg_tokens_per_sample = 500  # input + output
cost_per_1k_tokens = 0.01  # GPT-3.5 Turbo

total_cost = (num_samples * avg_tokens_per_sample / 1000) * cost_per_1k_tokens
print(f"Estimated cost: ${total_cost:.2f}")
```

**Cost-saving tips**:
- Use `--limit N` for testing
- Start with `gpt-3.5-turbo` before `gpt-4`
- Set `max_gen_toks` to minimum needed
- Use `num_fewshot=0` for zero-shot when possible

## Anthropic Models

### Setup

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

### Completion Models (Legacy)

```bash
lm_eval --model anthropic-completions \
  --model_args model=claude-2.1 \
  --tasks lambada_openai,hellaswag \
  --batch_size auto
```

### Chat Models (Recommended)

**Available models**: `claude-3-5-sonnet-20241022`, `claude-3-opus-20240229`, `claude-3-sonnet-20240229`, `claude-3-haiku-20240307`

```bash
lm_eval --model anthropic-chat \
  --model_args model=claude-3-5-sonnet-20241022 \
  --tasks mmlu,gsm8k,humaneval \
  --num_fewshot 5 \
  --batch_size auto
```

**Aliases**: `anthropic-chat-completions` (same as `anthropic-chat`)

### Configuration Options

```bash
lm_eval --model anthropic-chat \
  --model_args \
    model=claude-3-5-sonnet-20241022,\
    base_url=https://api.anthropic.com,\
    num_concurrent=5,\
    max_retries=3,\
    timeout=60
```

### Cost Management

Anthropic pricing (as of 2024):
- Claude 3.5 Sonnet: $3.00 / 1M input, $15.00 / 1M output
- Claude 3 Opus: $15.00 / 1M input, $75.00 / 1M output
- Claude 3 Haiku: $0.25 / 1M input, $1.25 / 1M output

**Budget-friendly strategy**:
```bash
# Test on small sample first
lm_eval --model anthropic-chat \
  --model_args model=claude-3-haiku-20240307 \
  --tasks mmlu \
  --limit 100

# Then run full eval on best model
lm_eval --model anthropic-chat \
  --model_args model=claude-3-5-sonnet-20241022 \
  --tasks mmlu \
  --num_fewshot 5
```

## Local OpenAI-Compatible APIs

Many local inference servers expose OpenAI-compatible APIs (vLLM, Text Generation Inference, llama.cpp, Ollama).

### vLLM Local Server

**Start server**:
```bash
vllm serve meta-llama/Llama-2-7b-hf \
  --host 0.0.0.0 \
  --port 8000
```

**Evaluate**:
```bash
lm_eval --model local-completions \
  --model_args \
    model=meta-llama/Llama-2-7b-hf,\
    base_url=http://localhost:8000/v1,\
    num_concurrent=1 \
  --tasks mmlu,gsm8k \
  --batch_size auto
```

### Text Generation Inference (TGI)

**Start server**:
```bash
docker run --gpus all --shm-size 1g -p 8080:80 \
  ghcr.io/huggingface/text-generation-inference:latest \
  --model-id meta-llama/Llama-2-7b-hf
```

**Evaluate**:
```bash
lm_eval --model local-completions \
  --model_args \
    model=meta-llama/Llama-2-7b-hf,\
    base_url=http://localhost:8080/v1 \
  --tasks hellaswag,arc_challenge
```

### Ollama

**Start server**:
```bash
ollama serve
ollama pull llama2:7b
```

**Evaluate**:
```bash
lm_eval --model local-completions \
  --model_args \
    model=llama2:7b,\
    base_url=http://localhost:11434/v1 \
  --tasks mmlu
```

### llama.cpp Server

**Start server**:
```bash
./server -m models/llama-2-7b.gguf --host 0.0.0.0 --port 8080
```

**Evaluate**:
```bash
lm_eval --model local-completions \
  --model_args \
    model=llama2,\
    base_url=http://localhost:8080/v1 \
  --tasks gsm8k
```

## Custom API Implementation

For custom API endpoints, subclass `TemplateAPI`:

### Create `my_api.py`

```python
from lm_eval.models.api_models import TemplateAPI
import requests

class MyCustomAPI(TemplateAPI):
    """Custom API model."""

    def __init__(self, base_url, api_key, **kwargs):
        super().__init__(base_url=base_url, **kwargs)
        self.api_key = api_key

    def _create_payload(self, messages, gen_kwargs):
        """Create API request payload."""
        return {
            "messages": messages,
            "api_key": self.api_key,
            **gen_kwargs
        }

    def parse_generations(self, response):
        """Parse generation response."""
        return response.json()["choices"][0]["text"]

    def parse_logprobs(self, response):
        """Parse logprobs (if available)."""
        # Return None if API doesn't provide logprobs
        logprobs = response.json().get("logprobs")
        if logprobs:
            return logprobs["token_logprobs"]
        return None
```

### Register and Use

```python
from lm_eval import evaluator
from my_api import MyCustomAPI

model = MyCustomAPI(
    base_url="https://api.example.com/v1",
    api_key="your-key"
)

results = evaluator.simple_evaluate(
    model=model,
    tasks=["mmlu", "gsm8k"],
    num_fewshot=5,
    batch_size="auto"
)
```

## Comparing API and Open Models

### Side-by-Side Evaluation

```bash
# Evaluate OpenAI GPT-4
lm_eval --model openai-chat-completions \
  --model_args model=gpt-4-turbo \
  --tasks mmlu,gsm8k,hellaswag \
  --num_fewshot 5 \
  --output_path results/gpt4.json

# Evaluate open Llama 2 70B
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-70b-hf,dtype=bfloat16 \
  --tasks mmlu,gsm8k,hellaswag \
  --num_fewshot 5 \
  --output_path results/llama2-70b.json

# Compare results
python scripts/compare_results.py \
  results/gpt4.json \
  results/llama2-70b.json
```

### Typical Comparisons

| Model | MMLU | GSM8K | HumanEval | Cost |
|-------|------|-------|-----------|------|
| GPT-4 Turbo | 86.4% | 92.0% | 67.0% | $$$$ |
| Claude 3 Opus | 86.8% | 95.0% | 84.9% | $$$$ |
| GPT-3.5 Turbo | 70.0% | 57.1% | 48.1% | $$ |
| Llama 2 70B | 68.9% | 56.8% | 29.9% | Free (self-host) |
| Mixtral 8x7B | 70.6% | 58.4% | 40.2% | Free (self-host) |

## Best Practices

### Rate Limiting

Respect API rate limits:
```bash
lm_eval --model openai-chat-completions \
  --model_args \
    model=gpt-4-turbo,\
    num_concurrent=3,\  # Lower concurrency
    timeout=120 \  # Longer timeout
  --tasks mmlu
```

### Reproducibility

Set temperature to 0 for deterministic results:
```bash
lm_eval --model openai-chat-completions \
  --model_args model=gpt-4-turbo \
  --tasks mmlu \
  --gen_kwargs temperature=0.0
```

Or use `seed` for sampling:
```bash
lm_eval --model anthropic-chat \
  --model_args model=claude-3-5-sonnet-20241022 \
  --tasks gsm8k \
  --gen_kwargs temperature=0.7,seed=42
```

### Caching

API models automatically cache responses to avoid redundant calls:
```bash
# First run: makes API calls
lm_eval --model openai-chat-completions \
  --model_args model=gpt-4-turbo \
  --tasks mmlu \
  --limit 100

# Second run: uses cache (instant, free)
lm_eval --model openai-chat-completions \
  --model_args model=gpt-4-turbo \
  --tasks mmlu \
  --limit 100
```

Cache location: `~/.cache/lm_eval/`

### Error Handling

APIs can fail. Use retries:
```bash
lm_eval --model openai-chat-completions \
  --model_args \
    model=gpt-4-turbo,\
    max_retries=5,\
    timeout=120 \
  --tasks mmlu
```

## Troubleshooting

### "Authentication failed"

Check API key:
```bash
echo $OPENAI_API_KEY  # Should print sk-...
echo $ANTHROPIC_API_KEY  # Should print sk-ant-...
```

### "Rate limit exceeded"

Reduce concurrency:
```bash
--model_args num_concurrent=1
```

Or add delays between requests.

### "Timeout error"

Increase timeout:
```bash
--model_args timeout=180
```

### "Model not found"

For local APIs, verify server is running:
```bash
curl http://localhost:8000/v1/models
```

### Cost Runaway

Use `--limit` for testing:
```bash
lm_eval --model openai-chat-completions \
  --model_args model=gpt-4-turbo \
  --tasks mmlu \
  --limit 50  # Only 50 samples
```

## Advanced Features

### Custom Headers

```bash
lm_eval --model local-completions \
  --model_args \
    base_url=http://api.example.com/v1,\
    header="Authorization: Bearer token,X-Custom: value"
```

### Disable SSL Verification (Development Only)

```bash
lm_eval --model local-completions \
  --model_args \
    base_url=https://localhost:8000/v1,\
    verify_certificate=false
```

### Custom Tokenizer

```bash
lm_eval --model openai-chat-completions \
  --model_args \
    model=gpt-4-turbo,\
    tokenizer=gpt2,\
    tokenizer_backend=huggingface
```

## References

- OpenAI API: https://platform.openai.com/docs/api-reference
- Anthropic API: https://docs.anthropic.com/claude/reference
- TemplateAPI: `lm_eval/models/api_models.py`
- OpenAI models: `lm_eval/models/openai_completions.py`
- Anthropic models: `lm_eval/models/anthropic_llms.py`



---

## Lampiran: `references/benchmark-guide.md`

# Benchmark Guide

Complete guide to all 60+ evaluation tasks in lm-evaluation-harness, what they measure, and how to interpret results.

## Overview

The lm-evaluation-harness includes 60+ benchmarks spanning:
- Language understanding (MMLU, GLUE)
- Mathematical reasoning (GSM8K, MATH)
- Code generation (HumanEval, MBPP)
- Instruction following (IFEval, AlpacaEval)
- Long-context understanding (LongBench)
- Multilingual capabilities (AfroBench, NorEval)
- Reasoning (BBH, ARC)
- Truthfulness (TruthfulQA)

**List all tasks**:
```bash
lm_eval --tasks list
```

## Major Benchmarks

### MMLU (Massive Multitask Language Understanding)

**What it measures**: Broad knowledge across 57 subjects (STEM, humanities, social sciences, law).

**Task variants**:
- `mmlu`: Original 57-subject benchmark
- `mmlu_pro`: More challenging version with reasoning-focused questions
- `mmlu_prox`: Multilingual extension

**Format**: Multiple choice (4 options)

**Example**:
```
Question: What is the capital of France?
A. Berlin
B. Paris
C. London
D. Madrid
Answer: B
```

**Command**:
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks mmlu \
  --num_fewshot 5
```

**Interpretation**:
- Random: 25% (chance)
- GPT-3 (175B): 43.9%
- GPT-4: 86.4%
- Human expert: ~90%

**Good for**: Assessing general knowledge and domain expertise.

### GSM8K (Grade School Math 8K)

**What it measures**: Mathematical reasoning on grade-school level word problems.

**Task variants**:
- `gsm8k`: Base task
- `gsm8k_cot`: With chain-of-thought prompting
- `gsm_plus`: Adversarial variant with perturbations

**Format**: Free-form generation, extract numerical answer

**Example**:
```
Question: A baker made 200 cookies. He sold 3/5 of them in the morning and 1/4 of the remaining in the afternoon. How many cookies does he have left?
Answer: 60
```

**Command**:
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks gsm8k \
  --num_fewshot 5
```

**Interpretation**:
- Random: ~0%
- GPT-3 (175B): 17.0%
- GPT-4: 92.0%
- Llama 2 70B: 56.8%

**Good for**: Testing multi-step reasoning and arithmetic.

### HumanEval

**What it measures**: Python code generation from docstrings (functional correctness).

**Task variants**:
- `humaneval`: Standard benchmark
- `humaneval_instruct`: For instruction-tuned models

**Format**: Code generation, execution-based evaluation

**Example**:
```python
def has_close_elements(numbers: List[float], threshold: float) -> bool:
    """ Check if in given list of numbers, are any two numbers closer to each other than
    given threshold.
    >>> has_close_elements([1.0, 2.0, 3.0], 0.5)
    False
    >>> has_close_elements([1.0, 2.8, 3.0, 4.0, 5.0, 2.0], 0.3)
    True
    """
```

**Command**:
```bash
lm_eval --model hf \
  --model_args pretrained=codellama/CodeLlama-7b-hf \
  --tasks humaneval \
  --batch_size 1
```

**Interpretation**:
- Random: 0%
- GPT-3 (175B): 0%
- Codex: 28.8%
- GPT-4: 67.0%
- Code Llama 34B: 53.7%

**Good for**: Evaluating code generation capabilities.

### BBH (BIG-Bench Hard)

**What it measures**: 23 challenging reasoning tasks where models previously failed to beat humans.

**Categories**:
- Logical reasoning
- Math word problems
- Social understanding
- Algorithmic reasoning

**Format**: Multiple choice and free-form

**Command**:
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks bbh \
  --num_fewshot 3
```

**Interpretation**:
- Random: ~25%
- GPT-3 (175B): 33.9%
- PaLM 540B: 58.3%
- GPT-4: 86.7%

**Good for**: Testing advanced reasoning capabilities.

### IFEval (Instruction-Following Evaluation)

**What it measures**: Ability to follow specific, verifiable instructions.

**Instruction types**:
- Format constraints (e.g., "answer in 3 sentences")
- Length constraints (e.g., "use at least 100 words")
- Content constraints (e.g., "include the word 'banana'")
- Structural constraints (e.g., "use bullet points")

**Format**: Free-form generation with rule-based verification

**Command**:
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-chat-hf \
  --tasks ifeval \
  --batch_size auto
```

**Interpretation**:
- Measures: Instruction adherence (not quality)
- GPT-4: 86% instruction following
- Claude 2: 84%

**Good for**: Evaluating chat/instruct models.

### GLUE (General Language Understanding Evaluation)

**What it measures**: Natural language understanding across 9 tasks.

**Tasks**:
- `cola`: Grammatical acceptability
- `sst2`: Sentiment analysis
- `mrpc`: Paraphrase detection
- `qqp`: Question pairs
- `stsb`: Semantic similarity
- `mnli`: Natural language inference
- `qnli`: Question answering NLI
- `rte`: Recognizing textual entailment
- `wnli`: Winograd schemas

**Command**:
```bash
lm_eval --model hf \
  --model_args pretrained=bert-base-uncased \
  --tasks glue \
  --num_fewshot 0
```

**Interpretation**:
- BERT Base: 78.3 (GLUE score)
- RoBERTa Large: 88.5
- Human baseline: 87.1

**Good for**: Encoder-only models, fine-tuning baselines.

### LongBench

**What it measures**: Long-context understanding (4K-32K tokens).

**21 tasks covering**:
- Single-document QA
- Multi-document QA
- Summarization
- Few-shot learning
- Code completion
- Synthetic tasks

**Command**:
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks longbench \
  --batch_size 1
```

**Interpretation**:
- Tests context utilization
- Many models struggle beyond 4K tokens
- GPT-4 Turbo: 54.3%

**Good for**: Evaluating long-context models.

## Additional Benchmarks

### TruthfulQA

**What it measures**: Model's propensity to be truthful vs. generate plausible-sounding falsehoods.

**Format**: Multiple choice with 4-5 options

**Command**:
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks truthfulqa_mc2 \
  --batch_size auto
```

**Interpretation**:
- Larger models often score worse (more convincing lies)
- GPT-3: 58.8%
- GPT-4: 59.0%
- Human: ~94%

### ARC (AI2 Reasoning Challenge)

**What it measures**: Grade-school science questions.

**Variants**:
- `arc_easy`: Easier questions
- `arc_challenge`: Harder questions requiring reasoning

**Command**:
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks arc_challenge \
  --num_fewshot 25
```

**Interpretation**:
- ARC-Easy: Most models >80%
- ARC-Challenge random: 25%
- GPT-4: 96.3%

### HellaSwag

**What it measures**: Commonsense reasoning about everyday situations.

**Format**: Choose most plausible continuation

**Command**:
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks hellaswag \
  --num_fewshot 10
```

**Interpretation**:
- Random: 25%
- GPT-3: 78.9%
- Llama 2 70B: 85.3%

### WinoGrande

**What it measures**: Commonsense reasoning via pronoun resolution.

**Example**:
```
The trophy doesn't fit in the brown suitcase because _ is too large.
A. the trophy
B. the suitcase
```

**Command**:
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks winogrande \
  --num_fewshot 5
```

### PIQA

**What it measures**: Physical commonsense reasoning.

**Example**: "To clean a keyboard, use compressed air or..."

**Command**:
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks piqa
```

## Multilingual Benchmarks

### AfroBench

**What it measures**: Performance across 64 African languages.

**15 tasks**: NLU, text generation, knowledge, QA, math reasoning

**Command**:
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks afrobench
```

### NorEval

**What it measures**: Norwegian language understanding (9 task categories).

**Command**:
```bash
lm_eval --model hf \
  --model_args pretrained=NbAiLab/nb-gpt-j-6B \
  --tasks noreval
```

## Domain-Specific Benchmarks

### MATH

**What it measures**: High-school competition math problems.

**Command**:
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks math \
  --num_fewshot 4
```

**Interpretation**:
- Very challenging
- GPT-4: 42.5%
- Minerva 540B: 33.6%

### MBPP (Mostly Basic Python Problems)

**What it measures**: Python programming from natural language descriptions.

**Command**:
```bash
lm_eval --model hf \
  --model_args pretrained=codellama/CodeLlama-7b-hf \
  --tasks mbpp \
  --batch_size 1
```

### DROP

**What it measures**: Reading comprehension requiring discrete reasoning.

**Command**:
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks drop
```

## Benchmark Selection Guide

### For General Purpose Models

Run this suite:
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks mmlu,gsm8k,hellaswag,arc_challenge,truthfulqa_mc2 \
  --num_fewshot 5
```

### For Code Models

```bash
lm_eval --model hf \
  --model_args pretrained=codellama/CodeLlama-7b-hf \
  --tasks humaneval,mbpp \
  --batch_size 1
```

### For Chat/Instruct Models

```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-chat-hf \
  --tasks ifeval,mmlu,gsm8k_cot \
  --batch_size auto
```

### For Long Context Models

```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-3.1-8B \
  --tasks longbench \
  --batch_size 1
```

## Interpreting Results

### Understanding Metrics

**Accuracy**: Percentage of correct answers (most common)

**Exact Match (EM)**: Requires exact string match (strict)

**F1 Score**: Balances precision and recall

**BLEU/ROUGE**: Text generation similarity

**Pass@k**: Percentage passing when generating k samples

### Typical Score Ranges

| Model Size | MMLU | GSM8K | HumanEval | HellaSwag |
|------------|------|-------|-----------|-----------|
| 7B | 40-50% | 10-20% | 5-15% | 70-80% |
| 13B | 45-55% | 20-35% | 15-25% | 75-82% |
| 70B | 60-70% | 50-65% | 35-50% | 82-87% |
| GPT-4 | 86% | 92% | 67% | 95% |

### Red Flags

- **All tasks at random chance**: Model not trained properly
- **Exact 0% on generation tasks**: Likely format/parsing issue
- **Huge variance across runs**: Check seed/sampling settings
- **Better than GPT-4 on everything**: Likely contamination

## Best Practices

1. **Always report few-shot setting**: 0-shot, 5-shot, etc.
2. **Run multiple seeds**: Report mean ± std
3. **Check for data contamination**: Search training data for benchmark examples
4. **Compare to published baselines**: Validate your setup
5. **Report all hyperparameters**: Model, batch size, max tokens, temperature

## References

- Task list: `lm_eval --tasks list`
- Task README: `lm_eval/tasks/README.md`
- Papers: See individual benchmark papers



---

## Lampiran: `references/distributed-eval.md`

# Distributed Evaluation

Guide to running evaluation across multiple GPUs using data parallelism and tensor/pipeline parallelism.

## Overview

Distributed evaluation speeds up benchmarking by:
- **Data Parallelism**: Split evaluation samples across GPUs (each GPU has full model copy)
- **Tensor Parallelism**: Split model weights across GPUs (for large models)
- **Pipeline Parallelism**: Split model layers across GPUs (for very large models)

**When to use**:
- Data Parallel: Model fits on single GPU, want faster evaluation
- Tensor/Pipeline Parallel: Model too large for single GPU

## HuggingFace Models (`hf`)

### Data Parallelism (Recommended)

Each GPU loads a full copy of the model and processes a subset of evaluation data.

**Single Node (8 GPUs)**:
```bash
accelerate launch --multi_gpu --num_processes 8 \
  -m lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf,dtype=bfloat16 \
  --tasks mmlu,gsm8k,hellaswag \
  --batch_size 16
```

**Speedup**: Near-linear (8 GPUs = ~8× faster)

**Memory**: Each GPU needs full model (7B model ≈ 14GB × 8 = 112GB total)

### Tensor Parallelism (Model Sharding)

Split model weights across GPUs for models too large for single GPU.

**Without accelerate launcher**:
```bash
lm_eval --model hf \
  --model_args \
    pretrained=meta-llama/Llama-2-70b-hf,\
    parallelize=True,\
    dtype=bfloat16 \
  --tasks mmlu,gsm8k \
  --batch_size 8
```

**With 8 GPUs**: 70B model (140GB) / 8 = 17.5GB per GPU ✅

**Advanced sharding**:
```bash
lm_eval --model hf \
  --model_args \
    pretrained=meta-llama/Llama-2-70b-hf,\
    parallelize=True,\
    device_map_option=auto,\
    max_memory_per_gpu=40GB,\
    max_cpu_memory=100GB,\
    dtype=bfloat16 \
  --tasks mmlu
```

**Options**:
- `device_map_option`: `"auto"` (default), `"balanced"`, `"balanced_low_0"`
- `max_memory_per_gpu`: Max memory per GPU (e.g., `"40GB"`)
- `max_cpu_memory`: Max CPU memory for offloading
- `offload_folder`: Disk offloading directory

### Combined Data + Tensor Parallelism

Use both for very large models.

**Example: 70B model on 16 GPUs (2 copies, 8 GPUs each)**:
```bash
accelerate launch --multi_gpu --num_processes 2 \
  -m lm_eval --model hf \
  --model_args \
    pretrained=meta-llama/Llama-2-70b-hf,\
    parallelize=True,\
    dtype=bfloat16 \
  --tasks mmlu \
  --batch_size 8
```

**Result**: 2× speedup from data parallelism, 70B model fits via tensor parallelism

### Configuration with `accelerate config`

Create `~/.cache/huggingface/accelerate/default_config.yaml`:
```yaml
compute_environment: LOCAL_MACHINE
distributed_type: MULTI_GPU
num_machines: 1
num_processes: 8
gpu_ids: all
mixed_precision: bf16
```

**Then run**:
```bash
accelerate launch -m lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks mmlu
```

## vLLM Models (`vllm`)

vLLM provides highly optimized distributed inference.

### Tensor Parallelism

**Single Node (4 GPUs)**:
```bash
lm_eval --model vllm \
  --model_args \
    pretrained=meta-llama/Llama-2-70b-hf,\
    tensor_parallel_size=4,\
    dtype=auto,\
    gpu_memory_utilization=0.9 \
  --tasks mmlu,gsm8k \
  --batch_size auto
```

**Memory**: 70B model split across 4 GPUs = ~35GB per GPU

### Data Parallelism

**Multiple model replicas**:
```bash
lm_eval --model vllm \
  --model_args \
    pretrained=meta-llama/Llama-2-7b-hf,\
    data_parallel_size=4,\
    dtype=auto,\
    gpu_memory_utilization=0.8 \
  --tasks hellaswag,arc_challenge \
  --batch_size auto
```

**Result**: 4 model replicas = 4× throughput

### Combined Tensor + Data Parallelism

**Example: 8 GPUs = 4 TP × 2 DP**:
```bash
lm_eval --model vllm \
  --model_args \
    pretrained=meta-llama/Llama-2-70b-hf,\
    tensor_parallel_size=4,\
    data_parallel_size=2,\
    dtype=auto,\
    gpu_memory_utilization=0.85 \
  --tasks mmlu \
  --batch_size auto
```

**Result**: 70B model fits (TP=4), 2× speedup (DP=2)

### Multi-Node vLLM

vLLM doesn't natively support multi-node. Use Ray:

```bash
# Start Ray cluster
ray start --head --port=6379

# Run evaluation
lm_eval --model vllm \
  --model_args \
    pretrained=meta-llama/Llama-2-70b-hf,\
    tensor_parallel_size=8,\
    dtype=auto \
  --tasks mmlu
```

## NVIDIA NeMo Models (`nemo_lm`)

### Data Replication

**8 replicas on 8 GPUs**:
```bash
torchrun --nproc-per-node=8 --no-python \
  lm_eval --model nemo_lm \
  --model_args \
    path=/path/to/model.nemo,\
    devices=8 \
  --tasks hellaswag,arc_challenge \
  --batch_size 32
```

**Speedup**: Near-linear (8× faster)

### Tensor Parallelism

**4-way tensor parallelism**:
```bash
torchrun --nproc-per-node=4 --no-python \
  lm_eval --model nemo_lm \
  --model_args \
    path=/path/to/70b_model.nemo,\
    devices=4,\
    tensor_model_parallel_size=4 \
  --tasks mmlu,gsm8k \
  --batch_size 16
```

### Pipeline Parallelism

**2 TP × 2 PP on 4 GPUs**:
```bash
torchrun --nproc-per-node=4 --no-python \
  lm_eval --model nemo_lm \
  --model_args \
    path=/path/to/model.nemo,\
    devices=4,\
    tensor_model_parallel_size=2,\
    pipeline_model_parallel_size=2 \
  --tasks mmlu \
  --batch_size 8
```

**Constraint**: `devices = TP × PP`

### Multi-Node NeMo

Currently not supported by lm-evaluation-harness.

## SGLang Models (`sglang`)

### Tensor Parallelism

```bash
lm_eval --model sglang \
  --model_args \
    pretrained=meta-llama/Llama-2-70b-hf,\
    tp_size=4,\
    dtype=auto \
  --tasks gsm8k \
  --batch_size auto
```

### Data Parallelism (Deprecated)

**Note**: SGLang is deprecating data parallelism. Use tensor parallelism instead.

```bash
lm_eval --model sglang \
  --model_args \
    pretrained=meta-llama/Llama-2-7b-hf,\
    dp_size=4,\
    dtype=auto \
  --tasks mmlu
```

## Performance Comparison

### 70B Model Evaluation (MMLU, 5-shot)

| Method | GPUs | Time | Memory/GPU | Notes |
|--------|------|------|------------|-------|
| HF (no parallel) | 1 | 8 hours | 140GB (OOM) | Won't fit |
| HF (TP=8) | 8 | 2 hours | 17.5GB | Slower, fits |
| HF (DP=8) | 8 | 1 hour | 140GB (OOM) | Won't fit |
| vLLM (TP=4) | 4 | 30 min | 35GB | Fast! |
| vLLM (TP=4, DP=2) | 8 | 15 min | 35GB | Fastest |

### 7B Model Evaluation (Multiple Tasks)

| Method | GPUs | Time | Speedup |
|--------|------|------|---------|
| HF (single) | 1 | 4 hours | 1× |
| HF (DP=4) | 4 | 1 hour | 4× |
| HF (DP=8) | 8 | 30 min | 8× |
| vLLM (DP=8) | 8 | 15 min | 16× |

**Takeaway**: vLLM is significantly faster than HuggingFace for inference.

## Choosing Parallelism Strategy

### Decision Tree

```
Model fits on single GPU?
├─ YES: Use data parallelism
│   ├─ HF: accelerate launch --multi_gpu --num_processes N
│   └─ vLLM: data_parallel_size=N (fastest)
│
└─ NO: Use tensor/pipeline parallelism
    ├─ Model < 70B:
    │   └─ vLLM: tensor_parallel_size=4
    ├─ Model 70-175B:
    │   ├─ vLLM: tensor_parallel_size=8
    │   └─ Or HF: parallelize=True
    └─ Model > 175B:
        └─ Contact framework authors
```

### Memory Estimation

**Rule of thumb**:
```
Memory (GB) = Parameters (B) × Precision (bytes) × 1.2 (overhead)
```

**Examples**:
- 7B FP16: 7 × 2 × 1.2 = 16.8GB ✅ Fits A100 40GB
- 13B FP16: 13 × 2 × 1.2 = 31.2GB ✅ Fits A100 40GB
- 70B FP16: 70 × 2 × 1.2 = 168GB ❌ Need TP=4 or TP=8
- 70B BF16: 70 × 2 × 1.2 = 168GB (same as FP16)

**With tensor parallelism**:
```
Memory per GPU = Total Memory / TP
```

- 70B on 4 GPUs: 168GB / 4 = 42GB per GPU ✅
- 70B on 8 GPUs: 168GB / 8 = 21GB per GPU ✅

## Multi-Node Evaluation

### HuggingFace with SLURM

**Submit job**:
```bash
#!/bin/bash
#SBATCH --nodes=4
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1

srun accelerate launch --multi_gpu \
  --num_processes $((SLURM_NNODES * 8)) \
  -m lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks mmlu,gsm8k,hellaswag \
  --batch_size 16
```

**Submit**:
```bash
sbatch eval_job.sh
```

### Manual Multi-Node Setup

**On each node, run**:
```bash
accelerate launch \
  --multi_gpu \
  --num_machines 4 \
  --num_processes 32 \
  --main_process_ip $MASTER_IP \
  --main_process_port 29500 \
  --machine_rank $NODE_RANK \
  -m lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks mmlu
```

**Environment variables**:
- `MASTER_IP`: IP of rank 0 node
- `NODE_RANK`: 0, 1, 2, 3 for each node

## Best Practices

### 1. Start Small

Test on small sample first:
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-70b-hf,parallelize=True \
  --tasks mmlu \
  --limit 100  # Just 100 samples
```

### 2. Monitor GPU Usage

```bash
# Terminal 1: Run evaluation
lm_eval --model hf ...

# Terminal 2: Monitor
watch -n 1 nvidia-smi
```

Look for:
- GPU utilization > 90%
- Memory usage stable
- All GPUs active

### 3. Optimize Batch Size

```bash
# Auto batch size (recommended)
--batch_size auto

# Or tune manually
--batch_size 16  # Start here
--batch_size 32  # Increase if memory allows
```

### 4. Use Mixed Precision

```bash
--model_args dtype=bfloat16  # Faster, less memory
```

### 5. Check Communication

For data parallelism, check network bandwidth:
```bash
# Should see InfiniBand or high-speed network
nvidia-smi topo -m
```

## Troubleshooting

### "CUDA out of memory"

**Solutions**:
1. Increase tensor parallelism:
   ```bash
   --model_args tensor_parallel_size=8  # Was 4
   ```

2. Reduce batch size:
   ```bash
   --batch_size 4  # Was 16
   ```

3. Lower precision:
   ```bash
   --model_args dtype=int8  # Quantization
   ```

### "NCCL error" or Hanging

**Check**:
1. All GPUs visible: `nvidia-smi`
2. NCCL installed: `python -c "import torch; print(torch.cuda.nccl.version())"`
3. Network connectivity between nodes

**Fix**:
```bash
export NCCL_DEBUG=INFO  # Enable debug logging
export NCCL_IB_DISABLE=0  # Use InfiniBand if available
```

### Slow Evaluation

**Possible causes**:
1. **Data loading bottleneck**: Preprocess dataset
2. **Low GPU utilization**: Increase batch size
3. **Communication overhead**: Reduce parallelism degree

**Profile**:
```bash
lm_eval --model hf \
  --model_args pretrained=meta-llama/Llama-2-7b-hf \
  --tasks mmlu \
  --limit 100 \
  --log_samples  # Check timing
```

### GPUs Imbalanced

**Symptom**: GPU 0 at 100%, others at 50%

**Solution**: Use `device_map_option=balanced`:
```bash
--model_args parallelize=True,device_map_option=balanced
```

## Example Configurations

### Small Model (7B) - Fast Evaluation

```bash
# 8 A100s, data parallel
accelerate launch --multi_gpu --num_processes 8 \
  -m lm_eval --model hf \
  --model_args \
    pretrained=meta-llama/Llama-2-7b-hf,\
    dtype=bfloat16 \
  --tasks mmlu,gsm8k,hellaswag,arc_challenge \
  --num_fewshot 5 \
  --batch_size 32

# Time: ~30 minutes
```

### Large Model (70B) - vLLM

```bash
# 8 H100s, tensor parallel
lm_eval --model vllm \
  --model_args \
    pretrained=meta-llama/Llama-2-70b-hf,\
    tensor_parallel_size=8,\
    dtype=auto,\
    gpu_memory_utilization=0.9 \
  --tasks mmlu,gsm8k,humaneval \
  --num_fewshot 5 \
  --batch_size auto

# Time: ~1 hour
```

### Very Large Model (175B+)

**Requires specialized setup - contact framework maintainers**

## References

- HuggingFace Accelerate: https://huggingface.co/docs/accelerate/
- vLLM docs: https://docs.vllm.ai/
- NeMo docs: https://docs.nvidia.com/nemo-framework/
- lm-eval distributed guide: `docs/model_guide.md`

---

## Catatan adaptasi Zeline
- File pendukung tidak di-inline (terlalu besar/biner): references/custom-tasks.md.

