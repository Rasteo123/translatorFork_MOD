"""Every way the separate runner can fail, and the one thing that may happen then."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from gemini_translator.qa.estimators.base import (
    QualityEstimateRequest,
    SourceTranslationWindow,
)
from gemini_translator.qa.estimators.cometkiwi_client import (
    SCHEMA_VERSION,
    CometKiwiEstimator,
    CometKiwiRunnerConfig,
    RunnerProcessError,
    run_runner_process,
)


_RUNNER_PATH = (
    Path(__file__).resolve().parents[2] / "tools/translation_qa_cometkiwi_runner.py"
)


def _load_runner():
    spec = importlib.util.spec_from_file_location("qa_cometkiwi_runner", _RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _config(tmp_path) -> CometKiwiRunnerConfig:
    runner = tmp_path / "runner.py"
    runner.write_text("print()", encoding="utf-8")
    weights = tmp_path / "weights"
    weights.mkdir(exist_ok=True)
    return CometKiwiRunnerConfig(
        runner_path=str(runner),
        model_dir=str(weights),
        model="wmt22-cometkiwi-da",
    )


def _request(count: int = 2) -> QualityEstimateRequest:
    return QualityEstimateRequest(
        chapter_id="chapter-1",
        windows=tuple(
            SourceTranslationWindow(
                window_id=f"gap-{index:020x}",
                source="原文",
                translation="Перевод",
                visible_chars=7,
            )
            for index in range(count)
        ),
        source_language="zh",
        target_language="ru",
    )


def _estimate(tmp_path, answer):
    """Run the client against a stub process that answers exactly ``answer``."""
    seen: dict[str, object] = {}

    async def run(command, payload, *, timeout, cancellation=None):
        seen["request"] = json.loads(payload)
        seen["command"] = command
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            return answer(seen["request"])
        return answer

    estimator = CometKiwiEstimator(
        _config(tmp_path), license_accepted=True, run_process=run
    )
    result = asyncio.run(estimator.estimate(_request(), None))
    return result, seen


def test_the_request_carries_an_id_a_schema_version_and_the_model(tmp_path):
    """Without them a stale or foreign answer could be read as this chapter's."""
    result, seen = _estimate(
        tmp_path,
        lambda request: json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "request_id": request["request_id"],
                "model": request["model"],
                "device": request["device"],
                "scores": [0.7, 0.8],
            }
        ),
    )

    request = seen["request"]
    assert request["schema_version"] == SCHEMA_VERSION
    assert request["request_id"]
    assert request["model"] == "wmt22-cometkiwi-da"
    assert request["device"] == "cpu"
    assert result.status == "completed"


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        ("", "invalid_response"),
        ("not json at all", "invalid_response"),
        ('{"schema_version": 99, "request_id": "x", "scores": [0.1, 0.2]}', "unsupported_schema_version"),
        ('{"schema_version": 1, "request_id": "someone-else", "scores": [0.1, 0.2]}', "request_id_mismatch"),
    ],
)
def test_a_malformed_answer_is_unavailable_with_a_named_reason(tmp_path, answer, reason):
    """A broken protocol must never look like a low quality score."""
    result, _ = _estimate(tmp_path, answer)

    assert result.status == "unavailable"
    assert result.metadata["reason"] == reason


def test_a_partial_score_batch_is_refused(tmp_path):
    """Fewer scores than windows would silently attribute one window's score to another."""
    result, _ = _estimate(
        tmp_path,
        lambda request: json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "request_id": request["request_id"],
                "scores": [0.7],
            }
        ),
    )

    assert result.metadata["reason"] == "score_count_mismatch"


def test_a_runner_error_marker_becomes_a_short_reason_without_chapter_text(tmp_path):
    """Whatever the runner says about the failure must not carry the book into a log."""
    result, _ = _estimate(
        tmp_path,
        lambda request: json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "request_id": request["request_id"],
                "error": "CUDA out of memory while scoring 原文",
            }
        ),
    )

    assert result.status == "unavailable"
    assert "原文" not in result.metadata["reason"]
    assert result.metadata["reason"].startswith("runner_error:")


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (TimeoutError("slow"), "timeout"),
        (RunnerProcessError("out_of_memory"), "out_of_memory"),
        (RunnerProcessError("runner_exit_1"), "runner_exit_1"),
        (OSError("gone"), "runner_failed"),
    ],
)
def test_every_process_failure_leaves_the_session_running(tmp_path, error, reason):
    """QA is optional: nothing it runs may raise into the translation loop."""
    result, _ = _estimate(tmp_path, error)

    assert result.status == "unavailable"
    assert result.metadata["reason"] == reason
    assert result.chapter_score is None


