# Local Model Integration

Current local model support, hardware recommendations, optional CPU installation instructions, configuration, and capability limitations are maintained in [MODEL_ENGINE.md](MODEL_ENGINE.md).

AEGIS uses a lazy `llama.cpp` adapter for local GGUF models. It does not download models or install inference dependencies automatically. Real inference is unavailable until an optional runtime and a compatible `.gguf` model are installed and configured.
