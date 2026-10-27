#!/usr/bin/env bash
#
# 로컬 Vue 기업금융 조사 화면을 제공한다.
# 금융: 먼저 bash sh/run_research.sh 실행. Vue/폰트는 프로젝트의 고정 파일이다.
# 금융 템플릿 변경 시 node sh/build_finance_ui.cjs로 미리 컴파일한다.
#
# 백엔드(8100)를 먼저 실행한다.
#
# 종료하려면 이 터미널에서 Ctrl+C.
#
set -euo pipefail
cd "$(dirname "$0")/.."

# 이 머신은 plain python3/python이 alias로 다른 경로를 가리켜 venv를 무시하는
# 경우가 있어, 이 프로젝트 전용 .venv가 있으면 PATH 맨 앞에 명시적으로 꽂아둔다.
[ -x ".venv/bin/python3" ] && export PATH="$PWD/.venv/bin:$PATH"

# ── 0) 백엔드(8100)가 먼저 떠 있는지 확인 ───────────────────────────────
if ! curl -s --max-time 2 http://127.0.0.1:8100/health >/dev/null 2>&1; then
    echo "ERROR: 백엔드(http://127.0.0.1:8100)가 응답하지 않습니다."
    echo "  먼저 다른 터미널에서 아래 중 하나로 백엔드를 띄우세요:"
    echo "    bash sh/run_research.sh    (금융 조사, 로컬 전용)"
    echo "    bash sh/run_research_ollama.sh  (로컬 Ollama)"
    echo "    bash sh/run_research_openai.sh  (명시적 외부 모델)"
    exit 1
fi

# ── 1) 이전 실행 정리 ──────────────────────────────────────────────────
if lsof -tiTCP:8600 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "ERROR: 8600 포트를 이미 사용 중입니다. 기존 프로세스를 확인해주세요."
    exit 1
fi

cleanup() {
    echo
    echo "프론트엔드(web_demo_server.py)를 종료합니다..."
    if [ -n "${SERVER_PID:-}" ]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

# ── 2) web_demo_server.py 기동 (백그라운드로 띄운 뒤 준비될 때까지 대기) ─
python3 web_demo_server.py &
SERVER_PID=$!
for _ in $(seq 1 40); do
    curl -s http://127.0.0.1:8600/health >/dev/null 2>&1 && break
    sleep 0.5
done

cat <<EOF

################################################################
 프론트엔드가 떠 있습니다 → http://localhost:8600
 (기업금융 조사 및 보고서)
################################################################

브라우저에서 http://localhost:8600 접속해 저장된 조사와 보고서를 확인합니다.
종료하려면 이 터미널에서 Ctrl+C.
EOF

wait "$SERVER_PID"
