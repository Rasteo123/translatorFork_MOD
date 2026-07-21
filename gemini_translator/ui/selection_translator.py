"""Global Google Translate helper for selected text in Qt editors.

The controller is installed once on QApplication and therefore also covers
dialogs and tool windows created later.  Translation is opt-in: selecting
foreign text only shows a small action button; the network request starts
after the user clicks it, uses the context-menu action, or presses the
keyboard shortcut.
"""

from __future__ import annotations

import json
import weakref
from dataclasses import dataclass, field
from urllib.parse import urlencode

from PyQt6 import QtCore, QtGui, QtNetwork, QtWidgets


GOOGLE_TRANSLATE_ENDPOINT = "https://translate.googleapis.com/translate_a/single"
GOOGLE_TRANSLATE_TIMEOUT_MS = 20_000
GOOGLE_TRANSLATE_CHUNK_CHARS = 4_000
MAX_SELECTION_CHARS = 50_000

_TEXT_EDITOR_TYPES = (
    QtWidgets.QLineEdit,
    QtWidgets.QPlainTextEdit,
    QtWidgets.QTextEdit,
)
_ITEM_VIEW_TYPES = (
    QtWidgets.QListWidget,
    QtWidgets.QTableWidget,
    QtWidgets.QTreeWidget,
)
_SECRET_FIELD_MARKERS = (
    "api_key",
    "apikey",
    "access_key",
    "password",
    "passwd",
    "secret",
    "credential",
    "token",
    "ключ api",
    "api ключ",
    "парол",
    "токен",
    "секрет",
)
_RUSSIAN_LETTERS = frozenset("абвгдеёжзийклмнопрстуфхцчшщъыьэюя")


def parse_google_translate_response(payload: bytes | str) -> str:
    """Extract translated text from the ``translate_a/single`` response."""

    if isinstance(payload, bytes):
        payload = payload.decode("utf-8-sig")
    data = json.loads(payload)
    if not isinstance(data, list) or not data or not isinstance(data[0], list):
        raise ValueError("Google Translate вернул ответ неизвестного формата.")

    parts: list[str] = []
    for part in data[0]:
        if isinstance(part, list) and part and part[0] is not None:
            parts.append(str(part[0]))
    translated = "".join(parts)
    if not translated:
        raise ValueError("Google Translate вернул пустой перевод.")
    return translated


