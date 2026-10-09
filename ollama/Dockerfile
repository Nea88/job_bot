# Version must match `version` in config.yaml
FROM ollama/ollama:0.40.2

# models persist in the add-on's /data; one model at a time to fit Raspberry Pi RAM
ENV OLLAMA_MODELS=/data/models \
    OLLAMA_HOST=0.0.0.0:11434 \
    OLLAMA_NUM_PARALLEL=1 \
    OLLAMA_MAX_LOADED_MODELS=1 \
    OLLAMA_KEEP_ALIVE=30m
