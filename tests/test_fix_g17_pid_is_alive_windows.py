"""Фикс: _pid_is_alive использовал os.kill(pid, 0) как POSIX-пробу существования
процесса и на Windows (в отличие от touch() в этом же файле и worker.cancel_process)
не ветвился по os.name — на Windows os.kill(pid, 0) не проверяет существование
процесса, а уходит в GenerateConsoleCtrlEvent(CTRL_C_EVENT, pid). Это либо
ошибочно «хоронит» живых MCP-клиентов (файл сессии удаляется в
list_active_client_sessions), либо, наоборот, никогда не хоронит мёртвых —
в зависимости от того, есть ли у процесса консоль.

Тест воспроизводит дефект напрямую: на macOS/Linux (os.name != "nt") нельзя
позвать реальный WinAPI, поэтому Windows-путь тестируется через внедряемую
реализацию kernel32/get_last_error (та же граница платформенного вызова, что
и в реальном коде) — а не через мок самой проверяемой функции.

Все подмены глобального состояния (os.name, os.kill, client_sessions._pid_is_alive_windows)
идут через unittest.mock.patch.object, а не ручным присвоением с расчётом на finally:
patch.object гарантированно восстанавливает оригинал даже при исключении внутри блока
или ошибке в самом тесте (замечание ревью: ручная подмена os.kill без сохранения
оригинала навсегда отравляла os.kill на весь процесс pytest).
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gemini_translator.mcp import client_sessions


class PidIsAliveWindowsBranchTests(unittest.TestCase):
    """_pid_is_alive должна ветвиться по os.name, как это уже делает touch()
    в этом же файле (os.name != "nt") и worker.cancel_process (os.name == "nt")."""

    def test_windows_branch_is_not_posix_os_kill(self):
        """На os.name == 'nt' функция не должна звать os.kill(pid, 0) —
        до фикса os.kill вызывался безусловно, что на Windows не является
        POSIX-пробой существования процесса.

        Реальная сборка WinDLL недоступна на этой (не-Windows) машине, поэтому
        платформенная зависимость (_pid_is_alive_windows) подменяется — но
        диспетчеризация в _pid_is_alive (os.name == "nt" -> не os.kill) —
        боевой код."""
        real_os_name = os.name
        kill_calls = []
        windows_calls = []

        def spy_kill(pid, sig):
            kill_calls.append((pid, sig))
            raise ProcessLookupError()

        def fake_windows_check(pid_value, **kwargs):
            windows_calls.append(pid_value)
            return True

        with mock.patch.object(client_sessions.os, "kill", spy_kill), \
                mock.patch.object(client_sessions.os, "name", "nt"), \
                mock.patch.object(client_sessions, "_pid_is_alive_windows", fake_windows_check):
            result = client_sessions._pid_is_alive(os.getpid())

        # После выхода из `with` подмены гарантированно сняты (patch.object сам
        # это гарантирует) — проверяем явно для os.kill, чтобы регрессия (снова
        # ручное присвоение без восстановления) была поймана здесь, а не в
        # падении случайного соседнего теста.
        self.assertIs(client_sessions.os.kill, os.kill)
        self.assertEqual(os.name, real_os_name)  # платформа не подменена за пределами блока

        self.assertEqual(
            kill_calls, [],
            "на os.name == 'nt' _pid_is_alive не должна звать os.kill(pid, 0) — "
            "это POSIX-семантика, на Windows CTRL_C_EVENT",
        )
        self.assertEqual(windows_calls, [os.getpid()])
        self.assertTrue(result)

    def test_windows_process_exists_via_openprocess_success(self):
        """OpenProcess вернул валидный handle -> процесс жив."""

        class FakeKernel32:
            def OpenProcess(self, access, inherit, pid):
                return 1234  # ненулевой handle

            def CloseHandle(self, handle):
                return True

        self.assertTrue(
            client_sessions._pid_is_alive_windows(
                42, kernel32=FakeKernel32(), get_last_error=lambda: 0
            )
        )

    def test_windows_process_missing_via_openprocess_failure(self):
        """OpenProcess вернул NULL и ERROR_INVALID_PARAMETER -> процесса нет."""

        class FakeKernel32:
            def OpenProcess(self, access, inherit, pid):
                return 0

            def CloseHandle(self, handle):
                raise AssertionError("CloseHandle не должен звать при NULL handle")

        self.assertFalse(
            client_sessions._pid_is_alive_windows(
                999999, kernel32=FakeKernel32(), get_last_error=lambda: 87
            )
        )

    def test_windows_process_access_denied_means_alive(self):
        """OpenProcess вернул NULL с ERROR_ACCESS_DENIED -> процесс есть, просто
        нет прав (как PermissionError на POSIX-ветке — тоже трактуется как alive)."""

        class FakeKernel32:
            def OpenProcess(self, access, inherit, pid):
                return 0

            def CloseHandle(self, handle):
                raise AssertionError("CloseHandle не должен звать при NULL handle")

        self.assertTrue(
            client_sessions._pid_is_alive_windows(
                1, kernel32=FakeKernel32(), get_last_error=lambda: 5
            )
        )

    def test_non_oserror_exception_from_windows_check_is_swallowed(self):
        """ctypes.ArgumentError/AttributeError на границе ctypes — не OSError,
        но status_payload не должен падать: любая ошибка Windows-проверки
        должна трактоваться как «процесса нет», а не пробрасываться наружу."""

        def broken_check(pid_value, **kwargs):
            raise AttributeError("сломанный символ в kernel32")

        with mock.patch.object(client_sessions.os, "name", "nt"), \
                mock.patch.object(client_sessions, "_pid_is_alive_windows", broken_check):
            result = client_sessions._pid_is_alive(os.getpid())

        self.assertFalse(result)


class ListActiveClientSessionsWindowsSemanticsTests(unittest.TestCase):
    """Пользовательски видимое поведение: на Windows list_active_client_sessions
    не должна удалять файл живого клиента и должна удалять файл мёртвого —
    именно ради этого делалась правка в _pid_is_alive.

    Прямая подмена os.name == "nt" здесь непригодна: pathlib.Path.__new__
    сам смотрит на os.name и на не-Windows машине не может построить
    WindowsPath (см. эксперимент — NotImplementedError при попытке), а
    list_active_client_sessions строит Path на КАЖДОМ вызове (ensure_state_dirs).
    Поэтому дошедшая до этого места Windows-семантика воспроизводится через
    настоящую _pid_is_alive_windows на подставленном kernel32 — как и в тестах
    выше, платформенная граница инъецируется, а не мокается сама проверяемая
    функция; дистпетчеризация os.name == "nt" -> _pid_is_alive_windows уже
    отдельно проверена test_windows_branch_is_not_posix_os_kill."""

    def test_alive_pid_kept_dead_pid_removed_under_windows_semantics(self):
        alive_pid = os.getpid()  # текущий процесс — заведомо жив
        dead_pid = 999999991  # заведомо не существует ни на одной платформе

        class FakeKernel32:
            def OpenProcess(self, access, inherit, pid):
                return 1234 if pid == alive_pid else 0

            def CloseHandle(self, handle):
                return True

        def fake_pid_is_alive(pid):
            # Воспроизводит боевую ветку _pid_is_alive при os.name == "nt":
            # делегирование в _pid_is_alive_windows (настоящий код, не мок).
            return client_sessions._pid_is_alive_windows(
                int(pid), kernel32=FakeKernel32(), get_last_error=lambda: 87
            )

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            root = client_sessions.ensure_state_dirs(state_dir)
            client_root = client_sessions.clients_dir(root)
            client_root.mkdir(parents=True, exist_ok=True)

            alive_payload = {
                "id": "alive_client",
                "transport": "stdio",
                "client_name": "Alive",
                "pid": alive_pid,
                "last_seen_epoch": 0,  # давно, чтобы решала именно проверка pid, а не свежесть
            }
            dead_payload = {
                "id": "dead_client",
                "transport": "stdio",
                "client_name": "Dead",
                "pid": dead_pid,
                "last_seen_epoch": 0,
            }
            (client_root / "alive_client.json").write_text(
                __import__("json").dumps(alive_payload), encoding="utf-8"
            )
            (client_root / "dead_client.json").write_text(
                __import__("json").dumps(dead_payload), encoding="utf-8"
            )

            with mock.patch.object(client_sessions, "_pid_is_alive", fake_pid_is_alive):
                active = client_sessions.list_active_client_sessions(state_dir)

            active_ids = {item["id"] for item in active}
            self.assertEqual(active_ids, {"alive_client"})
            self.assertTrue((client_root / "alive_client.json").exists())
            self.assertFalse((client_root / "dead_client.json").exists())


if __name__ == "__main__":
    unittest.main()
