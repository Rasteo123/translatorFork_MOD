"""An optional local multilingual E5 embedder, loaded only when it is chosen.

onnxruntime and the tokenizer are imported inside the loader alone, so an
application without them starts normally and the online providers are unaffected.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np

from .base import EmbeddingBatch, EmbeddingRequest, validate_and_normalize_batch
from .local_model_manager import OptionalEmbeddingDependencyMissing


QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "
PREFIX_MODES = {"query": QUERY_PREFIX, "passage": PASSAGE_PREFIX}
MAX_INTRA_OP_THREADS = 8
DEFAULT_MAX_TOKENS = 512


class LocalOnnxEmbeddingProvider:
    """Embed locally with E5 conventions: prefixes, mean pooling, L2 norm."""

    name = "local_onnx"

    def __init__(
        self,
        model_dir: Path | str,
        runtime_loader: Callable[[], object],
        *,
        prefix_mode: str = "passage",
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        if not callable(runtime_loader):
            raise OptionalEmbeddingDependencyMissing("runtime_loader must be callable")
        if prefix_mode not in PREFIX_MODES:
            raise OptionalEmbeddingDependencyMissing("unsupported E5 prefix mode")
        self.model_dir = Path(model_dir)
        self.prefix_mode = prefix_mode
        self.max_tokens = max(1, int(max_tokens))
        self._runtime_loader = runtime_loader
        self._runtime = None
        self.truncated_texts = 0

    def _load(self):
        if self._runtime is None:
            self._runtime = self._runtime_loader()
        if self._runtime is None:
            raise OptionalEmbeddingDependencyMissing("local embedding runtime missing")
        return self._runtime

    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        """Return one normalized float32 vector per text, in the same order."""
        if not isinstance(request, EmbeddingRequest):
            raise OptionalEmbeddingDependencyMissing("request must be an EmbeddingRequest")
        runtime = self._load()
        prefix = PREFIX_MODES[self.prefix_mode]
        texts = tuple(f"{prefix}{text}" for text in request.texts)
        encoded = runtime.tokenize(texts, self.max_tokens)
        input_ids = np.asarray(encoded["input_ids"])
        attention_mask = np.asarray(encoded["attention_mask"]).astype(np.float32)
        if input_ids.shape[0] != len(texts):
            raise OptionalEmbeddingDependencyMissing("tokenizer returned a wrong batch")
        self.truncated_texts = int(encoded.get("truncated", 0) or 0)

        hidden = np.asarray(runtime.run(encoded), dtype=np.float32)
        if hidden.ndim != 3 or hidden.shape[0] != len(texts):
            raise OptionalEmbeddingDependencyMissing("model returned an unexpected shape")
        pooled = _mean_pool(hidden, attention_mask)
        batch = EmbeddingBatch(
            vectors=pooled,
            provider=self.name,
            model=request.model,
            dimensions=int(pooled.shape[1]),
        )
        return validate_and_normalize_batch(batch, len(texts))


def _mean_pool(hidden: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    """Average only the real tokens, exactly as the E5 models are trained."""
    mask = attention_mask[:, :, None]
    totals = (hidden * mask).sum(axis=1)
    counts = np.clip(mask.sum(axis=1), 1e-9, None)
    return np.asarray(totals / counts, dtype=np.float32)


def load_local_runtime(model_dir: Path | str, *, intra_op_threads: int = 2):
    """Load the ONNX session and tokenizer, importing their packages only here."""
    directory = Path(model_dir)
    try:
        import onnxruntime
        from tokenizers import Tokenizer
    except ImportError as error:
        raise OptionalEmbeddingDependencyMissing(
            "onnxruntime and tokenizers are not installed"
        ) from error

    model_path = directory / "model.onnx"
    tokenizer_path = directory / "tokenizer.json"
    if not model_path.is_file() or not tokenizer_path.is_file():
        raise OptionalEmbeddingDependencyMissing("local embedding model is not installed")

    options = onnxruntime.SessionOptions()
    # Translation and narration run beside this: never take every core.
    options.intra_op_num_threads = max(1, min(int(intra_op_threads), MAX_INTRA_OP_THREADS))
    session = onnxruntime.InferenceSession(
        str(model_path), options, providers=["CPUExecutionProvider"]
    )
    return _OnnxRuntimeFacade(session, Tokenizer.from_file(str(tokenizer_path)))


class _OnnxRuntimeFacade:
    """Adapt one ONNX session and tokenizer to the narrow interface above."""

    def __init__(self, session, tokenizer) -> None:
        self._session = session
        self._tokenizer = tokenizer

    def tokenize(self, texts, max_tokens: int) -> dict:
        self._tokenizer.enable_truncation(max_length=max_tokens)
        self._tokenizer.enable_padding()
        encodings = self._tokenizer.encode_batch(list(texts))
        truncated = sum(
            1 for encoding in encodings if len(encoding.ids) >= max_tokens
        )
        return {
            "input_ids": np.asarray([encoding.ids for encoding in encodings], dtype=np.int64),
            "attention_mask": np.asarray(
                [encoding.attention_mask for encoding in encodings], dtype=np.int64
            ),
            "truncated": truncated,
        }

    def run(self, encoded: dict):
        inputs = {
            "input_ids": np.asarray(encoded["input_ids"], dtype=np.int64),
            "attention_mask": np.asarray(encoded["attention_mask"], dtype=np.int64),
        }
        names = {item.name for item in self._session.get_inputs()}
        payload = {key: value for key, value in inputs.items() if key in names}
        return self._session.run(None, payload)[0]
