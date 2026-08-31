"""What a reference-free quality estimate may look at, and what it may decide."""

from __future__ import annotations

import asyncio
import json
import math

import pytest

from gemini_translator.qa.estimators.base import (
    QualityEstimate,
    QualityEstimateError,
    QualityEstimateRequest,
    SourceTranslationWindow,
    length_weighted_mean,
    percentile_score,
)
from gemini_translator.qa.estimators.cometkiwi_client import (
    SCHEMA_VERSION,
    CometKiwiEstimator,
    CometKiwiRunnerConfig,
)


class _FakeRunner:
    """Stand in for the separate process: record the request, answer as told."""

    def __init__(self, scores=(0.81, 0.63), **overrides) -> None:
        self.scores = scores
        self.overrides = overrides
        self.requests: list[dict] = []
        self.starts = 0

    async def __call__(self, command, payload, *, timeout, cancellation=None):
        self.starts += 1
        request = json.loads(payload)
        self.requests.append(request)
        answer = {
            "schema_version": SCHEMA_VERSION,
            "request_id": request["request_id"],
            "model": request["model"],
            "device": request["device"],
            "scores": list(self.scores),
        }
        answer.update(self.overrides)
        return json.dumps(answer)


def _config(tmp_path, **overrides) -> CometKiwiRunnerConfig:
    runner = tmp_path / "runner.py"
    runner.write_text("print()", encoding="utf-8")
    weights = tmp_path / "weights"
    weights.mkdir(exist_ok=True)
    values = {
        "runner_path": str(runner),
        "model_dir": str(weights),
        "model": "wmt22-cometkiwi-da",
        "device": "cpu",
    }
    values.update(overrides)
    return CometKiwiRunnerConfig(**values)


def _estimator(tmp_path, runner=None, **overrides) -> CometKiwiEstimator:
    return CometKiwiEstimator(
        _config(tmp_path, **overrides.pop("config", {})),
        enabled=overrides.pop("enabled", True),
        license_accepted=overrides.pop("license_accepted", True),
        run_process=runner or _FakeRunner(),
    )


def _request(*pairs) -> QualityEstimateRequest:
    return QualityEstimateRequest(
        chapter_id="chapter-1",
        windows=tuple(
            SourceTranslationWindow(
                window_id=f"gap-{index:020x}",
                source=source,
                translation=translation,
                visible_chars=sum(
                    1 for character in translation if not character.isspace()
                ),
            )
            for index, (source, translation) in enumerate(pairs)
        ),
        source_language="zh",
        target_language="ru",
    )


def test_the_estimator_sends_only_source_and_translation(tmp_path):
    """A reference would turn this into a different metric that we cannot compute."""
    runner = _FakeRunner()
    estimator = _estimator(tmp_path, runner)

    result = asyncio.run(
        estimator.estimate(
            _request(("原文一", "Первый перевод"), ("原文二", "Второй перевод")),
            None,
        )
    )

    payload = runner.requests[0]
    assert set(payload["segments"][0]) == {"source", "translation"}
    assert "reference" not in payload["segments"][0]
    assert result.window_scores == (0.81, 0.63)
    assert result.status == "completed"
    assert not hasattr(result, "auto_fix_allowed")


def test_the_chapter_score_is_weighted_by_the_text_each_window_covers(tmp_path):
    """A one-line window must not outvote a paragraph."""
    runner = _FakeRunner(scores=(1.0, 0.0))
    estimator = _estimator(tmp_path, runner)
    request = _request(("原文一", "к"), ("原文二", "а" * 99))

    result = asyncio.run(estimator.estimate(request, None))

    assert result.chapter_score == pytest.approx(
        length_weighted_mean((1.0, 0.0), (1, 99))
    )
    assert result.minimum_score == 0.0
    assert result.p10_score == 0.0


def test_equal_scores_aggregate_to_the_same_answer_every_time(tmp_path):
    """A score that moves between runs would make the evidence unciteable."""
    estimator = _estimator(tmp_path, _FakeRunner(scores=(0.5, 0.5, 0.5)))
    request = _request(("一", "один"), ("二", "два"), ("三", "три"))

    first = asyncio.run(estimator.estimate(request, None))
    second = asyncio.run(estimator.estimate(request, None))

    assert first.window_scores == second.window_scores
    assert first.chapter_score == second.chapter_score == pytest.approx(0.5)
    assert first.p10_score == second.p10_score == 0.5


