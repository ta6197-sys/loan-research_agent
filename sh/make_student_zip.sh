#!/usr/bin/env bash
#
# all_total_vue — 학생 배포용 zip을 만드는 스크립트.
#
# .env.example만 포함하고 자격증명·실행 DB·금융 실자료는 제외한다.
# 파일 내용 전체의 비밀 탐지를 보장하는 DLP는 아니다.
#
# 실행:
#   bash sh/make_student_zip.sh [새 ZIP 경로]
# 결과:
#   ../all_total_vue_student.zip  (단계별/ 바로 아래, all_total_vue와 같은 위치)
#
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -x .venv/bin/python ]; then
  exec .venv/bin/python sh/package_student.py "$@"
fi
exec python3 sh/package_student.py "$@"
