# gemini_translator/utils/project_migrator.py

import os
import re
import shutil
import zipfile
from .project_manager import TranslationProjectManager
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtCore import QThread, pyqtSignal, QMutex, QWaitCondition, QMetaObject, Qt, QObject

# --- ШАГ 1: Создаем маленький QObject для управления диалогом ---
# Это позволит нам не смешивать логику потока и логику UI
class QuestionHandler(QObject):
    # Сигнал, который вернет ответ обратно в SyncThread
    response_ready = pyqtSignal(bool)

    def __init__(self, parent_widget, title, text):
        super().__init__()
        self.parent = parent_widget
        self.title = title
        self.text = text

    def ask(self):
        """Этот метод будет вызван в GUI-потоке."""
        msg_box = QMessageBox(self.parent)
        msg_box.setWindowTitle(self.title)
        msg_box.setIcon(QMessageBox.Icon.Question)
        msg_box.setText(self.text)
        yes_button = msg_box.addButton("Да", QMessageBox.ButtonRole.YesRole)
        msg_box.addButton("Нет", QMessageBox.ButtonRole.NoRole)
        
        # Подключаем сигнал к нашему обработчику
        # finished - это сигнал, который QMessageBox испускает после закрытия
        msg_box.finished.connect(lambda result: self._on_dialog_finished(msg_box, yes_button))
        
        # Используем НЕБЛОКИРУЮЩИЙ show()
        msg_box.show()

    def _on_dialog_finished(self, msg_box, yes_button):
        """Обрабатывает результат и отправляет сигнал с ответом."""
        response = (msg_box.clickedButton() == yes_button)
        self.response_ready.emit(response)
        # Важно: удаляем себя, чтобы не было утечек памяти
        self.deleteLater()
        
class SyncThread(QThread):
    finished_sync = pyqtSignal(bool, str)
    # Этот сигнал теперь будет передавать объект QuestionHandler
    _ask_question_in_ui_thread = pyqtSignal(QObject)

    def __init__(self, migrator_instance, parent_widget=None):
        super().__init__()
        self.migrator = migrator_instance
        self.parent = parent_widget
        self.user_response = None
        self.mutex = QMutex()
        self.condition = QWaitCondition()
        
        # Слот теперь просто вызывает метод ask() у полученного объекта
        self._ask_question_in_ui_thread.connect(lambda handler: handler.ask())

    def run(self):
        try:
            result, message = self.migrator.ensure_project_is_modern_and_synced(self)
            self.finished_sync.emit(result, message)
        except Exception as e:
            self.finished_sync.emit(False, f"Критическая ошибка: {e}")
    
    def ask_cleanup_ghosts(self, ghost_entries):
        text = (f"Найдено {len(ghost_entries)} записей в карте проекта, которых больше нет в EPUB файле (главы-призраки).\n"
                "Это могло произойти, если вы заменили исходный EPUB на новую версию.\n\n"
                "Рекомендуется очистить эти устаревшие записи. Удалить их?")
        return self._ask_question("Синхронизация (главы-призраки)", text)
        
    def _ask_question(self, title, text):
        # 1. Создаем обработчик
        handler = QuestionHandler(self.parent, title, text)
        
        # 2. Подключаем его сигнал к нашему слоту, который разбудит поток
        handler.response_ready.connect(self._set_user_response)
        
        # 3. Отправляем сам объект обработчика в GUI-поток
        self._ask_question_in_ui_thread.emit(handler)
        
        # 4. Блокируемся и ждем, как и раньше
        self.mutex.lock()
        self.condition.wait(self.mutex)
        response = self.user_response
        self.mutex.unlock()
        return response

    def _set_user_response(self, response: bool):
        """Этот слот вызывается по сигналу от QuestionHandler."""
        self.mutex.lock()
        self.user_response = response
        self.condition.wakeAll()
        self.mutex.unlock()
    
    def ask_cleanup(self, dead_entries):
        return self._ask_question("Синхронизация", f"Найдено {len(dead_entries)} отсутствующих файлов. Очистить записи?")

    def ask_add_untracked(self, untracked_files):
        return self._ask_question("Синхронизация", f"Найдено {len(untracked_files)} незарегистрированных файлов. Добавить их в карту?")

