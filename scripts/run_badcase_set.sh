#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -x /Applications/Docker.app/Contents/Resources/bin/docker ]]; then
  export PATH="/Applications/Docker.app/Contents/Resources/bin:${PATH}"
fi

TB2_PATH="${DUMATE_TB2_PATH:-/Users/gaoyansong/Downloads/auto_dumate_terminal/terminal-bench-2-main}"
JOB_NAME="${JOB_NAME:-dumate-harbor-badcase-set-$(date +%Y%m%d-%H%M%S)}"

DATASET_ARGS=(--dataset terminal-bench@2.0)
if [[ -d "${TB2_PATH}" ]]; then
  DATASET_ARGS=(--path "${TB2_PATH}")
fi

uv run harbor run \
  "${DATASET_ARGS[@]}" \
  --agent-import-path dumate_harbor_tb2.agent:DumateAgent \
  --environment-import-path dumate_harbor_tb2.docker_nowait:DockerNoWaitEnvironment \
  --force-build \
  --include-task-name feal-differential-cryptanalysis \
  --include-task-name financial-document-processor \
  --include-task-name gpt2-codegolf \
  --include-task-name large-scale-text-editing \
  --include-task-name make-mips-interpreter \
  --include-task-name model-extraction-relu-logits \
  --include-task-name path-tracing \
  --include-task-name caffe-cifar-10 \
  --include-task-name compile-compcert \
  --include-task-name count-dataset-tokens \
  --include-task-name extract-moves-from-video \
  --include-task-name fix-ocaml-gc \
  --include-task-name qemu-startup \
  --n-concurrent 1 \
  --jobs-dir jobs \
  --job-name "${JOB_NAME}" \
  -y
