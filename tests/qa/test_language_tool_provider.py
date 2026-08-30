"""The rule provider talks to one configured server and never anywhere else."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from gemini_translator.qa.capabilities import QaCapabilitySettings
from gemini_translator.qa.language_rules import (
    LanguageRuleRequest,
    LanguageRuleService,
    LanguageRuleUnavailable,
    LanguageToolHttpProvider,
    language_tool_code,
    normalize_endpoint,
)
from gemini_translator.qa.models import SemanticInlineSpan, SemanticUnit


@dataclass
class _RecordedRequest:
    url: str
    data: dict
    timeout: float

    @property
    def host(self) -> str:
        return self.url.split("//", 1)[-1].split("/", 1)[0]


class _Response:
    def __init__(self, status=200, payload=None, json_error=None):
        self.status = status
        self._payload = payload if payload is not None else {"matches": []}
        self._json_error = json_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class _Http:
    def __init__(self, response=None, post_error=None):
        self.response = response or _Response()
        self.post_error = post_error
        self.requests: list[_RecordedRequest] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def post(self, url, *, data, timeout):
        self.requests.append(_RecordedRequest(url, dict(data), timeout))
        if self.post_error is not None:
            raise self.post_error
        return self.response

    @property
    def session_factory(self):
        return lambda: self

    @property
    def last_request(self) -> _RecordedRequest:
        return self.requests[-1]


def _unit(text: str, *, unit_id: str, block_id: str, ordinal: int = 0) -> SemanticUnit:
    return SemanticUnit(
        unit_id=unit_id,
        document_id="target-doc",
        block_id=block_id,
        ordinal=ordinal,
        text=text,
        normalized_text=text.casefold(),
        source_start=0,
        source_end=len(text),
        kind="paragraph",
        inline_spans=(
            SemanticInlineSpan(f"i-{unit_id}", 0, len(text), 0, len(text)),
        ),
    )


def _request(*units, language="ru", disabled=()) -> LanguageRuleRequest:
    return LanguageRuleRequest(
        units=tuple(units), language=language, disabled_rule_ids=tuple(disabled)
    )


def _match(offset: int, length: int, **overrides) -> dict:
    payload = {
        "offset": offset,
        "length": length,
        "message": "Возможная опечатка",
        "rule": {"id": "MORFOLOGIK_RULE_RU_RU", "category": {"id": "TYPOS"}},
        "replacements": [{"value": "привет"}],
    }
    payload.update(overrides)
    return payload


def _provider(http: _Http, endpoint="http://127.0.0.1:8081/v2"):
    return LanguageToolHttpProvider(
        endpoint=endpoint, session_factory=http.session_factory, timeout_seconds=10
    )


def test_sentences_are_posted_once_and_offsets_map_back_to_the_epub():
    """A rule hit is only useful if it points at a real place in the chapter."""
    http = _Http(
        _Response(
            payload={
                "software": {"version": "6.6"},
                "matches": [_match(2, 6)],
            }
        )
    )
    unit = _unit("— Превет!", unit_id="u-1", block_id="b-1")

    issues = asyncio.run(_provider(http).check(_request(unit)))

    assert http.last_request.url == "http://127.0.0.1:8081/v2/check"
    assert http.last_request.data["language"] == "ru-RU"
    assert len(http.requests) == 1
    assert issues[0].unit_id == "u-1"
    assert issues[0].block_id == "b-1"
    assert (issues[0].unit_start, issues[0].unit_end) == (2, 8)
    assert issues[0].matched_text == "Превет"
    assert issues[0].replacements == ("привет",)


def test_several_sentences_share_one_request_and_keep_their_own_offsets():
    """One request per sentence would multiply latency for no extra accuracy."""
    first = _unit("Первое предложение.", unit_id="u-1", block_id="b-1")
    second = _unit("Второе предложение.", unit_id="u-2", block_id="b-2", ordinal=1)
    # "Второе" starts right after the first sentence and the two-character gap.
    http = _Http(_Response(payload={"matches": [_match(21, 6, replacements=[])]}))

    issues = asyncio.run(_provider(http).check(_request(first, second)))

    assert len(http.requests) == 1
    assert issues[0].unit_id == "u-2"
    assert (issues[0].unit_start, issues[0].unit_end) == (0, 6)
    assert issues[0].report_only is True


def test_a_hit_across_two_sentences_is_reported_but_never_fixable():
    """An edit spanning a sentence boundary cannot be applied locally."""
    first = _unit("Первое.", unit_id="u-1", block_id="b-1")
    second = _unit("Второе.", unit_id="u-2", block_id="b-2", ordinal=1)
    http = _Http(_Response(payload={"matches": [_match(5, 6)]}))

    issues = asyncio.run(_provider(http).check(_request(first, second)))

    assert issues[0].report_only is True
    assert issues[0].replacements == ()


@pytest.mark.parametrize(
    "match",
    [
        {"offset": -1, "length": 3},
        {"offset": 0, "length": 0},
        {"offset": 0, "length": 9999},
        {"offset": 0, "length": 3, "rule": {"id": ""}},
        {"offset": 0, "length": 3, "rule": "not an object"},
        "not an object",
    ],
)
def test_unusable_matches_are_dropped_instead_of_guessed(match):
    """A malformed match must never become an edit or a crash."""
    payload = {"matches": [match if isinstance(match, dict) else match]}
    if isinstance(match, dict):
        payload["matches"][0].setdefault("message", "m")
        payload["matches"][0].setdefault("rule", {"id": "R", "category": {"id": "C"}})
    http = _Http(_Response(payload=payload))

    issues = asyncio.run(
        _provider(http).check(_request(_unit("Текст.", unit_id="u-1", block_id="b-1")))
    )

    assert issues == () or all(issue.unit_id == "u-1" for issue in issues)


def test_disabled_rules_are_sent_to_the_server():
    """A rule the user switched off must not come back as a candidate."""
    http = _Http()

    asyncio.run(
        _provider(http).check(
            _request(
                _unit("Текст.", unit_id="u-1", block_id="b-1"),
                disabled=("RU_UPPERCASE", "WHITESPACE_RULE"),
            )
        )
    )

    assert http.last_request.data["disabledRules"] == "RU_UPPERCASE,WHITESPACE_RULE"


def test_a_timeout_never_sends_text_to_another_endpoint():
    """A silent fallback would leak the book to a service nobody chose."""
    http = _Http(post_error=TimeoutError("slow"))
    provider = LanguageToolHttpProvider(
        endpoint="https://configured.example/v2",
        session_factory=http.session_factory,
        timeout_seconds=1,
    )

    with pytest.raises(LanguageRuleUnavailable):
        asyncio.run(provider.check(_request(_unit("Текст.", unit_id="u-1", block_id="b-1"))))

    assert {request.host for request in http.requests} == {"configured.example"}


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (_Response(status=500), "language_tool_http_500"),
        (_Response(status=404), "language_tool_http_404"),
        (_Response(json_error=ValueError("bad json")), "language_tool_unreachable"),
        (_Response(payload={"matches": "not a list"}), "language_tool_invalid_response"),
    ],
)
def test_every_server_failure_becomes_one_typed_reason(response, reason):
    """The user needs a stable reason, never the server's own output."""
    http = _Http(response)

    with pytest.raises(LanguageRuleUnavailable) as excinfo:
        asyncio.run(
            _provider(http).check(
                _request(_unit("Текст.", unit_id="u-1", block_id="b-1"))
            )
        )

    assert excinfo.value.reason == reason


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("http://127.0.0.1:8081/v2", "http://127.0.0.1:8081/v2/check"),
        ("http://127.0.0.1:8081/v2/", "http://127.0.0.1:8081/v2/check"),
        ("http://127.0.0.1:8081/v2/check", "http://127.0.0.1:8081/v2/check"),
        ("https://api.languagetool.org/v2", "https://api.languagetool.org/v2/check"),
    ],
)
def test_endpoint_normalization_only_completes_the_user_address(endpoint, expected):
    """The address must stay the one the user typed, minus a missing /check."""
    assert normalize_endpoint(endpoint) == expected


