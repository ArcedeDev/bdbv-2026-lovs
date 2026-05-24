#!/bin/zsh
# SPDX-License-Identifier: Apache-2.0
set -eu

SCRIPT_DIR="${0:A:h}"
REPO_ROOT="${LOVS_REPO_ROOT:-${SCRIPT_DIR:h}}"
PYTHON_BIN="${LOVS_PYTHON:-python3}"
LOG_PATH="${LOVS_PREP_LOG:-/tmp/bdbv-lovs-source-prep.log}"
LOCK_PATH="${LOVS_PREP_LOCK:-/tmp/bdbv-lovs-source-prep.lock}"

mkdir -p "$(dirname "$LOG_PATH")"

{
  echo "========================================================================"
  echo "bdbv daily prep start $(date -u +%Y-%m-%dT%H:%M:%SZ) args=$*"
  echo "repo=$REPO_ROOT"
} >> "$LOG_PATH"

if ! mkdir "$LOCK_PATH" 2>/dev/null; then
  echo "bdbv daily prep skipped $(date -u +%Y-%m-%dT%H:%M:%SZ): another prep run is active" >> "$LOG_PATH"
  exit 0
fi
trap 'rmdir "$LOCK_PATH" 2>/dev/null || true' EXIT INT TERM

cd "$REPO_ROOT"
"$PYTHON_BIN" daily_snapshot_prep.py "$@" >> "$LOG_PATH" 2>&1

echo "bdbv daily prep end $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$LOG_PATH"
