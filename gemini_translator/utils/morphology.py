# -*- coding: utf-8 -*-
"""Общий ленивый доступ к морфологии (pymorphy3/pymorphy2) и прогрев jieba.

Словари pymorphy — ~35МБ кучи и ~секунда на построение, jieba — ещё ~18МБ.
Раньше pymorphy строился при импорте glossary.py (то есть при старте GUI),
jieba грелся безусловно в main.py, а glued_words строил ВТОРОЙ независимый
анализатор. Теперь:
- PYMORPHY_AVAILABLE — дешёвая проверка importability, словари не грузит;
- get_morph_analyzer() строит анализатор один раз при первом обращении
  (потокобезопасно) — единый экземпляр на всё приложение;
- warm_up_morphology_async() — фоновый прогрев (morph + jieba) при открытии
  окна глоссария, чтобы первый клик по морфо-функциям не ловил паузу;
- сессии перевода без глоссария/CJK эти ~60-110МБ не платят вовсе
  (первый вызов jieba в CJK-воркере строит словарь там — на фоне сетевых
  секунд это незаметно).
"""

import importlib.util
import threading

PYMORPHY_AVAILABLE = bool(
    importlib.util.find_spec("pymorphy3") or importlib.util.find_spec("pymorphy2")
)

_morph_analyzer = None
_MORPH_BUILD_LOCK = threading.Lock()
_warmup_started = False


def morph_analyzer_loaded() -> bool:
    return _morph_analyzer is not None


def get_morph_analyzer():
    """Возвращает единый MorphAnalyzer, строя его при первом обращении.

    Может занять ~секунду на холодном вызове — для GUI-потока предпочтителен
    предварительный warm_up_morphology_async(). Возвращает None, если
    pymorphy недоступен или словари не построились.
    """
    global _morph_analyzer, PYMORPHY_AVAILABLE
    if _morph_analyzer is not None or not PYMORPHY_AVAILABLE:
        return _morph_analyzer
    with _MORPH_BUILD_LOCK:
        if _morph_analyzer is None:
            try:
                import pymorphy3
                _morph_analyzer = pymorphy3.MorphAnalyzer(lang='ru')
                print("INFO: Используется библиотека pymorphy3.")
            except Exception:
                try:
                    import pymorphy2
                    _morph_analyzer = pymorphy2.MorphAnalyzer()
                    print("INFO: Используется библиотека pymorphy2 (рекомендуется обновиться до pymorphy3).")
                except Exception:
                    PYMORPHY_AVAILABLE = False
    return _morph_analyzer


def warm_up_morphology_async() -> None:
    """Фоновый прогрев словарей морфологии и jieba (демон-поток, один раз)."""
    global _warmup_started
    if _warmup_started:
        return
    _warmup_started = True

    def _warm():
        get_morph_analyzer()
        try:
            import jieba
            jieba.lcut("прогрев", cut_all=False)
        except Exception:
            pass

    threading.Thread(target=_warm, name="morph-warmup", daemon=True).start()
