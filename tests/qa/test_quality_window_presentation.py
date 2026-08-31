"""The quality report belongs inside the window, not beside it."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6 import QtWidgets

from gemini_translator.ui.overlay_host import (
    OverlayHost,
    _usable_host,
    exec_dialog,
    find_overlay_host,
)


@pytest.fixture(scope="module")
def qt_app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture()
def shell(qt_app):
    """A window arranged the way the application's shell is: page plus host."""
    window = QtWidgets.QWidget()
    window.resize(1200, 800)
    layout = QtWidgets.QVBoxLayout(window)
    page = QtWidgets.QWidget(window)
    layout.addWidget(page)
    window.overlay_host = OverlayHost(window, blocked=page)
    window.show()
    qt_app.processEvents()
    yield window, page
    window.close()
    window.deleteLater()
    qt_app.processEvents()


def _dialog(parent):
    from gemini_translator.ui.dialogs.validation_dialogs import TranslationQualityDialog

    return TranslationQualityDialog(parent)


def test_a_page_inside_the_shell_finds_the_card_host(shell):
    """Отчёт открывается со страницы валидации — она обязана дотянуться до хоста."""
    window, page = shell

    assert find_overlay_host(page) is window.overlay_host
    assert _usable_host(page) is window.overlay_host


def test_the_report_is_shown_as_a_card_and_dims_the_interface(shell, qt_app):
    """Пользователь просил карточку поверх интерфейса, а не второе окно."""
    window, page = shell
    dialog = _dialog(page)
    codes: list[int] = []

    window.overlay_host.present(dialog, codes.append)
    qt_app.processEvents()

    assert not dialog.isWindow()
    assert dialog.window() is window
    # Disabling what is underneath is this host's modality.
    assert not page.isEnabled()

    dialog.reject()
    qt_app.processEvents()
    assert codes


def test_without_a_host_the_report_still_opens(qt_app):
    """Окно без шелла не должно остаться без отчёта — только без карточки."""
    plain = QtWidgets.QWidget()
    plain.show()
    qt_app.processEvents()

    assert find_overlay_host(plain) is None
    assert _usable_host(plain) is None
    assert callable(exec_dialog)

    plain.close()
    plain.deleteLater()


def test_a_hidden_window_is_not_a_usable_host(qt_app):
    """Карточка в невидимом окне — это диалог, которого никто не увидит."""
    window = QtWidgets.QWidget()
    page = QtWidgets.QWidget(window)
    window.overlay_host = OverlayHost(window, blocked=page)

    assert find_overlay_host(page) is window.overlay_host
    assert _usable_host(page) is None

    window.deleteLater()
