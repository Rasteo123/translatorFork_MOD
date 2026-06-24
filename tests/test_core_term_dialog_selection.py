import unittest
import os
import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.ui.dialogs.glossary_dialogs.core_term_dialog import CoreTermAnalyzerPage

class TestCoreTermDialogSelection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not QApplication.instance():
            cls.app = QApplication(sys.argv)
        else:
            cls.app = QApplication.instance()

    def test_left_list_selection_updates_current_pattern(self):
        # Mock analysis data
        mock_glossary = [
            {'original': 'test pattern one', 'rus': 'test1', 'note': ''},
            {'original': 'test pattern two', 'rus': 'test2', 'note': ''},
            {'original': 'another one term', 'rus': 'test3', 'note': ''}
        ]
        
        mock_analysis_results = {
            "test pattern": ["test pattern one", "test pattern two"],
            "another one": ["another one term"]
        }
        
        dialog = CoreTermAnalyzerPage(mock_glossary, None, mock_analysis_results, False)
        
        # Trigger UI load
        dialog._async_prepare_data_and_populate()
        
        self.assertEqual(dialog.left_list.count(), 2)
        
        # Select the first item
        first_item = dialog.left_list.item(0)
        dialog.left_list.setCurrentItem(first_item)
        
        # Check if the right panel state updated (current_lcs_tuple should be set)
        expected_tuple = first_item.data(Qt.ItemDataRole.UserRole)
        self.assertEqual(dialog.current_lcs_tuple, expected_tuple)
        
if __name__ == "__main__":
    unittest.main()
