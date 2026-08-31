"""QA must reach the network the way the translation session does — proxy included."""

from __future__ import annotations

import asyncio

from gemini_translator.qa.assembly import (
    aiohttp_session_factory,
    proxy_url_from_settings,
)


_SOCKS = {"enabled": True, "type": "SOCKS5", "host": "127.0.0.1", "port": 8080}


def test_the_proxy_url_matches_what_the_workers_would_build():
    """Два разных представления одного прокси — это два разных прокси."""
    assert proxy_url_from_settings(_SOCKS) == "socks5://127.0.0.1:8080"
    assert proxy_url_from_settings(
        {**_SOCKS, "user": "u", "pass": "p"}
    ) == "socks5://u:p@127.0.0.1:8080"
    assert proxy_url_from_settings({**_SOCKS, "type": "HTTP"}) == "http://127.0.0.1:8080"


def test_absent_disabled_or_incomplete_settings_mean_a_direct_connection():
    """Полупрокси хуже отсутствия прокси: он молча роняет каждый запрос."""
    assert proxy_url_from_settings(None) == ""
    assert proxy_url_from_settings({}) == ""
    assert proxy_url_from_settings({**_SOCKS, "enabled": False}) == ""
    assert proxy_url_from_settings({**_SOCKS, "host": ""}) == ""
    assert proxy_url_from_settings({**_SOCKS, "port": ""}) == ""
    assert proxy_url_from_settings("socks5://127.0.0.1:8080") == ""


def _connector_type(proxy_settings) -> str:
    async def probe() -> str:
        session = aiohttp_session_factory(proxy_settings)()
        try:
            return type(session.connector).__name__
        finally:
            await session.close()

    return asyncio.run(probe())


def test_an_enabled_proxy_reaches_the_embedding_session():
    """Поле, на котором это сломалось: перевод шёл через туннель, QA — мимо."""
    assert _connector_type(_SOCKS) == "ProxyConnector"


def test_without_a_proxy_the_session_stays_direct():
    assert _connector_type(None) == "TCPConnector"
    assert _connector_type({**_SOCKS, "enabled": False}) == "TCPConnector"
