# -*- coding: utf-8 -*-

from .base_processor import BaseTaskProcessor
from gemini_translator.api.errors import SuccessSignal, ValidationFailedError




class HelloTaskProcessor(BaseTaskProcessor):
    async def execute(self, task_info, use_stream=False):
        """Простая проверка доступности API."""
        if self.worker.api_provider_name == "dry_run":
            raise SuccessSignal(status_code="SUCCESS", message="Dry run 'hello' successful.")

        log_prefix = f"Приветствие (ключ …{self.worker.api_key[-4:]})"
        user_prompt = "Ответь одним словом: 'OK'"
        self.worker.prompt_builder.system_instruction = None

        operation_context = self._build_operation_context(
            task_info,
            action='warmup',
            task_type='hello_task',
        )
        response = await self._execute_api_call(
            user_prompt,
            log_prefix,
            task_info=task_info,
            operation_context=operation_context,
            use_stream=use_stream,
            allow_incomplete=True,
        )
        
        if response and "ok" in response.lower():
            raise SuccessSignal(status_code="SUCCESS", message="API 'hello' successful.")
        else:
            raise ValidationFailedError(f"Неожиданный ответ на 'hello': {response[:50]}")
