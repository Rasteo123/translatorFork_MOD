"""Reject provider blocks and model refusals masquerading as translations."""

import json
import re

from bs4 import BeautifulSoup

from gemini_translator.api.errors import ContentFilterError, ValidationFailedError


# Some Google browser/proxy transports return this notice as ordinary text,
# without a safety finish reason. Match the service notice, not policy mentions
# or quoted dialogue in a chapter.
_GOOGLE_PROMPT_BLOCK = re.compile(
    r"\AThe prompt could not be submitted\.\s*"
    r"The prompt contains sensitive words that violate Google's\s*"
    r"(?:\\?\[\s*)?Generative AI Prohibited Use policy\b",
    re.IGNORECASE,
)


# A refusal alone is ambiguous in fiction. Require both a first-person refusal
# to translate and a copyright/policy explanation or an offered summary.
_REFUSAL = re.compile(
    r"(?:^|\n)\s*[#*]*\s*"
    r"(?:(?:i(?:'m| am) sorry|sorry|unfortunately|к сожалению|извините|простите)"
    r"\s*[,!:—-]?\s*(?:but\s+)?)?"
    r"(?:i\s+(?:cannot|can't|won't)|i(?:\s+am|'m)\s+unable to|я\s+не\s+могу)"
    r"[^.!?]{0,240}?(?:\btranslat\w*\b|\bперев(?:ести|од\w*)\b)"
    r"[^.!?]{0,240}",
    re.IGNORECASE,
)
_EXPLANATION = re.compile(
    r"copyright|авторск\w*\s+прав|content policy|политик\w*\s+(?:безопасности|контента)"
    r"|(?:i can|i could|here (?:is|instead)|instead|however)[\s\S]{0,160}\bsummary\b"
    r"|(?:вместо|могу предложить|могу предоставить|вот)[\s\S]{0,160}"
    r"(?:пересказ|кратк\w*\s+(?:содержани\w*|обзор))",
    re.IGNORECASE,
)


def _response_strings(raw_response):
    text = raw_response.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[^\n]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    if text.startswith(("{", "[")):
        try:
            pending = [json.loads(text)]
        except (ValueError, RecursionError):
            pass  # Malformed JSON remains the transport parser's responsibility.
        else:
            while pending:
                value = pending.pop()
                if isinstance(value, str):
                    yield value
                elif isinstance(value, dict):
                    pending.extend(value.values())
                elif isinstance(value, list):
                    pending.extend(value)
            return
    yield text


def _response_texts(raw_response):
    if not isinstance(raw_response, str) or not raw_response.strip():
        return
    for candidate in _response_strings(raw_response):
        soup = BeautifulSoup(candidate, "html.parser")
        for tag in soup(["head", "script", "style"]):
            tag.decompose()
        yield (soup.body or soup).get_text("\n").replace("’", "'")


def validate_content_filter_response(raw_response: str) -> None:
    """Raise a content block for a provider's textual safety rejection."""
    for text in _response_texts(raw_response):
        if _GOOGLE_PROMPT_BLOCK.search(" ".join(text.split())):
            error = ContentFilterError(
                "Google заблокировал отправку запроса: содержимое нарушает политику безопасности."
            )
            error.raw_package_text = raw_response
            raise error


def validate_translation_response(raw_response):
    """Reject content blocks and retryable refusals, retaining the raw package.

    This is independent of HTML structure validation and force_accept. A refusal
    must not be saved, merged into a partial translation, or used as a prompt
    for completion. Ordinary story dialogue about translation is left alone.
    """
    validate_content_filter_response(raw_response)
    for text in _response_texts(raw_response):
        for refusal in _REFUSAL.finditer(text):
            explanation = text[refusal.start():refusal.end() + 400]
            if _EXPLANATION.search(explanation):
                error = ValidationFailedError(
                    "Модель вернула отказ от перевода или предложила пересказ вместо перевода. "
                    "Ответ отклонён; требуется повторная отправка исходного текста."
                )
                error.raw_package_text = raw_response
                raise error
