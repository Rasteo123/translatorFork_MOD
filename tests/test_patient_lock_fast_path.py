import threading
import time
import unittest

import os_patch


class PatientLockFastPathTests(unittest.TestCase):
    def test_uncontended_acquire_release_is_fast(self):
        """200 захватов свободного замка должны занимать миллисекунды.
        Безусловный time.sleep в acquire() делал каждый вызов настроек
        медленнее на ~1-2 мс по всему приложению."""
        lock = os_patch.PatientLock()

        started = time.perf_counter()
        for _ in range(200):
            lock.acquire()
            lock.release()
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.1,
                        f"200 uncontended acquires took {elapsed:.3f}s")

    def test_owner_stack_not_captured_by_default(self):
        """traceback.format_stack на каждом захвате — плата за диагностику,
        которая должна включаться только явно."""
        lock = os_patch.PatientLock()
        lock.acquire()
        try:
            self.assertIsNone(lock._owner_stack)
        finally:
            lock.release()

    def test_recursion_detection_still_raises(self):
        lock = os_patch.PatientLock()
        lock.acquire()
        try:
            with self.assertRaises(RuntimeError):
                lock.acquire()
        finally:
            lock.release()

    def test_contended_acquire_still_hands_over(self):
        lock = os_patch.PatientLock()
        results = []

        def worker():
            lock.acquire()
            results.append(threading.get_ident())
            lock.release()

        lock.acquire()
        thread = threading.Thread(target=worker)
        thread.start()
        time.sleep(0.05)
        lock.release()
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
