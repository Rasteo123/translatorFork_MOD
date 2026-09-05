"""QA rate limits must preserve server deadlines and bounded key rotation."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from gemini_translator.api.errors import TemporaryRateLimitError
from gemini_translator.api.handlers.gemini import GeminiApiHandler
from gemini_translator.qa.handler_factory import QaHandlerError, RotatingQaHandler
from gemini_translator.qa.key_pool import QaKeyPool
from gemini_translator.qa.embeddings.gemini import GeminiEmbeddingProvider
from gemini_translator.qa.embeddings.factory import EmbeddingHttpError


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


def test_retry_after_is_not_shortened_by_request_wait_budget():
    clock = Clock()
    calls = []

    class Handler:
        async def execute_api_call(self, *args, **kwargs):
            calls.append(clock.now)
            raise TemporaryRateLimitError('Wait one hour', delay_seconds=3600)

    handler = RotatingQaHandler(
        QaKeyPool(['fake-key'], clock=clock), lambda key: Handler(),
        sleep=clock.sleep, max_wait_seconds=120,
    )
    with pytest.raises(QaHandlerError):
        asyncio.run(handler.execute_api_call('fake prompt', '[QA]'))
    # A later chapter shares the deadline; the first chapter's timeout must
    # not make the key usable again.
    with pytest.raises(QaHandlerError):
        asyncio.run(handler.execute_api_call('next chapter', '[QA]'))
    assert calls == [1000.0], f'Request timestamps: {calls}; server requested 3600 seconds'


def test_first_throttle_prevents_another_coroutine_from_using_same_key():
    clock = Clock()
    pool = QaKeyPool(['fake-a', 'fake-b'], clock=clock)
    key = pool.acquire()
    assert pool.note_throttled(key, 60) is False
    assert pool.acquire() is None, 'A second QA request can use the key during its server-mandated pause'


def test_inflight_success_cannot_cancel_another_requests_throttle():
    clock = Clock()
    pool = QaKeyPool(['fake-a', 'fake-b'], clock=clock)
    assert pool.acquire() == pool.acquire() == 'fake-b'
    pool.note_throttled('fake-b', 60)
    pool.note_success('fake-b')
    assert pool.acquire() is None
    clock.now += 60
    assert pool.acquire() == 'fake-b'


@pytest.mark.parametrize('elapsed', [0, 60])
def test_translation_exhaustion_releases_first_throttled_key_for_rotation(elapsed):
    clock = Clock()
    limited = set()
    settings = SimpleNamespace(
        get_key_info=lambda key: {'key': key},
        is_key_limit_active=lambda info, model: info['key'] in limited,
    )
    pool = QaKeyPool(['fake-a', 'fake-b'], clock=clock, settings_manager=settings, model_id='m')
    assert pool.acquire() == 'fake-b'
    pool.note_throttled('fake-b', 60)
    limited.add('fake-b')
    clock.now += elapsed
    assert pool.seconds_until_available() == 0
    assert pool.acquire() == 'fake-a'


@pytest.mark.parametrize('status', [401, 403])
@pytest.mark.parametrize('stream', [False, True])
def test_project_suspension_stops_instead_of_trying_remaining_keys(status, stream):
    clock = Clock()
    calls = []

    class Response:
        async def text(self):
            return json.dumps({'error': {'message': 'Project has been suspended', 'status': 'UNAUTHENTICATED'}})

    class Handler:
        def __init__(self, key):
            self.key = key

        async def execute_api_call(self, *args, **kwargs):
            calls.append((self.key, clock.now))
            classifier = object.__new__(GeminiApiHandler)
            classifier.worker = SimpleNamespace(api_key=self.key)
            if stream:
                classifier._raise_for_stream_error({'message': 'Project has been suspended', 'status': 'UNAUTHENTICATED'})
            response = Response()
            response.status = status
            await classifier._handle_error_response(response, None)

    handler = RotatingQaHandler(
        QaKeyPool(['fake-a', 'fake-b', 'fake-c', 'fake-d'], clock=clock), Handler,
        sleep=clock.sleep,
    )
    for _ in range(2):
        with pytest.raises(QaHandlerError, match='доступ'):
            asyncio.run(handler.execute_api_call('fake prompt', '[QA]'))
    assert len(calls) == 1, f'Keys attempted after suspension: {calls}'


def test_embedding_429_waits_on_first_refusal_and_rotates_after_second():
    clock = Clock()
    calls = []

    class Response:
        status = 429
        headers = {'Retry-After': '60'}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def text(self):
            return json.dumps({'error': {'status': 'RESOURCE_EXHAUSTED', 'message': 'Please retry in 60s', 'details': [{'retryDelay': '60s'}]}})

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def post(self, url, *, headers, json, timeout):
            calls.append((headers['x-goog-api-key'], clock.now))
            return Response()

    provider = GeminiEmbeddingProvider(
        [f'fake-{i}' for i in range(150)], Session, 10, retry_sleep=clock.sleep, clock=clock,
    )
    with pytest.raises(EmbeddingHttpError):
        asyncio.run(provider._post_json('https://fake.invalid', {}))
    # A different ready key may be used after a second refusal (translation
    # rotation). The refused key itself must always get its full pause.
    assert calls[:4] == [('fake-0', 1000.0), ('fake-0', 1060.0), ('fake-1', 1060.0), ('fake-1', 1120.0)]
    previous = {}
    for key, timestamp in calls:
        if key in previous:
            assert timestamp - previous[key] >= 60
        previous[key] = timestamp


class EmbeddingEndpoint:
    """HTTP boundary fake; pool, parsing and request orchestration stay real."""

    def __init__(self, clock, responses):
        self.clock = clock
        self.responses = responses
        self.calls = []

    def session(self):
        endpoint = self

        class Response:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def text(self):
                return self.body

            async def json(self):
                return {'ok': True}

        class Session(Response):
            def post(self, url, *, headers, json, timeout):
                index = min(len(endpoint.calls), len(endpoint.responses) - 1)
                endpoint.calls.append((headers['x-goog-api-key'], endpoint.clock.now))
                response = Response()
                response.status, response.headers, response.body = endpoint.responses[index]
                return response

        return Session()


@pytest.mark.parametrize('headers,body', [
    ({'Retry-After': '3600'}, '{"error":{"details":[{"retryDelay":"60s"}]}}'),
    ({'Retry-After': '60'}, '{"error":{"details":[{"retryDelay":"3600s"}]}}'),
    ({}, 'Please retry in 3600s'),
])
def test_embedding_long_pause_survives_the_current_chapter(headers, body):
    clock = Clock()
    endpoint = EmbeddingEndpoint(clock, [(429, headers, body), (200, {}, '')])
    provider = GeminiEmbeddingProvider(
        ['fake-a', 'fake-b'], endpoint.session, 10, retry_sleep=clock.sleep, clock=clock,
    )
    # Neither the current chapter nor the next one may shorten the deadline.
    with pytest.raises(EmbeddingHttpError):
        asyncio.run(provider._post_json('https://fake.invalid', {}))
    from gemini_translator.qa.embeddings.factory import EmbeddingUnavailableError
    with pytest.raises(EmbeddingUnavailableError):
        asyncio.run(provider._post_json('https://fake.invalid', {}))
    assert endpoint.calls == [('fake-a', 1000.0)]
    clock.now = 4600.0
    assert asyncio.run(provider._post_json('https://fake.invalid', {})) == {'ok': True}
    assert endpoint.calls[-1] == ('fake-a', 4600.0)


@pytest.mark.parametrize('status', [401, 403])
def test_embedding_access_refusal_stops_later_chapters(status):
    clock = Clock()
    endpoint = EmbeddingEndpoint(clock, [(status, {}, 'project suspended'), (200, {}, '')])
    provider = GeminiEmbeddingProvider(
        ['fake-a', 'fake-b'], endpoint.session, 10, retry_sleep=clock.sleep, clock=clock,
    )
    for _ in range(2):
        with pytest.raises(EmbeddingHttpError) as error:
            asyncio.run(provider._post_json('https://fake.invalid', {}))
        assert error.value.status == status
    assert endpoint.calls == [('fake-a', 1000.0)]


def test_embedding_minute_quota_is_waited_out_without_exhausting_key():
    clock = Clock()
    body = json.dumps({'error': {'status': 'RESOURCE_EXHAUSTED', 'details': [
        {'violations': [{'quotaId': 'GenerateRequestsPerMinutePerProject'}]},
        {'retryDelay': '4.5s'},
    ]}})
    endpoint = EmbeddingEndpoint(clock, [(429, {}, body), (200, {}, '')])
    marked = []
    health = SimpleNamespace(is_active=lambda key: True, mark_exhausted=lambda *args: marked.append(args))
    provider = GeminiEmbeddingProvider(
        ['fake-a', 'fake-b'], endpoint.session, 10, retry_sleep=clock.sleep, clock=clock, key_health=health,
    )
    assert asyncio.run(provider._post_json('https://fake.invalid', {})) == {'ok': True}
    assert endpoint.calls == [('fake-a', 1000.0), ('fake-a', 1004.5)]
    assert marked == []


def test_embedding_daily_exhaustion_rotates_and_retains_successful_key():
    clock = Clock()
    endpoint = EmbeddingEndpoint(clock, [
        (429, {}, '{"error":{"message":"quota exceeded per day"}}'), (200, {}, ''),
    ])
    provider = GeminiEmbeddingProvider(
        ['fake-a', 'fake-b'], endpoint.session, 10, retry_sleep=clock.sleep, clock=clock,
    )
    for _ in range(2):
        assert asyncio.run(provider._post_json('https://fake.invalid', {})) == {'ok': True}
    assert endpoint.calls == [('fake-a', 1000.0), ('fake-b', 1000.0), ('fake-b', 1000.0)]


def test_embedding_requests_share_the_pool_minute_budget():
    clock = Clock()
    endpoint = EmbeddingEndpoint(clock, [(200, {}, '')])
    provider = GeminiEmbeddingProvider(
        ['fake-a', 'fake-b'], endpoint.session, 10, retry_sleep=clock.sleep, clock=clock,
    )
    for _ in range(21):
        asyncio.run(provider._post_json('https://fake.invalid', {}))
    assert len(endpoint.calls) == 21
    assert endpoint.calls[-1][1] >= endpoint.calls[0][1] + 60
