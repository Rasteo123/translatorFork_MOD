"""Strict JSON response parsing for translation QA model calls."""

from __future__ import annotations

import json
import math
import re
from typing import NoReturn


class QaResponseSchemaError(ValueError):
    """Raised when a QA model response cannot satisfy its strict contract."""


_FENCED_OBJECT = re.compile(
    r"\A```(?:json)?[ \t]*\r?\n(?P<body>.*?)\r?\n```[ \t]*\Z",
    re.DOTALL,
)


def _reject_nonstandard_number(value: str) -> NoReturn:
    raise QaResponseSchemaError(f"non-standard JSON number is forbidden: {value}")


def _object_without_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise QaResponseSchemaError("duplicate JSON object keys are forbidden")
        result[key] = value
    return result


def _require_finite_numbers(value: object) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, (int, float)):
        try:
            normalized = float(value)
        except (OverflowError, ValueError):
            raise QaResponseSchemaError("JSON numbers must be finite") from None
        if not math.isfinite(normalized):
            raise QaResponseSchemaError("JSON numbers must be finite")
        return
    if isinstance(value, list):
        for item in value:
            _require_finite_numbers(item)
        return
    if isinstance(value, dict):
        for item in value.values():
            _require_finite_numbers(item)


def _parse_single_json_object_unsafe(response: str) -> dict[str, object]:
    if not isinstance(response, str) or not response.strip():
        raise QaResponseSchemaError("QA response must be nonempty text")

    stripped = response.strip()
    if stripped.startswith("```"):
        match = _FENCED_OBJECT.fullmatch(stripped)
        if match is None or "```" in match.group("body"):
            raise QaResponseSchemaError("QA response contains an invalid markdown fence")
        serialized = match.group("body")
    else:
        serialized = stripped

    parsed = json.loads(
        serialized,
        parse_constant=_reject_nonstandard_number,
        object_pairs_hook=_object_without_duplicate_keys,
    )

    if not isinstance(parsed, dict):
        raise QaResponseSchemaError("QA response must be a JSON object")
    _require_finite_numbers(parsed)
    return parsed


def parse_single_json_object(response: str) -> dict[str, object]:
    """Parse one JSON object without retaining rejected raw response text."""
    invalid = False
    try:
        return _parse_single_json_object_unsafe(response)
    except (
        QaResponseSchemaError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ):
        invalid = True

    # Raising outside the decoder exception context avoids retaining its raw
    # ``doc`` field. Clearing the argument also keeps parser traceback locals
    # safe for error reporters that inspect frames.
    response = None
    if invalid:  # pragma: no branch - the except path is the only fallthrough
        raise QaResponseSchemaError("QA response is not one valid JSON object") from None
    raise AssertionError("unreachable")  # pragma: no cover
