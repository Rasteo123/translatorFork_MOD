import unittest

from gemini_translator.core.worker_helpers.rpm_limiter import RPMLimiter


class RPMLimiterWaitTests(unittest.TestCase):
    def test_seconds_until_next_allowed_zero_before_first_request(self):
        limiter = RPMLimiter(60)  # interval = 1.0s
        self.assertEqual(limiter.seconds_until_next_allowed(), 0.0)

    def test_seconds_until_next_allowed_after_consuming_slot(self):
        limiter = RPMLimiter(60)  # interval = 1.0s
        self.assertTrue(limiter.can_proceed())  # consumes the slot at "now"
        remaining = limiter.seconds_until_next_allowed()
        self.assertGreater(remaining, 0.0)
        self.assertLessEqual(remaining, 1.0)
        # Still blocked right after consuming, so a positive wait is meaningful.
        self.assertFalse(limiter.can_proceed())

    def test_no_limit_always_zero(self):
        limiter = RPMLimiter(0)  # disabled limiter
        self.assertEqual(limiter.seconds_until_next_allowed(), 0.0)


if __name__ == "__main__":
    unittest.main()
