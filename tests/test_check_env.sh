#!/usr/bin/env bash
# 环境预检的 smoke：无论 bsk / venv 在不在，脚本都要给出明确提示且不阻塞。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

out="$(bash tools/check_env.sh)"
grep -q 'bsk' <<<"$out"
grep -q 'venv/bin' <<<"$out"
grep -q 'acceptance_seed.py' <<<"$out"
echo "PASS: check_env 提示 bsk / venv / acceptance_seed"
