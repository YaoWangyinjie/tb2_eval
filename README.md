# Dumate Harbor Terminal-Bench 2.0 Eval

This repository connects Dumate to Harbor so Dumate can run Terminal-Bench 2.0 tasks through Harbor's official task environments and verifiers.

The adapter does not ask Dumate to run Docker itself. Harbor starts each task container, this project opens an exec bridge into `/app`, Dumate solves through that bridge, and Harbor runs the official verifier.

## Requirements

- Docker Desktop is installed and running.
- `uv` is installed.
- Dumate/opencode desktop server is running.
- The Terminal-Bench 2.0 task directory is available locally.

The local task directory used during development was:

```bash
/Users/yaowangyinjie/Desktop/auto_dumate_terminal/terminal-bench-2-main
```

Override it with `DUMATE_TB2_PATH` if your path is different.

## Run All 89 Tasks

Use `uv run harbor run` directly. No extra wrapper script is required.

```bash
cd /path/to/tb2_eval

export DUMATE_TB2_PATH="/Users/yaowangyinjie/Desktop/auto_dumate_terminal/terminal-bench-2-main"
export OPENCODE_SERVER_PORT="$(grep 'Dumate-agt started on port' "$HOME/Library/Application Support/qianfan-desktop-app/log/electron.log" | tail -1 | sed -E 's/.*port ([0-9]+).*/\1/')"

caffeinate -dimsu uv run harbor run \
  --path "$DUMATE_TB2_PATH" \
  --agent-import-path dumate_harbor_tb2.agent:DumateAgent \
  --agent-env "OPENCODE_SERVER_PORT=${OPENCODE_SERVER_PORT}" \
  --environment-import-path dumate_harbor_tb2.docker_nowait:DockerNoWaitEnvironment \
  --force-build \
  --n-concurrent 1 \
  --agent-kwarg message_timeout_sec=3900 \
  --agent-kwarg poll_after_send_sec=300 \
  --jobs-dir jobs \
  --job-name "dumate-harbor-tb2-89-$(date +%Y%m%d-%H%M%S)" \
  -y
```

Notes:

- `--force-build` builds each task image locally instead of pulling prebuilt `alexgshaw/...` images from Docker Hub.
- The official Harbor/Terminal-Bench timeout multiplier remains unchanged.
- `message_timeout_sec=3900` and `poll_after_send_sec=300` only adjust the Dumate adapter wait behavior.
- `DockerNoWaitEnvironment` keeps Docker Compose from blocking on `up --wait` and preloads `uv` for the verifier using the actual task container architecture (`linux/amd64` or `linux/arm64`).

## Outputs

Harbor writes results under:

```bash
jobs/<job-name>/
```

Useful files:

- `result.json`: job or trial result summary.
- `verifier/test-stdout.txt`: official verifier output.
- `verifier/reward.txt`: parsed reward.
- `agent/bridge.jsonl`: commands Dumate sent into the task container.
- `agent/dumate/*_messages_readable.txt`: Dumate transcript.