class ProjectMigrator:
    """
    Инструмент для синхронизации и поддержания целостности проекта.
    Версия 3.0: Без поддержки устаревшей "плоской" структуры.
    """
    def __init__(self, project_folder, original_epub_path, project_manager):
        self.project_folder = project_folder
        self.original_epub_path = original_epub_path
        self.project_manager = project_manager

    def ensure_project_is_modern_and_synced(self, communicator: 'SyncThread'):
        """
        Главный "оркестратор". Использует коммуникатор для взаимодействия с UI.
        Версия 2.0: Добавлена проверка на "призрачные" записи.
        """
        map_file_path = self.project_manager.map_file_path
        
        if os.path.exists(map_file_path):
            # --- Этап 1: Проверка "мертвых" записей (файлы удалены с диска) ---
            dead_entries = self.project_manager.validate_map_with_filesystem()
            if dead_entries:
                if communicator.ask_cleanup(dead_entries):
                    self.project_manager.cleanup_dead_entries(dead_entries)
            
            # --- Этап 2 (НОВЫЙ): Проверка "призрачных" записей (главы удалены из EPUB) ---
            epub_structure = self.project_manager._build_epub_structure_map(self.original_epub_path)
            if epub_structure: # Проверяем, только если EPUB успешно прочитан
                ghost_entries = [
                    original for original in self.project_manager.get_all_originals()
                    if original not in epub_structure
                ]
                if ghost_entries:
                    if communicator.ask_cleanup_ghosts(ghost_entries):
                        with self.project_manager.lock:
                            current_data = self.project_manager._load_unsafe()
                            for original_path in ghost_entries:
                                if original_path in current_data:
                                    del current_data[original_path]
                            self.project_manager._save_unsafe(current_data)

            # --- Этап 3: Проверка "беспризорных" файлов (файлы есть, но не в карте) ---
            untracked_files = self.project_manager.find_untracked_files(self.original_epub_path)
            if untracked_files:
                if communicator.ask_add_untracked(untracked_files):
                    self.project_manager.register_multiple_translations(untracked_files)
            
            return True, "Сверка проекта завершена."

        rebuilt_count = self.rebuild_map_from_structure()
        if rebuilt_count > 0:
            return True, f"Карта проекта восстановлена из {rebuilt_count} файлов."

        try:
            self.project_manager.save()
            return True, "Новый файл проекта 'translation_map.json' успешно создан."
        except Exception as e:
            return False, f"Не удалось создать файл проекта: {e}"

    def sync_project_with_ui(self, ui_parent=None):
        """
        Проверяет проект на "мертвые" и "беспризорные" записи и предлагает их исправить.
        Возвращает True, если были внесены изменения.
        """
        if not self.project_manager:
            return False

        changes_made = False
        
        dead_entries = self.project_manager.validate_map_with_filesystem()
        if dead_entries:
            msg_box = QMessageBox(ui_parent)
            msg_box.setWindowTitle("Синхронизация проекта")
            msg_box.setIcon(QMessageBox.Icon.Question)
            msg_box.setText(f"Найдено {len(dead_entries)} отсутствующих файлов в карте проекта. Очистить эти записи?")
            yes_button = msg_box.addButton("Да, очистить", QMessageBox.ButtonRole.YesRole)
            no_button = msg_box.addButton("Нет", QMessageBox.ButtonRole.NoRole)
            msg_box.exec()
            if msg_box.clickedButton() == yes_button:
                self.project_manager.cleanup_dead_entries(dead_entries)
                changes_made = True

        untracked_files = self.project_manager.find_untracked_files(self.original_epub_path)
        if untracked_files:
            msg_box = QMessageBox(ui_parent)
            msg_box.setWindowTitle("Синхронизация проекта")
            msg_box.setIcon(QMessageBox.Icon.Question)
            msg_box.setText(f"Найдено {len(untracked_files)} незарегистрированных файлов перевода. Добавить их в карту проекта?")
            yes_button = msg_box.addButton("Да, добавить", QMessageBox.ButtonRole.YesRole)
            no_button = msg_box.addButton("Нет", QMessageBox.ButtonRole.NoRole)
            msg_box.exec()
            if msg_box.clickedButton() == yes_button:
                self.project_manager.register_multiple_translations(untracked_files)
                changes_made = True
        
        return changes_made

    def rebuild_map_from_structure(self):
        """
        Сканирует проект и воссоздает 'translation_map.json' с нуля.
        """
        untracked_files = self.project_manager.find_untracked_files(self.original_epub_path)
        if untracked_files:
            self.project_manager.register_multiple_translations(untracked_files)
            return len(untracked_files)
        return 0