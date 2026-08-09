import os
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("GT_DISABLE_LOCAL_MODEL_DISCOVERY", "1")

from PyQt6 import QtWidgets

from gemini_translator.api import config as api_config
from test_model_settings_widget import _DummyBus, _WidgetSettingsStub
from gemini_translator.ui.widgets.model_settings_widget import ModelSettingsWidget

import tempfile


class AsyncModelDiscoveryTests(unittest.TestCase):
    """HTTP discovery моделей не должен блокировать GUI-поток.

    При лежащем локальном сервере ensure_dynamic_provider_models висит
    таймаут на каждый discovery-источник; вызов из set_available_models
    замораживал окно на каждую смену провайдера.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        if not hasattr(cls.app, "event_bus"):
            cls.app.event_bus = _DummyBus()

    def _create_widget(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        settings = _WidgetSettingsStub(temp_dir.name)
        self.app.get_settings_manager = lambda settings=settings: settings
        widget = ModelSettingsWidget(settings_manager=settings)
        self.addCleanup(widget.close)
        return widget

    def test_set_available_models_does_not_block_gui_on_discovery(self):
        widget = self._create_widget()

        ensure_ran_on_main_thread = []
        done = threading.Event()

        def tracking_ensure(provider_id, force=False):
            ensure_ran_on_main_thread.append(
                threading.current_thread() is threading.main_thread()
            )
            done.set()
            return {}

        real_ensure = api_config.ensure_dynamic_provider_models
        self.addCleanup(
            setattr, api_config, "ensure_dynamic_provider_models", real_ensure
        )
        api_config.ensure_dynamic_provider_models = tracking_ensure

        if hasattr(api_config, "provider_needs_dynamic_model_refresh"):
            real_needs = api_config.provider_needs_dynamic_model_refresh
            self.addCleanup(
                setattr,
                api_config,
                "provider_needs_dynamic_model_refresh",
                real_needs,
            )
            api_config.provider_needs_dynamic_model_refresh = lambda pid: True

        widget.set_available_models("local")

        deadline = time.time() + 3.0
        while not done.is_set() and time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.01)

        self.assertTrue(done.is_set(), "discovery так и не был запущен")
        self.assertTrue(
            all(on_main is False for on_main in ensure_ran_on_main_thread),
            "ensure_dynamic_provider_models вызван синхронно в GUI-потоке",
        )

    def test_provider_needs_dynamic_model_refresh_ttl(self):
        real_enabled = api_config._local_model_discovery_enabled
        self.addCleanup(
            setattr, api_config, "_local_model_discovery_enabled", real_enabled
        )
        api_config._local_model_discovery_enabled = lambda: True

        with api_config._DYNAMIC_PROVIDER_MODELS_LOCK:
            saved_models = dict(api_config._DYNAMIC_PROVIDER_MODELS)
            saved_ts = dict(api_config._DYNAMIC_PROVIDER_MODELS_TS)

        def restore():
            with api_config._DYNAMIC_PROVIDER_MODELS_LOCK:
                api_config._DYNAMIC_PROVIDER_MODELS.clear()
                api_config._DYNAMIC_PROVIDER_MODELS.update(saved_models)
                api_config._DYNAMIC_PROVIDER_MODELS_TS.clear()
                api_config._DYNAMIC_PROVIDER_MODELS_TS.update(saved_ts)

        self.addCleanup(restore)

        with api_config._DYNAMIC_PROVIDER_MODELS_LOCK:
            api_config._DYNAMIC_PROVIDER_MODELS.pop("local", None)
            api_config._DYNAMIC_PROVIDER_MODELS_TS.pop("local", None)

        # Нет кэша — обновление нужно.
        self.assertTrue(api_config.provider_needs_dynamic_model_refresh("local"))

        with api_config._DYNAMIC_PROVIDER_MODELS_LOCK:
            api_config._DYNAMIC_PROVIDER_MODELS["local"] = {}
            api_config._DYNAMIC_PROVIDER_MODELS_TS["local"] = time.time()
        # Кэш свеж — обновление не нужно.
        self.assertFalse(api_config.provider_needs_dynamic_model_refresh("local"))

        with api_config._DYNAMIC_PROVIDER_MODELS_LOCK:
            api_config._DYNAMIC_PROVIDER_MODELS_TS["local"] = (
                time.time() - api_config._LOCAL_MODEL_DISCOVERY_TTL_SECONDS - 1
            )
        # Кэш протух — обновление снова нужно.
        self.assertTrue(api_config.provider_needs_dynamic_model_refresh("local"))

        # Провайдер без динамического discovery не требует обновлений.
        self.assertFalse(api_config.provider_needs_dynamic_model_refresh("gemini"))


if __name__ == "__main__":
    unittest.main()
