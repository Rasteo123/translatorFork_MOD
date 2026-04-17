import unittest

from gemini_translator.core.glossary_pipeline import (
    PIPELINE_STATUS_CANCELLED,
    PIPELINE_STATUS_FAILED,
    PIPELINE_STATUS_SUCCESS,
    STEP_STATUS_CANCELLED,
    STEP_STATUS_FAILED,
    STEP_STATUS_PENDING,
    STEP_STATUS_SUCCESS,
    GlossaryPipelineRun,
    build_default_step_name,
    classify_shutdown_reason,
    create_step_from_settings,
    steps_from_template_payload,
    steps_to_template_payload,
    summarize_step_settings,
)


class GlossaryPipelineTests(unittest.TestCase):
    def test_template_roundtrip_preserves_step_settings_without_runtime(self):
        first = create_step_from_settings(
            {
                "temperature": 0.7,
                "merge_mode": "supplement",
                "is_sequential": True,
                "task_size_limit": 12000,
                "new_terms_limit": 40,
            },
            name="Первый проход",
        )
        second = create_step_from_settings(
            {
                "temperature": 1.1,
                "merge_mode": "update",
                "is_sequential": False,
                "task_size_limit": 18000,
            },
            name="Второй проход",
        )
        first.status = STEP_STATUS_SUCCESS
        first.last_reason = "Сессия успешно завершена"
        first.append_log("runtime message")

        payload = steps_to_template_payload([first, second])
        restored = steps_from_template_payload(payload)

        self.assertEqual(payload["version"], 1)
        self.assertEqual(len(restored), 2)
        self.assertEqual(restored[0].name, "Первый проход")
        self.assertEqual(restored[0].settings["temperature"], 0.7)
        self.assertEqual(restored[0].status, STEP_STATUS_PENDING)
        self.assertEqual(restored[0].log_lines, [])
        self.assertEqual(restored[1].settings["merge_mode"], "update")

    def test_pipeline_run_executes_steps_in_order_and_stops_on_failure(self):
        steps = [
            create_step_from_settings({"temperature": 0.6}, name="Шаг 1"),
            create_step_from_settings({"temperature": 0.9}, name="Шаг 2"),
            create_step_from_settings({"temperature": 1.2}, name="Шаг 3"),
        ]
        run = GlossaryPipelineRun(steps)
        run.start()

        first = run.start_next_step()
        self.assertEqual(first.name, "Шаг 1")
        self.assertEqual(first.status, "running")

        run.mark_current_step_success("Сессия успешно завершена")
        self.assertEqual(run.steps[0].status, STEP_STATUS_SUCCESS)
        self.assertEqual(run.status, "running")

        second = run.start_next_step()
        self.assertEqual(second.name, "Шаг 2")
        run.mark_current_step_failed("API error")

        self.assertEqual(run.steps[1].status, STEP_STATUS_FAILED)
        self.assertEqual(run.status, PIPELINE_STATUS_FAILED)
        self.assertIsNone(run.start_next_step())
        self.assertEqual(run.steps[2].status, STEP_STATUS_PENDING)

    def test_pipeline_run_marks_success_when_all_steps_finish(self):
        steps = [
            create_step_from_settings({"temperature": 0.6}, name="Шаг 1"),
            create_step_from_settings({"temperature": 0.9}, name="Шаг 2"),
        ]
        run = GlossaryPipelineRun(steps)

        run.start()
        run.start_next_step()
        run.mark_current_step_success("Сессия успешно завершена")
        run.start_next_step()
        run.mark_current_step_success("Сессия успешно завершена")

        self.assertTrue(run.is_finished())
        self.assertEqual(run.status, PIPELINE_STATUS_SUCCESS)
        self.assertEqual([step.status for step in run.steps], [STEP_STATUS_SUCCESS, STEP_STATUS_SUCCESS])

    def test_pipeline_run_marks_cancelled(self):
        run = GlossaryPipelineRun([create_step_from_settings({"temperature": 1.0}, name="Шаг 1")])
        run.start()
        run.start_next_step()
        run.mark_current_step_cancelled("Отменено пользователем")

        self.assertEqual(run.steps[0].status, STEP_STATUS_CANCELLED)
        self.assertEqual(run.status, PIPELINE_STATUS_CANCELLED)

    def test_shutdown_reason_classifier_and_summary(self):
        self.assertEqual(classify_shutdown_reason("Сессия успешно завершена"), STEP_STATUS_SUCCESS)
        self.assertEqual(classify_shutdown_reason("Отменено пользователем"), STEP_STATUS_CANCELLED)
        self.assertEqual(classify_shutdown_reason("Unhandled API error"), STEP_STATUS_FAILED)

        summary = summarize_step_settings(
            {
                "temperature": 0.8,
                "merge_mode": "update",
                "is_sequential": True,
                "task_size_limit": 24000,
                "new_terms_limit": 70,
            }
        )
        self.assertEqual(summary["temperature"], "0.8")
        self.assertEqual(summary["merge_mode"], "Обновление")
        self.assertEqual(summary["execution_mode"], "Последовательный")
        self.assertEqual(summary["task_size"], "24000")
        self.assertEqual(summary["new_terms_limit"], "70")

    def test_default_step_name_contains_mode_and_temperature(self):
        name = build_default_step_name({"temperature": 1.3, "merge_mode": "accumulate"}, index=3)
        self.assertIn("Шаг 3", name)
        self.assertIn("Накопление", name)
        self.assertIn("T=1.3", name)


if __name__ == "__main__":
    unittest.main()
