import unittest
from types import SimpleNamespace
from unittest import mock

from gemini_translator.api.handlers import _deepseek_common
from gemini_translator.api.handlers.deepseek import DeepseekApiHandler
from gemini_translator.api.handlers.nvidia import NvidiaApiHandler


def _make_deepseek_handler(model_config, **worker_attrs):
    worker = SimpleNamespace(
        provider_config={"is_async": True},
        model_config=model_config,
        **worker_attrs,
    )
    return DeepseekApiHandler(worker)


def _make_nvidia_handler(model_config, **worker_attrs):
    worker = SimpleNamespace(
        provider_config={"is_async": True},
        model_config=model_config,
        temperature=worker_attrs.pop("temperature", 0.7),
        temperature_override_enabled=worker_attrs.pop("temperature_override_enabled", True),
        thinking_enabled=worker_attrs.pop("thinking_enabled", False),
        thinking_level=worker_attrs.pop("thinking_level", None),
        **worker_attrs,
    )
    return NvidiaApiHandler(worker)


class BuildDeepseekThinkingOptionsCharacterizationTests(unittest.TestCase):
    """Характеризационные тесты канонической build_deepseek_thinking_options.

    Крайние случаи, ранее различавшие копии deepseek.py / nvidia.py
    (формула effort, ранние return-ы, порядок вычисления thinking_enabled).
    """

    def test_no_thinking_config_leaves_payload_unchanged(self):
        payload = {"model": "x", "temperature": 0.7}
        worker = SimpleNamespace()
        _deepseek_common.build_deepseek_thinking_options(payload, {"id": "x"}, worker)
        self.assertEqual(payload, {"model": "x", "temperature": 0.7})

    def test_explicit_disabled_mode_sets_disabled_and_keeps_temperature(self):
        payload = {"model": "x", "temperature": 0.7}
        worker = SimpleNamespace(thinking_enabled=True, thinking_level="MAX")
        model_config = {"deepseek_thinking": "disabled", "min_thinking_budget": False}
        _deepseek_common.build_deepseek_thinking_options(payload, model_config, worker)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", payload)
        self.assertEqual(payload["temperature"], 0.7)

    def test_explicit_enabled_mode_sets_max_effort_and_drops_temperature(self):
        payload = {"model": "x", "temperature": 0.7}
        worker = SimpleNamespace(thinking_enabled=False, thinking_level="MAX")
        model_config = {
            "deepseek_thinking": "enabled",
            "thinkingLevel": ["high", "max"],
            "min_thinking_budget": "high",
        }
        _deepseek_common.build_deepseek_thinking_options(payload, model_config, worker)
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["reasoning_effort"], "max")
        self.assertNotIn("temperature", payload)

    def test_unset_mode_falls_back_to_worker_thinking_enabled_flag(self):
        payload = {"model": "x", "temperature": 0.7}
        worker = SimpleNamespace(thinking_enabled=True, thinking_level=None)
        model_config = {"thinkingLevel": ["high"]}
        _deepseek_common.build_deepseek_thinking_options(payload, model_config, worker)
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        # thinking_level отсутствует -> effort из default_reasoning_effort/min_thinking_budget/'high'
        self.assertEqual(payload["reasoning_effort"], "high")

    def test_effort_falls_back_to_default_reasoning_effort_then_min_thinking_budget(self):
        payload = {"model": "x"}
        worker = SimpleNamespace(thinking_enabled=True, thinking_level=None)
        model_config = {
            "thinkingLevel": ["high"],
            "default_reasoning_effort": "xhigh",
        }
        _deepseek_common.build_deepseek_thinking_options(payload, model_config, worker)
        self.assertEqual(payload["reasoning_effort"], "max")


class DeepseekThinkingRoutingTests(unittest.TestCase):
    """Маршрутизация: оба хендлера обязаны звать общий build_deepseek_thinking_options."""

    def test_deepseek_handler_routes_through_common_builder(self):
        model_config = {"id": "deepseek-chat", "deepseek_thinking": "enabled"}
        handler = _make_deepseek_handler(model_config, thinking_enabled=True, thinking_level="max")
        payload = {"model": "deepseek-chat"}

        with mock.patch.object(
            _deepseek_common,
            "build_deepseek_thinking_options",
            wraps=_deepseek_common.build_deepseek_thinking_options,
        ) as spy:
            handler._apply_deepseek_thinking_options(payload)

        spy.assert_called_once()
        args = spy.call_args.args
        self.assertIs(args[0], payload)
        self.assertEqual(args[1], model_config)
        self.assertIs(args[2], handler.worker)

    def test_nvidia_handler_routes_through_common_builder(self):
        model_config = {
            "id": "deepseek-ai/deepseek-v4-pro",
            "nvidia_reasoning": "deepseek",
            "deepseek_thinking": "enabled",
        }
        handler = _make_nvidia_handler(model_config, thinking_level="max")
        payload = {"model": "deepseek-ai/deepseek-v4-pro", "temperature": 0.7}

        with mock.patch.object(
            _deepseek_common,
            "build_deepseek_thinking_options",
            wraps=_deepseek_common.build_deepseek_thinking_options,
        ) as spy:
            handler._apply_nvidia_model_options(payload)

        spy.assert_called_once()
        args = spy.call_args.args
        self.assertIs(args[0], payload)
        self.assertEqual(args[1], model_config)
        self.assertIs(args[2], handler.worker)


if __name__ == "__main__":
    unittest.main()
