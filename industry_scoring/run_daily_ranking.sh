#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="/Users/liuguan1/Documents/github/Trading_assess/industry_scoring"
OUT_DIR="${BASE_DIR}/daily_outputs"
TODAY="$(date +%F)"

mkdir -p "${OUT_DIR}"

python3 "${BASE_DIR}/daily_industry_ranking.py" \
  --date "${TODAY}" \
  --output-dir "${OUT_DIR}" \
  "$@"

