"""Characterization + routing tests for
finding-ui-dialogs-glossary-b_design_1-residue-analyzer-fragile-owner.

ResidueAnalyzerPage used to locate its glossary owner by walking up the
parent chain and duck-typing on ``hasattr(node, 'logic')``, instead of using
the canonical ``ancestor_utils.find_ancestor_by_class_name`` helper already
used by the sibling ``CorrectionSessionPage._locate_glossary_owner`` in
``ai_correction.py``. The duck-typing approach is fragile: any intermediate
widget that happens to carry a ``logic`` attribute (but is not actually the
glossary owner) gets picked up first, silently returning the wrong ancestor.

These tests:
  (a) characterize the canonical helper's behavior on the exact divergence
      scenario the finding describes (an attribute-decoy ancestor), and
  (b) prove ResidueAnalyzerPage's owner lookup is routed through that
      canonical helper rather than reimplementing its own traversal.
"""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.widgets.ancestor_utils import find_ancestor_by_class_name
from gemini_translator.ui.dialogs.glossary_dialogs import residue_analyzer as residue_analyzer_module
from gemini_translator.ui.dialogs.glossary_dialogs.residue_analyzer import ResidueAnalyzerPage


class _ResidueSettingsStub:
    def get_last_word_exceptions_text(self):
        return " "


def _make_glossary_manager_page_class():
    """Build a QWidget subclass whose __class__.__name__ is exactly
    'GlossaryManagerPage', mirroring the real production class name that
    find_ancestor_by_class_name matches against."""
    return type("GlossaryManagerPage", (QtWidgets.QWidget,), {})


class FindAncestorDivergenceCharacterizationTests(unittest.TestCase):
    """(a) Characterize the canonical helper on the fragile-owner scenario."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_skips_attribute_decoy_ancestor_and_finds_real_owner_by_class_name(self):
        """A nearer ancestor that merely happens to carry a 'logic' attribute
        (but is not the real owner class) must NOT be returned: the
        canonical helper matches on class name, not duck-typed attributes."""
        real_owner = _make_glossary_manager_page_class()()
        self.addCleanup(real_owner.close)

        decoy = QtWidgets.QWidget(real_owner)
        decoy.logic = object()  # attribute-decoy: has 'logic' but wrong class

        leaf = QtWidgets.QWidget(decoy)

        found = find_ancestor_by_class_name(leaf, "MainWindow", "GlossaryManagerPage")
        self.assertIs(found, real_owner)
        self.assertIsNot(found, decoy)

    def test_returns_none_past_unrelated_hierarchy(self):
        leaf = QtWidgets.QWidget()
        self.addCleanup(leaf.close)
        self.assertIsNone(find_ancestor_by_class_name(leaf, "MainWindow", "GlossaryManagerPage"))


class ResidueAnalyzerOwnerLookupTests(unittest.TestCase):
    """(a)+(b) ResidueAnalyzerPage._locate_glossary_owner must route through
    the canonical helper, and must therefore be immune to the attribute-decoy
    divergence characterized above."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_page(self, parent):
        return ResidueAnalyzerPage({}, [], _ResidueSettingsStub(), parent)

    def test_skips_attribute_decoy_and_locates_real_glossary_manager_page(self):
        """Regression test for the exact risk in the finding: today's
        duck-typed hasattr(node, 'logic') walk would stop at the decoy
        (which is not actually the glossary owner) instead of continuing
        to the real GlossaryManagerPage ancestor."""
        real_owner = _make_glossary_manager_page_class()()
        self.addCleanup(real_owner.close)

        decoy = QtWidgets.QWidget(real_owner)
        decoy.logic = object()  # duck-typing bait: no find_untranslated_residue

        page = self._make_page(decoy)
        self.addCleanup(page.close)

        self.assertIs(page._get_glossary_owner(), real_owner)
        self.assertIsNot(page._get_glossary_owner(), decoy)

    def test_locate_glossary_owner_routes_through_canonical_helper(self):
        """Routing test: must call ancestor_utils.find_ancestor_by_class_name
        with the expected class names, and use its return value verbatim.

        Fails before the refactor (residue_analyzer reimplements its own
        hasattr('logic') walk and never calls the canonical helper), passes
        after (the module calls it directly)."""
        real_owner = _make_glossary_manager_page_class()()
        self.addCleanup(real_owner.close)

        page = self._make_page(real_owner)
        self.addCleanup(page.close)

        sentinel = object()
        with patch.object(
            residue_analyzer_module,
            "find_ancestor_by_class_name",
            return_value=sentinel,
            create=True,
        ) as mock_helper:
            result = page._locate_glossary_owner()

        mock_helper.assert_called_once_with(page, "MainWindow", "GlossaryManagerPage")
        self.assertIs(result, sentinel)


if __name__ == "__main__":
    unittest.main()