def split_text_for_translation(
    text: str,
    max_chars: int = GOOGLE_TRANSLATE_CHUNK_CHARS,
) -> list[str]:
    """Split text without losing characters, preferring paragraph/word edges."""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if not text:
        return []

    chunks: list[str] = []
    offset = 0
    text_length = len(text)
    while text_length - offset > max_chars:
        upper = offset + max_chars
        lower = offset + max(1, max_chars // 2)
        candidates = (
            text.rfind("\n\n", lower, upper),
            text.rfind("\n", lower, upper),
            text.rfind(" ", lower, upper),
            text.rfind("\t", lower, upper),
        )
        split_at = max(candidates)
        end = split_at + 1 if split_at >= lower else upper
        chunks.append(text[offset:end])
        offset = end
    if offset < text_length:
        chunks.append(text[offset:])
    return chunks


def looks_foreign(text: str) -> bool:
    """Return True when a selection visibly contains non-Russian letters."""

    for character in text:
        if not character.isalpha():
            continue
        lowered = character.lower()
        if "a" <= lowered <= "z":
            return True
        codepoint = ord(character)
        if 0x0400 <= codepoint <= 0x052F:
            if lowered not in _RUSSIAN_LETTERS:
                return True
        else:
            return True
    return False


def _has_letters(text: str) -> bool:
    return any(character.isalpha() for character in text)


def _normalize_selected_text(text: str) -> str:
    return text.replace("\u2029", "\n").replace("\u2028", "\n")


@dataclass
class SelectionSnapshot:
    widget_ref: object
    text: str
    start: int
    end: int
    editable: bool

    def widget(self):
        try:
            return self.widget_ref()
        except (ReferenceError, RuntimeError):
            return None


@dataclass
class _TranslationJob:
    popup: "TranslationPopup"
    snapshot: SelectionSnapshot
    chunks: list[str]
    translated_chunks: list[str] = field(default_factory=list)
    index: int = 0
    reply: QtNetwork.QNetworkReply | None = None
    pending_prefix: str = ""
    pending_suffix: str = ""


class TranslationPopup(QtWidgets.QDialog):
    """Small non-modal result window with copy/replace actions."""

    replace_requested = QtCore.pyqtSignal(str)

    def __init__(self, source_text: str, *, can_replace: bool, parent=None):
        super().__init__(parent)
        self.setObjectName("selectionTranslationPopup")
        self.setProperty("selectionTranslationDisabled", True)
        self.setWindowTitle("Перевод выделенного текста")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumSize(520, 300)
        self.resize(640, 420)

        layout = QtWidgets.QVBoxLayout(self)
        source_label = QtWidgets.QLabel("Оригинал")
        self.source_view = QtWidgets.QPlainTextEdit(self)
        self.source_view.setReadOnly(True)
        self.source_view.setPlainText(source_text)
        self.source_view.setMaximumHeight(125)

        result_label = QtWidgets.QLabel("Перевод на русский")
        self.result_view = QtWidgets.QPlainTextEdit(self)
        self.result_view.setReadOnly(True)
        self.result_view.setPlaceholderText("Перевод выполняется…")

        self.status_label = QtWidgets.QLabel(
            "Текст отправляется в Google Translate…",
            self,
        )
        self.status_label.setWordWrap(True)

        buttons = QtWidgets.QHBoxLayout()
        self.copy_button = QtWidgets.QPushButton("Копировать", self)
        self.copy_button.setEnabled(False)
        self.replace_button = QtWidgets.QPushButton("Заменить выделение", self)
        self.replace_button.setEnabled(False)
        self.replace_button.setVisible(can_replace)
        close_button = QtWidgets.QPushButton("Закрыть", self)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.replace_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)

        layout.addWidget(source_label)
        layout.addWidget(self.source_view)
        layout.addWidget(result_label)
        layout.addWidget(self.result_view, 1)
        layout.addWidget(self.status_label)
        layout.addLayout(buttons)

        self.copy_button.clicked.connect(self._copy_result)
        self.replace_button.clicked.connect(
            lambda: self.replace_requested.emit(self.result_view.toPlainText())
        )
        close_button.clicked.connect(self.close)

    def set_progress(self, current: int, total: int) -> None:
        if total > 1:
            self.status_label.setText(
                f"Google Translate: перевод части {current} из {total}…"
            )

    def set_result(self, translated: str) -> None:
        self.result_view.setPlainText(translated)
        self.status_label.setText("Готово · Google Translate")
        self.copy_button.setEnabled(bool(translated))
        self.replace_button.setEnabled(bool(translated))

    def set_error(self, message: str) -> None:
        self.result_view.clear()
        self.result_view.setPlaceholderText("Перевод не получен")
        self.status_label.setText(f"Ошибка: {message}")
        self.copy_button.setEnabled(False)
        self.replace_button.setEnabled(False)

    def _copy_result(self) -> None:
        text = self.result_view.toPlainText()
        if text:
            QtWidgets.QApplication.clipboard().setText(text)
            self.status_label.setText("Перевод скопирован в буфер обмена")


