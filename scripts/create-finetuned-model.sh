#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL_NAME="${OLLAMA_MODEL:-evoharness-alert-qwen2.5-7b:latest}"
MODEL_DIR="$ROOT_DIR/models/evoharness-alert-qwen2.5-7b"
echo "请将告警助手 GGUF 放到 $MODEL_DIR/evoharness-alert-qwen2.5-7b-q4_k_m.gguf"
ollama create "$MODEL_NAME" -f "$MODEL_DIR/Modelfile"
