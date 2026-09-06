import contextlib
import io
import os
import tempfile
import time
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from gemini_translator.utils import settings as settings_module
from gemini_translator.utils.settings import SettingsManager


class _RecordingBus(QtCore.QObject):
    event_posted = QtCore.pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.events = []
        self.event_posted.connect(self.events.append)


class DailyResetPolicyTimezoneTests(unittest.TestCase):
    """gemini_translator/utils/settings.py: daily reset_policy timezone handling.

    Проверяет замену pytz.timezone -> zoneinfo.ZoneInfo (libs-pytz):
    1) корректный офсет в день перехода на летнее время (DST spring-forward)
       в _filter_request_timestamps_in_window и is_key_limit_active;
    2) осмысленную деградацию до 24ч при неизвестной таймзоне;
    3) предупреждение о неизвестной зоне печатается один раз на имя зоны,
       а не на каждый вызов (эти методы дергаются каждые 5с таймером
       обслуживания лимитов на каждый ключ/модель, плюс из перерисовки UI
       и выбора ключа в QA/воркерах).
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        # Дедуп предупреждений о неизвестной зоне держится в module-level
        # множестве -- сбрасываем его перед каждым тестом, чтобы порядок
        # запуска тестов не влиял на результат (иначе тест дедупа мог бы
        # "пройти" просто потому, что зона уже засветилась в другом тесте).
        settings_module._WARNED_UNKNOWN_TIMEZONES.clear()
        self.addCleanup(settings_module._WARNED_UNKNOWN_TIMEZONES.clear)

    def _create_manager(self):
        return SettingsManager(
            event_bus=_RecordingBus(),
            config_file=os.path.join(self.temp_dir.name, "settings.json"),
        )

    @contextmanager
    def _patched_provider_policy(self, provider_name, reset_policy):
        import gemini_translator.api.config as api_config

        original_view = api_config.api_providers_view
        api_config.api_providers_view = lambda: {
            provider_name: {"reset_policy": reset_policy}
        }
        try:
            yield
        finally:
            api_config.api_providers_view = original_view

    def _require_real_dst_transition(self, zone_name, before_utc, after_utc):
        """Явная проверка предпосылки перед DST-тестом.

        Если база IANA-таймзон недоступна (Windows без пакета tzdata) или
        правило перехода для этой зоны в будущем изменится, тест должен
        внятно объяснить, почему он не может ничего проверить, а не упасть
        с невнятным "Lists differ" / assertFalse mismatch.
        """
        try:
            tz = ZoneInfo(zone_name)
        except ZoneInfoNotFoundError:
            self.skipTest(
                f"База IANA-таймзон недоступна на этой машине: зона '{zone_name}' "
                f"не найдена (нет пакета tzdata?) -- DST-сценарий непроверяем"
            )
        offset_before = before_utc.astimezone(tz).utcoffset()
        offset_after = after_utc.astimezone(tz).utcoffset()
        if offset_before == offset_after:
            self.skipTest(
                f"Правило DST-перехода для '{zone_name}' не даёт разницы офсетов "
                f"между {before_utc.isoformat()} ({offset_before}) и "
                f"{after_utc.isoformat()} ({offset_after}) -- возможно, правило "
                f"изменилось в новой версии tzdata. Тест невалиден для этой базы."
            )

    def test_daily_reset_cutoff_at_dst_spring_forward_uses_post_transition_offset(self):
        # 2026-03-29: Europe/Berlin переводит часы на летнее время в 02:00 CET -> 03:00 CEST
        # (сам переход происходит в 01:00 UTC). reset_hour=0, reset_minute=1 -> окно
        # суточного сброса начинается ДО перехода, значит верный офсет для 00:01
        # локального времени 29 марта -- это ещё CET (+01:00), а не CEST (+02:00),
        # в которое попадает "текущий момент" (08:00 UTC = 10:00 CEST).
        self._require_real_dst_transition(
            "Europe/Berlin",
            datetime(2026, 3, 29, 0, 30, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 29, 1, 30, 0, tzinfo=timezone.utc),
        )

        policy = {
            "type": "daily",
            "timezone": "Europe/Berlin",
            "reset_hour": 0,
            "reset_minute": 1,
        }
        now_ts = int(datetime(2026, 3, 29, 8, 0, 0, tzinfo=timezone.utc).timestamp())

        # Верный cutoff (zoneinfo) = 2026-03-28 23:01:00Z (00:01 CET, +01:00).
        # Ошибочный cutoff (pytz, без localize/normalize) = 2026-03-28 22:01:00Z (+02:00).
        ts_before_correct_cutoff = int(
            datetime(2026, 3, 28, 22, 30, 0, tzinfo=timezone.utc).timestamp()
        )
        ts_after_correct_cutoff = int(
            datetime(2026, 3, 28, 23, 30, 0, tzinfo=timezone.utc).timestamp()
        )

        manager = self._create_manager()
        result = manager._filter_request_timestamps_in_window(
            [ts_before_correct_cutoff, ts_after_correct_cutoff],
            policy,
            now_ts=now_ts,
        )

        # До правильного cutoff (23:01Z) -- должен быть отфильтрован.
        # Ошибочная pytz-логика (cutoff 22:01Z) ошибочно сохраняет обе метки.
        self.assertEqual(result, [ts_after_correct_cutoff])

    def test_is_key_limit_active_dst_spring_forward_uses_post_transition_offset(self):
        # Тот же переход, но через is_key_limit_active (settings.py:452) -- отдельная
        # функция с тем же паттерном .replace(hour=..., minute=...) поверх ZoneInfo,
        # от неё зависит, покажет ли UI ключ как "исчерпан".
        self._require_real_dst_transition(
            "Europe/Berlin",
            datetime(2026, 3, 29, 0, 30, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 29, 1, 30, 0, tzinfo=timezone.utc),
        )

        policy = {
            "type": "daily",
            "timezone": "Europe/Berlin",
            "reset_hour": 0,
            "reset_minute": 1,
        }
        now_utc = datetime(2026, 3, 29, 8, 0, 0, tzinfo=timezone.utc)
        # Ключ исчерпан в 22:30Z 28.03 -- это ПОСЛЕ ошибочного pytz-cutoff (22:01Z),
        # но ДО верного zoneinfo-cutoff (23:01Z = 00:01 CET).
        exhausted_at = datetime(2026, 3, 28, 22, 30, 0, tzinfo=timezone.utc)

        manager = self._create_manager()
        key_info = {
            "provider": "dst_test_provider",
            "status_by_model": {
                "model-x": {
                    "exhausted_at": exhausted_at.timestamp(),
                    "exhausted_level": 2,
                    "requests": [],
                }
            },
        }

        with self._patched_provider_policy("dst_test_provider", policy):
            is_active = manager.is_key_limit_active(key_info, "model-x", now_utc=now_utc)

        # Верный (zoneinfo) результат: лимит уже сброшен (исчерпание было ДО верного
        # момента сброса) -> False. Ошибочный (pytz) офсет сдвинул бы момент сброса
        # на час раньше и посчитал бы лимит всё ещё активным (True) -- это был бы баг.
        self.assertFalse(is_active)

    def test_daily_reset_unknown_timezone_falls_back_to_24h_window(self):
        policy = {
            "type": "daily",
            "timezone": "Not/A_Real_Zone",
            "reset_hour": 0,
            "reset_minute": 1,
        }
        now_ts = int(datetime(2026, 3, 29, 8, 0, 0, tzinfo=timezone.utc).timestamp())

        ts_within_24h = now_ts - (10 * 3600)
        ts_older_than_24h = now_ts - (25 * 3600)

        manager = self._create_manager()
        result = manager._filter_request_timestamps_in_window(
            [ts_older_than_24h, ts_within_24h],
            policy,
            now_ts=now_ts,
        )

        self.assertEqual(result, [ts_within_24h])

    def test_is_key_limit_active_unknown_timezone_falls_back_to_24h(self):
        manager = self._create_manager()
        key_info = {
            "provider": "unknown_provider_with_daily_policy",
            "status_by_model": {
                "model-x": {
                    "exhausted_at": int(time.time()) - (10 * 3600),
                    "exhausted_level": 2,
                    "requests": [],
                }
            },
        }
        policy = {
            "type": "daily",
            "timezone": "Not/A_Real_Zone",
            "reset_hour": 0,
            "reset_minute": 1,
        }

        with self._patched_provider_policy("unknown_provider_with_daily_policy", policy):
            self.assertTrue(manager.is_key_limit_active(key_info, "model-x"))

    def test_unknown_timezone_warning_logged_once_per_zone_not_per_call(self):
        # settings.py: is_key_limit_active/_filter_request_timestamps_in_window
        # вызываются в хот-пути (таймер обслуживания лимитов раз в 5с на каждый
        # ключ/модель, перерисовка UI, выбор ключа в QA/воркерах). Неизвестная
        # зона (опечатка в config/api_providers.json или Windows без tzdata) не
        # должна печатать предупреждение на каждый такой вызов.
        policy = {
            "type": "daily",
            "timezone": "Dedup/Test_Zone",
            "reset_hour": 0,
            "reset_minute": 1,
        }
        now_ts = int(datetime(2026, 3, 29, 8, 0, 0, tzinfo=timezone.utc).timestamp())
        manager = self._create_manager()

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            for _ in range(5):
                manager._filter_request_timestamps_in_window(
                    [now_ts - 3600], policy, now_ts=now_ts
                )

        warning_lines = [
            line for line in buf.getvalue().splitlines() if "Dedup/Test_Zone" in line
        ]
        self.assertEqual(
            len(warning_lines),
            1,
            f"ожидалось ровно одно предупреждение на 5 вызовов, получено: {warning_lines}",
        )


if __name__ == "__main__":
    unittest.main()
