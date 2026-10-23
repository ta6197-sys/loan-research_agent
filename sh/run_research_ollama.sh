#!/usr/bin/env bash
# 금융 초안을 이 컴퓨터의 Ollama qwen3.5:2b로만 생성한다.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  echo "프로젝트 .venv가 필요합니다. bash sh/setup_venv.sh로 준비해주세요."
  exit 1
fi
if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama가 필요합니다. 설치 후 ollama serve와 ollama pull qwen3.5:2b를 실행해주세요."
  exit 1
fi
if ! curl -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  echo "Ollama 서버가 실행 중이 아닙니다. 별도 터미널에서 ollama serve를 실행해주세요."
  exit 1
fi
if ! ollama list | awk 'NR>1 {print $1}' | grep -qx 'qwen3.5:2b'; then
  echo "qwen3.5:2b 모델이 없습니다. ollama pull qwen3.5:2b를 실행해주세요."
  exit 1
fi

unset OPENAI_API_KEY APP_ALLOW_EXTERNAL_MODEL FINANCE_ALLOW_EXTERNAL_MODEL
APP_MODE=research FINANCE_MODEL_BACKEND=local exec .venv/bin/python server.py
