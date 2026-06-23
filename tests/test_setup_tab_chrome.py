from pathlib import Path


def test_translator_main_tabs_are_not_document_mode():
    setup_source = Path("gemini_translator/ui/dialogs/setup.py").read_text(encoding="utf-8")

    assert "self.tabs_group.setDocumentMode(False)" in setup_source
    assert "self.tabs_group.setDocumentMode(True)" not in setup_source
