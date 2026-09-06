# -*- coding: utf-8 -*-
"""cluster-04: _coerce_positive_int дублировался дословно в
gemini_translator/api/config.py, .../handlers/local.py и .../handlers/openmodel.py.

Канон: gemini_translator.api.config._coerce_positive_int (чистая модульная
функция). Оба хендлера должны маршрутизировать через неё, а не через
собственные @staticmethod-копии.

(а) характеризационные тесты — фиксируют поведение канонической реализации
    на граничных случаях, которые различали бы копии, если бы они разошлись;
(б) тест-маршрутизация — подменяет каноническую функцию и проверяет, что
    каждое бывшее место вызова (_resolve_max_tokens в openmodel.py,
    инлайн-резолюция max_tokens в local.py::call_api) действительно идёт
    через неё. До рефакторинга у каждого хендлера была своя копия — тест
    обязан падать на HEAD и проходить после удаления копий.
"""

import types
import unittest
from unittest import mock

from gemini_translator.api import config as api_config
from gemini_translator.api.handlers.local import LocalApiHandler
from gemini_translator.api.handlers.openmodel import OpenModelApiHandler


def _make_worker(model_config=None, provider_config=None):
    worker = types.SimpleNamespace()
    worker.model_config = model_config if model_config is not None else {}
    worker.provider_config = provider_config if provider_config is not None else {}
    worker.prompt_builder = types.SimpleNamespace(system_instruction=None)
    worker.api_key = "test-key"
    worker.model_id = "test-model"
    worker.temperature_override_enabled = False
    worker.temperature = None

    def _post_event(*_args, **_kwargs):
        return None

    worker._post_event = _post_event
    return worker


class CoercePositiveIntCharacterizationTests(unittest.TestCase):
    """(а) Граничные случаи канонической реализации."""

    def test_bool_is_rejected_even_though_bool_is_an_int(self):
        self.assertIsNone(api_config._coerce_positive_int(True))
        self.assertIsNone(api_config._coerce_positive_int(False))

    def test_none_is_rejected(self):
        self.assertIsNone(api_config._coerce_positive_int(None))

    def test_plain_int_passthrough(self):
        self.assertEqual(api_config._coerce_positive_int(4096), 4096)

    def test_zero_and_negative_int_rejected(self):
        self.assertIsNone(api_config._coerce_positive_int(0))
        self.assertIsNone(api_config._coerce_positive_int(-5))

    def test_string_with_separators_is_normalized(self):
        self.assertEqual(api_config._coerce_positive_int(" 1,000 "), 1000)
        self.assertEqual(api_config._coerce_positive_int("12_00"), 1200)
        self.assertEqual(api_config._coerce_positive_int("8 192"), 8192)

    def test_negative_string_rejected_not_isdigit(self):
        self.assertIsNone(api_config._coerce_positive_int("-5"))

    def test_zero_string_rejected(self):
        self.assertIsNone(api_config._coerce_positive_int("0"))

    def test_non_digit_string_rejected(self):
        self.assertIsNone(api_config._coerce_positive_int("abc"))
        self.assertIsNone(api_config._coerce_positive_int("12.5"))

    def test_float_value_truncates_via_int(self):
        self.assertEqual(api_config._coerce_positive_int(5.7), 5)

    def test_uncoercible_type_returns_none(self):
        self.assertIsNone(api_config._coerce_positive_int([]))
        self.assertIsNone(api_config._coerce_positive_int(object()))


class CoercePositiveIntRoutingTests(unittest.TestCase):
    """(б) Маршрутизация: хендлеры обязаны звать каноническую функцию."""

    def test_openmodel_resolve_max_tokens_routes_through_canonical(self):
        worker = _make_worker(model_config={"max_output_tokens": "2_000"})
        handler = OpenModelApiHandler(worker)

        spy = mock.Mock(side_effect=api_config._coerce_positive_int)
        with mock.patch.object(api_config, "_coerce_positive_int", spy):
            result = handler._resolve_max_tokens(
                allow_incomplete=False, max_output_tokens="1,500"
            )

        self.assertEqual(result, 1500)
        spy.assert_any_call("1,500")

    def test_openmodel_resolve_max_tokens_falls_back_to_configured(self):
        worker = _make_worker(model_config={"max_output_tokens": "2_000"})
        handler = OpenModelApiHandler(worker)

        spy = mock.Mock(side_effect=api_config._coerce_positive_int)
        with mock.patch.object(api_config, "_coerce_positive_int", spy):
            result = handler._resolve_max_tokens(allow_incomplete=False, max_output_tokens=None)

        self.assertEqual(result, 2000)
        spy.assert_any_call(None)
        spy.assert_any_call("2_000")

    def test_local_call_api_routes_through_canonical(self):
        worker = _make_worker(model_config={"max_output_tokens": "3_000"})
        handler = LocalApiHandler(worker)
        handler.base_url = "http://example.invalid/v1/chat/completions"
        handler.timeout_seconds = 1
        handler.prepared_proxies = None

        class _StopHere(Exception):
            pass

        fake_session = types.SimpleNamespace(post=mock.Mock(side_effect=_StopHere("no network")))
        handler._get_http_session = lambda: fake_session

        spy = mock.Mock(side_effect=api_config._coerce_positive_int)
        with mock.patch.object(api_config, "_coerce_positive_int", spy):
            with self.assertRaises(Exception):
                handler.call_api(
                    prompt="hi",
                    log_prefix="[test]",
                    allow_incomplete=True,
                    use_stream=False,
                    max_output_tokens="500",
                )

        spy.assert_any_call("500")
        fake_session.post.assert_called_once()
        _, kwargs = fake_session.post.call_args
        self.assertEqual(kwargs["json"]["max_tokens"], 500)


if __name__ == "__main__":
    unittest.main()
