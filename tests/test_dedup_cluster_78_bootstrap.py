# tests/test_dedup_cluster_78_bootstrap.py
"""Характеризация и тест-маршрутизация для cluster-78.

main_translator_only.py раньше держал собственную копию bootstrap-логики
main.py, которая разошлась по трём пунктам:
  - не ставился Fusion-стиль на Windows;
  - не было фикса дублирования Dock-иконки на macOS;
  - jieba грелась безусловно на старте (~18МБ у каждой сессии), тогда как
    в main.py безусловный прогрев убран (ленивая загрузка при первом
    CJK-вызове).

Канонической стала main.bootstrap_application(argv, *, translator_only=False),
вызываемая и из main.py, и из main_translator_only.py._bootstrap_application.

ВАЖНО про импорт main_translator_only: его импорт НЕОБРАТИМО мутирует
os.environ (GT_TRANSLATOR_ONLY_MODE, GT_DISABLED_PROVIDER_IDS) — это читают
gemini_translator/api/config.py и ui/dialogs при каждом initialize_configs()
в любом другом тесте, импортированном позже. Поэтому импорт здесь обёрнут
save/restore-guard'ом (тем же приёмом, что и в
tests/test_main_translator_only_shutdown.py), а сам факт отсутствия утечки
проверяется тестом EnvironmentLeakageTests ниже через дочерний процесс
(в текущем процессе модуль уже импортирован интерпретатором один раз, и
повторный import не выполнит побочные эффекты заново).
"""
import os
import subprocess
import sys
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import main as app_main

_saved_translator_only_mode = os.environ.get("GT_TRANSLATOR_ONLY_MODE")
_saved_disabled_provider_ids = os.environ.get("GT_DISABLED_PROVIDER_IDS")
import main_translator_only  # noqa: E402  (после guard-снимка окружения)

if _saved_translator_only_mode is None:
    os.environ.pop("GT_TRANSLATOR_ONLY_MODE", None)
else:
    os.environ["GT_TRANSLATOR_ONLY_MODE"] = _saved_translator_only_mode
if _saved_disabled_provider_ids is None:
    os.environ.pop("GT_DISABLED_PROVIDER_IDS", None)
else:
    os.environ["GT_DISABLED_PROVIDER_IDS"] = _saved_disabled_provider_ids


class _FakeApp:
    """Достаточно QApplication-подобный объект, чтобы пройти bootstrap."""

    def __init__(self):
        self.installed_translators = []
        self.style_calls = []
        self.desktop_file_name = None

    def installTranslator(self, translator):
        self.installed_translators.append(translator)

    def setStyle(self, name):
        self.style_calls.append(name)

    def setDesktopFileName(self, name):
        self.desktop_file_name = name

    def initialize_managers(self):
        pass

    def get_settings_manager(self):
        return SimpleNamespace(load_proxy_settings=lambda: {})


class _FakeQThread:
    def __init__(self, parent=None):
        self.parent = parent
        self.finished = MagicMock()
        self.started_called = False

    def start(self):
        self.started_called = True


class _FakeQtCore:
    QThread = _FakeQThread

    class QMetaObject:
        @staticmethod
        def invokeMethod(*args, **kwargs):
            pass

    class Qt:
        class ConnectionType:
            QueuedConnection = object()


def _patch_obj(stack, name, **kw):
    return stack.enter_context(patch.object(app_main, name, **kw))


