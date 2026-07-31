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
                "-D", "127.0.0.1:8080",
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

    def test_missing_ssh_binary_emits_error_and_schedules_a_retry(self):
        def popen_factory(args):
            raise FileNotFoundError("ssh not found")

        manager = SshTunnelManager(popen_factory=popen_factory)
        events = []
        manager.status_changed.connect(lambda state, msg: events.append((state, msg)))

        manager.start(
            ssh_host="h", ssh_port=22, ssh_user="root", ssh_key_path="/key", local_port=8080,
        )

        self.assertEqual(events[-1], ("error", "ssh not found"))
        self.assertTrue(manager._restart_timer.isActive())
        self.assertEqual(manager._restart_timer.interval(), BACKOFF_SCHEDULE_SECONDS[0] * 1000)
        self.assertFalse(manager.active)

    def test_stop_cancels_retry_when_no_process_is_active(self):
        def popen_factory(args):
            raise OSError("temporary spawn failure")

        manager = SshTunnelManager(popen_factory=popen_factory)
        manager.start(
            ssh_host="h", ssh_port=22, ssh_user="root", ssh_key_path="/key", local_port=8080,
        )
        self.assertTrue(manager._restart_timer.isActive())

        manager.stop()

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

    def test_default_stderr_reader_reads_a_real_pipe(self):
        from gemini_translator.utils.ssh_tunnel import _default_stderr_reader

        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b"Permission denied (publickey).\n")

            class _ProcWithRealStderr:
                def __init__(self, stderr):
                    self.stderr = stderr

            with os.fdopen(read_fd, "rb") as stderr_file:
                read_fd = None  # fdopen now owns it, avoid double-close
                process = _ProcWithRealStderr(stderr_file)
                line = _default_stderr_reader(process)
                self.assertEqual(line, "Permission denied (publickey).")
        finally:
            os.close(write_fd)
            if read_fd is not None:
                os.close(read_fd)

    def test_default_stderr_reader_buffers_partial_real_pipe_line(self):
        from gemini_translator.utils.ssh_tunnel import _default_stderr_reader

        read_fd, write_fd = os.pipe()
        try:
            os.set_blocking(read_fd, False)

            class _ProcWithRealStderr:
                def __init__(self, stderr):
                    self.stderr = stderr

            with os.fdopen(read_fd, "rb") as stderr_file:
                read_fd = None
                process = _ProcWithRealStderr(stderr_file)
                os.write(write_fd, b"Permission denied")
                self.assertIsNone(_default_stderr_reader(process))

                os.write(write_fd, b" (publickey).\n")
                self.assertEqual(
                    _default_stderr_reader(process),
                    "Permission denied (publickey).",
                )
        finally:
            os.close(write_fd)
            if read_fd is not None:
                os.close(read_fd)

    def test_default_stderr_reader_drains_multiple_buffered_real_pipe_lines(self):
        from gemini_translator.utils.ssh_tunnel import _default_stderr_reader

        read_fd, write_fd = os.pipe()
        try:
            class _ProcWithRealStderr:
                def __init__(self, stderr):
                    self.stderr = stderr

            with os.fdopen(read_fd, "rb") as stderr_file:
                read_fd = None
                process = _ProcWithRealStderr(stderr_file)
                os.write(write_fd, b"first error\nsecond error\n")

                self.assertEqual(_default_stderr_reader(process), "first error")
                self.assertEqual(_default_stderr_reader(process), "second error")
        finally:
            os.close(write_fd)
            if read_fd is not None:
                os.close(read_fd)

    def test_stop_kills_process_when_terminate_times_out(self):
        import subprocess as subprocess_module

        class _FakeProcessWithTimeout(_FakeProcess):
            def __init__(self):
                super().__init__()
                self._wait_calls = 0

            def wait(self, timeout=None):
                self.waited = True
                self._wait_calls += 1
                if self._wait_calls == 1:
                    raise subprocess_module.TimeoutExpired(cmd="ssh", timeout=3)
                # Second call succeeds

        manager, process, _ = self._make_manager(process=_FakeProcessWithTimeout())
        manager.start(
            ssh_host="h", ssh_port=22, ssh_user="root", ssh_key_path="/key", local_port=8080,
        )

        manager.stop()

        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        self.assertTrue(process.waited)
        self.assertFalse(manager.active)
        self.assertFalse(manager._check_timer.isActive())
        self.assertFalse(manager._restart_timer.isActive())

    def test_stop_kills_process_when_terminate_raises(self):
        class _FakeProcessWithTerminateFailure(_FakeProcess):
            def terminate(self):
                self.terminated = True
                raise OSError("terminate failed")

        manager, process, _ = self._make_manager(process=_FakeProcessWithTerminateFailure())
        manager.start(
            ssh_host="h", ssh_port=22, ssh_user="root", ssh_key_path="/key", local_port=8080,
        )

        manager.stop()

        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        self.assertTrue(process.waited)
        self.assertIsNone(manager._process)

    def test_stop_retains_process_when_fallback_kill_fails(self):
        class _FakeProcessWithShutdownFailure(_FakeProcess):
            def terminate(self):
                self.terminated = True
                raise OSError("terminate failed")

            def kill(self):
                self.killed = True
                raise OSError("kill failed")

        manager, process, _ = self._make_manager(process=_FakeProcessWithShutdownFailure())
        manager.start(
            ssh_host="h", ssh_port=22, ssh_user="root", ssh_key_path="/key", local_port=8080,
        )

        manager.stop()

        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        self.assertIs(manager._process, process)


if __name__ == "__main__":
    unittest.main()
