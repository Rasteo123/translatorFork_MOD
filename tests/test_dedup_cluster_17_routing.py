# -*- coding: utf-8 -*-
"""cluster-17 маршрутизация: 14 тонких QDialog-обёрток (InitialSetupDialog,
ConsistencyValidatorDialog, AIRepairReviewDialog, TranslationValidatorDialog,
MainWindow (glossary), UntranslatedFixerDialog, ComplexOverlapResolverDialog,
ReverseConflictResolverDialog, CoreTermAnalyzerDialog, ResidueAnalyzerDialog,
CorrectionSessionDialog, TermFrequencyAnalyzerDialog, GroupAnalysisDialog,
GenerationSessionDialog) обязаны идти через каноническую реализацию из
``gemini_translator.ui.dialogs.menu_utils``:

  - ``PageDialogProxyMixin`` — делегирование НЕИЗВЕСТНЫХ атрибутов
    экземпляра в ``self.page``;
  - ``make_page_delegating_meta(page_cls)`` — фабрика метакласса,
    делегирующая атрибуты КЛАССА в page_cls (тесты вида
    ``TranslationValidatorDialog._some_method`` завязаны именно на это).

До рефакторинга ``PageDialogProxyMixin``/``make_page_delegating_meta`` в
menu_utils не существуют (у каждой обёртки была своя копия __getattr__ и
свой *DialogMeta) — импорт ниже падает, тест RED. После рефакторинга каждая
обёртка наследует миксин и использует фабрику — тест GREEN.
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from gemini_translator.ui.dialogs import menu_utils
from gemini_translator.ui.dialogs.menu_utils import PageDialogProxyMixin

import gemini_translator.ui.dialogs.consistency_checker as consistency_checker
import gemini_translator.ui.dialogs.glossary as glossary
import gemini_translator.ui.dialogs.setup as setup
import gemini_translator.ui.dialogs.validation as validation
import gemini_translator.ui.dialogs.validation_dialogs.untranslated_fixer_dialog as untranslated_fixer_dialog
import gemini_translator.ui.dialogs.glossary_dialogs.ai_correction as ai_correction
import gemini_translator.ui.dialogs.glossary_dialogs.ai_generation as ai_generation
import gemini_translator.ui.dialogs.glossary_dialogs.conflict_resolvers as conflict_resolvers
import gemini_translator.ui.dialogs.glossary_dialogs.core_term_dialog as core_term_dialog
import gemini_translator.ui.dialogs.glossary_dialogs.group_analyzer as group_analyzer
import gemini_translator.ui.dialogs.glossary_dialogs.residue_analyzer as residue_analyzer
import gemini_translator.ui.dialogs.glossary_dialogs.term_frequency_analyzer as term_frequency_analyzer


# (Диалог, соответствующая Page-реализация) для всех 14 бывших копий.
PAGE_CLASSES = {
    setup.InitialSetupDialog: setup.InitialSetupPage,
    consistency_checker.ConsistencyValidatorDialog: consistency_checker.ConsistencyValidatorPage,
    validation.AIRepairReviewDialog: validation.AIRepairReviewPage,
    validation.TranslationValidatorDialog: validation.TranslationValidatorPage,
    glossary.MainWindow: glossary.GlossaryManagerPage,
    untranslated_fixer_dialog.UntranslatedFixerDialog: untranslated_fixer_dialog.UntranslatedFixerPage,
    conflict_resolvers.ComplexOverlapResolverDialog: conflict_resolvers.ComplexOverlapResolverPage,
    conflict_resolvers.ReverseConflictResolverDialog: conflict_resolvers.ReverseConflictResolverPage,
    core_term_dialog.CoreTermAnalyzerDialog: core_term_dialog.CoreTermAnalyzerPage,
    residue_analyzer.ResidueAnalyzerDialog: residue_analyzer.ResidueAnalyzerPage,
    ai_correction.CorrectionSessionDialog: ai_correction.CorrectionSessionPage,
    term_frequency_analyzer.TermFrequencyAnalyzerDialog: term_frequency_analyzer.TermFrequencyAnalyzerPage,
    group_analyzer.GroupAnalysisDialog: group_analyzer.GroupAnalysisPage,
    ai_generation.GenerationSessionDialog: ai_generation.GenerationSessionPage,
}


class _StubPage:
    probe_attr = "value-from-page"


class InstanceLevelDelegationRoutingTests(unittest.TestCase):
    """Все 14 обёрток обязаны наследовать PageDialogProxyMixin и звать
    именно его __getattr__, а не свою копию."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_each_wrapper_is_subclass_of_canonical_mixin(self):
        for dialog_cls in PAGE_CLASSES:
            with self.subTest(dialog_cls=dialog_cls.__name__):
                self.assertTrue(
                    issubclass(dialog_cls, PageDialogProxyMixin),
                    f"{dialog_cls.__name__} должен наследовать "
                    "PageDialogProxyMixin вместо собственной копии __getattr__",
                )

    def test_each_wrapper_instance_getattr_routes_through_canonical_mixin(self):
        original_getattr = PageDialogProxyMixin.__getattr__
        calls = []

        def spy(self, name):
            # Обычная функция (а не Mock) нужна, чтобы CPython применил к
            # ней дескрипторный протокол и передал `self` при вызове
            # __getattr__ как слот-метода — Mock() дескриптором не
            # является и получил бы только `name`.
            calls.append((type(self).__name__, name))
            return original_getattr(self, name)

        for dialog_cls in PAGE_CLASSES:
            with self.subTest(dialog_cls=dialog_cls.__name__):
                # Обходим Qt C++ __init__ — юнит-тесту он не нужен, а
                # __getattr__ сам по себе не трогает C++-объект (см. тот
                # же приём в test_dedup_cluster_41_routing.py).
                instance = dialog_cls.__new__(dialog_cls)
                instance.__dict__["page"] = _StubPage()
                with mock.patch.object(PageDialogProxyMixin, "__getattr__", spy):
                    result = instance.probe_attr
                self.assertEqual(result, "value-from-page")
        self.assertEqual(
            len(calls),
            len(PAGE_CLASSES),
            "каждая обёртка обязана звать канонический "
            "PageDialogProxyMixin.__getattr__ ровно один раз на обращение",
        )


class ClassLevelDelegationRoutingTests(unittest.TestCase):
    """Метакласс каждой обёртки обязан быть построен фабрикой
    make_page_delegating_meta, а не собственным ``_XxxDialogMeta``."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_each_wrapper_class_getattr_routes_through_canonical_resolver(self):
        spy = mock.Mock(wraps=menu_utils._resolve_page_class_attr)
        probe_name = "__dedup17_class_probe__"
        for dialog_cls, page_cls in PAGE_CLASSES.items():
            with self.subTest(dialog_cls=dialog_cls.__name__):
                setattr(page_cls, probe_name, "class-level-value")
                try:
                    with mock.patch.object(menu_utils, "_resolve_page_class_attr", spy):
                        result = getattr(dialog_cls, probe_name)
                    self.assertEqual(result, "class-level-value")
                finally:
                    delattr(page_cls, probe_name)
        self.assertEqual(
            spy.call_count,
            len(PAGE_CLASSES),
            "каждый метакласс обёртки обязан идти через каноническую "
            "menu_utils._resolve_page_class_attr, а не свою копию *DialogMeta",
        )


if __name__ == "__main__":
    unittest.main()
