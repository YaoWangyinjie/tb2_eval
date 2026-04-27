#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -x /Applications/Docker.app/Contents/Resources/bin/docker ]]; then
  export PATH="/Applications/Docker.app/Contents/Resources/bin:${PATH}"
fi

TB2_PATH="${DUMATE_TB2_PATH:-/Users/yaowangyinjie/Desktop/auto_dumate_terminal/terminal-bench-2-main}"
JOBS_DIR="${JOBS_DIR:-jobs}"
STAMP="${STAMP:-$(date +%Y%m%d-%H%M%S)}"
JOB_NAME="${JOB_NAME:-dumate-harbor-uvfix-auto-${STAMP}}"
OPENCODE_SERVER_PORT="${OPENCODE_SERVER_PORT:-}"

# These were the rerun tasks whose verifier logs showed:
#   error: Python interpreter not found at ... linux-aarch64-gnu ...
# Override with:
#   TASKS="path-tracing train-fasttext" ./scripts/run_uvfix_amd64_subset.sh
TASKS="${TASKS:-path-tracing rstan-to-pystan path-tracing-reverse compile-compcert train-fasttext}"

if [[ -z "${OPENCODE_SERVER_PORT}" ]]; then
  ELECTRON_LOG="${HOME}/Library/Application Support/qianfan-desktop-app/log/electron.log"
  if [[ -f "${ELECTRON_LOG}" ]]; then
    OPENCODE_SERVER_PORT="$(
      grep 'Dumate-agt started on port' "${ELECTRON_LOG}" \
        | tail -1 \
        | sed -E 's/.*port ([0-9]+).*/\1/'
    )"
  fi
fi

if [[ -z "${OPENCODE_SERVER_PORT}" ]]; then
  echo "ERROR: OPENCODE_SERVER_PORT is not set and could not be inferred from Dumate logs." >&2
  echo "Set it explicitly, for example: OPENCODE_SERVER_PORT=50294 $0" >&2
  exit 2
fi

if [[ ! -d "${TB2_PATH}" ]]; then
  echo "ERROR: TB2 task path does not exist: ${TB2_PATH}" >&2
  echo "Override with: DUMATE_TB2_PATH=/path/to/terminal-bench-2-main $0" >&2
  exit 2
fi

mkdir -p "${JOBS_DIR}"

echo "Job name: ${JOB_NAME}"
echo "TB2 path: ${TB2_PATH}"
echo "Dumate port: ${OPENCODE_SERVER_PORT}"
echo "Tasks: ${TASKS}"
echo "Verifier uv platform is selected automatically from the task container architecture."
echo "Official timeout multiplier remains 1.0; only Dumate adapter wait settings are changed."

EXTRA_PROMPT='Before finishing, read verifier/test files when available and verify exact output paths. For long installs, builds, training, sampling, video processing, or simulations, use the raw Harbor bridge API with timeout_sec larger than 120 instead of the default helper. Keep official Terminal-Bench time_multiplier at 1.0. Do not start Docker yourself. Do not stop after only exploring the environment; produce the required files before finishing.'

INCLUDE_ARGS=()
for task in ${TASKS}; do
  INCLUDE_ARGS+=(--include-task-name "${task}")
done

caffeinate -dimsu uv run harbor run \
  --path "${TB2_PATH}" \
  --agent-import-path dumate_harbor_tb2.agent:DumateAgent \
  --agent-env "OPENCODE_SERVER_PORT=${OPENCODE_SERVER_PORT}" \
  --environment-import-path dumate_harbor_tb2.docker_nowait:DockerNoWaitEnvironment \
  --force-build \
  "${INCLUDE_ARGS[@]}" \
  --n-concurrent 1 \
  --agent-kwarg message_timeout_sec=3900 \
  --agent-kwarg poll_after_send_sec=300 \
  --agent-kwarg "extra_prompt=${EXTRA_PROMPT}" \
  --jobs-dir "${JOBS_DIR}" \
  --job-name "${JOB_NAME}" \
  -y
