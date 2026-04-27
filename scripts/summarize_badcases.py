#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


TASKS = [
    "feal-differential-cryptanalysis",
    "financial-document-processor",
    "gpt2-codegolf",
    "large-scale-text-editing",
    "make-mips-interpreter",
    "model-extraction-relu-logits",
    "path-tracing",
    "caffe-cifar-10",
    "compile-compcert",
    "count-dataset-tokens",
    "extract-moves-from-video",
    "fix-ocaml-gc",
    "qemu-startup",
]


def latest_job_for_task(jobs_dir: Path, task: str) -> Path | None:
    candidates = []
    for path in jobs_dir.iterdir():
        if not path.is_dir():
            continue
        if task in path.name:
            candidates.append(path)
            continue
        for child in path.iterdir():
            if child.is_dir() and child.name.startswith(f"{task}__"):
                candidates.append(path)
                break
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def load_job_status(job_dir: Path, task: str) -> dict[str, str]:
    result = {
        "task": task,
        "job": job_dir.name,
        "status": "running",
        "reward": "",
        "path": str(job_dir),
    }

    top_result = job_dir / "result.json"
    if top_result.exists():
        try:
            data = json.loads(top_result.read_text())
            evals = data.get("stats", {}).get("evals", {})
            for eval_data in evals.values():
                reward_stats = eval_data.get("reward_stats", {}).get("reward", {})
                if reward_stats:
                    reward = next(iter(reward_stats.keys()))
                    result["reward"] = reward
                    result["status"] = "finished"
                    return result
                if eval_data.get("exception_stats"):
                    result["status"] = "finished_error"
                    return result
            result["status"] = "finished"
            return result
        except Exception:
            result["status"] = "result_parse_error"
            return result

    reward_files = list(job_dir.glob("*/verifier/reward.txt"))
    if reward_files:
        try:
            reward = reward_files[0].read_text().strip()
        except Exception:
            reward = "?"
        result["reward"] = reward
        result["status"] = "trial_finished"
        return result

    bridge_files = list(job_dir.glob("*/agent/bridge.jsonl"))
    if bridge_files:
        result["status"] = "agent_running"
        return result

    trial_dirs = [p for p in job_dir.iterdir() if p.is_dir()]
    if trial_dirs:
        result["status"] = "env_or_verifier_running"
        return result

    return result


def main() -> None:
    jobs_dir = Path(__file__).resolve().parent.parent / "jobs"
    print("task\tstatus\treward\tjob\tpath")
    for task in TASKS:
        job_dir = latest_job_for_task(jobs_dir, task)
        if job_dir is None:
            print(f"{task}\tnot_started\t\t\t")
            continue
        status = load_job_status(job_dir, task)
        print(
            f"{status['task']}\t{status['status']}\t{status['reward']}\t"
            f"{status['job']}\t{status['path']}"
        )


if __name__ == "__main__":
    main()
