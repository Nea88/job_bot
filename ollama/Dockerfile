# CPU-only Ollama for Raspberry Pi: the official image is ~3 GB compressed, almost all of it
# GPU backends (CUDA, JetPack, Vulkan, MLX) that a Pi cannot use.
# Built by CI and published as ghcr.io/nea88/job_bot-ollama; the version comes from config.yaml.
ARG OLLAMA_VERSION=0.40.2
FROM ollama/ollama:${OLLAMA_VERSION} AS full

# CPU backend (llama-server, ggml-cpu variants) is top-level in /usr/lib/ollama,
# each GPU backend lives in its own subdirectory
RUN find /usr/lib/ollama -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +

FROM ubuntu:24.04
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libopenblas0 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=full /usr/bin/ollama /usr/bin/ollama
COPY --from=full /usr/lib/ollama /usr/lib/ollama

# models persist in the add-on's /data; one model at a time to fit Raspberry Pi RAM
ENV OLLAMA_MODELS=/data/models \
    OLLAMA_HOST=0.0.0.0:11434 \
    OLLAMA_NUM_PARALLEL=1 \
    OLLAMA_MAX_LOADED_MODELS=1 \
    OLLAMA_KEEP_ALIVE=30m

EXPOSE 11434
ENTRYPOINT ["/usr/bin/ollama"]
CMD ["serve"]
