import contextlib
import os
import tempfile
import traceback
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("GT_DISABLE_LOCAL_MODEL_DISCOVERY", "1")

from PyQt6 import QtCore, QtWidgets

from main import EventBus
from gemini_translator.utils.settings import SettingsManager


class _OrphanShowFilter(QtCore.QObject):
    """Ловит событие Show у виджета без родителя: такой виджет по правилам
    Qt становится отдельным топ-уровневым окном и на мгновение мигает на
    экране (источник «маленьких окошек»)."""

    def __init__(self):
        super().__init__()
        self.violations = []

    def eventFilter(self, obj, event):
        if (event.type() == QtCore.QEvent.Type.Show
                and isinstance(obj, QtWidgets.QWidget)
                and obj.isWindow()
                and not isinstance(obj, (QtWidgets.QDialog, QtWidgets.QMainWindow))):
            stack = "".join(traceback.format_stack(limit=8)[:-1])
            self.violations.append(f"{type(obj).__name__}\n{stack}")
        return False


@contextlib.contextmanager
def capture_orphan_shows():
    app = QtWidgets.QApplication.instance()
    tracker = _OrphanShowFilter()
    app.installEventFilter(tracker)
    try:
        yield tracker.violations
    finally:
        app.removeEventFilter(tracker)


class OrphanWidgetFlashTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.app.event_bus = EventBus()
        self.settings_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.settings_file.close()
        self.settings = SettingsManager(
            event_bus=self.app.event_bus,
            config_file=self.settings_file.name,
        )
        self.app.settings_manager = self.settings
        self.app.get_settings_manager = lambda: self.settings

    def tearDown(self):
        self.settings.flush()
        try:
            os.unlink(self.settings_file.name)
        except FileNotFoundError:
            pass

    def test_preset_widget_construction_shows_no_orphan_widgets(self):
        from gemini_translator.ui.widgets.preset_widget import PresetWidget

        with capture_orphan_shows() as violations:
            widget = PresetWidget(None, "Промпт", show_default_button=True)
        self.addCleanup(widget.deleteLater)

        self.assertEqual(violations, [],
                         "PresetWidget мигает окном при создании:\n"
                         + "\n".join(violations))

    def test_epub_selector_full_ui_shows_no_orphan_widgets(self):
        from gemini_translator.ui.dialogs.epub import EpubHtmlSelectorDialog

        dialog = EpubHtmlSelectorDialog.__new__(EpubHtmlSelectorDialog)
        QtWidgets.QDialog.__init__(dialog)
        dialog.output_folder = None
        dialog.project_manager = None
        dialog.real_epub_path = "book.epub"
        dialog._init_lazy_ui_skeleton()

        with capture_orphan_shows() as violations:
            dialog._populate_full_ui()
        self.addCleanup(dialog.deleteLater)

        self.assertEqual(violations, [],
                         "Селектор EPUB мигает окном при построении UI:\n"
                         + "\n".join(violations))


if __name__ == "__main__":
    unittest.main()
