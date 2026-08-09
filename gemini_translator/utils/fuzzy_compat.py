# -*- coding: utf-8 -*-
"""Fuzzy-скоринг: rapidfuzz (C++), при его отсутствии — fuzzywuzzy.

Контракт — байт-в-байт совместимость с fuzzywuzzy при установленном
python-Levenshtein (под неё откалиброваны пороги Stage-3 глоссария и
consistency checker), поэтому rapidfuzz-ветка воспроизводит две неочевидные
особенности fuzzywuzzy:

1. token_set_ratio по умолчанию работает с force_ascii=True: перед обработкой
   из строк удаляются символы Latin-1 (коды 128..255, например é/ü). Кириллицу
   и CJK это НЕ затрагивает (их кодовые точки выше 255).
2. Препроцессинг — regex `\W` (как в fuzzywuzzy.utils.full_process), а не
   rapidfuzz.utils.default_process: `\W` считает подчёркивание словесным
   символом и сохраняет его, default_process заменил бы пробелом — токены
   разошлись бы.
3. Финальный скор округляется до int тем же round(), что и fuzzywuzzy.

Эквивалентность закреплена корпусным тестом tests/test_fuzzy_compat.py.
"""

import re

# fuzzywuzzy.utils.asciidammit: удаляются ровно коды 128..255.
_LATIN1_STRIP_TABLE = {code: None for code in range(128, 256)}
_NON_WORD_RE = re.compile(r"(?u)\W")


def _full_process(value) -> str:
    """Реплика fuzzywuzzy.utils.full_process(force_ascii=True)."""
    text = str(value).translate(_LATIN1_STRIP_TABLE)
    text = _NON_WORD_RE.sub(" ", text)
    return text.lower().strip()


try:
    from rapidfuzz import fuzz as _rf_fuzz

    FUZZY_BACKEND = "rapidfuzz"
    FUZZ_AVAILABLE = True

    def ratio(s1, s2) -> int:
        # fuzzywuzzy.fuzz.ratio не делает ни препроцессинга, ни force_ascii.
        return int(round(_rf_fuzz.ratio(str(s1), str(s2))))

    def token_set_ratio(s1, s2) -> int:
        return int(round(_rf_fuzz.token_set_ratio(
            _full_process(s1),
            _full_process(s2),
            processor=None,
        )))

    def token_set_ratio_preclean(s1, s2) -> int:
        """Для строк, УЖЕ прошедших `\\W+`→' ' и lower() (universal_cleaner
        в language_tools): из полного препроцессинга остаётся только
        Latin-1-зачистка force_ascii. На таких входах результат равен
        token_set_ratio (закреплено tests/test_fuzzy_compat.py)."""
        return int(round(_rf_fuzz.token_set_ratio(
            str(s1).translate(_LATIN1_STRIP_TABLE),
            str(s2).translate(_LATIN1_STRIP_TABLE),
            processor=None,
        )))

except ImportError:
    try:
        from fuzzywuzzy import fuzz as _fw_fuzz

        FUZZY_BACKEND = "fuzzywuzzy"
        FUZZ_AVAILABLE = True

        ratio = _fw_fuzz.ratio
        token_set_ratio = _fw_fuzz.token_set_ratio
        # full_process fuzzywuzzy идемпотентен на предочищенном входе.
        token_set_ratio_preclean = _fw_fuzz.token_set_ratio

    except ImportError:
        FUZZY_BACKEND = None
        FUZZ_AVAILABLE = False

        def ratio(s1, s2) -> int:
            raise RuntimeError(
                "Нет fuzzy-бэкенда: установите rapidfuzz (или fuzzywuzzy)."
            )

        def token_set_ratio(s1, s2) -> int:
            raise RuntimeError(
                "Нет fuzzy-бэкенда: установите rapidfuzz (или fuzzywuzzy)."
            )

        def token_set_ratio_preclean(s1, s2) -> int:
            raise RuntimeError(
                "Нет fuzzy-бэкенда: установите rapidfuzz (или fuzzywuzzy)."
            )
