#!/usr/bin/env bash
# 보관된 금융 근거 요약만 OpenAI로 보내는 조사 서버 기동 스크립트.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env.openai.local ]; then
  echo "ERROR: .env.openai.local 파일이 없습니다. OPENAI_API_KEY를 그 파일에만 입력하세요."
  exit 1
fi
if ! grep -Eq '^OPENAI_API_KEY=.{8,}' .env.openai.local; then
  echo "ERROR: .env.openai.local의 OPENAI_API_KEY가 비어 있거나 짧습니다."
  exit 1
fi

echo "OpenAI 금융 초안을 시작합니다. 실제 호출마다 비용이 발생할 수 있습니다."
echo "전송 범위: 검증된 금융 수치 문장과 보류 항목의 ID·설명만. 원문·첨부·사용자 답변·키·DB 경로는 제외합니다."
echo "모델: ${OPENAI_FINANCE_MODEL:-gpt-5-mini}; 추적: 비활성화"
echo "보관 공개 공시 전송 예외: ${FINANCE_ALLOW_ARCHIVED_PUBLIC_EXTERNAL_MODEL:-false}"
read -r -p "서버를 기동하시겠습니까? [y/N] " confirm
case "$confirm" in [yY]|[yY][eE][sS]) ;; *) echo "중단합니다."; exit 1 ;; esac

export APP_MODE=research
export FINANCE_MODEL_BACKEND=openai
export FINANCE_ALLOW_EXTERNAL_MODEL=true
export APP_ALLOW_EXTERNAL_TRACE=false
export LANGCHAIN_TRACING_V2=false
export LANGSMITH_TRACING=false
exec "${PYTHON_BIN:-.venv/bin/python}" server.py