@pytest.mark.parametrize(
    "scores",
    [
        [float("nan"), 0.5],
        [float("inf"), 0.5],
        [0.5],
        [0.5, 0.5, 0.5],
        ["0.5", 0.5],
        [1.5, 0.5],
    ],
)
def test_an_unusable_score_list_is_refused_without_a_score(tmp_path, scores):
    """A NaN or a miscounted batch must not become a chapter's quality number."""
    estimator = _estimator(tmp_path, _FakeRunner(scores=scores))

    result = asyncio.run(
        estimator.estimate(_request(("一", "один"), ("二", "два")), None)
    )

    assert result.status == "unavailable"
    assert result.chapter_score is None
    assert result.window_scores == ()


def test_a_disabled_or_unlicensed_estimator_never_starts_the_process(tmp_path):
    """Neither an unchecked box nor an unread licence may spend the user's machine."""
    off = _FakeRunner()
    unlicensed = _FakeRunner()

    disabled_result = asyncio.run(
        _estimator(tmp_path, off, enabled=False).estimate(
            _request(("一", "один")), None
        )
    )
    unlicensed_result = asyncio.run(
        _estimator(tmp_path, unlicensed, license_accepted=False).estimate(
            _request(("一", "один")), None
        )
    )

    assert (disabled_result.status, off.starts) == ("disabled", 0)
    assert (unlicensed_result.status, unlicensed.starts) == ("disabled", 0)


def test_an_unconfigured_runner_or_model_reports_what_is_missing(tmp_path):
    """"Unavailable" without a reason is indistinguishable from a silent failure."""
    runner = _FakeRunner()
    missing_model = CometKiwiEstimator(
        _config(tmp_path, model=""),
        license_accepted=True,
        run_process=runner,
    )
    missing_runner = CometKiwiEstimator(
        _config(tmp_path, runner_path=str(tmp_path / "nope.py")),
        license_accepted=True,
        run_process=runner,
    )

    first = asyncio.run(missing_model.estimate(_request(("一", "один")), None))
    second = asyncio.run(missing_runner.estimate(_request(("一", "один")), None))

    assert first.metadata["reason"] == "model_missing"
    assert second.metadata["reason"] == "runner_not_found"
    assert runner.starts == 0


def test_an_estimate_never_carries_a_score_it_did_not_measure():
    """The three non-completed states are the only ones allowed to be empty."""
    with pytest.raises(QualityEstimateError):
        QualityEstimate(
            estimator="cometkiwi",
            model="m",
            window_scores=(0.5,),
            status="unavailable",
        )
    with pytest.raises(QualityEstimateError):
        QualityEstimate(estimator="cometkiwi", model="m", status="completed")


def test_a_percentile_of_one_score_is_that_score():
    """p10 must be defined for the common case of a single disputed window."""
    assert percentile_score((0.42,), 0.10) == 0.42
    assert percentile_score((0.9, 0.1, 0.5), 0.10) == 0.1
    assert not math.isnan(percentile_score((0.9, 0.1), 1.0))


# --- who is allowed to start the process ------------------------------------


def _verified_candidate(decision: str):
    """A minimal verified candidate, shaped exactly as the cascade produces one."""
    import hashlib

    from gemini_translator.qa.foreign_text_filter import ForeignTextFilter
    from gemini_translator.qa.llm.schemas import OmissionVerdict
    from gemini_translator.qa.models import (
        AlignmentSpan,
        CandidateContext,
        GapCandidate,
        VerifiedCandidate,
    )

    candidate_id = "gap-" + hashlib.sha256(b"window").hexdigest()[:20]
    left = AlignmentSpan(("s0",), ("t0",), 0.95, "1:1")
    right = AlignmentSpan(("s2",), ("t1",), 0.95, "1:1")
    candidate = GapCandidate(
        candidate_id, "source", ("s1",), (), left, right, True, ("missing_in_target",)
    )
    context = CandidateContext(
        candidate_id=candidate_id,
        source_text="原文二",
        target_text="",
        source_before="原文一",
        source_after="原文三",
        target_before="Первый перевод",
        target_after="Третий перевод",
        source_language="zh",
        target_language="ru",
        candidate_language="zh",
    )
    return VerifiedCandidate(
        candidate=candidate,
        context=context,
        verdict=OmissionVerdict(
            decision=decision,
            confidence=0.9,
            source_unit_ids=("s1",),
            missing_facts=("Потеряно.",) if decision == "missing_content" else (),
            explanation="Проверка.",
        ),
        foreign_text_decision=ForeignTextFilter().classify(candidate, context),
        eligible_for_repair=decision == "missing_content",
        status="verified",
    )


def _chapter_result(*verified):
    from gemini_translator.qa.models import RiskLevel
    from gemini_translator.qa.service import ChapterQaResult

    return ChapterQaResult(
        chapter_id="chapter-1",
        risk_level=RiskLevel.LOW if not verified else RiskLevel.MEDIUM,
        may_continue_translation=True,
        coverage_mode="semantic_alignment",
        verified=tuple(verified),
    )


