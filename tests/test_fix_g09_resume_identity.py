"""RanobeLib: resume-состояние должно определяться по стабильному идентификатору
главы (том+номер), а не по позиционному индексу внутри отфильтрованного списка
ВЫБРАННЫХ глав.

Регрессия находки ranobelib/bugs/2-resume-index-selected-vs-widge: chapter_done_signal
нумерует главы позицией внутри `selected` (главы с включённым чекбоксом), а старый
_check_resume снимал галочки по тому же числу в ПОЛНОМ chapters_list_widget. При
частичном выборе глав это два разных списка: возобновление снимало галочки не с тех
глав, а реально уже залитые главы оставались отмеченными и уходили повторно.
"""
import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TESTS_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
RANOBELIB_DIR = os.path.join(PROJECT_ROOT, "ranobelib")
if RANOBELIB_DIR not in sys.path:
    sys.path.insert(0, RANOBELIB_DIR)

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QListWidget, QListWidgetItem, QMessageBox  # noqa: E402

from main_window import RanobeUploaderApp  # noqa: E402
from models import ChapterData  # noqa: E402

_APP = QApplication.instance() or QApplication([])


class _Field:
    def __init__(self, value=""):
        self._value = value

    def text(self):
        return self._value

    def setText(self, value):
        self._value = value


class _SettingsStub:
    """Мини-замена QSettings: хранит значения в dict, как в других тестах ranobelib."""

    def __init__(self, values=None):
        self.values = dict(values or {})

    def value(self, key, fallback=None, type=None):
        return self.values.get(key, fallback)

    def setValue(self, key, value):
        self.values[key] = value

    def remove(self, key):
        self.values.pop(key, None)


class _ResumeHarness:
    """Реальные методы возобновления, привязанные к лёгкому объекту."""

    _check_resume = RanobeUploaderApp._check_resume
    _save_resume_state = RanobeUploaderApp._save_resume_state
    _clear_resume_state = RanobeUploaderApp._clear_resume_state
    _on_chapter_done = RanobeUploaderApp._on_chapter_done
    _load_resume_done_keys = RanobeUploaderApp._load_resume_done_keys

    def __init__(self, chapters, file_path="book.fb2"):
        self.settings = _SettingsStub()
        self._current_file_path = file_path
        self.url_input = _Field()
        self.logs = []
        self.chapters_list_widget = QListWidget()
        for ch in chapters:
            item = QListWidgetItem(str(ch))
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, ch)
            self.chapters_list_widget.addItem(item)

    def _append_log(self, level, message):
        self.logs.append((level, message))


def _chapters(count):
    return [ChapterData("1", float(i + 1), f"Глава {i + 1}", "<p>текст</p>") for i in range(count)]


