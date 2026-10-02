import asyncio
import json
import unittest
from types import SimpleNamespace

from gemini_translator.api.errors import RateLimitExceededError, TemporaryRateLimitError
from gemini_translator.api.handlers.gemini import GeminiApiHandler


def _make_handler() -> GeminiApiHandler:
    worker = SimpleNamespace(
        provider_config={"is_async": True},
        model_config={"id": "gemini-test"},
        api_key="test-api-key",
    )
    return GeminiApiHandler(worker)


async def _async_text(value):
    return value


class GeminiHandlerTests(unittest.TestCase):
    def test_429_pause_of_181_seconds_rotates_key(self):
        handler = _make_handler()
        response = SimpleNamespace(
            status=429,
            text=lambda: _async_text(json.dumps({
                "error": {"message": "Please retry in 179s", "details": [{"retryDelay": "179s"}]}
            })),
        )

        with self.assertRaises(RateLimitExceededError) as raised:
            asyncio.run(handler._handle_error_response(response))

        self.assertEqual(raised.exception.retry_after_seconds, 181)

    def test_429_pause_of_180_seconds_stays_temporary(self):
        handler = _make_handler()
        response = SimpleNamespace(
            status=429,
            text=lambda: _async_text(json.dumps({
                "error": {"message": "Please retry in 178s", "details": [{"retryDelay": "178s"}]}
            })),
        )

        with self.assertRaises(TemporaryRateLimitError) as raised:
            asyncio.run(handler._handle_error_response(response))

        self.assertEqual(raised.exception.delay_seconds, 180)

    def test_stream_pause_of_181_seconds_rotates_key(self):
        handler = _make_handler()
        stream_error = {
            "status": "RESOURCE_EXHAUSTED",
            "message": "Please retry in 179s",
            "details": [{"retryDelay": "179s"}],
        }

        with self.assertRaises(RateLimitExceededError) as raised:
            handler._raise_for_stream_error(stream_error)

        self.assertEqual(raised.exception.retry_after_seconds, 181)

    def test_429_with_thirteen_hour_retry_delay_rotates_key(self):
        handler = _make_handler()
        response = SimpleNamespace(
            status=429,
            text=lambda: _async_text(json.dumps({
                "error": {
                    "message": "Resource exhausted. Please retry in 47616s",
                    "details": [{"retryDelay": "47616s"}],
                }
            })),
        )

        with self.assertRaises(RateLimitExceededError) as raised:
            asyncio.run(handler._handle_error_response(response))

        self.assertEqual(raised.exception.retry_after_seconds, 47618)

    def test_stream_resource_exhausted_with_long_retry_delay_rotates_key(self):
        handler = _make_handler()
        stream_error = {
            "status": "RESOURCE_EXHAUSTED",
            "message": "Quota exhausted",
            "details": [{"retryDelay": "47616s"}],
        }

        with self.assertRaises(RateLimitExceededError) as raised:
            handler._raise_for_stream_error(stream_error)

        self.assertEqual(raised.exception.retry_after_seconds, 47618)

    def test_429_with_short_retry_delay_remains_temporary(self):
        handler = _make_handler()
        response = SimpleNamespace(
            status=429,
            text=lambda: _async_text(json.dumps({
                "error": {"message": "Please retry in 30s", "details": [{"retryDelay": "30s"}]}
            })),
        )

        with self.assertRaises(TemporaryRateLimitError) as raised:
            asyncio.run(handler._handle_error_response(response))

        self.assertEqual(raised.exception.delay_seconds, 32)

    def test_stream_unavailable_high_demand_is_temporary_rate_limit(self):
        handler = _make_handler()
        stream_error = {
            "message": (
                "This model is currently experiencing high demand. "
                "Spikes in demand are usually temporary. Please try again later."
            ),
            "status": "UNAVAILABLE",
        }

        with self.assertRaises(TemporaryRateLimitError) as raised:
            handler._raise_for_stream_error(stream_error)

        self.assertEqual(raised.exception.delay_seconds, 20)
        self.assertIn("перегружена", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
