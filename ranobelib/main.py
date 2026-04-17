import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from constants import APP_NAME, SETTINGS_ORG
from main_window import RanobeUploaderApp
from window_branding import install_window_title_branding


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(SETTINGS_ORG)
    install_window_title_branding(app)

    window = RanobeUploaderApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

