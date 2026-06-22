import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gemini_translator.api.errors import ContentFilterError, RateLimitExceededError
from gemini_translator.core.worker_helpers import provider_orchestrator as orchestrator


class _ApiHandlerStub:
    async def execute_api_call(self, prompt, log_prefix, **kwargs):
        return "synthesis"


class ProviderOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_strategy_is_not_overridden_by_multi_pass_default(self):
        attempts = [
            orchestrator.ProviderAttempt(
                provider_id="primary",
                model_name="Primary",
                model_config={"id": "primary-model"},
                api_key="primary-key",
                label="primary",
            ),
            orchestrator.ProviderAttempt(
                provider_id="secondary",
                model_name="Secondary",
                model_config={"id": "secondary-model"},
                api_key="secondary-key",
                label="secondary",
            ),
        ]
        results_by_label = {
            "primary": orchestrator.ProviderAttemptResult(
                attempt=attempts[0],
                text="short",
            ),
            "secondary": orchestrator.ProviderAttemptResult(
                attempt=attempts[1],
                text="longer translated chapter text",
            ),
        }
        worker = SimpleNamespace(
            parallel_provider_strategy="best_score",
            multi_pass_strategy="merge",
            multi_pass_enabled=False,
            multi_pass_chapter_translation=False,
            api_handler_instance=_ApiHandlerStub(),
            _post_event=lambda *_args, **_kwargs: None,
        )

        async def fake_run_attempt(_worker, attempt, _prompt, _log_prefix, _call_kwargs):
            return results_by_label[attempt.label]

        with patch.object(orchestrator, "_build_attempts", return_value=attempts), \
             patch.object(orchestrator, "_run_attempt", side_effect=fake_run_attempt), \
             patch.object(orchestrator, "_save_attempt_results"):
            result = await orchestrator.execute_orchestrated_api_call(
                worker,
                "prompt",
                "[Test]",
                task_info=("task-1", ("epub",)),
                operation_context={"task_type": "epub", "action": "translate_chapter"},
                call_kwargs={},
            )

        self.assertEqual(result, "longer translated chapter text")

    async def test_all_failed_raises_dominant_original_exception(self):
        attempts = [
            orchestrator.ProviderAttempt(
                provider_id="primary",
                model_name="Primary",
                model_config={"id": "primary-model"},
                api_key="primary-key",
                label="primary",
            ),
            orchestrator.ProviderAttempt(
                provider_id="secondary",
                model_name="Secondary",
                model_config={"id": "secondary-model"},
                api_key="secondary-key",
                label="secondary",
            ),
        ]
        filter_error = ContentFilterError("blocked")
        quota_error = RateLimitExceededError("quota exhausted")
        results_by_label = {
            "primary": orchestrator.ProviderAttemptResult(
                attempt=attempts[0],
                error=f"{type(filter_error).__name__}: {filter_error}",
                exception=filter_error,
            ),
            "secondary": orchestrator.ProviderAttemptResult(
                attempt=attempts[1],
                error=f"{type(quota_error).__name__}: {quota_error}",
                exception=quota_error,
            ),
        }
        worker = SimpleNamespace(
            parallel_provider_strategy="best_score",
            multi_pass_strategy="merge",
            multi_pass_enabled=False,
            multi_pass_chapter_translation=False,
            api_handler_instance=_ApiHandlerStub(),
            _post_event=lambda *_args, **_kwargs: None,
        )

        async def fake_run_attempt(_worker, attempt, _prompt, _log_prefix, _call_kwargs):
            return results_by_label[attempt.label]

        with patch.object(orchestrator, "_build_attempts", return_value=attempts), \
             patch.object(orchestrator, "_run_attempt", side_effect=fake_run_attempt), \
             patch.object(orchestrator, "_save_attempt_results"):
            with self.assertRaises(RateLimitExceededError) as raised:
                await orchestrator.execute_orchestrated_api_call(
                    worker,
                    "prompt",
                    "[Test]",
                    task_info=("task-1", ("epub",)),
                    operation_context={"task_type": "epub", "action": "translate_chapter"},
                    call_kwargs={},
                )

        self.assertIs(raised.exception, quota_error)

    async def test_run_attempt_preserves_original_exception_on_result(self):
        class RaisingHandler:
            def __init__(self, _worker):
                pass

            def setup_client(self, _client, proxy_settings=None):
                return True

            def execute_api_call(self, _prompt, _log_prefix, **_kwargs):
                raise ContentFilterError("safety block")

        attempt = orchestrator.ProviderAttempt(
            provider_id="primary",
            model_name="Primary",
            model_config={"id": "primary-model"},
            api_key="primary-key",
            label="primary",
        )
        worker = SimpleNamespace(
            worker_id="worker-1",
            temperature=None,
            temperature_override_enabled=True,
            proxy_settings=None,
        )

        with patch.object(orchestrator, "_provider_info", return_value={"handler_class": "RaisingHandler"}), \
             patch.object(orchestrator, "get_api_handler_class", return_value=RaisingHandler):
            result = await orchestrator._run_attempt(worker, attempt, "prompt", "[Test]", {})

        self.assertFalse(result.ok)
        self.assertIsInstance(result.exception, ContentFilterError)
        self.assertEqual(str(result.exception), "safety block")

    async def test_first_success_returns_first_completed_success_and_cancels_pending(self):
        attempts = [
            orchestrator.ProviderAttempt(
                provider_id="slow",
                model_name="Slow",
                model_config={"id": "slow-model"},
                api_key="slow-key",
                label="slow",
            ),
            orchestrator.ProviderAttempt(
                provider_id="fast",
                model_name="Fast",
                model_config={"id": "fast-model"},
                api_key="fast-key",
                label="fast",
            ),
        ]
        slow_started = asyncio.Event()
        slow_cancelled = asyncio.Event()
        worker = SimpleNamespace(
            parallel_provider_strategy="first_success",
            multi_pass_strategy="merge",
            multi_pass_enabled=False,
            multi_pass_chapter_translation=False,
            api_handler_instance=_ApiHandlerStub(),
            _post_event=lambda *_args, **_kwargs: None,
        )

        async def fake_run_attempt(_worker, attempt, _prompt, _log_prefix, _call_kwargs):
            if attempt.label == "slow":
                slow_started.set()
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    slow_cancelled.set()
                    raise
                return orchestrator.ProviderAttemptResult(attempt=attempt, text="slow")

            await slow_started.wait()
            return orchestrator.ProviderAttemptResult(attempt=attempt, text="fast")

        with patch.object(orchestrator, "_build_attempts", return_value=attempts), \
             patch.object(orchestrator, "_run_attempt", side_effect=fake_run_attempt), \
             patch.object(orchestrator, "_save_attempt_results"):
            result = await asyncio.wait_for(
                orchestrator.execute_orchestrated_api_call(
                    worker,
                    "prompt",
                    "[Test]",
                    task_info=("task-1", ("epub",)),
                    operation_context={"task_type": "epub", "action": "translate_chapter"},
                    call_kwargs={},
                ),
                timeout=1,
            )

        self.assertEqual(result, "fast")
        self.assertTrue(slow_cancelled.is_set())

    async def test_merge_still_synthesizes_multiple_successful_results(self):
        attempts = [
            orchestrator.ProviderAttempt(
                provider_id="primary",
                model_name="Primary",
                model_config={"id": "primary-model"},
                api_key="primary-key",
                label="primary",
            ),
            orchestrator.ProviderAttempt(
                provider_id="secondary",
                model_name="Secondary",
                model_config={"id": "secondary-model"},
                api_key="secondary-key",
                label="secondary",
            ),
        ]
        results_by_label = {
            "primary": orchestrator.ProviderAttemptResult(attempt=attempts[0], text="one"),
            "secondary": orchestrator.ProviderAttemptResult(attempt=attempts[1], text="two"),
        }
        worker = SimpleNamespace(
            parallel_provider_strategy="merge",
            multi_pass_strategy="merge",
            multi_pass_enabled=False,
            multi_pass_chapter_translation=False,
            api_handler_instance=_ApiHandlerStub(),
            _post_event=lambda *_args, **_kwargs: None,
        )

        async def fake_run_attempt(_worker, attempt, _prompt, _log_prefix, _call_kwargs):
            return results_by_label[attempt.label]

        with patch.object(orchestrator, "_build_attempts", return_value=attempts), \
             patch.object(orchestrator, "_run_attempt", side_effect=fake_run_attempt), \
             patch.object(orchestrator, "_save_attempt_results"):
            result = await orchestrator.execute_orchestrated_api_call(
                worker,
                "prompt",
                "[Test]",
                task_info=("task-1", ("epub",)),
                operation_context={"task_type": "epub", "action": "translate_chapter"},
                call_kwargs={"use_stream": True},
            )

        self.assertEqual(result, "synthesis")

    def test_build_attempts_collapses_browser_single_profile_fanout(self):
        events = []
        worker = SimpleNamespace(
            parallel_providers_enabled=False,
            multi_pass_enabled=True,
            multi_pass_chapter_translation=False,
            multi_pass_count=3,
            api_provider_name="web_chatgpt_free",
            model="Default Model",
            model_config={},
            api_key="profile-session",
            temperature=1.0,
            translation_orchestration_max_attempts=8,
            _post_event=lambda event, payload: events.append((event, payload)),
        )
        provider_info = {
            "handler_class": "BrowserApiHandler",
            "max_instances": 1,
            "models": {
                "Default Model": {
                    "id": "default",
                    "provider": "web_chatgpt_free",
                }
            },
        }

        with patch.object(orchestrator, "_provider_info", return_value=provider_info), \
             patch.object(orchestrator.api_config, "provider_max_instances", return_value=1):
            attempts = orchestrator._build_attempts(worker)

        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].provider_id, "web_chatgpt_free")
        self.assertTrue(
            any(
                "fan-out collapsed" in payload.get("message", "")
                for event, payload in events
                if event == "log_message"
            )
        )


if __name__ == "__main__":
    unittest.main()
