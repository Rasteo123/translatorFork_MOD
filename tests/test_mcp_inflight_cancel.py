import unittest

from gemini_translator.mcp import inflight


class _FakeClient:
    def __init__(self, fail_ids=()):
        self.cancelled = []
        self._fail_ids = set(fail_ids)

    def cancel_ai_completion(self, request_id):
        if request_id in self._fail_ids:
            raise RuntimeError("daemon unreachable")
        self.cancelled.append(request_id)
        return {"ok": True}


class InflightRegistryTests(unittest.TestCase):
    def setUp(self):
        inflight.clear()
        self.addCleanup(inflight.clear)

    def test_registered_ids_are_visible_in_snapshot(self):
        inflight.register("aaa")
        inflight.register("bbb")

        self.assertEqual(set(inflight.snapshot()), {"aaa", "bbb"})

    def test_unregister_removes_the_id(self):
        inflight.register("aaa")
        inflight.unregister("aaa")

        self.assertEqual(inflight.snapshot(), [])

    def test_unregister_of_unknown_id_is_harmless(self):
        inflight.unregister("nope")

        self.assertEqual(inflight.snapshot(), [])

    def test_empty_request_id_is_ignored(self):
        inflight.register("")
        inflight.register(None)

        self.assertEqual(inflight.snapshot(), [])


class CancelAllTests(unittest.TestCase):
    def setUp(self):
        inflight.clear()
        self.addCleanup(inflight.clear)

    def test_cancel_all_cancels_every_inflight_request(self):
        client = _FakeClient()
        inflight.register("aaa")
        inflight.register("bbb")

        cancelled = inflight.cancel_all(client_factory=lambda: client, background=False)

        self.assertEqual(cancelled, 2)
        self.assertEqual(set(client.cancelled), {"aaa", "bbb"})

    def test_cancel_all_clears_the_registry(self):
        client = _FakeClient()
        inflight.register("aaa")

        inflight.cancel_all(client_factory=lambda: client, background=False)

        self.assertEqual(inflight.snapshot(), [])

    def test_cancel_all_on_empty_registry_does_not_touch_the_daemon(self):
        calls = []

        def factory():
            calls.append(1)
            return _FakeClient()

        self.assertEqual(inflight.cancel_all(client_factory=factory, background=False), 0)
        self.assertEqual(calls, [])

    def test_one_failing_cancel_does_not_stop_the_others(self):
        client = _FakeClient(fail_ids={"aaa"})
        inflight.register("aaa")
        inflight.register("bbb")

        inflight.cancel_all(client_factory=lambda: client, background=False)

        self.assertEqual(client.cancelled, ["bbb"])

    def test_unreachable_daemon_is_swallowed(self):
        def factory():
            raise RuntimeError("daemon is not running")

        inflight.register("aaa")

        # Отмена по кнопке не имеет права падать из-за недоступного демона.
        self.assertEqual(inflight.cancel_all(client_factory=factory, background=False), 0)

    def test_background_mode_returns_without_blocking_and_still_cancels(self):
        client = _FakeClient()
        inflight.register("aaa")

        inflight.cancel_all(client_factory=lambda: client, background=True)
        inflight.wait_for_pending_cancels(timeout=5)

        self.assertEqual(client.cancelled, ["aaa"])


if __name__ == "__main__":
    unittest.main()
