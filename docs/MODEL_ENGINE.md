# Local model engine

## Status

AEGIS now has a provider-independent streaming inference engine, local GGUF discovery, a single-model manager, conservative memory preflight, bounded context assembly, generation settings, cancellation/timeouts, and health counters. The only implemented real provider is llama.cpp through the optional `llama-cpp-python` binding. No model or binding is installed/configured in this checkout, so actual token generation is **not verified here**.

The base application has no third-party runtime dependencies. Install the native binding only in a virtual environment when you choose to enable local inference. See `requirements/inference.txt`; the package is MIT licensed and includes a native C++ runtime. On this 4 GB machine, building it can take time and temporary memory. The package advertises Python 3.14 support, but a compatible wheel/build for this exact OS and CPU has not been tested. Official references: [llama-cpp-python](https://github.com/abetlen/llama-cpp-python), [PyPI release metadata](https://pypi.org/project/llama-cpp-python/), [llama.cpp CPU build instructions](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md).

## Components

- `ModelProvider` is the provider contract. Existing `InferenceProvider` imports remain supported as an alias.
- `LlamaCppProvider` is lazy, CPU-only, GGUF-capable, streams chat-completion chunks, honors generation settings, and accepts cooperative cancellation.
- `ModelRegistry` discovers `.gguf` files in `models/` and can register an explicit configured model path. Registration checks that the file exists; it never downloads files.
- `ModelConfiguration` validates context, CPU thread count, timeout, token cap, sampling, memory guard, and system instruction settings.
- `ModelManager` loads on first inference, preflights available memory, unloads before model switching, and keeps at most one provider loaded.
- `InferenceEngine` applies timeout and generation limits and records load latency, request latency, estimated token rate, process RSS, errors, and cancellation counts.
- `ConversationEngine` preserves the existing WebSocket stream contract and supplies bounded history, system instructions, and relevant memory context.

Token counts/rates are estimates from emitted text length (approximately one token per four characters). Memory is process RSS, not isolated model memory. Memory preflight is conservative but approximate; it cannot predict all native allocator and OS behavior. A generation timeout cancels between provider chunks. The native model load itself is synchronous and cannot be forcibly interrupted while the C++ library is loading.

## Configuration

All settings are optional. Without `AEGIS_MODEL_PATH`, or if the runtime/model is unavailable, AEGIS responds with `model_unavailable` and reports the configured state at `/api/health` and `/api/models`.

| Variable | Default | Purpose |
| --- | ---: | --- |
| `AEGIS_MODEL_PATH` | unset | Explicit path to a `.gguf` file. |
| `AEGIS_MODEL_CONTEXT` | `1024` | Context window; choose a modest value on 4 GB RAM. |
| `AEGIS_MODEL_THREADS` | `2` | CPU threads; this Pentium has two physical cores. |
| `AEGIS_INFERENCE_TIMEOUT` | `120` | Request timeout in seconds (includes model load time, which is not interruptible). |
| `AEGIS_MODEL_MAX_TOKENS` | `256` | Upper bound on generated tokens. |
| `AEGIS_MODEL_TEMPERATURE` | `0.2` | Sampling temperature. |
| `AEGIS_MODEL_TOP_P` | `0.9` | Nucleus sampling value. |
| `AEGIS_MODEL_REPEAT_PENALTY` | `1.1` | Repetition penalty. |
| `AEGIS_MODEL_MEMORY_FRACTION` | `0.75` | Maximum fraction of currently available RAM allowed by preflight estimate. |
| `AEGIS_SYSTEM_PROMPT` | AEGIS default | Optional system instruction. |

The API at `/api/models` lists locally discovered models and active model state. `POST /api/models/active` with `{"model_id":"..."}` switches to a model already discovered under `models/` (or the explicitly configured file). Switching unloads the current model; the new one loads on the next message.

## Hardware and model choice

Detected hardware: Intel Pentium Gold 4415U, 2 cores / 4 threads at up to 2.30 GHz, 3.38 GiB total RAM, integrated graphics, x86_64. The CPU exposes SSE4.2 but no AVX/AVX2 in this environment, so the provider forces CPU inference and GPU layers to zero. During this assessment, Linux reported about 0.95 GiB currently available. With that current headroom, the memory guard is expected to refuse even the smallest listed model. Close other applications or reboot before attempting a model; the guard uses *currently available* memory, not total RAM.

| Candidate | Download size | Estimated peak RAM | Assessment |
| --- | ---: | ---: | --- |
| Qwen3.5 0.8B Q4_K_M | about 528 MB | about 1.7 GiB, context 2048 | Best first experiment if the current `llama.cpp` build supports this architecture. The upstream card describes this size as suited to prototyping/research; do not expect robust complex reasoning. Apache-2.0 upstream; GGUF community quantization. |
| Qwen3 0.6B Q4_K_M | about 484 MB | about 1.4 GiB, context 1024 | Lower-memory fallback, with weaker instruction-following and reasoning expected from its smaller size. Apache-2.0 upstream; GGUF community quantization. |
| Qwen2.5 1.5B Instruct Q4_K_M | about 1.12 GiB | about 2.8 GiB, context 1024 | Potentially more capable for instruction following, but a risky fit on a 4 GiB machine and likely slow on this CPU. Try only with ample available RAM and a low context. |

Sizes are model-file sizes, not runtime RAM. Peak estimates include runtime and KV-cache headroom and are planning estimates, not measurements. No candidate has been benchmarked on this machine; response speed and comparative quality are therefore **unknown**. Tiny parameter counts constrain reasoning quality even if latency is tolerable. Hardware recommendations are refreshed from detected available memory at `/api/models/recommendations`.

Sources: [Qwen3.5-0.8B upstream model card and license](https://huggingface.co/Qwen/Qwen3.5-0.8B), [Qwen3.5 Q4_K_M GGUF listing and file size](https://huggingface.co/axteam/Qwen3.5-0.8B-GGUF), [Qwen3-0.6B GGUF quantization sizes](https://huggingface.co/tensorblock/Qwen_Qwen3-0.6B-GGUF), [Qwen2.5-1.5B-Instruct model card](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct), [llama-cpp-python chat completion API](https://github.com/abetlen/llama-cpp-python#chat-completion).

## Optional setup (not run)

These commands are provided for a later, explicitly chosen installation. The inference dependency is not in `requirements/base.txt`, and no model download was performed.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
CMAKE_BUILD_PARALLEL_LEVEL=1 CMAKE_ARGS="-DGGML_CUDA=OFF -DGGML_VULKAN=OFF -DGGML_NATIVE=OFF -DGGML_OPENMP=ON" .venv/bin/python -m pip install -r requirements/inference.txt
mkdir -p models
curl -L --fail --output models/Qwen3.5-0.8B-Q4_K_M.gguf \
  https://huggingface.co/axteam/Qwen3.5-0.8B-GGUF/resolve/main/Qwen3.5-0.8B-Q4_K_M.gguf
AEGIS_MODEL_PATH="$PWD/models/Qwen3.5-0.8B-Q4_K_M.gguf" \
AEGIS_MODEL_CONTEXT=2048 AEGIS_MODEL_THREADS=2 AEGIS_INFERENCE_TIMEOUT=180 \
  .venv/bin/python main.py
```

If the Qwen3.5 GGUF is unsupported by the installed llama.cpp revision, use a compatible Qwen3 GGUF with the same CPU-only setup and context 1024. The frontend and WebSocket protocol do not need model-specific changes.

## Verification classification

- Provider contracts, registry, model config, discovery, switching, context, streaming lifecycle, timeout/cancellation, metrics, and server wiring: **tested with mocked providers**.
- llama.cpp load/generation argument plumbing: **mocked adapter test only**.
- Real GGUF load, real generated answer, performance, and peak RAM: **not tested**; no model/runtime is installed.
- Remote inference providers, GPU acceleration, and automatic downloads: **not implemented**.