class SelectionTranslationController(QtCore.QObject):
    """Application-wide event filter and asynchronous translation client."""

    def __init__(self, app: QtWidgets.QApplication):
        super().__init__(app)
        self._app = app
        self._network = QtNetwork.QNetworkAccessManager(self)
        self._jobs: dict[int, _TranslationJob] = {}
        self._next_job_id = 1
        self._offered_snapshot: SelectionSnapshot | None = None

        self._offer_button = QtWidgets.QToolButton()
        self._offer_button.setObjectName("selectionTranslateButton")
        self._offer_button.setProperty("selectionTranslationDisabled", True)
        self._offer_button.setText("Перевести на русский")
        self._offer_button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self._offer_button.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating,
            True,
        )
        self._offer_button.setWindowFlags(
            QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self._offer_button.hide()
        self._offer_button.clicked.connect(self._translate_offered_selection)

        app.installEventFilter(self)
        app.aboutToQuit.connect(self.shutdown)

    def eventFilter(self, obj, event):  # noqa: N802 - Qt API name
        event_type = event.type()

        if event_type == QtCore.QEvent.Type.ApplicationDeactivate:
            # A click on the top-level tool button can briefly deactivate the
            # main window on Windows. Keep the pending selection in that case
            # so the button's clicked signal can still start the translation.
            if not self._offer_click_in_progress():
                self._hide_offer()
            return False

        if event_type == QtCore.QEvent.Type.MouseButtonPress:
            if not self._event_targets_offer(obj, event):
                self._hide_offer()

        editor = self._resolve_text_source(obj)
        if editor is None or self._translation_disabled(editor):
            return False

        if event_type == QtCore.QEvent.Type.MouseButtonRelease:
            if event.button() == QtCore.Qt.MouseButton.LeftButton:
                editor_ref = weakref.ref(editor)
                global_pos = event.globalPosition().toPoint()
                QtCore.QTimer.singleShot(
                    0,
                    lambda ref=editor_ref, pos=global_pos: self._offer_after_selection(
                        ref, pos
                    ),
                )
            return False

        if event_type == QtCore.QEvent.Type.ContextMenu:
            if not isinstance(editor, _TEXT_EDITOR_TYPES):
                return False
            snapshot = self._selection_snapshot(editor)
            if snapshot is not None and _has_letters(snapshot.text):
                self._show_context_menu(editor, event, snapshot)
                return True
            return False

        if event_type == QtCore.QEvent.Type.KeyPress:
            if event.key() == QtCore.Qt.Key.Key_Escape:
                self._hide_offer()
                return False
            modifiers = event.modifiers()
            shortcut_modifiers = (
                QtCore.Qt.KeyboardModifier.ControlModifier
                | QtCore.Qt.KeyboardModifier.ShiftModifier
            )
            if event.key() == QtCore.Qt.Key.Key_T and modifiers == shortcut_modifiers:
                snapshot = self._selection_snapshot(editor)
                if snapshot is not None and _has_letters(snapshot.text):
                    self.translate(snapshot, anchor=QtGui.QCursor.pos())
                    return True

        return False

    def shutdown(self) -> None:
        self._app.removeEventFilter(self)
        self._hide_offer()
        for job_id in list(self._jobs):
            job = self._jobs.pop(job_id)
            if job.reply is not None:
                job.reply.abort()
                job.reply.deleteLater()
        self._offer_button.close()

    def translate(self, snapshot: SelectionSnapshot, *, anchor: QtCore.QPoint) -> None:
        text = snapshot.text
        if not text.strip():
            return
        if len(text) > MAX_SELECTION_CHARS:
            QtWidgets.QMessageBox.information(
                snapshot.widget().window() if snapshot.widget() is not None else None,
                "Слишком большое выделение",
                f"За один раз можно перевести до {MAX_SELECTION_CHARS:,} символов."
                .replace(",", " "),
            )
            return

        parent = snapshot.widget().window() if snapshot.widget() is not None else None
        popup = TranslationPopup(text, can_replace=snapshot.editable, parent=parent)
        popup.replace_requested.connect(
            lambda translated, snap=snapshot, window=popup: self._replace_selection(
                snap, translated, window
            )
        )

        chunks = split_text_for_translation(text)
        job_id = self._next_job_id
        self._next_job_id += 1
        self._jobs[job_id] = _TranslationJob(
            popup=popup,
            snapshot=snapshot,
            chunks=chunks,
        )
        popup.finished.connect(lambda _result, value=job_id: self._cancel_job(value))
        self._position_popup(popup, anchor)
        popup.show()
        popup.raise_()
        self._hide_offer()
        self._request_next_chunk(job_id)

    def _request_next_chunk(self, job_id: int) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return

        while job.index < len(job.chunks) and not job.chunks[job.index].strip():
            job.translated_chunks.append(job.chunks[job.index])
            job.index += 1

        if job.index >= len(job.chunks):
            translated = "".join(job.translated_chunks)
            job.popup.set_result(translated)
            job.reply = None
            return

        chunk = job.chunks[job.index]
        leading_length = len(chunk) - len(chunk.lstrip())
        trailing_length = len(chunk) - len(chunk.rstrip())
        core_end = len(chunk) - trailing_length if trailing_length else len(chunk)
        job.pending_prefix = chunk[:leading_length]
        job.pending_suffix = chunk[core_end:]
        chunk_to_translate = chunk[leading_length:core_end]

        if not chunk_to_translate:
            job.translated_chunks.append(chunk)
            job.index += 1
            self._request_next_chunk(job_id)
            return

        job.popup.set_progress(job.index + 1, len(job.chunks))
        url = QtCore.QUrl(GOOGLE_TRANSLATE_ENDPOINT)
        query = QtCore.QUrlQuery()
        query.addQueryItem("client", "gtx")
        query.addQueryItem("sl", "auto")
        query.addQueryItem("tl", "ru")
        query.addQueryItem("dt", "t")
        url.setQuery(query)

        request = QtNetwork.QNetworkRequest(url)
        request.setHeader(
            QtNetwork.QNetworkRequest.KnownHeaders.ContentTypeHeader,
            "application/x-www-form-urlencoded; charset=UTF-8",
        )
        request.setRawHeader(
            b"User-Agent",
            b"Mozilla/5.0 TranslatorFork Selection Translator",
        )
        request.setTransferTimeout(GOOGLE_TRANSLATE_TIMEOUT_MS)
        body = urlencode({"q": chunk_to_translate}).encode("utf-8")
        reply = self._network.post(request, body)
        job.reply = reply
        reply.finished.connect(
            lambda value=job_id, current_reply=reply: self._finish_reply(
                value, current_reply
            )
        )

    def _finish_reply(self, job_id: int, reply: QtNetwork.QNetworkReply) -> None:
        job = self._jobs.get(job_id)
        if job is None or job.reply is not reply:
            reply.deleteLater()
            return

        job.reply = None
        error = reply.error()
        status = reply.attribute(
            QtNetwork.QNetworkRequest.Attribute.HttpStatusCodeAttribute
        )
        payload = bytes(reply.readAll())
        error_string = reply.errorString()
        reply.deleteLater()

        if error != QtNetwork.QNetworkReply.NetworkError.NoError:
            self._fail_job(job_id, error_string)
            return
        if status is not None and int(status) >= 400:
            self._fail_job(job_id, f"HTTP {status}")
            return

        try:
            translated = parse_google_translate_response(payload)
        except (UnicodeError, json.JSONDecodeError, ValueError, TypeError) as error_value:
            self._fail_job(job_id, str(error_value))
            return

        job.translated_chunks.append(
            f"{job.pending_prefix}{translated}{job.pending_suffix}"
        )
        job.pending_prefix = ""
        job.pending_suffix = ""
        job.index += 1
        self._request_next_chunk(job_id)

    def _fail_job(self, job_id: int, message: str) -> None:
        job = self._jobs.get(job_id)
        if job is not None:
            job.popup.set_error(message or "Google Translate недоступен.")

    def _cancel_job(self, job_id: int) -> None:
        job = self._jobs.pop(job_id, None)
        if job is not None and job.reply is not None:
            job.reply.abort()
            job.reply.deleteLater()
            job.reply = None

    def _offer_after_selection(self, editor_ref, global_pos: QtCore.QPoint) -> None:
        try:
            editor = editor_ref()
        except (ReferenceError, RuntimeError):
            editor = None
        if editor is None or self._translation_disabled(editor):
            return
        snapshot = self._selection_snapshot(editor)
        if (
            snapshot is None
            or not looks_foreign(snapshot.text)
            or len(snapshot.text) > MAX_SELECTION_CHARS
        ):
            return
        self._offered_snapshot = snapshot
        self._position_offer_button(global_pos)
        self._offer_button.show()
        self._offer_button.raise_()

    def _translate_offered_selection(self) -> None:
        snapshot = self._offered_snapshot
        if snapshot is not None:
            self.translate(snapshot, anchor=self._offer_button.pos())

    def _show_context_menu(self, editor, event, snapshot: SelectionSnapshot) -> None:
        menu = editor.createStandardContextMenu()
        menu.addSeparator()
        action = menu.addAction("Перевести на русский через Google")
        action.setShortcut(QtGui.QKeySequence("Ctrl+Shift+T"))
        action.setEnabled(len(snapshot.text) <= MAX_SELECTION_CHARS)
        action.triggered.connect(
            lambda _checked=False, snap=snapshot, pos=event.globalPos(): self.translate(
                snap, anchor=pos
            )
        )
        menu.exec(event.globalPos())
        menu.deleteLater()

    def _selection_snapshot(self, editor) -> SelectionSnapshot | None:
        try:
            if isinstance(editor, _ITEM_VIEW_TYPES):
                current_index = editor.currentIndex()
                indexes = []
                if current_index.isValid():
                    indexes.append(current_index)
                indexes.extend(
                    index
                    for index in editor.selectedIndexes()
                    if index.isValid() and index not in indexes
                )
                candidates = [
                    str(index.data(QtCore.Qt.ItemDataRole.DisplayRole) or "").strip()
                    for index in indexes
                ]
                text = next(
                    (value for value in candidates if looks_foreign(value)),
                    next((value for value in candidates if _has_letters(value)), ""),
                )
                if not text:
                    return None
                return SelectionSnapshot(
                    widget_ref=weakref.ref(editor),
                    text=text,
                    start=-1,
                    end=-1,
                    editable=False,
                )

            if isinstance(editor, QtWidgets.QLineEdit):
                start = editor.selectionStart()
                text = editor.selectedText()
                if start < 0 or not text:
                    return None
                return SelectionSnapshot(
                    widget_ref=weakref.ref(editor),
                    text=text,
                    start=start,
                    end=start + len(text),
                    editable=editor.isEnabled() and not editor.isReadOnly(),
                )

            cursor = editor.textCursor()
            if not cursor.hasSelection():
                return None
            text = _normalize_selected_text(cursor.selectedText())
            if not text:
                return None
            return SelectionSnapshot(
                widget_ref=weakref.ref(editor),
                text=text,
                start=cursor.selectionStart(),
                end=cursor.selectionEnd(),
                editable=editor.isEnabled() and not editor.isReadOnly(),
            )
        except RuntimeError:
            return None

    def _replace_selection(
        self,
        snapshot: SelectionSnapshot,
        translated: str,
        popup: TranslationPopup,
    ) -> None:
        editor = snapshot.widget()
        if editor is None or not snapshot.editable or not translated:
            popup.status_label.setText("Исходное поле уже недоступно.")
            return
        try:
            if isinstance(editor, QtWidgets.QLineEdit):
                current = editor.text()[snapshot.start:snapshot.end]
                if current != snapshot.text:
                    popup.status_label.setText(
                        "Текст в исходном поле изменился; замена отменена."
                    )
                    return
                editor.setSelection(snapshot.start, snapshot.end - snapshot.start)
                editor.insert(translated)
            else:
                cursor = editor.textCursor()
                cursor.setPosition(snapshot.start)
                cursor.setPosition(
                    snapshot.end,
                    QtGui.QTextCursor.MoveMode.KeepAnchor,
                )
                current = _normalize_selected_text(cursor.selectedText())
                if current != snapshot.text:
                    popup.status_label.setText(
                        "Текст в исходном поле изменился; замена отменена."
                    )
                    return
                cursor.insertText(translated)
                editor.setTextCursor(cursor)
            popup.status_label.setText("Выделенный текст заменён переводом")
        except RuntimeError:
            popup.status_label.setText("Исходное поле уже закрыто.")

    def _translation_disabled(self, editor) -> bool:
        if isinstance(editor, QtWidgets.QLineEdit):
            if editor.echoMode() != QtWidgets.QLineEdit.EchoMode.Normal:
                return True

        current = editor
        while current is not None:
            try:
                if bool(current.property("selectionTranslationDisabled")):
                    return True
            except RuntimeError:
                return True
            current = current.parentWidget()

        identifying_text = editor.objectName().lower()
        placeholder = getattr(editor, "placeholderText", None)
        if callable(placeholder):
            identifying_text += " " + str(placeholder()).lower()
        return any(marker in identifying_text for marker in _SECRET_FIELD_MARKERS)

    @classmethod
    def _resolve_text_source(cls, obj):
        current = obj if isinstance(obj, QtWidgets.QWidget) else None
        for _ in range(4):
            if isinstance(current, _TEXT_EDITOR_TYPES):
                return current
            if isinstance(current, _ITEM_VIEW_TYPES):
                return current if cls._is_chapter_item_view(current) else None
            current = current.parentWidget() if current is not None else None
        return None

    @staticmethod
    def _is_chapter_item_view(view) -> bool:
        identifiers: list[str] = []
        current = view
        while current is not None:
            identifiers.append(current.objectName())
            identifiers.append(type(current).__name__)
            current = current.parentWidget()
        try:
            identifiers.append(view.window().windowTitle())
        except RuntimeError:
            return False
        combined = " ".join(identifiers).lower()
        return "chapter" in combined or "глав" in combined

    def _hide_offer(self) -> None:
        self._offer_button.hide()
        self._offered_snapshot = None

    def _event_targets_offer(self, obj, event) -> bool:
        current = obj if isinstance(obj, QtWidgets.QWidget) else None
        while current is not None:
            if current is self._offer_button:
                return True
            current = current.parentWidget()

        try:
            if obj is self._offer_button.windowHandle():
                return True
            global_position = event.globalPosition().toPoint()
        except (AttributeError, RuntimeError):
            return False
        return self._offer_global_rect().contains(global_position)

    def _offer_click_in_progress(self) -> bool:
        return (
            self._offer_button.isVisible()
            and self._offer_global_rect().contains(QtGui.QCursor.pos())
            and bool(
                QtWidgets.QApplication.mouseButtons()
                & QtCore.Qt.MouseButton.LeftButton
            )
        )

    def _offer_global_rect(self) -> QtCore.QRect:
        top_left = self._offer_button.mapToGlobal(QtCore.QPoint(0, 0))
        return QtCore.QRect(top_left, self._offer_button.size())

    def _position_offer_button(self, anchor: QtCore.QPoint) -> None:
        self._offer_button.adjustSize()
        size = self._offer_button.sizeHint()
        point = anchor + QtCore.QPoint(12, 12)
        screen = QtGui.QGuiApplication.screenAt(anchor)
        if screen is not None:
            bounds = screen.availableGeometry()
            point.setX(min(max(point.x(), bounds.left()), bounds.right() - size.width()))
            point.setY(min(max(point.y(), bounds.top()), bounds.bottom() - size.height()))
        self._offer_button.move(point)

    @staticmethod
    def _position_popup(popup: TranslationPopup, anchor: QtCore.QPoint) -> None:
        popup.ensurePolished()
        size = popup.size()
        point = anchor + QtCore.QPoint(18, 18)
        screen = QtGui.QGuiApplication.screenAt(anchor)
        if screen is not None:
            bounds = screen.availableGeometry()
            point.setX(min(max(point.x(), bounds.left()), bounds.right() - size.width()))
            point.setY(min(max(point.y(), bounds.top()), bounds.bottom() - size.height()))
        popup.move(point)


def install_selection_translator(
    app: QtWidgets.QApplication | None = None,
) -> SelectionTranslationController:
    """Install the controller once and return the application-owned instance."""

    app = app or QtWidgets.QApplication.instance()
    if app is None:
        raise RuntimeError("QApplication must exist before installing translation support.")
    existing = getattr(app, "selection_translation_controller", None)
    if existing is not None:
        return existing
    controller = SelectionTranslationController(app)
    app.selection_translation_controller = controller
    return controller
