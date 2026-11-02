import sys
import zipfile

import pytest
from PyQt6 import QtCore, QtWidgets

from gemini_translator.ui.dialogs.setup import InitialSetupDialog
from gemini_translator.ui.widgets.status_bar_widget import StatusBarWidget
from gemini_translator.utils import epub_tools


class _SwapDialog(QtWidgets.QDialog):
    _on_swap_file_requested = InitialSetupDialog._on_swap_file_requested
    _collect_previous_translated_chapter_titles = (
        InitialSetupDialog._collect_previous_translated_chapter_titles
    )

    def __init__(self, source, folder):
        super().__init__()
        self.selected_file = str(source)
        self.output_folder = str(folder)
        self.html_files = []
        self.project_manager = None
        self.status_bar = StatusBarWidget(self)


@pytest.fixture
def swap_dialog(tmp_path, qtbot, monkeypatch):
    for name in ("old.epub", "new.epub"):
        with zipfile.ZipFile(tmp_path / name, "w") as archive:
            archive.writestr("META-INF/container.xml", """
                <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
                  <rootfiles><rootfile full-path="content.opf"/></rootfiles>
                </container>""")
            archive.writestr("content.opf", """
                <package xmlns="http://www.idpf.org/2007/opf">
                  <manifest><item id="c1" href="chapter.xhtml"
                    media-type="application/xhtml+xml"/></manifest>
                  <spine><itemref idref="c1"/></spine>
                </package>""")
            archive.writestr("chapter.xhtml", """
                <html><head><title>Chapter 1</title></head>
                <body><h1>Chapter 1</h1><p>Story text.</p></body></html>""")
    dialog = _SwapDialog(tmp_path / "old.epub", tmp_path)
    qtbot.addWidget(dialog)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        lambda *args: (str(tmp_path / "new.epub"), ""),
    )
    return dialog


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_swap_reaches_confirmation_and_cancel_preserves_sources(
    swap_dialog, tmp_path, monkeypatch, platform,
):
    monkeypatch.setattr(sys, "platform", platform)
    original_files = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    confirmations = []

    def reject_confirmation(message):
        confirmations.append(message.text())
        message.reject()
        return 0

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", reject_confirmation)
    QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.CrossCursor)
    try:
        if platform == "darwin":
            def unavailable_native_cursor(*args):
                pytest.fail("macOS source swap must bypass the crashing native cursor API")

            monkeypatch.setattr(QtWidgets.QApplication, "setOverrideCursor", unavailable_native_cursor)
            monkeypatch.setattr(QtWidgets.QApplication, "restoreOverrideCursor", unavailable_native_cursor)

        swap_dialog._on_swap_file_requested()

        assert len(confirmations) == 1
        assert "Совпало: 1" in confirmations[0]
        assert "Изменилось: 0" in confirmations[0]
        assert swap_dialog.selected_file == str(tmp_path / "old.epub")
        assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == original_files
        assert "Анализ совместимости" not in swap_dialog.status_bar.progress_bar_text.format()
        assert QtWidgets.QApplication.overrideCursor().shape() == QtCore.Qt.CursorShape.CrossCursor
    finally:
        monkeypatch.undo()
        while QtWidgets.QApplication.overrideCursor() is not None:
            QtWidgets.QApplication.restoreOverrideCursor()


def test_swap_comparison_error_restores_status_and_existing_cursor(swap_dialog, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    def broken_comparison(*args):
        raise RuntimeError("comparison failed")

    monkeypatch.setattr(epub_tools, "compare_epubs_for_swap", broken_comparison)
    QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.CrossCursor)
    try:
        with pytest.raises(RuntimeError, match="comparison failed"):
            swap_dialog._on_swap_file_requested()

        assert QtWidgets.QApplication.overrideCursor().shape() == QtCore.Qt.CursorShape.CrossCursor
        assert "Анализ совместимости" not in swap_dialog.status_bar.progress_bar_text.format()
    finally:
        while QtWidgets.QApplication.overrideCursor() is not None:
            QtWidgets.QApplication.restoreOverrideCursor()
