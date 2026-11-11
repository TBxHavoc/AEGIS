# Installation

Step 1 requires Python 3.14.4 or a compatible Python 3.14 interpreter. No package installation, model download, system package, dataset, or external service is required.

1. Copy `.env.example` to `.env` only if local overrides are needed.
2. Run `/usr/bin/python3 main.py`.
3. Visit `http://127.0.0.1:8765/`.

The default bind is loopback-only. Do not place credentials in `.env` or source files.

## Optional local inference

The base install remains dependency-free. To enable local GGUF inference later, use a virtual environment and the optional dependency file. `llama-cpp-python` includes native C++ code; on this low-memory CPU machine, compiling it can be slow and may need substantial temporary RAM. The following CPU-only, single-build-job commands are instructions and have not been run:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
CMAKE_BUILD_PARALLEL_LEVEL=1 CMAKE_ARGS="-DGGML_CUDA=OFF -DGGML_VULKAN=OFF -DGGML_NATIVE=OFF -DGGML_OPENMP=ON" .venv/bin/python -m pip install -r requirements/inference.txt
```

See [MODEL_ENGINE.md](MODEL_ENGINE.md) for selecting and configuring a model. The application does not download models automatically.

