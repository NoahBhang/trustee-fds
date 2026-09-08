#!/bin/bash
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "먼저 docs/ONPREM.md의 설치 절차를 실행해 주세요."
  exit 2
fi
exec .venv/bin/python -m src.onprem
