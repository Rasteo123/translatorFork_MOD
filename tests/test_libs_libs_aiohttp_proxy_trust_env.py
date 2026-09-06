"""
Библиотечный вердикт libs-aiohttp-proxy (aiohttp), пункт (2).

До фикса base.py создавал aiohttp.ClientSession БЕЗ trust_env (эффективно
False — сессия не подхватывает системный прокси из HTTP_PROXY/HTTPS_PROXY),
а qa/assembly.py — с trust_env=True (подхватывает системный прокси в
дополнение к прокси, явно настроенному в приложении).

Выбранная единая политика: trust_env=False в обоих местах.

Обоснование (behavior_choice): у apiшной сессии обновлятеля
(gemini_translator/utils/updater.py:build_updater_session) уже есть явный,
документированный прецедент для всего проекта — "системный прокси не должен
молча перехватывать трафик" (trust_env=False, requests.Session). Прокси в
этом приложении — explicit opt-in через настройки приложения (proxy_settings),
а не через переменные окружения ОС: и base.py, и qa/assembly.py уже сами
строят ProxyConnector из proxy_settings, когда пользователь включил прокси в
приложении. trust_env=True в assembly.py добавляет ВТОРОЙ, неявный источник
прокси (переменные окружения), который может молча включиться в
CI/корпоративной среде, где HTTP_PROXY/HTTPS_PROXY выставлены не пользователем
приложения, и создать несогласованность: QA-запросы идут через системный
прокси, а перевод (тот же чат с тем же провайдером) — напрямую. Это уже
наблюдалось как источник багов с "тихим" переключением сетевого пути
(см. content-filter/degenerate-partial и proxy-session-reset тесты). Оставляем
единственный источник истины — proxy_settings приложения — как для base.py,
так и для qa/assembly.py.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import patch

from gemini_translator.api import base as base_module
from gemini_translator.api.base import BaseApiHandler, get_worker_loop
from gemini_translator.qa import assembly as assembly_module


class _WorkerStub:
    def __init__(self):
        self.provider_config = {"is_async": True, "base_timeout": 600}


class _FakeClientSession:
    def __init__(self, *args, **kwargs):
        self.closed = False
        self.kwargs = kwargs

    async def close(self):
        self.closed = True


class _FakeTCPConnector:
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs


class TranslationSessionTrustEnvTests(unittest.TestCase):
    """base.py: сессия для перевода не должна молча брать прокси из ОС."""

    def setUp(self):
        self.handler = BaseApiHandler(_WorkerStub())

    def tearDown(self):
        loop = get_worker_loop()
        if not loop.is_closed():
            loop.run_until_complete(self.handler._close_thread_session_internal())
            loop.close()
        for attr in ("loop", "session", "session_proxy_signature", "session_timeout", "session_ssl_context_signature"):
            if hasattr(base_module._thread_local, attr):
                delattr(base_module._thread_local, attr)

    def test_direct_session_declares_trust_env_false(self):
        loop = get_worker_loop()
        with patch("gemini_translator.api.base.aiohttp.ClientSession", _FakeClientSession), \
             patch("gemini_translator.api.base.aiohttp.TCPConnector", _FakeTCPConnector):
            self.handler.setup_client(proxy_settings={"enabled": False})
            session = loop.run_until_complete(self.handler._get_or_create_session_internal(600))

        self.assertIn("trust_env", session.kwargs, "политика прокси должна быть явной, не значением по умолчанию")
        self.assertIs(session.kwargs["trust_env"], False)


class QaSessionTrustEnvMatchesTranslationTests(unittest.TestCase):
    """qa/assembly.py: тот же принцип, что и в base.py — только явно настроенный
    в приложении прокси, без молчаливого системного (trust_env=False)."""

    def test_qa_factory_session_declares_trust_env_false(self):
        factory = assembly_module.aiohttp_session_factory(proxy_settings=None)

        with patch("aiohttp.ClientSession", _FakeClientSession), \
             patch("aiohttp.TCPConnector", _FakeTCPConnector):
            session = factory()

        self.assertIs(session.kwargs.get("trust_env"), False)


if __name__ == "__main__":
    unittest.main()
