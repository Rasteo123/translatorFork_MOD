"""
Регресс на находку ui-dialogs-epub-consistency/bugs/4-epub-manager-on2-lcs-diff-bloc
(и на замечание рецензента к первой версии фикса).

_surgical_update строит LCS dp-таблицу n*m ячеек на чистом Python СИНХРОННО
на GUI-потоке. Первая версия фикса отсекала это построение по грубому порогу
n*m > MAX_LCS_CELLS, но порог срабатывал уже при ~1000 строк - то есть ровно
в САМОМ ЧАСТОМ сценарии (изменилась одна-две главы в большом списке) вместо
точечного diff всегда выполнялась полная перерисовка, которая стирает
чекбоксы "включить в сборку", выбранные версии перевода, выделение и скролл
для ВСЕХ строк, а не только изменившихся.

Правильный фикс: сначала дёшево (O(n+m)) срезать общий префикс и суффикс,
и только если ОСТАВШАЯСЯ середина всё ещё превышает MAX_LCS_CELLS - падать
в полную перерисовку. Тесты проверяют оба конца этого поведения:

1. Огромный список с одним изменением в конце - должен пойти точечным путём
   (после срезки префикса середина крошечная), сохраняя состояние остальных
   строк.
2. Огромный список, отличающийся полностью (нет общего префикса/суффикса) -
   середина после срезки всё ещё огромна, здесь полная перерисовка оправдана.

Пороги и хелперы берутся через getattr с дефолтом, а харнесс определяет
собственный fallback для _full_table_rebuild: так на неисправленном коде
(без срезки префикса/суффикса) тест падает именно на "точечный путь не
сработал" (assertEqual с реальным call_count), а не рушится на сборе с
AttributeError из-за отсутствующих атрибутов боевого класса.
"""

import os
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.ui.dialogs.epub import TranslatedChaptersManagerDialog

MAX_LCS_CELLS = getattr(TranslatedChaptersManagerDialog, "MAX_LCS_CELLS", 1_000_000)
CHUNKED_FILL_THRESHOLD = getattr(TranslatedChaptersManagerDialog, "CHUNKED_FILL_THRESHOLD", 150)


def _fallback_full_table_rebuild(self, new_ids):
    """Повторяет ожидаемое поведение полной перерисовки в харнессе, чтобы
    тест не зависел от наличия боевого _full_table_rebuild: на
    неисправленном коде (метода ещё нет) тест не рухнет на сборе, а
    честно провалит содержательную проверку (call_count и т.п.)."""
    for idx, path in enumerate(new_ids):
        self._populate_row(idx, path)
    return False


class _FakeDialog:
    """Минимальный харнесс: боевые тела _surgical_update (и, если есть,
    _full_table_rebuild) без реального Qt-виджета."""

    def __init__(self):
        self.table = Mock()
        self._populate_row = Mock()
        self._chunked_fill = Mock(side_effect=self._record_chunked_fill)
        self.chunked_fill_calls = []

    def _record_chunked_fill(self, target_paths):
        self.chunked_fill_calls.append(list(target_paths))

    MAX_LCS_CELLS = MAX_LCS_CELLS
    CHUNKED_FILL_THRESHOLD = CHUNKED_FILL_THRESHOLD
    _full_table_rebuild = getattr(
        TranslatedChaptersManagerDialog,
        "_full_table_rebuild",
        _fallback_full_table_rebuild,
    )
    _surgical_update = TranslatedChaptersManagerDialog._surgical_update


class SurgicalUpdateLcsSizeGuardTests(unittest.TestCase):
    def test_single_change_in_huge_list_uses_surgical_path_not_full_rebuild(self):
        """Главный сценарий: изменилась ОДНА глава в списке из тысяч.

        До фикса грубый порог n*m > MAX_LCS_CELLS срабатывал уже на таком
        размере и стирал состояние всех строк полной перерисовкой. После
        фикса общий префикс срезается дёшево, середина остаётся крошечной,
        и точечный путь применяет ровно одну операцию insert - остальные
        строки (включая их чекбоксы/версии) не трогаются вовсе.
        """
        dialog = _FakeDialog()

        n = 4000
        old_ids = [f"chapter_{i}.xhtml" for i in range(n)]
        new_ids = list(old_ids)
        new_ids[-1] = "chapter_new.xhtml"  # одно реальное отличие в конце

        result = dialog._surgical_update(old_ids, new_ids)

        self.assertFalse(
            result,
            "полная перерисовка не должна была срабатывать - после срезки "
            "общего префикса середина diff'а крошечная",
        )
        self.assertEqual(
            dialog._populate_row.call_count,
            1,
            "точечный diff должен был вызвать _populate_row только для "
            "единственной изменившейся строки, не перерисовывая всю таблицу "
            "(иначе теряются чекбоксы «включить»/версии остальных строк)",
        )
        dialog._chunked_fill.assert_not_called()

    def test_fully_different_huge_lists_fall_back_to_full_rebuild(self):
        """Если общего префикса/суффикса нет (списки различаются целиком),
        середина после срезки равна исходным спискам целиком, и при
        n*m > MAX_LCS_CELLS полная перерисовка - оправданный fallback."""
        dialog = _FakeDialog()

        n = m = 2000  # 2000*2000 = 4_000_000 > MAX_LCS_CELLS, без общих элементов
        old_ids = [f"old_{i}.xhtml" for i in range(n)]
        new_ids = [f"new_{i}.xhtml" for i in range(m)]

        result = dialog._surgical_update(old_ids, new_ids)

        self.assertTrue(
            result,
            "перерисовка должна была уйти в фон через _full_table_rebuild",
        )
        # Список настолько большой, что полная перерисовка идёт чанками
        # (см. _full_table_rebuild / CHUNKED_FILL_THRESHOLD), а не разом.
        dialog._chunked_fill.assert_called_once_with(new_ids)
        dialog._populate_row.assert_not_called()


if __name__ == "__main__":
    unittest.main()
