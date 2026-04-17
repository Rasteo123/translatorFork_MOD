# gemini_translator/utils/async_helpers.py
# -*- coding: utf-8 -*-

import asyncio
import contextvars
import functools
from concurrent.futures import Future, ThreadPoolExecutor
import threading

def run_sync(func, *args, forget: bool = False, timeout: float = None, **kwargs) -> any:
    """
    Универсальная утилита для запуска синхронной, блокирующей функции 
    в фоновом потоке из асинхронного контекста.

    Args:
        func: Синхронная функция для выполнения.
        *args: Позиционные аргументы для функции.
        forget (bool): 
            - Если False (по умолчанию): Функция становится асинхронной. 
              Возвращает корутину, которую нужно ожидать (`await`).
            - Если True ("fire and forget"): Функция запускается в фоновом потоке,
              и управление немедленно возвращается. Возвращает None.
        timeout (float): 
            Опциональный таймаут в секундах. Работает только если `forget=False`.
            Если время истекает, выбрасывается `asyncio.TimeoutError`.
        **kwargs: Именованные аргументы для функции.

    Returns:
        - Корутина, которая при ожидании вернет результат выполнения `func` (если forget=False).
        - None (если forget=True).

    Raises:
        asyncio.TimeoutError: если истек таймаут (только при `forget=False`).
    """
    
    # Готовим вызов функции со всеми её аргументами
    func_with_args = functools.partial(func, *args, **kwargs)
    captured_context = contextvars.copy_context()

    def context_bound_call():
        return captured_context.run(func_with_args)

    async def main_wrapper():
        """Внутренняя асинхронная обертка, которая будет возвращена как корутина."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Если event loop не запущен, асинхронный вызов невозможен.
            # Это может произойти, если утилиту пытаются использовать вне async-функции.
            # Мы не можем вернуть awaitable, поэтому выбрасываем понятную ошибку.
            raise RuntimeError(
                "run_sync() can only be awaited inside a running asyncio event loop."
            )
        
        # Запускаем в экзекуторе по умолчанию, получаем объект Future
        future = loop.run_in_executor(None, context_bound_call)
        
        # Асинхронно ждем завершения future с таймаутом.
        # asyncio.wait_for само обработает TimeoutError.
        return await asyncio.wait_for(future, timeout=timeout)

    if forget:
        try:
            loop = asyncio.get_running_loop()
            # Запускаем и "забываем"
            loop.run_in_executor(None, context_bound_call)
        except RuntimeError:
            # Если нет event loop'а, запускаем в обычном потоке.
            # Это обеспечивает предсказуемое поведение "fire and forget" всегда.
            thread = threading.Thread(target=context_bound_call)
            thread.daemon = True
            thread.start()
        return None # Для "fire and forget" всегда возвращаем None
    else:
        # Для режима ожидания возвращаем корутину, которую можно будет `await`.
        return main_wrapper()
