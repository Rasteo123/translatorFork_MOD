"""The local embedder is optional: absent packages must change nothing else."""

from __future__ import annotations

import asyncio
import builtins
import importlib
from pathlib import Path

import numpy as np
import pytest

from gemini_translator.qa.embeddings import EmbeddingRequest
from gemini_translator.qa.embeddings.factory import (
    EmbeddingProviderConfig,
    create_embedding_provider,
)
from gemini_translator.qa.embeddings.local_model_manager import (
    OptionalEmbeddingDependencyMissing,
)
from gemini_translator.qa.embeddings.local_onnx import (
    PASSAGE_PREFIX,
    QUERY_PREFIX,
    LocalOnnxEmbeddingProvider,
    load_local_runtime,
)


class _FakeRuntime:
    """Answers in the shape the real ONNX facade produces."""

    def __init__(self, *, hidden=None, batch_shift=0) -> None:
        self.seen_texts: list[str] = []
        self.max_tokens = None
        self._hidden = hidden
        self._batch_shift = batch_shift

    def tokenize(self, texts, max_tokens):
        self.seen_texts = list(texts)
        self.max_tokens = max_tokens
        lengths = [min(len(text.split()), max_tokens) for text in texts]
        width = max(lengths + [1])
        input_ids = np.zeros((len(texts), width), dtype=np.int64)
        attention = np.zeros((len(texts), width), dtype=np.int64)
        for row, length in enumerate(lengths):
            attention[row, :length] = 1
            input_ids[row, :length] = np.arange(1, length + 1)
        return {
            "input_ids": input_ids,
            "attention_mask": attention,
            "truncated": sum(1 for length in lengths if length >= max_tokens),
        }

    def run(self, encoded):
        if self._hidden is not None:
            return self._hidden
        attention = np.asarray(encoded["attention_mask"], dtype=np.float32)
        rows, width = attention.shape
        hidden = np.zeros((rows + self._batch_shift, width, 4), dtype=np.float32)
        for row in range(rows):
            for token in range(width):
                # Padding carries a wildly different value, so a pooling bug shows.
                hidden[row, token] = (
                    np.array([row + 1, token + 1, 1.0, 0.5], dtype=np.float32)
                    if attention[row, token]
                    else np.array([99.0, 99.0, 99.0, 99.0], dtype=np.float32)
                )
        return hidden


def _request(*texts, model="e5-small") -> EmbeddingRequest:
    return EmbeddingRequest(
        texts=tuple(texts),
        language="ru",
        model=model,
        dimensions=None,
        task_type="semantic-similarity",
    )


def _provider(runtime, **kwargs) -> LocalOnnxEmbeddingProvider:
    return LocalOnnxEmbeddingProvider("/models/e5", lambda: runtime, **kwargs)


def test_importing_embeddings_never_requires_the_optional_packages(monkeypatch):
    """Someone without onnxruntime must still be able to open the application."""
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.split(".")[0] in {"onnxruntime", "tokenizers"}:
            raise ImportError(f"{name} is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)

    module = importlib.reload(
        importlib.import_module("gemini_translator.qa.embeddings.local_onnx")
    )

    assert module.LocalOnnxEmbeddingProvider is not None
    with pytest.raises(OptionalEmbeddingDependencyMissing):
        module.load_local_runtime("/models/e5")


def test_choosing_local_without_the_packages_says_so(tmp_path: Path):
    """A clear refusal beats a mysterious failure in the middle of a book."""
    provider = create_embedding_provider(
        EmbeddingProviderConfig(kind="local_onnx", model_dir=str(tmp_path)),
        lambda: object(),
    )

    with pytest.raises(OptionalEmbeddingDependencyMissing):
        asyncio.run(provider.embed(_request("Текст.")))


def test_auto_never_falls_back_to_the_local_model():
    """An automatic setup must not start loading a model the user never chose."""
    from gemini_translator.qa.embeddings.factory import EmbeddingContractError

    with pytest.raises(EmbeddingContractError):
        EmbeddingProviderConfig(
            kind="auto",
            providers=(EmbeddingProviderConfig(kind="local_onnx", model_dir="/m"),),
        )


def test_e5_prefixes_follow_the_selected_mode():
    """E5 vectors are only comparable when both sides use the same prefix."""
    passage_runtime = _FakeRuntime()
    query_runtime = _FakeRuntime()

    asyncio.run(_provider(passage_runtime).embed(_request("Он ушёл")))
    asyncio.run(
        _provider(query_runtime, prefix_mode="query").embed(_request("Он ушёл"))
    )

    assert passage_runtime.seen_texts == [f"{PASSAGE_PREFIX}Он ушёл"]
    assert query_runtime.seen_texts == [f"{QUERY_PREFIX}Он ушёл"]


def test_pooling_ignores_padding_and_normalizes():
    """Padding included in the average would poison every short sentence."""
    runtime = _FakeRuntime()

    batch = asyncio.run(
        _provider(runtime).embed(_request("одно слово", "три слова здесь сейчас"))
    )

    assert batch.vectors.dtype == np.float32
    assert batch.vectors.shape[0] == 2
    norms = np.linalg.norm(batch.vectors, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)
    assert not np.allclose(batch.vectors[0], batch.vectors[1])


def test_batch_order_is_preserved():
    """A reordered batch silently pairs the wrong sentences."""
    runtime = _FakeRuntime()

    batch = asyncio.run(
        _provider(runtime).embed(_request("первое", "второе", "третье"))
    )

    assert [text.replace(PASSAGE_PREFIX, "") for text in runtime.seen_texts] == [
        "первое",
        "второе",
        "третье",
    ]
    assert batch.vectors.shape[0] == 3


def test_truncation_is_counted_not_hidden():
    """A silently truncated chapter would compare only its beginning."""
    runtime = _FakeRuntime()
    provider = _provider(runtime, max_tokens=2)

    asyncio.run(provider.embed(_request("одно два три четыре")))

    assert runtime.max_tokens == 2
    assert provider.truncated_texts == 1


def test_a_wrong_model_shape_is_refused():
    """A model that answers with the wrong batch must never produce vectors."""
    provider = _provider(_FakeRuntime(batch_shift=1))

    with pytest.raises(OptionalEmbeddingDependencyMissing):
        asyncio.run(provider.embed(_request("текст")))


def test_a_two_dimensional_answer_is_refused():
    """Pooling a shape the model never returned would produce nonsense vectors."""
    provider = _provider(_FakeRuntime(hidden=np.ones((1, 4), dtype=np.float32)))

    with pytest.raises(OptionalEmbeddingDependencyMissing):
        asyncio.run(provider.embed(_request("текст")))


def test_a_missing_model_directory_is_reported_before_loading(tmp_path: Path):
    """The user must learn that the model is not installed, not that ONNX failed."""
    with pytest.raises(OptionalEmbeddingDependencyMissing):
        load_local_runtime(tmp_path / "not-installed")
