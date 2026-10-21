#!/usr/bin/env bash
# 모델/MCP 호출 없이 조사 저장 기반을 실행한다. DB를 지우거나 다른 프로세스를 종료하지 않는다.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
    echo "프로젝트 .venv가 필요합니다. bash sh/setup_venv.sh로 준비해주세요."
    exit 1
fi
APP_MODE=research exec .venv/bin/python server.py
