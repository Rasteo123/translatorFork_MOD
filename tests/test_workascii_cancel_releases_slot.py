"""ChatGPT Web (work_ascii): отмена запроса не должна протекать слот моста.

Регрессия аудита api/bugs/1: call_api занимал слот в _acquire_bridge_request_slot,
а при CancelledError (глобальный таймаут asyncio.wait_for в api/base.py или отмена
пользователем) освобождал его только в ветке except Exception. Счётчик
_active_bridge_calls оставался >= 1 навсегда, и при включённом периодическом
перезапуске профиля (refresh_every_requests > 0) следующий запрос зависал в
_acquire_bridge_request_slot без единого способа проснуться.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gemini_translator.api.handlers.workascii_chatgpt import WorkAsciiChatGptApiHandler


def _handler(refresh_every_requests: int = 0) -> WorkAsciiChatGptApiHandler:
    worker = SimpleNamespace(
        provider_config={"is_async": True, "base_timeout": 1800},
        prompt_builder=SimpleNamespace(system_instruction=""),
        _post_event=lambda *args, **kwargs: None,
    )
    handler = WorkAsciiChatGptApiHandler(worker)
    handler.refresh_every_requests = refresh_every_requests
    handler._ensure_bridge_ready = AsyncMock()
    handler._terminate_bridge = AsyncMock()
    handler._debug_record_request = lambda *args, **kwargs: None
    handler._debug_record_response = lambda *args, **kwargs: None
    return handler


async def _hang_forever(*args, **kwargs):
    await asyncio.sleep(3600)


async def _ok_response(*args, **kwargs):
    return {"ok": True, "text": "переведено"}


def test_cancelled_call_releases_bridge_slot():
    handler = _handler()
    handler._send_command = _hang_forever

    async def scenario():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(handler.call_api("prompt", "[test]"), timeout=0.05)
        return handler._active_bridge_calls

    assert asyncio.run(scenario()) == 0


def test_calls_after_a_cancel_do_not_deadlock_with_periodic_refresh():
    handler = _handler(refresh_every_requests=2)
    handler._send_command = _hang_forever

    async def scenario():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(handler.call_api("prompt", "[test]"), timeout=0.05)
        handler._send_command = _ok_response
        results = []
        for _ in range(4):
            results.append(await asyncio.wait_for(handler.call_api("prompt", "[test]"), timeout=1.0))
        return results

    assert asyncio.run(scenario()) == ["переведено"] * 4
