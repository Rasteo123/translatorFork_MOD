"""Как обработчики читают подсказку сервиса «подожди N секунд» из ответа 429.

OmniRoute кладёт её в тело как ``retryAfterMs`` и в текст сообщения Google
(«Your quota will reset after 4h32m10s»), Google — в ``RetryInfo.retryDelay``,
обычные API — в заголовок ``Retry-After``.
"""
import unittest
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone

from gemini_translator.api.retry_hints import retry_after_seconds


class RetryAfterSecondsTests(unittest.TestCase):
    def test_retry_after_header_in_seconds(self):
        self.assertEqual(retry_after_seconds({"Retry-After": "45"}, ""), 45.0)

    def test_retry_after_header_as_http_date_counts_from_now(self):
        now = datetime(2026, 9, 22, 12, 0, 0, tzinfo=timezone.utc)
        header = format_datetime(now + timedelta(seconds=90), usegmt=True)

        self.assertEqual(
            retry_after_seconds({"retry-after": header}, "", now=now.timestamp()),
            90.0,
        )

    def test_http_date_in_the_past_means_no_wait(self):
        now = datetime(2026, 9, 22, 12, 0, 0, tzinfo=timezone.utc)
        header = format_datetime(now - timedelta(seconds=30), usegmt=True)

        self.assertEqual(retry_after_seconds({"Retry-After": header}, "", now=now.timestamp()), 0.0)

    def test_unreadable_header_is_ignored(self):
        self.assertIsNone(retry_after_seconds({"Retry-After": "soon"}, "Too Many Requests"))

    def test_omniroute_retry_after_ms_in_body(self):
        body = '{"error":{"message":"Antigravity upstream error (429)"},"retryAfterMs":16309000}'

        self.assertEqual(retry_after_seconds({}, body), 16309.0)

    def test_google_retry_info_delay_in_body(self):
        body = (
            '{"error":{"code":429,"message":"Resource has been exhausted","details":['
            '{"@type":"type.googleapis.com/google.rpc.RetryInfo","retryDelay":"26s"}]}}'
        )

        self.assertEqual(retry_after_seconds({}, body), 26.0)

    def test_retry_info_nested_in_upstream_details(self):
        body = (
            '{"error":{"message":"Antigravity upstream error (429): quota"},'
            '"upstream_details":{"error":{"details":[{"retryDelay":"1.5s"}]}}}'
        )

        self.assertEqual(retry_after_seconds({}, body), 1.5)

    def test_go_style_duration_in_message_text(self):
        body = (
            '{"error":{"message":"Antigravity upstream error (429): You have exhausted '
            'your capacity on this model. Your quota will reset after 4h32m10s."}}'
        )

        self.assertEqual(retry_after_seconds({}, body), 4 * 3600 + 32 * 60 + 10.0)

    def test_spelled_out_units_in_message_text(self):
        self.assertEqual(retry_after_seconds({}, "Please retry in 26 seconds."), 26.0)
        self.assertEqual(retry_after_seconds({}, "Rate limited, try again in 2 minutes"), 120.0)

    def test_header_wins_over_body(self):
        body = '{"retryAfterMs":900000,"error":{"message":"reset after 5m"}}'

        self.assertEqual(retry_after_seconds({"Retry-After": "45"}, body), 45.0)

    def test_no_hint_anywhere(self):
        self.assertIsNone(retry_after_seconds({}, '{"error":{"message":"Too Many Requests"}}'))
        self.assertIsNone(retry_after_seconds(None, None))


if __name__ == "__main__":
    unittest.main()
