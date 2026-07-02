from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess

from .jobs import JobRecord, load_job, mark_finished, mark_running, save_job
from .paths import repo_root


def _parse_stdout_json(stdout_path: str) -> tuple[dict | None, str | None]:
    try:
        payload = json.loads(Path(stdout_path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, f"Could not parse worker JSON result: {exc}"
    except OSError as exc:
        return None, f"Could not read worker stdout: {exc}"

    if not isinstance(payload, dict):
        return None, "Could not parse worker JSON result: expected a JSON object"
    return payload, None


def run_job(state_dir: Path, job_id: str) -> JobRecord:
    job = load_job(state_dir, job_id)

    try:
        with open(job.stdout_path, "w", encoding="utf-8") as stdout, open(
            job.stderr_path,
            "w",
            encoding="utf-8",
        ) as stderr:
            process = subprocess.Popen(
                job.argv,
                cwd=repo_root(),
                stdout=stdout,
                stderr=stderr,
                text=True,
            )
            mark_running(job, pid=process.pid)
            save_job(state_dir, job)
            exit_code = process.wait()
    except OSError as exc:
        mark_finished(job, status="failed", exit_code=None, error=f"Could not start worker process: {exc}")
        save_job(state_dir, job)
        return job

    result, parse_error = _parse_stdout_json(job.stdout_path)
    if result is not None:
        Path(job.result_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if exit_code == 0 and parse_error is None:
        mark_finished(job, status="succeeded", exit_code=exit_code)
    else:
        error = f"Worker process exited with code {exit_code}" if exit_code != 0 else parse_error
        mark_finished(job, status="failed", exit_code=exit_code, error=error)
    save_job(state_dir, job)
    return job


def cancel_process(pid: int) -> bool:
    try:
        if os.name == "nt":
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return completed.returncode == 0

        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False
