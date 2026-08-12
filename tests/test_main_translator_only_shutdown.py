import os
import unittest
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_translator_only_mode = os.environ.get("GT_TRANSLATOR_ONLY_MODE")
_disabled_provider_ids = os.environ.get("GT_DISABLED_PROVIDER_IDS")
import main_translator_only
if _translator_only_mode is None:
    os.environ.pop("GT_TRANSLATOR_ONLY_MODE", None)
else:
    os.environ["GT_TRANSLATOR_ONLY_MODE"] = _translator_only_mode
if _disabled_provider_ids is None:
    os.environ.pop("GT_DISABLED_PROVIDER_IDS", None)
else:
    os.environ["GT_DISABLED_PROVIDER_IDS"] = _disabled_provider_ids


class _FakeEngine:
    def __init__(self):
        self.cleaned_up = False

    def cleanup(self):
        self.cleaned_up = True


class _FakeEngineThread:
    def __init__(self, engine):
        self.engine = engine
        self.quit_called = False
        self.wait_called = False
        self.cleanup_happened_before_quit = False

    def isRunning(self):
        return True

    def quit(self):
        self.cleanup_happened_before_quit = self.engine.cleaned_up
        self.quit_called = True

    def wait(self):
        self.wait_called = True


class _FakeProxyController:
    def __init__(self):
        self.shutdown_called = False

    def shutdown(self):
        self.shutdown_called = True


class _FakeApplication:
    def __init__(self):
        self.engine = _FakeEngine()
        self.engine_thread = _FakeEngineThread(self.engine)
        self.proxy_controller = _FakeProxyController()


class _FakeQMetaObject:
    @staticmethod
    def invokeMethod(target, method_name, connection_type):
        getattr(target, method_name)()
        return True


class TranslatorOnlyShutdownTests(unittest.TestCase):
    def test_shutdown_cleans_engine_before_quitting_its_thread(self):
        app = _FakeApplication()

        with (
            patch.object(main_translator_only, "_bootstrap_application", return_value=app),
            patch.object(main_translator_only, "_create_translator_window", return_value=None),
            patch.object(main_translator_only.app_main, "LoadingDialog", return_value=object()),
            patch.object(main_translator_only.app_main.QtCore, "QMetaObject", _FakeQMetaObject),
        ):
            result = main_translator_only.run_translator_only()

        self.assertEqual(result, 0)
        self.assertTrue(app.engine.cleaned_up)
        self.assertTrue(app.engine_thread.cleanup_happened_before_quit)
        self.assertTrue(app.engine_thread.quit_called)
        self.assertTrue(app.engine_thread.wait_called)
        self.assertTrue(app.proxy_controller.shutdown_called)


if __name__ == "__main__":
    unittest.main()