def _coordinator_with(estimator, result):
    from gemini_translator.core.chapter_qa_coordinator import (
        ChapterQaCoordinator,
        TranslationReadyEvent,
    )

    class _Service:
        attached: list = []

        async def check_chapter(self, request, options, cancellation):
            return result

        def attach_quality_estimate(self, chapter_result, estimate):
            self.attached.append(estimate)
            return chapter_result

    service = _Service()
    service.attached = []
    coordinator = ChapterQaCoordinator(
        service=service,
        task_manager=None,
        request_builder=lambda event: event.chapter_id,
        quality_estimator=estimator,
    )
    event = TranslationReadyEvent(
        task_id="task-1",
        chapter_id="chapter-1",
        source_path="OEBPS/chapter-1",
        translated_path="/tmp/chapter-1",
        source_language="zh",
        target_language="ru",
    )
    return coordinator, service, event


class _CountingEstimator:
    def __init__(self) -> None:
        self.starts = 0
        self.requests = []

    async def estimate(self, request, cancellation=None):
        self.starts += 1
        self.requests.append(request)
        from gemini_translator.qa.estimators.base import aggregate

        return aggregate("cometkiwi", "wmt22-cometkiwi-da", request, (0.7,) * len(request.windows))


def test_a_clean_interchapter_check_never_starts_the_runner():
    """The estimator is heavy; a chapter nothing disputes must not pay for it."""
    estimator = _CountingEstimator()
    coordinator, service, event = _coordinator_with(estimator, _chapter_result())

    asyncio.run(coordinator._check_one(event, __import__(
        "gemini_translator.qa.service", fromlist=["QaOptions"]
    ).QaOptions()))

    assert estimator.starts == 0
    assert service.attached == []


def test_a_covered_candidate_is_settled_and_costs_nothing():
    """A model that said the text is there has already answered the question."""
    estimator = _CountingEstimator()
    coordinator, _, event = _coordinator_with(
        estimator, _chapter_result(_verified_candidate("covered"))
    )

    asyncio.run(coordinator._check_one(event, __import__(
        "gemini_translator.qa.service", fromlist=["QaOptions"]
    ).QaOptions()))

    assert estimator.starts == 0


def test_a_disputed_candidate_is_scored_once_with_its_own_window():
    """One unresolved candidate is exactly the case the estimate exists for."""
    estimator = _CountingEstimator()
    coordinator, service, event = _coordinator_with(
        estimator, _chapter_result(_verified_candidate("ambiguous"))
    )

    asyncio.run(coordinator._check_one(event, __import__(
        "gemini_translator.qa.service", fromlist=["QaOptions"]
    ).QaOptions()))

    assert estimator.starts == 1
    request = estimator.requests[0]
    assert len(request.windows) == 1
    assert "原文二" in request.windows[0].source
    assert "Первый перевод" in request.windows[0].translation
    assert service.attached[0].status == "completed"


def test_an_estimator_failure_never_breaks_the_chapter_check():
    """QA is optional twice over: the estimate may fail and the check still stands."""

    class _Broken:
        starts = 0

        async def estimate(self, request, cancellation=None):
            raise RuntimeError("runner exploded")

    coordinator, service, event = _coordinator_with(
        _Broken(), _chapter_result(_verified_candidate("ambiguous"))
    )

    result = asyncio.run(coordinator._check_one(event, __import__(
        "gemini_translator.qa.service", fromlist=["QaOptions"]
    ).QaOptions()))

    assert result is not None
    assert service.attached == []


def test_the_application_never_imports_torch_or_comet_to_run_qa():
    """The whole point of a separate runner is that the app stays light."""
    import subprocess
    import sys

    probe = (
        "import sys\n"
        "class _Blocker:\n"
        "    def find_module(self, name, path=None):\n"
        "        if name.split('.')[0] in {'torch', 'comet', 'pytorch_lightning'}:\n"
        "            raise AssertionError('QA imported ' + name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Blocker())\n"
        "import gemini_translator.qa.estimators as estimators\n"
        "import gemini_translator.qa.estimators.cometkiwi_client as client\n"
        "import gemini_translator.qa.estimators.cometkiwi_model_manager as manager\n"
        "import gemini_translator.core.chapter_qa_coordinator as coordinator\n"
        "assert estimators and client and manager and coordinator\n"
        "print('clean')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert completed.returncode == 0, completed.stderr[-2000:]
    assert completed.stdout.strip().endswith("clean")
