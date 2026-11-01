#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
  echo "Python 환경이 없습니다. clip_worker/README.md의 설치 단계를 진행하세요."
  exit 1
fi
exec /usr/bin/caffeinate -i .venv/bin/python -m clip_worker.worker
