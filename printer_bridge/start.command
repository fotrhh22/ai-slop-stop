#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo '프로젝트 .venv와 printer_bridge/requirements.txt 설치가 필요합니다.'
  exit 1
fi
exec "$PY" -m printer_bridge.worker --run
