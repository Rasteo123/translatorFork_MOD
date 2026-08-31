"""Asking the same model the same question twice should cost once."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest

from gemini_translator.qa.language_validation import (
    LanguageQaRequest,
    LanguageQualityPipeline,
)
from gemini_translator.qa.llm import CancellationToken, QaModelSelection
from gemini_translator.qa.llm.answer_cache import QaAnswerCache, answer_digest
from gemini_translator.utils.epub_json import (
    build_html_document_model,
    build_translation_payload,
)


_CHAPTER_HTML = "<p>Он взял себе решение уйти.</p><p>Она дала ему знать.</p>"


class _Client:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    async def complete_json(
        self, prompt, *, model, max_output_tokens, cancellation, purpose=""
    ):
        self.calls.append(purpose)
        response = self.responses.get(purpose)
        if isinstance(response, BaseException):
            raise response
        if response is None:
            raise AssertionError(f"unexpected purpose {purpose!r}")
        return deepcopy(response)


def _model() -> dict:
    return build_html_document_model(_CHAPTER_HTML, document_id="chapter-1")


def _issue(model: dict, index: int = 1) -> dict:
    block_id = build_translation_payload(model)["blocks"][0]["id"]
    return {
        "issue_id": f"issue-{index}",
        "category": "grammar",
        "block_id": block_id,
        "original_text": "взял себе решение",
        "replacement_text": "принял решение",
        "objective": True,
        "confidence": 0.95,
        "explanation": "Грамматика.",
    }


def _request(document: dict, **overrides) -> LanguageQaRequest:
    values: dict[str, object] = {
        "chapter_id": "chapter-1",
        "document_model": document,
        "source_language": "en",
        "target_language": "ru",
        "model": QaModelSelection("gemini", "qa-model"),
        "cancellation": CancellationToken(),
        # The diagnosed issue is grammar and only typos may be applied, so the
        # pass stops after the diagnosis and the request count is exactly the
        # thing under test.  An empty tuple would mean "no policy at all".
        "auto_fix_categories": ("typo",),
    }
    values.update(overrides)
    return LanguageQaRequest(**values)  # type: ignore[arg-type]


def _run(cache, client, document, **overrides):
    pipeline = LanguageQualityPipeline(client, diagnosis_cache=cache)
    return asyncio.run(pipeline.check_chapter(_request(document, **overrides)))


def test_the_second_pass_over_unchanged_text_asks_nobody(tmp_path: Path):
    """Отложенная глава, перепроверенная после сети, — тот же вопрос слово в слово."""
    model = _model()
    cache = QaAnswerCache(tmp_path)
    client = _Client({"language_diagnosis": {"issues": [_issue(model)]}})

    first = _run(cache, client, model)
    second = _run(cache, client, model)

    assert client.calls == ["language_diagnosis"]
    assert [issue.issue_id for issue in first.issues] == ["issue-1"]
    assert [issue.issue_id for issue in second.issues] == ["issue-1"]


def test_changed_text_is_a_different_question(tmp_path: Path):
    """Кэш обязан промахнуться ровно тогда, когда текст стал другим."""
    cache = QaAnswerCache(tmp_path)
    first_model = _model()
    client = _Client({"language_diagnosis": {"issues": [_issue(first_model)]}})
    _run(cache, client, first_model)

    edited = build_html_document_model(
        "<p>Он принял решение уйти.</p><p>Она дала ему знать.</p>",
        document_id="chapter-1",
    )
    client.responses = {"language_diagnosis": {"issues": []}}
    _run(cache, client, edited)

    assert client.calls == ["language_diagnosis", "language_diagnosis"]


def test_another_model_is_a_different_question(tmp_path: Path):
    """Ответ одной модели не отвечает за другую."""
    model = _model()
    cache = QaAnswerCache(tmp_path)
    client = _Client({"language_diagnosis": {"issues": [_issue(model)]}})

    _run(cache, client, model)
    _run(cache, client, model, model=QaModelSelection("openai", "other-model"))

    assert client.calls == ["language_diagnosis", "language_diagnosis"]


def test_without_a_cache_nothing_changes(tmp_path: Path):
    """Кэш необязателен: без него поведение прежнее."""
    model = _model()
    client = _Client({"language_diagnosis": {"issues": [_issue(model)]}})

    _run(None, client, model)
    _run(None, client, model)

    assert client.calls == ["language_diagnosis", "language_diagnosis"]


def test_a_refused_answer_is_never_stored(tmp_path: Path):
    """Ответ, не прошедший проверку, не имеет права стать кэшем."""
    model = _model()
    cache = QaAnswerCache(tmp_path)
    broken = dict(_issue(model))
    broken["block_id"] = "b-does-not-exist"
    client = _Client({"language_diagnosis": {"issues": [broken]}})

    result = _run(cache, client, model)
    second = _run(cache, client, model)

    assert "diagnosis_block_mismatch" in result.warnings
    assert client.calls == ["language_diagnosis", "language_diagnosis"]
    assert second.issues == ()


def test_a_cached_answer_is_validated_again_on_the_way_out(tmp_path: Path):
    """Испорченный кэш может замедлить проверку, но не уговорить её."""
    model = _model()
    cache = QaAnswerCache(tmp_path)
    client = _Client({"language_diagnosis": {"issues": [_issue(model)]}})
    _run(cache, client, model)

    poisoned = {"issues": [{**_issue(model), "block_id": "b-not-in-this-chapter"}]}
    for path in tmp_path.rglob("*.json"):
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored["answer"] = poisoned
        path.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")

    result = _run(cache, client, model)

    assert "diagnosis_block_mismatch" in result.warnings
    assert result.issues == ()


def test_a_broken_cache_is_a_miss_not_a_failure(tmp_path: Path):
    """Недоступный кэш обязан вести себя как пустой."""

    class _Broken:
        def get(self, digest):
            raise OSError("cache is unreadable")

        def put(self, digest, answer):
            raise OSError("cache is unwritable")

    model = _model()
    client = _Client({"language_diagnosis": {"issues": [_issue(model)]}})

    result = _run(_Broken(), client, model)

    assert [issue.issue_id for issue in result.issues] == ["issue-1"]


def test_an_expired_answer_is_asked_again(tmp_path: Path):
    """Ответ живёт ограниченное время: модели и промпты меняются."""
    model = _model()
    cache = QaAnswerCache(tmp_path, ttl_seconds=0.0001)
    client = _Client({"language_diagnosis": {"issues": [_issue(model)]}})

    _run(cache, client, model)
    import time

    time.sleep(0.01)
    _run(cache, client, model)

    assert client.calls == ["language_diagnosis", "language_diagnosis"]


def test_the_digest_is_the_prompt_and_the_model_and_nothing_else():
    """Ключ — сам вопрос, поэтому список полей ключа не может отстать от промпта."""
    first = answer_digest("data", "v1", "gemini", "flash")

    assert first == answer_digest("data", "v1", "gemini", "flash")
    assert first != answer_digest("data ", "v1", "gemini", "flash")
    assert first != answer_digest("data", "v2", "gemini", "flash")
    assert first != answer_digest("data", "v1", "openai", "flash")
    assert first != answer_digest("data", "v1", "gemini", "pro")


def test_a_nonsense_digest_is_refused_rather_than_writing_anywhere(tmp_path: Path):
    """Имя файла берётся из ключа: оно обязано быть проверено."""
    cache = QaAnswerCache(tmp_path)

    with pytest.raises(ValueError):
        cache._path("../../escape")