def _apply_common_bootstrap_patches(stack, *, platform, jieba_module):
    """Заводит все моки, общие для тестов bootstrap_application.

    Возвращает fake_app, который bootstrap_application вернёт вызывающему.
    """
    fake_app = _FakeApp()

    _patch_obj(stack, "prepare_console_streams", new=MagicMock())
    _patch_obj(stack, "configure_settings_scope_from_argv", new=MagicMock())
    _patch_obj(stack, "asyncio", new=MagicMock())
    _patch_obj(stack, "os_patch", new=MagicMock())
    _patch_obj(stack, "ApplicationWithContext", new=MagicMock(return_value=fake_app))
    _patch_obj(stack, "install_window_title_branding", new=MagicMock())
    _patch_obj(stack, "initialize_global_resources", new=MagicMock())
    _patch_obj(stack, "api_config", new=MagicMock())
    _patch_obj(stack, "EventBus", new=MagicMock())
    _patch_obj(stack, "apply_saved_app_theme", new=MagicMock())
    _patch_obj(stack, "install_selection_translator", new=MagicMock())
    _patch_obj(stack, "ChapterQueueManager", new=MagicMock())
    _patch_obj(stack, "GlobalProxyController", new=MagicMock())
    _patch_obj(stack, "ContextManager", new=MagicMock())
    _patch_obj(stack, "ServerManager", new=MagicMock())
    _patch_obj(stack, "TranslationEngine", new=MagicMock())
    _patch_obj(stack, "QtCore", new=_FakeQtCore)
    stack.enter_context(patch.object(app_main.os, "makedirs", MagicMock()))
    stack.enter_context(patch.object(app_main.sys, "platform", platform))
    stack.enter_context(patch("PyQt6.QtCore.QTranslator"))
    if jieba_module is not None:
        stack.enter_context(patch.dict(sys.modules, {"jieba": jieba_module}))

    original_excepthook = sys.excepthook
    stack.callback(setattr, sys, "excepthook", original_excepthook)

    return fake_app


def _run_bootstrap(*, translator_only, platform, jieba_module, patch_update_installer=True):
    """Запускает main.bootstrap_application с замоканными тяжёлыми зависимостями.

    Возвращает SimpleNamespace(app=..., ack=<мок или None>, cleanup=<мок или None>).
    """
    with ExitStack() as stack:
        _apply_common_bootstrap_patches(
            stack, platform=platform, jieba_module=jieba_module
        )

        ack_mock = cleanup_mock = None
        if patch_update_installer:
            ack_mock = stack.enter_context(
                patch(
                    "gemini_translator.utils.update_installer.write_startup_acknowledgement"
                )
            )
            cleanup_mock = stack.enter_context(
                patch("gemini_translator.utils.update_installer.cleanup_stale_staging")
            )

        result_app = app_main.bootstrap_application(
            ["prog"], translator_only=translator_only
        )

    return SimpleNamespace(app=result_app, ack=ack_mock, cleanup=cleanup_mock)


class BootstrapCanonicalBehaviorTests(unittest.TestCase):
    """(а) Характеризационные тесты канонического bootstrap_application."""

    def test_windows_applies_fusion_style_normal_mode(self):
        fake_jieba = MagicMock()
        result = _run_bootstrap(
            translator_only=False, platform="win32", jieba_module=fake_jieba
        )
        self.assertEqual(result.app.style_calls, ["Fusion"])

    def test_windows_applies_fusion_style_translator_only_mode(self):
        fake_jieba = MagicMock()
        result = _run_bootstrap(
            translator_only=True, platform="win32", jieba_module=fake_jieba
        )
        self.assertEqual(result.app.style_calls, ["Fusion"])

    def test_macos_fixes_dock_icon_normal_mode(self):
        fake_jieba = MagicMock()
        result = _run_bootstrap(
            translator_only=False, platform="darwin", jieba_module=fake_jieba
        )
        self.assertEqual(result.app.desktop_file_name, "com.siberianteam.translatorfork")

    def test_macos_fixes_dock_icon_translator_only_mode(self):
        fake_jieba = MagicMock()
        result = _run_bootstrap(
            translator_only=True, platform="darwin", jieba_module=fake_jieba
        )
        self.assertEqual(result.app.desktop_file_name, "com.siberianteam.translatorfork")

    def test_linux_gets_neither_windows_nor_macos_fixes(self):
        fake_jieba = MagicMock()
        result = _run_bootstrap(
            translator_only=True, platform="linux", jieba_module=fake_jieba
        )
        self.assertEqual(result.app.style_calls, [])
        self.assertIsNone(result.app.desktop_file_name)

    def test_translator_only_mode_does_not_warm_up_jieba_unconditionally(self):
        fake_jieba = MagicMock()
        _run_bootstrap(translator_only=True, platform="linux", jieba_module=fake_jieba)
        fake_jieba.lcut.assert_not_called()

    def test_normal_mode_does_not_warm_up_jieba_unconditionally(self):
        fake_jieba = MagicMock()
        _run_bootstrap(translator_only=False, platform="linux", jieba_module=fake_jieba)
        fake_jieba.lcut.assert_not_called()

    def test_normal_mode_runs_update_installer_ack(self):
        fake_jieba = MagicMock()
        result = _run_bootstrap(
            translator_only=False, platform="linux", jieba_module=fake_jieba
        )
        result.ack.assert_called_once()
        result.cleanup.assert_called_once()

    def test_translator_only_mode_skips_update_installer_ack(self):
        fake_jieba = MagicMock()
        result = _run_bootstrap(
            translator_only=True, platform="linux", jieba_module=fake_jieba
        )
        result.ack.assert_not_called()
        result.cleanup.assert_not_called()

    def test_returns_the_created_application(self):
        fake_jieba = MagicMock()
        result = _run_bootstrap(
            translator_only=True, platform="linux", jieba_module=fake_jieba
        )
        app = result.app
        self.assertIsInstance(app, _FakeApp)
        self.assertTrue(hasattr(app, "engine"))
        self.assertTrue(hasattr(app, "engine_thread"))
        self.assertTrue(app.engine_thread.started_called)


