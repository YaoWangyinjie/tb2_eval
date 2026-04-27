#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -x /Applications/Docker.app/Contents/Resources/bin/docker ]]; then
  export PATH="/Applications/Docker.app/Contents/Resources/bin:${PATH}"
fi

STAMP="${STAMP:-$(date +%Y%m%d-%H%M%S)}"
TASKS=(
  financial-document-processor
  gpt2-codegolf
  make-mips-interpreter
  model-extraction-relu-logits
  caffe-cifar-10
  compile-compcert
  count-dataset-tokens
  extract-moves-from-video
  fix-ocaml-gc
  qemu-startup
)

MANIFEST="jobs/dumate-harbor-badcase-queue-${STAMP}.manifest.tsv"
printf "task\tjob_name\tlauncher_log\n" > "${MANIFEST}"

for task in "${TASKS[@]}"; do
  job_name="dumate-harbor-badcase-${task}-${STAMP}"
  launcher_log="jobs/${job_name}.launcher.log"
  printf "[%s] starting %s -> %s\n" "$(date '+%F %T')" "${task}" "${job_name}" | tee -a "${launcher_log}"
  printf "%s\t%s\t%s\n" "${task}" "${job_name}" "${launcher_log}" >> "${MANIFEST}"

  JOB_NAME="${job_name}" ./scripts/run_smoke.sh "${task}" 2>&1 | tee -a "${launcher_log}"

  printf "[%s] finished %s\n" "$(date '+%F %T')" "${task}" | tee -a "${launcher_log}"
done

printf "[%s] manifest: %s\n" "$(date '+%F %T')" "${MANIFEST}"
