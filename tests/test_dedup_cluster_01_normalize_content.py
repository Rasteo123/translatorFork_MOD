# -*- coding: utf-8 -*-
"""Тесты для кластера дублей cluster-01: извлечение текста из content-массива
LLM-ответа было продублировано под именами _normalize_content (nvidia.py) и
_extract_text_from_content (openmodel.py). Каноническая версия —
BaseApiHandler._normalize_content в gemini_translator/api/base.py.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gemini_translator.api.base import BaseApiHandler
from gemini_translator.api.handlers.nvidia import NvidiaApiHandler
from gemini_translator.api.handlers.openmodel import OpenModelApiHandler


class _WorkerStub:
    def __init__(self, model_config=None):
        self.provider_config = {"base_url": "https://example.invalid", "is_async": False}
        self.model_config = {"id": "some-model"}
        if model_config:
            self.model_config.update(model_config)
        self.prompt_builder = SimpleNamespace(system_instruction=None)
        self.temperature = 0.2
        self.temperature_override_enabled = True
        self.api_key = ""
        self.model_id = ""
        self.events = []

    def _post_event(self, event, payload):
        self.events.append((event, payload))


class NormalizeContentCharacterizationTests(unittest.TestCase):
    """(а) Характеризационные тесты на поведение канонической реализации —
    крайние случаи, которые различали копии (обе копии вели себя одинаково,
    поэтому это доказательство их эквивалентности, а не расхождения)."""

    def test_str_passthrough(self):
        self.assertEqual(BaseApiHandler._normalize_content("hello"), "hello")

    def test_none_returns_empty_string(self):
        self.assertEqual(BaseApiHandler._normalize_content(None), "")

    def test_non_str_non_list_non_none_is_stringified(self):
        self.assertEqual(BaseApiHandler._normalize_content(42), "42")

    def test_list_of_strings_joined(self):
        self.assertEqual(BaseApiHandler._normalize_content(["a", "b"]), "ab")

    def test_list_dict_text_key(self):
        self.assertEqual(
            BaseApiHandler._normalize_content([{"text": "foo"}]), "foo"
        )

    def test_list_dict_content_key_fallback(self):
        self.assertEqual(
            BaseApiHandler._normalize_content([{"content": "bar"}]), "bar"
        )

    def test_list_dict_output_text_type_with_text_none(self):
        # Ветка type == "output_text" при text отсутствующем — text остаётся None,
        # элемент не добавляется в результат (совпадает с обеими копиями).
        self.assertEqual(
            BaseApiHandler._normalize_content([{"type": "output_text"}]), ""
        )

    def test_list_dict_without_recognized_keys_is_skipped(self):
        self.assertEqual(BaseApiHandler._normalize_content([{"other": "x"}]), "")

    def test_list_non_none_scalar_stringified(self):
        self.assertEqual(BaseApiHandler._normalize_content([1, None, "x"]), "1x")

    def test_mixed_list(self):
        content = ["a", {"text": "b"}, {"content": "c"}, 3, None]
        self.assertEqual(BaseApiHandler._normalize_content(content), "abc3")

    def test_is_staticmethod_not_using_self(self):
        # recommended_canonical требует staticmethod, т.к. self не используется.
        self.assertIsInstance(
            BaseApiHandler.__dict__["_normalize_content"], staticmethod
        )


class NormalizeContentRoutingTests(unittest.TestCase):
    """(б) Тест-маршрутизация: подмена канонической функции должна перехватывать
    вызовы из ОБОИХ бывших мест дублирования (nvidia.py и openmodel.py)."""

    def _make_nvidia_handler(self):
        worker = _WorkerStub()
        handler = NvidiaApiHandler(worker)
        return handler

    def _make_openmodel_handler(self):
        worker = _WorkerStub()
        handler = OpenModelApiHandler(worker)
        return handler

    def test_nvidia_clean_response_text_routes_through_canonical(self):
        handler = self._make_nvidia_handler()
        with patch.object(
            BaseApiHandler, "_normalize_content", return_value="patched"
        ) as mocked:
            result = handler._clean_response_text([{"text": "irrelevant"}])
        mocked.assert_called_once()
        self.assertEqual(result, "patched")

    def test_nvidia_apply_gemma_options_routes_through_canonical(self):
        handler = self._make_nvidia_handler()
        payload = {
            "messages": [{"role": "system", "content": [{"text": "sys"}]}]
        }
        handler.worker.thinking_enabled = True
        with patch.object(
            BaseApiHandler, "_normalize_content", return_value="patched-sys"
        ) as mocked:
            handler._apply_gemma_options(payload)
        mocked.assert_called_once()
        self.assertTrue(
            payload["messages"][0]["content"].startswith("<|think|>")
        )

    def test_openmodel_extract_text_from_result_routes_through_canonical(self):
        handler = self._make_openmodel_handler()
        result = {"content": [{"text": "irrelevant"}]}
        with patch.object(
            BaseApiHandler, "_normalize_content", return_value="patched"
        ) as mocked:
            text = handler._extract_text_from_result(result)
        mocked.assert_called_once()
        self.assertEqual(text, "patched")


if __name__ == "__main__":
    unittest.main()
