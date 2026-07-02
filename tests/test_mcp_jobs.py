from pathlib import Path

import pytest

from gemini_translator.mcp.paths import default_state_dir, job_dir, repo_root


def test_default_state_dir_can_be_overridden(monkeypatch, tmp_path):
    monkeypatch.setenv("TRANSLATOR_MCP_STATE_DIR", str(tmp_path / "state"))

    assert default_state_dir() == tmp_path / "state"


def test_job_dir_lives_under_state_dir(tmp_path):
    assert job_dir(tmp_path, "job_abc") == tmp_path / "jobs" / "job_abc"


@pytest.mark.parametrize("job_id", ["", "../job", "/tmp/job", "nested/job", r"nested\job", "."])
def test_job_dir_rejects_unsafe_job_ids(tmp_path, job_id):
    with pytest.raises(ValueError, match="unsafe job id"):
        job_dir(tmp_path, job_id)


def test_repo_root_points_to_checkout():
    root = repo_root()

    assert (root / "gemini_translator").is_dir()
    assert (root / "README.md").is_file()


import json

from gemini_translator.mcp.jobs import (
    JobRecord,
    create_job,
    job_path,
    list_jobs,
    load_job,
    mark_finished,
    mark_running,
    redact_for_mcp,
    save_job,
    tail_log,
)


def test_create_save_load_job_roundtrip(tmp_path):
    job = create_job(
        state_dir=tmp_path,
        job_type="translation",
        argv=["python", "-m", "gemini_translator.cli", "--api-key", "secret-key"],
        project="/books/project",
        epub="/books/book.epub",
        metadata={"tool": "start_translation"},
    )

    loaded = load_job(tmp_path, job.id)

    assert loaded.id == job.id
    assert loaded.type == "translation"
    assert loaded.status == "queued"
    assert loaded.project == "/books/project"
    assert loaded.epub == "/books/book.epub"
    assert loaded.metadata == {"tool": "start_translation"}
    assert loaded.stdout_path.endswith("stdout.log")
    assert loaded.stderr_path.endswith("stderr.log")
    assert loaded.result_path.endswith("result.json")


def test_job_status_transitions_are_persisted(tmp_path):
    job = create_job(tmp_path, "translation", ["python"], project=None, epub=None)
    mark_running(job, pid=1234)
    save_job(tmp_path, job)
    loaded = load_job(tmp_path, job.id)

    assert loaded.status == "running"
    assert loaded.pid == 1234
    assert loaded.started_at is not None

    mark_finished(loaded, status="succeeded", exit_code=0)
    save_job(tmp_path, loaded)
    finished = load_job(tmp_path, job.id)

    assert finished.status == "succeeded"
    assert finished.exit_code == 0
    assert finished.finished_at is not None


def test_save_job_replaces_existing_payload_atomically(monkeypatch, tmp_path):
    job = create_job(tmp_path, "translation", ["python"], project=None, epub=None)
    path = job_path(tmp_path, job.id)
    original_open = Path.open

    class PartialTargetWriter:
        def __enter__(self):
            self.handle = original_open(path, "w", encoding="utf-8")
            return self

        def __exit__(self, exc_type, exc, traceback):
            self.handle.close()
            return False

        def write(self, _data):
            self.handle.write("{")
            raise OSError("simulated partial direct write")

    def fail_if_target_opened_for_write(self, mode="r", *args, **kwargs):
        if self == path and "w" in mode:
            return PartialTargetWriter()
        return original_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_if_target_opened_for_write)

    job.status = "running"
    save_job(tmp_path, job)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "running"
    assert not list(path.parent.glob("*.tmp"))


def test_redact_for_mcp_hides_api_keys_and_text_payloads(tmp_path):
    job = create_job(
        tmp_path,
        "translation",
        ["python", "--api-key", "secret", "--prompt-file", "prompt.txt"],
        project="/project",
        epub="/book.epub",
        metadata={"api_key": "secret", "chapter_text": "very long text"},
    )

    redacted = redact_for_mcp(job.to_dict())

    assert "secret" not in json.dumps(redacted)
    assert redacted["metadata"]["api_key"] == "<redacted>"
    assert redacted["metadata"]["chapter_text"] == "<omitted>"


def test_redact_for_mcp_hides_common_argv_secret_forms(tmp_path):
    job = create_job(
        tmp_path,
        "translation",
        ["python", "--api-key=secret", "--token=tok", "--password", "pw"],
        project="/project",
        epub="/book.epub",
    )

    redacted = redact_for_mcp(job.to_dict())
    serialized = json.dumps(redacted)

    assert "secret" not in serialized
    assert "--token=tok" not in serialized
    assert "pw" not in serialized
    assert redacted["argv"] == [
        "python",
        "--api-key=<redacted>",
        "--token=<redacted>",
        "--password",
        "<redacted>",
    ]


def test_create_job_writes_command_and_job_payloads(tmp_path):
    job = create_job(
        tmp_path,
        "translation",
        ["python", "-m", "gemini_translator.cli"],
        project="/project",
        epub="/book.epub",
    )

    command_payload = json.loads(Path(job.command_path).read_text(encoding="utf-8"))
    job_payload = json.loads((tmp_path / "jobs" / job.id / "job.json").read_text(encoding="utf-8"))

    assert command_payload == {"argv": ["python", "-m", "gemini_translator.cli"]}
    assert job_payload["id"] == job.id
    assert job_payload["status"] == "queued"


def test_list_jobs_returns_empty_and_newest_first(tmp_path):
    assert list_jobs(tmp_path) == []

    older = create_job(tmp_path, "translation", ["older"], project=None, epub=None)
    newer = create_job(tmp_path, "translation", ["newer"], project=None, epub=None)
    older.created_at = "2026-01-01T00:00:00+00:00"
    newer.created_at = "2026-01-02T00:00:00+00:00"
    save_job(tmp_path, older)
    save_job(tmp_path, newer)

    assert [job.id for job in list_jobs(tmp_path)] == [newer.id, older.id]


def test_tail_log_returns_last_lines(tmp_path):
    path = tmp_path / "sample.log"
    path.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    assert tail_log(path, limit=2) == ["three", "four"]
