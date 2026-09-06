"""Дедуп dups-gt_qa_assembly-28: green_keys и green_embedding_keys — одна логика.

green_embedding_keys раньше повторяла фильтрацию ключей провайдера
(провайдер + непустота + is_key_limit_active) собственной копией кода.
Единственное отличие было в месте дедупликации (ранний ``key in keys`` против
финального ``dict.fromkeys``) и в обработке исключения ``is_key_limit_active``
(``pass`` против ``blocked = False``) — на конечный результат оба отличия не
влияют. Каноническая реализация — ``green_keys``; ``green_embedding_keys``
теперь вызывает её напрямую.
"""

from __future__ import annotations

import gemini_translator.qa.assembly as assembly


class _SettingsManager:
    def __init__(self, keys=(), blocked=()) -> None:
        self.keys = list(keys)
        self.blocked = set(blocked)

    def load_key_statuses(self):
        return [dict(item) for item in self.keys]

    def is_key_limit_active(self, key_info, model_id):
        return (key_info.get("key"), model_id) in self.blocked


def _keys(*pairs):
    return [{"key": key, "provider": provider} for key, provider in pairs]


# --- характеризационные тесты (поведение канонической green_keys) ----------


def test_green_embedding_keys_filters_by_provider_and_limit():
    manager = _SettingsManager(
        keys=_keys(("nv-1", "nvidia"), ("g-1", "gemini"), ("g-2", "gemini")),
        blocked=[("g-1", "embed-model")],
    )

    assert assembly.green_embedding_keys(manager, "gemini", "embed-model") == ("g-2",)


def test_green_embedding_keys_deduplicates_repeated_keys():
    manager = _SettingsManager(
        keys=_keys(("g-1", "gemini"), ("g-1", "gemini"), ("g-2", "gemini")),
    )

    assert assembly.green_embedding_keys(manager, "gemini", "m") == ("g-1", "g-2")


def test_green_embedding_keys_survives_none_manager_and_empty_provider():
    manager = _SettingsManager(keys=_keys(("g-1", "gemini")))

    assert assembly.green_embedding_keys(None, "gemini", "m") == ()
    assert assembly.green_embedding_keys(manager, "", "m") == ()


def test_green_embedding_keys_survives_unreadable_limit_status():
    class _Partial(_SettingsManager):
        def is_key_limit_active(self, key_info, model_id):
            raise OSError("status is unreadable")

    manager = _Partial(keys=_keys(("g-1", "gemini")))

    assert assembly.green_embedding_keys(manager, "gemini", "m") == ("g-1",)


# --- маршрутизация: green_embedding_keys обязана идти через green_keys -----


def test_green_embedding_keys_routes_through_green_keys(monkeypatch):
    """Красный до рефакторинга: green_embedding_keys держала свою копию логики."""
    calls = []

    def fake_green_keys(settings_manager, provider_id, model_id):
        calls.append((settings_manager, provider_id, model_id))
        return ("routed-key",)

    monkeypatch.setattr(assembly, "green_keys", fake_green_keys)

    manager = _SettingsManager(keys=_keys(("g-1", "gemini")))
    result = assembly.green_embedding_keys(manager, "gemini", "embed-model")

    assert result == ("routed-key",)
    assert calls == [(manager, "gemini", "embed-model")]
