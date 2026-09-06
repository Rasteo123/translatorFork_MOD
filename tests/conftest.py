import os
import tempfile


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("GT_DISABLE_LOCAL_MODEL_DISCOVERY", "1")

# Тесты не имеют права трогать настройки живого приложения.
# QSettings("SiberianTeam", "TranslatorFork") — это ~/Library/Preferences
# (macOS) и реестр (Windows), там же лежит галочка «Звуковые и системные
# уведомления». Запись из теста переживает прогон и молча меняет настройки
# пользователя.
#
# setDefaultFormat() тут не спасает: в Qt 6 конструктор по org/app всегда
# берёт NativeFormat, а setPath() для NativeFormat на macOS и Windows не
# действует. Поэтому подменяем сам класс: форму «организация + приложение»
# переписываем на ini-файл во временном каталоге.
_settings_dir = os.environ.get("GT_TEST_SETTINGS_DIR")
if not _settings_dir:
    _settings_dir = tempfile.mkdtemp(prefix="gt-test-settings-")
    os.environ["GT_TEST_SETTINGS_DIR"] = _settings_dir

if os.name == "nt":
    # На Windows abort() из C-кода (qFatal в Qt и т.п.) по умолчанию уходит в
    # fail-fast (код 0xC0000409) без единой строки в логе. Снимаем флаги
    # _WRITE_ABORT_MSG|_CALL_REPORTFAULT: тогда abort() поднимает SIGABRT, и
    # faulthandler pytest печатает стеки всех потоков в run_checks.log.
    try:
        import ctypes

        ctypes.cdll.ucrtbase._set_abort_behavior(0, 3)
    except Exception:
        pass

from PyQt6 import QtCore  # noqa: E402  (после QT_QPA_PLATFORM)

_NativeQSettings = QtCore.QSettings


class _IsolatedQSettings(_NativeQSettings):
    """QSettings, который в форме (организация, приложение) пишет в temp."""

    def __init__(self, *args, **kwargs):
        if len(args) == 2 and isinstance(args[0], str) and isinstance(args[1], str):
            args = (
                _NativeQSettings.Format.IniFormat,
                _NativeQSettings.Scope.UserScope,
            ) + args
        super().__init__(*args, **kwargs)


_NativeQSettings.setPath(
    _NativeQSettings.Format.IniFormat,
    _NativeQSettings.Scope.UserScope,
    _settings_dir,
)
_NativeQSettings.setPath(
    _NativeQSettings.Format.IniFormat,
    _NativeQSettings.Scope.SystemScope,
    _settings_dir,
)
QtCore.QSettings = _IsolatedQSettings


def pytest_runtest_logreport(report):
    """Печатать упавший тест сразу, минуя захват вывода.

    Итоговая сводка pytest появляется только в конце прогона; если процесс
    позже аварийно завершится (например, qFatal в Qt на Windows CI), имена
    упавших тестов пропадают вместе с ней."""
    if report.failed:
        import sys

        detail = ""
        try:
            text = report.longreprtext or ""
            tail = [line for line in text.splitlines() if line.strip()][-12:]
            if tail:
                detail = "\n" + "\n".join("    " + line for line in tail)
        except Exception:
            detail = ""
        sys.__stderr__.write(f"\n[FAILED] {report.nodeid} ({report.when}){detail}\n")
        sys.__stderr__.flush()
