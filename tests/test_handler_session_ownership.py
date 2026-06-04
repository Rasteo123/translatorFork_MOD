import types
import unittest


def _make_handler():
    from gemini_translator.api.base import BaseApiHandler
    worker = types.SimpleNamespace(
        provider_config={"is_async": True, "base_timeout": 600},
        proxy_settings=None,
    )
    return BaseApiHandler(worker)


class HandlerSessionOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_handlers_get_distinct_sessions(self):
        h1, h2 = _make_handler(), _make_handler()
        s1 = await h1._get_or_create_session_internal()
        s2 = await h2._get_or_create_session_internal()
        self.assertIsNot(s1, s2)
        await h1._close_thread_session_internal()
        await h2._close_thread_session_internal()

    async def test_reset_clears_only_this_handler(self):
        h1, h2 = _make_handler(), _make_handler()
        s1 = await h1._get_or_create_session_internal()
        s2 = await h2._get_or_create_session_internal()
        h1._force_session_reset()                      # sync, fire-and-forget close
        s1_new = await h1._get_or_create_session_internal()
        s2_same = await h2._get_or_create_session_internal()
        self.assertIsNot(s1_new, s1)   # h1 rebuilt
        self.assertIs(s2_same, s2)     # h2 untouched
        await h1._close_thread_session_internal()
        await h2._close_thread_session_internal()


if __name__ == "__main__":
    unittest.main()
