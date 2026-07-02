import json
from pathlib import Path
import sys

from gemini_translator.mcp.jobs import create_job, load_job
from gemini_translator.mcp.worker import cancel_process, run_job


def test_run_job_writes_result_and_marks_success(tmp_path):
    job = create_job(
        tmp_path,
        "fake",
        [
            sys.executable,
            "-c",
            "import json; print(json.dumps({'ok': True, 'value': 7}))",
        ],
        project=None,
        epub=None,
    )

    result = run_job(tmp_path, job.id)
    loaded = load_job(tmp_path, job.id)

    assert result.status == "succeeded"
    assert loaded.status == "succeeded"
    assert loaded.exit_code == 0
    assert json.loads(Path(loaded.result_path).read_text(encoding="utf-8")) == {"ok": True, "value": 7}


def test_run_job_marks_nonzero_exit_as_failed(tmp_path):
    job = create_job(
        tmp_path,
        "fake",
        [sys.executable, "-c", "import sys; sys.stderr.write('bad\\n'); sys.exit(3)"],
        project=None,
        epub=None,
    )

    result = run_job(tmp_path, job.id)

    assert result.status == "failed"
    assert result.exit_code == 3
    assert "bad" in Path(result.stderr_path).read_text(encoding="utf-8")


def test_run_job_preserves_invalid_json_stdout(tmp_path):
    job = create_job(
        tmp_path,
        "fake",
        [sys.executable, "-c", "print('not json')"],
        project=None,
        epub=None,
    )

    result = run_job(tmp_path, job.id)

    assert result.status == "failed"
    assert "Could not parse worker JSON result" in result.error


def test_cancel_process_returns_false_for_missing_pid():
    assert cancel_process(999999999) is False
