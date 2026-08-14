import unittest
from unittest.mock import patch

from gemini_translator.utils.power_inhibitor import (
    ES_CONTINUOUS,
    ES_DISPLAY_REQUIRED,
    ES_SYSTEM_REQUIRED,
    PowerInhibitor,
)


class _FakeProcess:
    def __init__(self):
        self.terminated = False
        self.waited = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.waited = True


class _FakeKernel32:
    def __init__(self):
        self.calls = []

    def SetThreadExecutionState(self, flags):
        self.calls.append(flags)
        return flags


class _FakeWindll:
    def __init__(self):
        self.kernel32 = _FakeKernel32()


class _FakeCtypes:
    def __init__(self):
        self.windll = _FakeWindll()


class PowerInhibitorTests(unittest.TestCase):
    def test_macos_starts_caffeinate_blocking_display_sleep_to_prevent_lock(self):
        calls = []
        process = _FakeProcess()

        inhibitor = PowerInhibitor(
            platform_name="darwin",
            popen_factory=lambda args: calls.append(args) or process,
        )

        with patch("os.getpid", return_value=4242):
            self.assertTrue(inhibitor.prevent_sleep())

        self.assertEqual(calls, [["caffeinate", "-dims", "-w", "4242"]])
        self.assertTrue(inhibitor.active)

        inhibitor.allow_sleep()

        self.assertTrue(process.terminated)
        self.assertTrue(process.waited)
        self.assertFalse(inhibitor.active)

    def test_windows_uses_system_required_and_display_required_to_prevent_lock(self):
        fake_ctypes = _FakeCtypes()
        inhibitor = PowerInhibitor(platform_name="win32", ctypes_module=fake_ctypes)

        self.assertTrue(inhibitor.prevent_sleep())
        inhibitor.allow_sleep()

        self.assertEqual(
            fake_ctypes.windll.kernel32.calls,
            [ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED, ES_CONTINUOUS],
        )
        self.assertFalse(inhibitor.active)

    def test_unsupported_platform_is_noop(self):
        inhibitor = PowerInhibitor(platform_name="linux")

        self.assertFalse(inhibitor.prevent_sleep())
        self.assertFalse(inhibitor.active)
        inhibitor.allow_sleep()


if __name__ == "__main__":
    unittest.main()
