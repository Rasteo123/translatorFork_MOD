import unittest

import gemini_reader_v3 as reader


class ReaderErrorClassificationTests(unittest.TestCase):
    def test_consumer_suspended_is_invalid_key_error(self):
        error_text = (
            "403 PERMISSION_DENIED. {'error': {'status': 'PERMISSION_DENIED', "
            "'details': [{'reason': 'CONSUMER_SUSPENDED'}], "
            "'message': \"Permission denied: Consumer 'api_key:AIza...' has been suspended.\"}}"
        )

        self.assertTrue(reader._is_invalid_api_key_error(error_text))

    def test_plain_permission_denied_is_not_invalid_key_error(self):
        error_text = "403 PERMISSION_DENIED. You do not have permission to access this model."

        self.assertFalse(reader._is_invalid_api_key_error(error_text))


if __name__ == "__main__":
    unittest.main()
