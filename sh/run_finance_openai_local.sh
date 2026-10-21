#!/usr/bin/env bash
# 보관 공개 자료의 제한된 요약만 OpenAI 금융 모델에 전송하고 로컬 화면을 함께 실행한다.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ] || [ ! -f .env.openai.local ]; then
  echo "ERROR: .venv와 .env.openai.local이 필요합니다."
  exit 1
fi
if ! grep -Eq '^OPENAI_API_KEY=.{8,}' .env.openai.local; then
  echo "ERROR: .env.openai.local의 OPENAI_API_KEY를 확인하세요."
  exit 1
fi
if lsof -tiTCP:8100 -sTCP:LISTEN >/dev/null 2>&1 || lsof -tiTCP:8600 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "ERROR: 8100 또는 8600 포트를 사용 중입니다. 기존 금융 서버를 종료한 뒤 다시 실행하세요."
  exit 1
fi
printf '%s\n' 'OpenAI 금융 초안을 시작합니다. 검증된 수치 문장과 보류 항목 설명만 전송하며, 원문·첨부·키는 전송하지 않습니다.'
read -r -p '보관 공개 자료 전송 예외를 적용해 실행할까요? [y/N] ' confirm
case "$confirm" in [yY]|[yY][eE][sS]) ;; *) echo '중단합니다.'; exit 0;; esac

APP_MODE=research FINANCE_MODEL_BACKEND=openai FINANCE_ALLOW_EXTERNAL_MODEL=true \
FINANCE_ALLOW_ARCHIVED_PUBLIC_EXTERNAL_MODEL=true APP_ALLOW_EXTERNAL_TRACE=false \
LANGCHAIN_TRACING_V2=false LANGSMITH_TRACING=false .venv/bin/python server.py >/tmp/finance-openai.log 2>&1 &
backend_pid=$!
cleanup() {
  kill "$backend_pid" 2>/dev/null || true
  wait "$backend_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
for _ in $(seq 1 40); do
  if curl -fsS --max-time 2 http://127.0.0.1:8100/health >/dev/null 2>&1; then break; fi
  sleep .25
done
if ! curl -fsS --max-time 2 http://127.0.0.1:8100/health >/dev/null 2>&1; then
  echo 'ERROR: OpenAI 금융 서버를 시작하지 못했습니다. /tmp/finance-openai.log를 확인하세요.'
  exit 1
fi
printf '%s\n' 'OpenAI 금융 사전조사 화면: http://127.0.0.1:8600'
.venv/bin/python web_demo_server.py
