"""Shared versioned prompt loading and untrusted-data framing for QA requests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from html import escape
from importlib import resources
import json
from pathlib import Path
from uuid import uuid4


PROMPT_FILENAME = "translation_qa_prompts.json"
TAG_MARKER = "__QA_DATA_TAG__"
PAYLOAD_MARKER = "__QA_DATA_PAYLOAD__"


class PromptConfigurationError(Exception):
    """Internal sanitized signal for unavailable or unusable prompt configuration."""


def load_prompt_template(prompt_path: Path | None, prompt_version: str) -> str:
    """Return one versioned template, failing closed on any configuration defect.

    A missing, unreadable, malformed, or structurally wrong prompt file must never
    fall back to an embedded default: a silent fallback would hide a broken data
    bundle and send an unreviewed prompt to a model.
    """

    try:
        if prompt_path is None:
            resource = resources.files("gemini_translator").joinpath(
                "config", PROMPT_FILENAME
            )
            raw_config = resource.read_text(encoding="utf-8")
        else:
            raw_config = Path(prompt_path).read_text(encoding="utf-8")
        payload = json.loads(raw_config)
        if not isinstance(payload, Mapping):
            raise PromptConfigurationError
        template = payload.get(prompt_version)
        if not isinstance(template, str) or not template.strip():
            raise PromptConfigurationError
        if template.count(TAG_MARKER) != 2 or template.count(PAYLOAD_MARKER) != 1:
            raise PromptConfigurationError
        return template
    except PromptConfigurationError:
        raise
    except (
        OSError,
        UnicodeError,
        RecursionError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
        raise PromptConfigurationError from None


def render_prompt(template: str, payload_lines: Sequence[str]) -> str:
    """Wrap payload lines in a unique per-request data boundary."""
    data_tag = f"qa_data_{uuid4().hex}"
    return template.replace(TAG_MARKER, data_tag).replace(
        PAYLOAD_MARKER, "\n".join(payload_lines)
    )


def escaped(value: str) -> str:
    """Escape one untrusted book fragment so it cannot close the data boundary."""
    return escape(value, quote=True)