def test_a_cancelled_estimate_is_not_swallowed(tmp_path):
    """Cancellation must reach the caller, or stopping a session would hang on it."""
    with pytest.raises(asyncio.CancelledError):
        _estimate(tmp_path, asyncio.CancelledError())


def test_warnings_on_the_stream_do_not_hide_the_answer(tmp_path):
    """Frameworks print to stdout uninvited; only the last JSON object is the answer."""
    result, _ = _estimate(
        tmp_path,
        lambda request: "Downloading tokenizer...\n"
        + json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "request_id": request["request_id"],
                "scores": [0.7, 0.8],
            }
        ),
    )

    assert result.status == "completed"
    assert result.window_scores == (0.7, 0.8)


def test_the_process_helper_never_uses_a_shell(tmp_path):
    """A chapter must not be able to reach a shell through an argument."""
    script = tmp_path / "echo.py"
    script.write_text(
        "import sys, json\n"
        "payload = json.loads(sys.stdin.read())\n"
        "print(json.dumps({'schema_version': 1, 'request_id': payload['request_id'],"
        " 'scores': [0.5]}))\n",
        encoding="utf-8",
    )

    answer = asyncio.run(
        run_runner_process(
            (sys.executable, str(script)),
            json.dumps({"request_id": "abc"}),
            timeout=30,
        )
    )

    assert json.loads(answer)["request_id"] == "abc"


def test_the_process_helper_reports_a_nonzero_exit(tmp_path):
    script = tmp_path / "fail.py"
    script.write_text("import sys; sys.exit(3)", encoding="utf-8")

    with pytest.raises(RunnerProcessError) as error:
        asyncio.run(
            run_runner_process((sys.executable, str(script)), "{}", timeout=30)
        )

    assert error.value.reason == "runner_exit_3"


# --- the runner script itself ---------------------------------------------


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ("not json", "invalid_request"),
        (json.dumps({"schema_version": 2, "request_id": "a", "model": "m", "model_dir": "."}), "unsupported_schema_version"),
        (json.dumps({"schema_version": 1, "request_id": "a", "model": "m"}), "invalid_request"),
    ],
)
def test_the_runner_refuses_a_bad_request_before_importing_torch(payload, reason):
    """Validation happens first, so a typo never costs a model load."""
    runner = _load_runner()

    with pytest.raises(runner.RequestError) as error:
        runner.read_request(_Stream(payload))

    assert str(error.value) == reason


def test_the_runner_refuses_a_segment_carrying_a_reference(tmp_path):
    """A reference field would quietly turn this into a metric we did not choose."""
    runner = _load_runner()
    payload = json.dumps(
        {
            "schema_version": 1,
            "request_id": "a",
            "model": "m",
            "model_dir": str(tmp_path),
            "segments": [{"source": "一", "translation": "один", "reference": "one"}],
        }
    )

    with pytest.raises(runner.RequestError) as error:
        runner.read_request(_Stream(payload))

    assert str(error.value) == "unexpected_segment_field"


def test_the_runner_refuses_a_missing_model_directory(tmp_path):
    """Nothing is ever downloaded during a check; absent weights are a refusal."""
    runner = _load_runner()
    payload = json.dumps(
        {
            "schema_version": 1,
            "request_id": "a",
            "model": "m",
            "model_dir": str(tmp_path / "nope"),
            "segments": [{"source": "一", "translation": "один"}],
        }
    )

    with pytest.raises(runner.RequestError) as error:
        runner.read_request(_Stream(payload))

    assert str(error.value) == "weights_missing"


def test_the_runner_answers_a_failure_instead_of_crashing(tmp_path, capsys):
    """A crash with no answer would look to the client like a hung process."""
    runner = _load_runner()
    stdin = _Stream("not json")
    original = sys.stdin
    sys.stdin = stdin
    try:
        code = runner.main([])
    finally:
        sys.stdin = original

    answer = json.loads(capsys.readouterr().out.strip())
    assert code == 0
    assert answer["error"] == "invalid_request"
    assert answer["schema_version"] == 1


class _Stream:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def read(self, size: int = -1) -> str:
        return self._payload
