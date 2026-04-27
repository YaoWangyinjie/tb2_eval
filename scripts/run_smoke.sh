#!/usr/bin/env bash
set -euo pipefail

TASK_NAME="${1:-regex-log}"
JOB_NAME="${JOB_NAME:-dumate-harbor-smoke-${TASK_NAME}-$(date +%Y%m%d-%H%M%S)}"
TB2_PATH="${DUMATE_TB2_PATH:-/Users/gaoyansong/Downloads/auto_dumate_terminal/terminal-bench-2-main}"

cd "$(dirname "$0")/.."

if [[ -x /Applications/Docker.app/Contents/Resources/bin/docker ]]; then
  export PATH="/Applications/Docker.app/Contents/Resources/bin:${PATH}"
fi

DATASET_ARGS=(--dataset terminal-bench@2.0)
if [[ -d "${TB2_PATH}" ]]; then
  DATASET_ARGS=(--path "${TB2_PATH}")
fi

uv run harbor run \
  "${DATASET_ARGS[@]}" \
  --agent-import-path dumate_harbor_tb2.agent:DumateAgent \
  --environment-import-path dumate_harbor_tb2.docker_nowait:DockerNoWaitEnvironment \
  --force-build \
  --include-task-name "${TASK_NAME}" \
  --n-tasks 1 \
  --n-concurrent 1 \
  --jobs-dir jobs \
  --job-name "${JOB_NAME}" \
  -y
