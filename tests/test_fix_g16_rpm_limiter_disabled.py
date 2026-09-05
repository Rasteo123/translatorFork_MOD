import unittest

from gemini_translator.api.errors import NetworkError, TemporaryRateLimitError, WorkerAction
from gemini_translator.core.worker_helpers.error_analyzer import ErrorAnalyzer
from gemini_translator.core.worker_helpers.rpm_limiter import RPMLimiter


class _DummyTaskManager:
    """Минимальная боевая замена task_manager — без сети и без реальных настроек."""

    def __init__(self):
        self.failures = []

    def _get_task_display_name(self, payload):
        return payload[2] if len(payload) > 2 else str(payload)

    def record_failure(self, task_info, error_type):
        self.failures.append(error_type)

    def get_failure_history(self, task_info):
        counts = {}
        for error_type in self.failures:
            counts[error_type] = counts.get(error_type, 0) + 1
        return {"total_count": len(self.failures), "errors": counts}


class _DummyWorker:
    """Заглушены только task_manager/_post_event; rpm_limiter — боевой RPMLimiter."""

    def __init__(self, rpm_limiter):
        self.task_manager = _DummyTaskManager()
        self.events = []
        self.chunking = False
        self.chunk_on_error = False
        self.skip_content_filter_retry = False
        self.rpm_limiter = rpm_limiter
        self.worker_id = "worker-0001"

    def _post_event(self, name, data=None):
        self.events.append((name, data or {}))


TASK = ("task-id", ("epub", "book.epub", "Text/ch.xhtml"))
EMPTY = {"total_count": 0, "errors": {}}


class RPMLimiterDisabledInstanceTests(unittest.TestCase):
    """
    Регресс на core-b/bugs/1-rpm-limiter-disabled-instance-.

    RPMLimiter(rpm_limit<=0) — задокументированный и протестированный
    "безлимитный" режим (см. test_no_limit_always_zero в
    tests/test_rpm_limiter.py). Изначально в этом режиме __init__ выходил
    раньше, чем создавались self.lock и self.last_request_time, а часть
    методов подменялась лямбдами (в т.ч. update_last_request_time без
    параметров). ErrorAnalyzer.analyze_and_act безусловно вызывает
    get_rpm(), decrease_rpm() и update_last_request_time(delay) при
    TEMPORARY_LIMIT и NETWORK ошибках — на такой заглушке это падало
    AttributeError/TypeError.

    Ревью показало, что простое устранение падения (лямбды остаются, но
    с "правильной" сигнатурой) — это лечение симптома: пауза, которую
    запросил сервер, продолжала молча теряться (can_proceed() всегда
    True независимо от update_last_request_time). Правка ниже убирает
    лямбды полностью и использует боевые методы класса с interval=0.0.
    """

    def test_disabled_limiter_has_lock_and_last_request_time(self):
        limiter = RPMLimiter(0)
        self.assertTrue(hasattr(limiter, "lock"))
        self.assertTrue(hasattr(limiter, "last_request_time"))

    def test_get_rpm_does_not_raise_on_disabled_limiter(self):
        limiter = RPMLimiter(0)
        self.assertEqual(limiter.get_rpm(), 0)

    def test_decrease_rpm_does_not_raise_on_disabled_limiter(self):
        limiter = RPMLimiter(0)
        # Не должно бросать AttributeError из-за отсутствующего self.lock.
        limiter.decrease_rpm(percentage=25)

    def test_sync_last_request_time_does_not_raise_on_disabled_limiter(self):
        limiter = RPMLimiter(0)
        limiter.sync_last_request_time(123.0)

    def test_update_last_request_time_accepts_delay_argument(self):
        limiter = RPMLimiter(0)
        # Реальная сигнатура — update_last_request_time(self, delay=0);
        # ErrorAnalyzer всегда вызывает её с позиционным delay.
        limiter.update_last_request_time(30)

    def test_can_proceed_true_and_no_wait_when_untouched(self):
        # Нетронутый "безлимитный" лимитер по-прежнему не тормозит запросы —
        # это уже проверено в test_rpm_limiter.py::test_no_limit_always_zero
        # для seconds_until_next_allowed, здесь дублируем и для can_proceed,
        # чтобы явно зафиксировать контракт рядом с остальными assert'ами.
        limiter = RPMLimiter(0)
        self.assertTrue(limiter.can_proceed())
        self.assertEqual(limiter.seconds_until_next_allowed(), 0.0)

    def test_decrease_rpm_does_not_desync_disabled_limiter(self):
        # Замечание рецензента (minor #2): decrease_rpm на отключённом
        # лимитере не должна тайно включать "1 RPM" — get_rpm() обязана
        # оставаться согласованной с реальным (отсутствующим) троттлингом.
        limiter = RPMLimiter(0)
        limiter.decrease_rpm(percentage=25)
        self.assertEqual(limiter.get_rpm(), 0)
        self.assertTrue(limiter.can_proceed())

    def test_temporary_limit_pause_is_honored_on_disabled_limiter(self):
        # Замечание рецензента (major): пауза, запрошенная сервером через
        # TemporaryRateLimitError(delay_seconds=...), не должна молча
        # теряться на "безлимитном" (rpm=0) воркере. Боевой ErrorAnalyzer
        # поверх боевого RPMLimiter(0) — без моков лимитера.
        limiter = RPMLimiter(0)
        worker = _DummyWorker(limiter)
        action, error_type, _ = ErrorAnalyzer(worker).analyze_and_act(
            TemporaryRateLimitError("slow down", delay_seconds=61), TASK, EMPTY
        )
        self.assertEqual(action, WorkerAction.RETRY_NON_COUNTABLE)
        self.assertEqual(error_type.name, "TEMPORARY_LIMIT")

        # До фикса тут было can_proceed() == True и seconds_until_next_allowed() == 0.0 —
        # то есть запрошенная сервером пауза в 61 секунду полностью игнорировалась.
        self.assertFalse(limiter.can_proceed())
        remaining = limiter.seconds_until_next_allowed()
        self.assertGreater(remaining, 60.0)
        self.assertLessEqual(remaining, 61.0)

    def test_network_error_pause_is_honored_on_disabled_limiter(self):
        limiter = RPMLimiter(0)
        worker = _DummyWorker(limiter)
        action, error_type, _ = ErrorAnalyzer(worker).analyze_and_act(
            NetworkError("conn reset", delay_seconds=30), TASK, EMPTY
        )
        self.assertEqual(action, WorkerAction.RETRY_COUNTABLE)
        self.assertEqual(error_type.name, "NETWORK")

        self.assertFalse(limiter.can_proceed())
        remaining = limiter.seconds_until_next_allowed()
        self.assertGreater(remaining, 29.0)
        self.assertLessEqual(remaining, 30.0)


if __name__ == "__main__":
    unittest.main()
