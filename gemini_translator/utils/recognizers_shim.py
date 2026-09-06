# -*- coding: utf-8 -*-
"""
Единая точка совместимости recognizers-text / recognizers-text-number с
пакетом emoji.

Проблема: `recognizers_text.utilities` на верхнем уровне делает
`from emoji import UNICODE_EMOJI`. В установленных версиях пакета `emoji`
этого атрибута больше нет, поэтому голый `import recognizers_text` падает
`ImportError: cannot import name 'UNICODE_EMOJI' from 'emoji'`.

Патч — подставить `emoji.UNICODE_EMOJI = {}` до первого импорта
recognizers_text — раньше дублировался по разным модулям-потребителям и
применялся не везде: в местах без патча `from recognizers_text import ...`
стоял в try/except и просто ловил ImportError, молча выключая функцию.
Поскольку сбойный импорт recognizers_text кешируется на весь процесс,
результат зависел от того, какой модуль-потребитель успевал
импортироваться первым — если это был модуль без патча, recognizers
считались недоступными во всём процессе, даже там, где патч был.

Импортируя этот модуль вместо прямого `from recognizers_text import ...`,
потребитель гарантированно получает патч до попытки импорта, независимо
от порядка импорта модулей приложения.

Замена recognizers-text на собственный парсер числительных здесь
сознательно не делается — это отдельная задача.
"""

try:
    import emoji as _emoji
    if not hasattr(_emoji, 'UNICODE_EMOJI'):
        _emoji.UNICODE_EMOJI = {}
except Exception:
    # Самого пакета emoji нет или он сломан (например, урезанная сборка без
    # emoji.json — emoji читает данные на импорте и падает не ImportError-ом,
    # а FileNotFoundError). recognizers_text всё равно не импортируется
    # (это его собственная зависимость), ниже это будет поймано мягко.
    pass

try:
    from recognizers_text import Culture
    from recognizers_number import recognize_number
    RECOGNIZERS_AVAILABLE = True
except Exception:
    # Ловим любую ошибку, не только ImportError: это единственная и теперь
    # общая для всех потребителей точка импорта recognizers_text, цена её
    # падения (весь import main) выросла — деградация должна быть мягкой
    # при любой поломке зависимости, а не только при её отсутствии.
    Culture = None
    recognize_number = None
    RECOGNIZERS_AVAILABLE = False

__all__ = ["Culture", "recognize_number", "RECOGNIZERS_AVAILABLE"]