class RoutingThroughSharedBootstrapTests(unittest.TestCase):
    """(б) Маршрутизация: main_translator_only должен идти через main.bootstrap_application.

    Это должно ПАДАТЬ до рефакторинга (своя копия логики в
    main_translator_only._bootstrap_application) и ПРОХОДИТЬ после
    (тонкая обёртка вызывает app_main.bootstrap_application(..., translator_only=True)).
    """

    def test_translator_only_bootstrap_delegates_to_canonical_function(self):
        sentinel_app = object()
        with patch.object(
            main_translator_only.app_main,
            "bootstrap_application",
            return_value=sentinel_app,
        ) as mocked:
            result = main_translator_only._bootstrap_application()

        mocked.assert_called_once()
        args, kwargs = mocked.call_args
        # Позиционный argv должен доходить без изменений: от него зависят
        # configure_settings_scope_from_argv(argv) и ApplicationWithContext(argv)
        # внутри bootstrap_application — если сюда когда-нибудь подставят
        # пустой список вместо sys.argv, разбор флагов и scope настроек
        # тихо сломаются в translator-only режиме.
        self.assertEqual(args[0], sys.argv)
        self.assertEqual(kwargs.get("translator_only"), True)
        self.assertIs(result, sentinel_app)


class EnvironmentLeakageTests(unittest.TestCase):
    """(в) Импорт main_translator_only не должен необратимо мутировать окружение.

    main_translator_only.py:24-25 при импорте ставит GT_TRANSLATOR_ONLY_MODE=1
    и добавляет провайдеров в GT_DISABLED_PROVIDER_IDS. Эти переменные читаются
    в рантайме (gemini_translator/api/config.py:_filter_disabled_providers,
    ui/dialogs/menu_utils.py, ui/dialogs/setup.py) при каждом
    initialize_configs() — если их не восстановить, все тесты, импортируемые
    после этого файла в общем прогоне, тихо получают урезанный список
    провайдеров. Проверяем это через дочерний процесс, потому что в текущем
    процессе модуль main_translator_only уже импортирован один раз и повторный
    import не выполнит побочные эффекты снова.
    """

    def test_importing_main_translator_only_does_not_leak_env_vars(self):
        env = os.environ.copy()
        env.pop("GT_TRANSLATOR_ONLY_MODE", None)
        env.pop("GT_DISABLED_PROVIDER_IDS", None)
        env.setdefault("QT_QPA_PLATFORM", "offscreen")

        test_file = os.path.abspath(__file__)
        script = (
            "import os, importlib.util\n"
            "before_mode = os.environ.get('GT_TRANSLATOR_ONLY_MODE')\n"
            "before_disabled = os.environ.get('GT_DISABLED_PROVIDER_IDS')\n"
            f"spec = importlib.util.spec_from_file_location('_cluster78_probe', {test_file!r})\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(mod)\n"
            "after_mode = os.environ.get('GT_TRANSLATOR_ONLY_MODE')\n"
            "after_disabled = os.environ.get('GT_DISABLED_PROVIDER_IDS')\n"
            "assert after_mode == before_mode, ('GT_TRANSLATOR_ONLY_MODE', before_mode, after_mode)\n"
            "assert after_disabled == before_disabled, ('GT_DISABLED_PROVIDER_IDS', before_disabled, after_disabled)\n"
            "print('OK')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            env=env,
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(test_file)),
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )


if __name__ == "__main__":
    unittest.main()
