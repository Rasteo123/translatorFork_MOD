import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.utils.ssh_tunnel import BACKOFF_SCHEDULE_SECONDS, SshTunnelManager


class _FakeProcess:
    def __init__(self):
        self.terminated = False
        self.killed = False
        self.waited = False
        self._returncode = None

    def poll(self):
        return self._returncode

    def set_exited(self, code=1):
        self._returncode = code

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waited = True


class SshTunnelManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_manager(self, process=None, stderr_lines=None):
        process = process or _FakeProcess()
        calls = []
        stderr_lines = list(stderr_lines or [])

        def popen_factory(args):
            calls.append(args)
            return process

        def stderr_reader(proc):
            return stderr_lines.pop(0) if stderr_lines else None

        manager = SshTunnelManager(popen_factory=popen_factory, stderr_reader=stderr_reader)
        return manager, process, calls

    def test_start_spawns_ssh_with_expected_arguments_and_emits_up(self):
        manager, process, calls = self._make_manager()
        events = []
        manager.status_changed.connect(lambda state, msg: events.append((state, msg)))

        manager.start(
            ssh_host="91.107.201.91",
            ssh_port=22,
            ssh_user="root",
            ssh_key_path="/Users/rasreo/Documents/ssh/_ssh/id_ed25519",
            local_port=8080,
        )

        self.assertEqual(
            calls,
            [[
                "ssh",
                "-i", "/Users/rasreo/Documents/ssh/_ssh/id_ed25519",
                "-p", "22",
                "-D", "8080",
                "-N",
                "-o", "StrictHostKeyChecking=yes",
                "-o", "ServerAliveInterval=15",
                "-o", "ServerAliveCountMax=3",
                "-o", "ExitOnForwardFailure=yes",
                "root@91.107.201.91",
            ]],
        )
        self.assertTrue(manager.active)
        self.assertEqual(events, [("connecting", ""), ("up", "")])

    def test_process_exit_emits_down_and_schedules_first_backoff(self):
        manager, process, _ = self._make_manager(stderr_lines=["Connection refused"])
        events = []
        manager.status_changed.connect(lambda state, msg: events.append((state, msg)))

        manager.start(
            ssh_host="91.107.201.91", ssh_port=22, ssh_user="root",
            ssh_key_path="/key", local_port=8080,
        )
        process.set_exited(255)
        manager._check_process()

        self.assertFalse(manager.active)
        self.assertEqual(events[-1], ("down", "Connection refused"))
        self.assertTrue(manager._restart_timer.isActive())
        self.assertEqual(manager._restart_timer.interval(), BACKOFF_SCHEDULE_SECONDS[0] * 1000)

    def test_repeated_failures_grow_backoff_up_to_cap(self):
        manager, process, _ = self._make_manager()
        manager.start(
            ssh_host="h", ssh_port=22, ssh_user="root", ssh_key_path="/key", local_port=8080,
        )

        observed_intervals = []
        for _ in range(len(BACKOFF_SCHEDULE_SECONDS) + 2):
            process.set_exited(255)
            manager._check_process()
            observed_intervals.append(manager._restart_timer.interval())
            manager._restart_timer.stop()
            manager._spawn()

        expected = [s * 1000 for s in BACKOFF_SCHEDULE_SECONDS] + [
            BACKOFF_SCHEDULE_SECONDS[-1] * 1000,
            BACKOFF_SCHEDULE_SECONDS[-1] * 1000,
        ]
        self.assertEqual(observed_intervals, expected)

    def test_stop_terminates_process_and_stops_timers(self):
        manager, process, _ = self._make_manager()
        manager.start(
            ssh_host="h", ssh_port=22, ssh_user="root", ssh_key_path="/key", local_port=8080,
        )

        manager.stop()

        self.assertTrue(process.terminated)
        self.assertTrue(process.waited)
        self.assertFalse(manager.active)
        self.assertFalse(manager._check_timer.isActive())
        self.assertFalse(manager._restart_timer.isActive())

    def test_missing_ssh_binary_emits_error_and_does_not_schedule_restart(self):
        def popen_factory(args):
            raise FileNotFoundError("ssh not found")

        manager = SshTunnelManager(popen_factory=popen_factory)
        events = []
        manager.status_changed.connect(lambda state, msg: events.append((state, msg)))

        manager.start(
            ssh_host="h", ssh_port=22, ssh_user="root", ssh_key_path="/key", local_port=8080,
        )

        self.assertEqual(events[-1], ("error", "ssh not found"))
        self.assertFalse(manager._restart_timer.isActive())
        self.assertFalse(manager.active)

    def test_start_is_idempotent_when_already_active(self):
        manager, process, calls = self._make_manager()
        manager.start(
            ssh_host="h", ssh_port=22, ssh_user="root", ssh_key_path="/key", local_port=8080,
        )
        manager.start(
            ssh_host="h", ssh_port=22, ssh_user="root", ssh_key_path="/key", local_port=8080,
        )

        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
