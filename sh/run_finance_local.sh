#!/usr/bin/env bash
# 금융 조사 백엔드와 로컬 Vue 화면을 한 터미널에서 함께 실행한다.
# DB·원문은 삭제하거나 초기화하지 않는다.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  echo "프로젝트 .venv가 필요합니다. bash sh/setup_venv.sh로 준비해주세요."
  exit 1
fi
if lsof -tiTCP:8600 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "ERROR: 8600 포트가 이미 사용 중입니다. 기존 프런트를 종료한 뒤 다시 실행하세요."
  exit 1
fi
backend_pid=''
if ! curl -fsS --max-time 2 http://127.0.0.1:8100/health >/dev/null 2>&1; then
  APP_MODE=research .venv/bin/python server.py >/tmp/finance-research.log 2>&1 &
  backend_pid=$!
  for _ in $(seq 1 40); do
    if curl -fsS --max-time 2 http://127.0.0.1:8100/health >/dev/null 2>&1; then break; fi
    sleep 0.25
  done
  if ! curl -fsS --max-time 2 http://127.0.0.1:8100/health >/dev/null 2>&1; then
    echo "ERROR: 금융 조사 백엔드를 시작하지 못했습니다. /tmp/finance-research.log를 확인하세요."
    kill "$backend_pid" 2>/dev/null || true
    exit 1
  fi
fi
cleanup() {
  if [ -n "$backend_pid" ]; then
    kill "$backend_pid" 2>/dev/null || true
    wait "$backend_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "금융 사전조사 화면: http://127.0.0.1:8600"
.venv/bin/python web_demo_server.py