class ResumeIdentityTests(unittest.TestCase):
    def test_partial_selection_resume_unchecks_actually_uploaded_chapters(self):
        """Сценарий из находки: пользователь снимает галочки с первых 49 глав из 100,
        грузит выбранные (позиции 0..50 в `selected`), после 3 успешных глав сеть падает.
        Возобновление обязано снять галочки именно с реально залитых глав 50-52,
        а не с первых трёх глав полного списка (1-3), которые и так были не выбраны."""
        chapters = _chapters(100)
        harness = _ResumeHarness(chapters)

        # Пользователь снимает галочки с первых 49 глав.
        for i in range(49):
            harness.chapters_list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)

        # Строим `selected` буквально так же, как _start_upload (main_window.py:2604-2608).
        selected = []
        for i in range(harness.chapters_list_widget.count()):
            item = harness.chapters_list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                selected.append(item.data(Qt.ItemDataRole.UserRole))
        self.assertEqual(len(selected), 51)
        self.assertEqual(selected[0].number, 50.0)

        # Имитация воркера: атрибут chapters_list — тот же объект selected.
        harness.worker = type("W", (), {})()
        harness.worker.chapters_list = selected

        # Цикл воркера (main_window.py-совместимый: workers.py:3346): успели загрузить
        # три первые из ВЫБРАННЫХ глав (реальные главы 50, 51, 52), затем сеть упала.
        for index in range(3):
            harness._on_chapter_done(index)

        # Возобновление: подтверждаем «Да» на диалоге.
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            harness._check_resume(harness._current_file_path, len(chapters))

        unchecked_numbers = {
            harness.chapters_list_widget.item(i).data(Qt.ItemDataRole.UserRole).number
            for i in range(harness.chapters_list_widget.count())
            if harness.chapters_list_widget.item(i).checkState() == Qt.CheckState.Unchecked
        }

        # Реально залитые главы (50, 51, 52) должны быть сняты...
        self.assertTrue({50.0, 51.0, 52.0}.issubset(unchecked_numbers))
        # ...а главы, которые пользователь и не выбирал (1, 2, 3), не должны
        # ошибочно считаться «уже отправленными» из-за рассинхрона индексов.
        # (Они и так были Unchecked изначально, но проверка ловит регресс,
        # если бы код продолжил использовать позиционный индекс.)
        for i in range(3):
            self.assertEqual(
                harness.chapters_list_widget.item(i).checkState(),
                Qt.CheckState.Unchecked,
            )

    def test_full_selection_resume_still_works_like_before(self):
        """При выборе ВСЕХ глав (обычный случай) резюме по-прежнему снимает галочки
        именно с уже отправленных глав."""
        chapters = _chapters(10)
        harness = _ResumeHarness(chapters)

        selected = list(chapters)
        harness.worker = type("W", (), {})()
        harness.worker.chapters_list = selected

        for index in range(4):
            harness._on_chapter_done(index)

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            harness._check_resume(harness._current_file_path, len(chapters))

        for i in range(4):
            self.assertEqual(
                harness.chapters_list_widget.item(i).checkState(),
                Qt.CheckState.Unchecked,
            )
        for i in range(4, 10):
            self.assertEqual(
                harness.chapters_list_widget.item(i).checkState(),
                Qt.CheckState.Checked,
            )

    def test_switching_to_another_book_does_not_leak_previous_books_done_keys(self):
        """Регрессия рецензента (major, main_window.py:2521): chapter_identity —
        это просто "том:номер" ("1:1", "1:2"…), он НЕ уникален между разными
        книгами/файлами. _save_resume_state раньше дописывал новый идентификатор
        в РАНЕЕ накопленное множество безусловно, не проверяя, что оно
        относится к тому же файлу/URL. Книга A залила 5 глав и оборвалась —
        сохранились ключи "1:1".."1:5". Открываем книгу B (другой файл, те же
        номера глав 1..20) и заливаем только её первую главу: resume не должен
        унаследовать чужие 5 ключей книги A — иначе при следующем открытии
        книги B будут молча сняты (и не отправлены) главы 2-5, реально ещё не
        залитые."""
        chapters_a = _chapters(5)
        harness_a = _ResumeHarness(chapters_a, file_path="bookA.fb2")
        harness_a.worker = type("W", (), {})()
        harness_a.worker.chapters_list = list(chapters_a)
        for index in range(5):
            harness_a._on_chapter_done(index)
        self.assertEqual(
            harness_a._load_resume_done_keys(),
            {"1:1", "1:2", "1:3", "1:4", "1:5"},
        )

        # Открываем книгу B в том же приложении (то есть с теми же QSettings) —
        # другой файл, номера глав тоже начинаются с 1.
        chapters_b = _chapters(20)
        harness_b = _ResumeHarness(chapters_b, file_path="bookB.fb2")
        harness_b.settings = harness_a.settings  # то же хранилище, как в реальном GUI
        harness_b.worker = type("W", (), {})()
        harness_b.worker.chapters_list = list(chapters_b)

        # Книга B: реально ушла только первая глава.
        harness_b._on_chapter_done(0)

        done_keys_b = harness_b._load_resume_done_keys()
        self.assertEqual(
            done_keys_b, {"1:1"},
            f"ключи книги A просочились в резюме книги B: {done_keys_b}",
        )

        # Возобновление книги B не должно ошибочно снять галочки с глав 2-5 —
        # они книгой B ещё не отправлялись.
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            harness_b._check_resume(harness_b._current_file_path, len(chapters_b))

        unchecked_numbers_b = {
            harness_b.chapters_list_widget.item(i).data(Qt.ItemDataRole.UserRole).number
            for i in range(harness_b.chapters_list_widget.count())
            if harness_b.chapters_list_widget.item(i).checkState() == Qt.CheckState.Unchecked
        }
        self.assertEqual(unchecked_numbers_b, {1.0})

    def test_declining_resume_clears_state(self):
        chapters = _chapters(5)
        harness = _ResumeHarness(chapters)
        harness.worker = type("W", (), {})()
        harness.worker.chapters_list = list(chapters)
        harness._on_chapter_done(0)

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            harness._check_resume(harness._current_file_path, len(chapters))

        self.assertEqual(harness._load_resume_done_keys(), set())


if __name__ == "__main__":
    unittest.main()
