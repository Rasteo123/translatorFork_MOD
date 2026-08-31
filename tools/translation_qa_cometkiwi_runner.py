#!/usr/bin/env python3
"""Score source/translation pairs with a local COMETKiwi model, in its own process.

This script is deliberately not part of the application package: it is meant to
run under an interpreter where PyTorch and ``unbabel-comet`` are installed,
which the translator itself never requires.  It reads one JSON request from
stdin, loads the model directory that request names, and writes one JSON answer
to stdout.

It never downloads weights, never accepts a model URL, and never sees a
reference translation — COMETKiwi is reference-free by construction, and that
is the whole reason it is usable here at all.

Protocol (one JSON object per stream, schema version 1):

    request:  {"schema_version": 1, "request_id": "...", "model": "...",
               "model_dir": "...", "device": "cpu",
               "source_language": "zh", "target_language": "ru",
               "segments": [{"source": "...", "translation": "..."}, ...]}
    answer:   {"schema_version": 1, "request_id": "...", "model": "...",
               "device": "cpu", "scores": [0.81, 0.63], "runtime_seconds": 4.2}
    failure:  {"schema_version": 1, "request_id": "...", "error": "out_of_memory"}
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time


SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 8_000_000
MAX_SEGMENTS = 512


class RequestError(ValueError):
    """The request is not something this runner is allowed to act on."""


def read_request(stream) -> dict:
    """Read and validate the request before importing anything heavy."""
    raw = stream.read(MAX_REQUEST_BYTES + 1)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if len(raw) > MAX_REQUEST_BYTES:
        raise RequestError("request_too_large")
    try:
        payload = json.loads(raw)
    except ValueError:
        raise RequestError("invalid_request") from None
    if not isinstance(payload, dict):
        raise RequestError("invalid_request")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise RequestError("unsupported_schema_version")
    for field in ("request_id", "model", "model_dir"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise RequestError("invalid_request")
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        raise RequestError("invalid_request")
    if len(segments) > MAX_SEGMENTS:
        raise RequestError("too_many_segments")
    for segment in segments:
        if not isinstance(segment, dict):
            raise RequestError("invalid_request")
        if set(segment) - {"source", "translation"}:
            raise RequestError("unexpected_segment_field")
        for field in ("source", "translation"):
            if not isinstance(segment.get(field), str) or not segment[field].strip():
                raise RequestError("invalid_request")
    model_dir = Path(payload["model_dir"])
    if not model_dir.is_dir():
        raise RequestError("weights_missing")
    return payload


def score(payload: dict) -> list[float]:
    """Import the deep-learning stack only now, and only for a valid request."""
    from comet import load_from_checkpoint  # noqa: PLC0415 - deliberately late

    checkpoint = _checkpoint_path(Path(payload["model_dir"]))
    model = load_from_checkpoint(str(checkpoint))
    data = [
        {"src": segment["source"], "mt": segment["translation"]}
        for segment in payload["segments"]
    ]
    device = str(payload.get("device") or "cpu")
    output = model.predict(
        data,
        batch_size=8,
        gpus=1 if device.startswith("cuda") else 0,
        progress_bar=False,
    )
    scores = getattr(output, "scores", None)
    if scores is None and isinstance(output, dict):
        scores = output.get("scores")
    if scores is None:
        raise RequestError("invalid_model_output")
    return [float(value) for value in scores]


def _checkpoint_path(model_dir: Path) -> Path:
    """Use the checkpoint that is in the installed directory, never a URL."""
    for candidate in sorted(model_dir.glob("*.ckpt")):
        return candidate
    raise RequestError("checkpoint_missing")


def main(argv: list[str] | None = None) -> int:
    request_id = ""
    try:
        payload = read_request(sys.stdin)
        request_id = payload["request_id"]
        started = time.monotonic()
        scores = score(payload)
        if len(scores) != len(payload["segments"]):
            raise RequestError("score_count_mismatch")
        answer = {
            "schema_version": SCHEMA_VERSION,
            "request_id": request_id,
            "model": payload["model"],
            "device": str(payload.get("device") or "cpu"),
            "scores": scores,
            "runtime_seconds": round(time.monotonic() - started, 3),
        }
    except RequestError as error:
        answer = _failure(request_id, str(error))
    except MemoryError:
        answer = _failure(request_id, "out_of_memory")
    except ImportError:
        answer = _failure(request_id, "runner_environment_incomplete")
    except Exception as error:  # noqa: BLE001 - the caller only ever sees a reason
        answer = _failure(request_id, type(error).__name__.lower())
    sys.stdout.write(json.dumps(answer, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


def _failure(request_id: str, reason: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "error": reason,
    }


if __name__ == "__main__":
    raise SystemExit(main())
