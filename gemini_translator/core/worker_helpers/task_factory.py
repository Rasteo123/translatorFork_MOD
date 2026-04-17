# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# task_factory.py
# ---------------------------------------------------------------------------
# Фабрика, отвечающая за выбор и предоставление нужной стратегии
# для обработки задачи в UniversalWorker.
# ---------------------------------------------------------------------------

# Импортируем все конкретные реализации стратегий
from .taskers.epub_batch_processor import EpubBatchProcessor
from .taskers.epub_single_file_processor import EpubSingleFileProcessor
from .taskers.epub_chunk_processor import EpubChunkProcessor
from .taskers.glossary_batch_processor import GlossaryBatchProcessor
from .taskers.raw_text_processor import RawTextProcessor
from .taskers.hello_task_processor import HelloTaskProcessor

# Карта соответствия типа задачи и класса-обработчика
# Раньше она была в UniversalWorker, теперь ее дом здесь.
_TASK_PROCESSOR_STRATEGY_MAP = {
    'epub_batch': EpubBatchProcessor,
    'epub': EpubSingleFileProcessor,
    'epub_chunk': EpubChunkProcessor,
    'glossary_batch_task': GlossaryBatchProcessor,
    'raw_text_translation': RawTextProcessor,
    'hello_task': HelloTaskProcessor
}

def get_task_processor_class(task_type: str):
    """
    Возвращает класс обработчика для указанного типа задачи.
    
    Args:
        task_type (str): Строковый идентификатор типа задачи (e.g., 'epub').
        
    Returns:
        Класс обработчика, унаследованный от BaseTaskProcessor.
        
    Raises:
        TypeError: Если для task_type не найден обработчик.
    """
    processor_class = _TASK_PROCESSOR_STRATEGY_MAP.get(task_type)
    if not processor_class:
        raise TypeError(f"Неизвестный тип задачи: {task_type}")
    return processor_class