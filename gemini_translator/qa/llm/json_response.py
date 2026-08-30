"""Strict JSON response parsing for translation QA model calls."""

from __future__ import annotations

import json
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


def parse_single_json_object(response: str) -> dict[str, object]:
    """Parse exactly one JSON object, optionally wrapped in one markdown fence."""
    if not isinstance(response, str) or not response.strip():
        raise QaResponseSchemaError("QA response must be nonempty text")

    stripped = response.strip()
    if "```" in stripped:
        match = _FENCED_OBJECT.fullmatch(stripped)
        if match is None or "```" in match.group("body"):
            raise QaResponseSchemaError("QA response contains an invalid markdown fence")
        serialized = match.group("body")
    else:
        serialized = stripped

    try:
        parsed = json.loads(
            serialized,
            parse_constant=_reject_nonstandard_number,
            object_pairs_hook=_object_without_duplicate_keys,
        )
    except QaResponseSchemaError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise QaResponseSchemaError("QA response must contain exactly one JSON value") from exc

    if not isinstance(parsed, dict):
        raise QaResponseSchemaError("QA response must be a JSON object")
    return parsed
