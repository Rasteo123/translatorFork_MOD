import time
from PyQt6 import QtWidgets

from ..base import BaseApiHandler, _current_debug_trace
from ..errors import ModelNotFoundError


class DryRunApiHandler(BaseApiHandler):
    """
    Псевдо-API хендлер. Взаимодействует с GUI, поэтому требует особого
    пути выполнения.
    """

    def setup_client(self, client_override=None, proxy_settings=None):
        self.worker.api_key = client_override.api_key
        self.worker.model_id = self.worker.model_config.get("id", "dry-run-model")
        return True

    async def execute_api_call(self, prompt, log_prefix, allow_incomplete=False, debug=False, use_stream=True, max_output_tokens=None):
        """
        Переопределяем основной метод, чтобы избежать его запуска в фоновом
        потоке.
        """
        from ...ui.dialogs.setup_dialogs.dry_run_dialog import DryRunPromptDialog

        trace = self._create_debug_trace(log_prefix)
        trace_token = _current_debug_trace.set(trace)
        started_at = time.perf_counter()

        try:
            system_instruction = getattr(self.worker.prompt_builder, 'system_instruction', None)

            final_output = []
            if system_instruction:
                final_output.append("====================================================")
                final_output.append("SYSTEM INSTRUCTION")
                final_output.append("====================================================")
                final_output.append(system_instruction.strip())
                final_output.append("")
                final_output.append("")
                final_output.append("====================================================")
                final_output.append("USER PROMPT")
                final_output.append("====================================================")

            final_output.append(prompt.strip())
            full_prompt_text = "\n".join(final_output)

            self._debug_record_request(
                {
                    "mode": "dry_run",
                    "prompt": prompt,
                    "full_prompt_text": full_prompt_text,
                    "system_instruction": system_instruction,
                }
            )

            self.worker._post_event('log_message', {'message': "[INFO] Ожидание ручного ввода ответа для пробного запуска…"})

            app = QtWidgets.QApplication.instance()
            main_window = next((w for w in app.topLevelWidgets() if isinstance(w, QtWidgets.QMainWindow)), None)
            user_translation = DryRunPromptDialog.get_translation(main_window, full_prompt_text)

            if user_translation is not None:
                self._debug_record_response(
                    {"mode": "dry_run", "translation": user_translation},
                    status="ok",
                )
                self.worker._post_event('log_message', {'message': "[INFO] Получен ручной перевод. Обработка как ответа API…"})
                self._finalize_debug_trace(trace, started_at=started_at, status="success")
                return user_translation

            error = ModelNotFoundError("Пробный запуск отменен пользователем.")
            self._debug_record_response(
                {"mode": "dry_run", "cancelled": True},
                status="cancelled",
            )
            self.worker._post_event('log_message', {'message': "[INFO] Ручной ввод отменен. Симуляция ошибки 'Модель не найдена' для остановки сессии."})
            self._finalize_debug_trace(
                trace,
                started_at=started_at,
                status=self._debug_status_from_exception(error),
                error=error,
            )
            raise error
        except Exception as exc:
            if not isinstance(exc, ModelNotFoundError):
                self._finalize_debug_trace(
                    trace,
                    started_at=started_at,
                    status=self._debug_status_from_exception(exc),
                    error=exc,
                )
            raise
        finally:
            _current_debug_trace.reset(trace_token)
