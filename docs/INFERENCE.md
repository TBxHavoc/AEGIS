# Inference

See [MODEL_ENGINE.md](MODEL_ENGINE.md) for the implemented provider boundary, llama.cpp/GGUF setup, model compatibility, resource guard, health status, and limitations.

AEGIS prefers local inference. The base application remains dependency-free and reports inference as unavailable unless an existing GGUF path and optional `llama-cpp-python` runtime are configured. No model is downloaded automatically. No real inference has been run in this checkout.