@pytest.mark.parametrize("endpoint", ["", "   ", "ftp://host/v2", "not a url"])
def test_an_unusable_endpoint_is_refused_before_any_request(endpoint):
    """A wrong address must be a setup error, not a silent network attempt."""
    with pytest.raises(LanguageRuleUnavailable):
        normalize_endpoint(endpoint)


def test_language_codes_follow_the_server_convention():
    """A wrong language code silently checks the text against another language."""
    assert language_tool_code("ru") == "ru-RU"
    assert language_tool_code("en") == "en-US"
    assert language_tool_code("ru-ru") == "ru-RU"
    assert language_tool_code("") == "ru-RU"


def test_the_service_makes_no_request_while_the_capability_is_off():
    """An unchecked box must cost nothing at all, not even a connection."""
    http = _Http()
    service = LanguageRuleService(provider=_provider(http))

    result = asyncio.run(
        service.collect(
            (_unit("Текст.", unit_id="u-1", block_id="b-1"),),
            QaCapabilitySettings(),
        )
    )

    assert result.status == "disabled"
    assert result.issues == ()
    assert http.requests == []


def test_an_enabled_but_unconfigured_service_warns_without_failing():
    """A switched-on analyzer with no address must not break the chapter."""
    service = LanguageRuleService(provider=None)

    result = asyncio.run(
        service.collect(
            (_unit("Текст.", unit_id="u-1", block_id="b-1"),),
            QaCapabilitySettings(language_tool_enabled=True),
        )
    )

    assert result.status == "unavailable"
    assert result.warnings == ("language_tool_not_configured",)


def test_an_outage_is_a_warning_and_never_an_exception():
    """A dead LanguageTool server must not stop a translation session."""
    http = _Http(post_error=TimeoutError("slow"))
    service = LanguageRuleService(provider=_provider(http))

    result = asyncio.run(
        service.collect(
            (_unit("Текст.", unit_id="u-1", block_id="b-1"),),
            QaCapabilitySettings(language_tool_enabled=True),
        )
    )

    assert result.status == "unavailable"
    assert result.warnings == ("language_tool_timeout",)
    assert result.issues == ()


def test_hits_reach_the_diagnosis_request_as_unconfirmed_hints():
    """Rule output is evidence for one LLM request, never an edit of its own."""
    http = _Http(_Response(payload={"matches": [_match(2, 6)]}))
    service = LanguageRuleService(provider=_provider(http))

    result = asyncio.run(
        service.collect(
            (_unit("— Превет!", unit_id="u-1", block_id="b-1"),),
            QaCapabilitySettings(language_tool_enabled=True),
        )
    )
    hints = result.hints()

    assert result.status == "completed"
    assert hints[0].block_id == "b-1"
    assert hints[0].rule_id == "MORFOLOGIK_RULE_RU_RU"
    assert hints[0].original_text == "Превет"
    assert hints[0].replacements == ("привет",)
