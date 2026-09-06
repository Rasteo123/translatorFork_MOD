# -*- coding: utf-8 -*-
"""cluster-17: тонкая QDialog-обёртка (*Meta + __getattr__-делегат к .page)
скопирована минимум в 14 местах (setup.py, consistency_checker.py,
validation.py x2, glossary.py, untranslated_fixer_dialog.py,
conflict_resolvers.py x2, core_term_dialog.py, residue_analyzer.py,
ai_correction.py, term_frequency_analyzer.py, group_analyzer.py,
ai_generation.py).

Каноническая реализация: gemini_translator.ui.dialogs.menu_utils —
``PageDialogProxyMixin`` (делегирование атрибутов ЭКЗЕМПЛЯРА в self.page)
и ``make_page_delegating_meta(page_cls)`` (фабрика метакласса, делегирующего
атрибуты КЛАССА в page_cls — этим пользуются тесты, которые забирают
небound-методы вида ``TranslationValidatorDialog._some_method``).

(а) Характеризационные тесты — крайние случаи, которые различали копии:
    - page не установлен вовсе -> AttributeError(name);
    - self.page is None -> AttributeError(name) (не AttributeError на None);
    - page есть -> делегирование ему;
    - на уровне класса метакласс отдаёт атрибут page_cls.
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QDialog

from gemini_translator.ui.dialogs.menu_utils import (
    PageDialogProxyMixin,
    make_page_delegating_meta,
)


class _StubPage:
    marker = "page-marker"

    def greet(self):
        return "hello-from-page"


class _DialogWithoutPage(PageDialogProxyMixin):
    """Голый объект (без Qt-инициализации) — проверяем чистую логику миксина."""

    def __init__(self, page=None):
        if page is not None:
            self.__dict__["page"] = page


class PageDialogProxyMixinCharacterizationTests(unittest.TestCase):
    def test_delegates_unknown_attribute_to_page(self):
        stub = _StubPage()
        obj = _DialogWithoutPage(page=stub)

        self.assertEqual(obj.marker, "page-marker")
        self.assertEqual(obj.greet(), "hello-from-page")

    def test_missing_page_attribute_raises_attribute_error(self):
        obj = _DialogWithoutPage()  # self.__dict__ never gets "page"

        with self.assertRaises(AttributeError):
            _ = obj.anything

    def test_page_explicitly_none_raises_attribute_error(self):
        """Копии проверяют `is not None`, а не просто truthiness/наличие ключа —
        page=None должен вести себя как "страницы нет", а не падать на
        getattr(None, name)."""
        obj = _DialogWithoutPage.__new__(_DialogWithoutPage)
        obj.__dict__["page"] = None

        with self.assertRaises(AttributeError):
            _ = obj.marker

    def test_attribute_error_message_is_the_missing_name(self):
        obj = _DialogWithoutPage()
        try:
            _ = obj.some_missing_name
            self.fail("ожидался AttributeError")
        except AttributeError as exc:
            self.assertIn("some_missing_name", str(exc))


class MakePageDelegatingMetaCharacterizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_class_level_attribute_delegates_to_page_cls(self):
        meta = make_page_delegating_meta(_StubPage)

        class SomeDialog(QDialog, metaclass=meta):
            pass

        # marker не определён на SomeDialog -> метакласс должен отдать его
        # с _StubPage (нужно тестам, забирающим unbound-методы старых
        # диалогов через ИмяДиалога.some_method).
        self.assertEqual(SomeDialog.marker, "page-marker")

    def test_class_level_missing_on_both_raises_attribute_error(self):
        meta = make_page_delegating_meta(_StubPage)

        class SomeOtherDialog(QDialog, metaclass=meta):
            pass

        with self.assertRaises(AttributeError):
            _ = SomeOtherDialog.totally_missing_attr

    def test_two_dialogs_delegate_to_their_own_page_class(self):
        """Разные диалоги должны делегировать каждый в СВОЙ page_cls, а не
        путаться друг с другом (иначе фабрика была бы бесполезна)."""

        class PageA:
            only_on_a = "a-value"

        class PageB:
            only_on_b = "b-value"

        class DialogA(QDialog, metaclass=make_page_delegating_meta(PageA)):
            pass

        class DialogB(QDialog, metaclass=make_page_delegating_meta(PageB)):
            pass

        self.assertEqual(DialogA.only_on_a, "a-value")
        self.assertEqual(DialogB.only_on_b, "b-value")
        with self.assertRaises(AttributeError):
            _ = DialogA.only_on_b
        with self.assertRaises(AttributeError):
            _ = DialogB.only_on_a


if __name__ == "__main__":
    unittest.main()
