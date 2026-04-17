# -*- coding: utf-8 -*-

import sys
import os
import zipfile  # <<< Добавлено для работы с EPUB
import html     # Для безопасной обработки ошибок

# --- ИМПОРТ ФУНКЦИИ ЛЕЧЕНИЯ ---
try:
    # Адаптируйте путь, если нужно, как и в прошлом файле
    from gemini_translator.utils.text import prettify_html
    IMPORT_SUCCESS = True
    
except ImportError as e:
    IMPORT_SUCCESS = False
    IMPORT_ERROR_MESSAGE = f"""
ОШИБКА ИМПОРТА prettify_html!
------------------------------------
{e}

Приложение запустится, но лечение работать не будет.
Проверьте пути в начале файла text_epub_doctor.py
"""
    def prettify_html(text):
        return text # Заглушка

# --- GUI ---

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QLabel, QPlainTextEdit, QPushButton, QSplitter,
    QFileDialog, QMessageBox, QProgressBar, QFrame, QHBoxLayout
)
from PyQt6.QtGui import QFont, QColor, QPalette
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from window_branding import install_window_title_branding

# Выносим тяжелую задачу в отдельный поток, чтобы окно не зависало
class EpubWorker(QThread):
    progress = pyqtSignal(int)
    log = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, input_path):
        super().__init__()
        self.input_path = input_path

    def run(self):
        folder = os.path.dirname(self.input_path)
        filename = os.path.basename(self.input_path)
        name, ext = os.path.splitext(filename)
        output_path = os.path.join(folder, f"{name}_prettified{ext}")

        self.log.emit(f"Начинаем операцию над: {filename}")
        self.log.emit("Вскрытие пациента (unzipping)...")

        files_processed = 0
        
        try:
            with zipfile.ZipFile(self.input_path, 'r') as zin:
                # Считаем файлы для прогресс-бара
                all_files = zin.infolist()
                total_files = len(all_files)
                
                with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zout:
                    for i, item in enumerate(all_files):
                        # Обновляем прогресс
                        percent = int((i / total_files) * 100)
                        self.progress.emit(percent)

                        raw_data = zin.read(item.filename)
                        
                        # Лечим только HTML-ткани
                        if item.filename.lower().endswith(('.html', '.xhtml', '.htm')):
                            try:
                                # Декодируем
                                text_content = raw_data.decode('utf-8')
                                
                                # === ХИРУРГИЧЕСКОЕ ВМЕШАТЕЛЬСТВО ===
                                healed_content = prettify_html(text_content)
                                # ===================================

                                # Записываем вылеченное
                                zout.writestr(item, healed_content.encode('utf-8'))
                                files_processed += 1
                                
                            except UnicodeDecodeError:
                                # Если кодировка не UTF-8, оставляем как есть
                                self.log.emit(f"⚠️ Пропущен (кодировка): {item.filename}")
                                zout.writestr(item, raw_data)
                            except Exception as e:
                                self.log.emit(f"❌ Ошибка в {item.filename}: {e}")
                                zout.writestr(item, raw_data)
                        else:
                            # Остальные органы (картинки, css, ncx) переносим как есть
                            zout.writestr(item, raw_data)

            self.progress.emit(100)
            result_msg = f"Готово! Обработано файлов: {files_processed}.\nСохранено в: {os.path.basename(output_path)}"
            self.finished.emit(True, result_msg)

        except Exception as e:
            self.finished.emit(False, str(e))


class TextEpubDoctor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Text & Epub Doctor (Хирургия текста)")
        self.setGeometry(100, 100, 1000, 850)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # --- БЛОК 1: Ручной тестер (Splitter) ---
        tester_label = QLabel("🔬 МИКРОСКОП (Тест на фрагменте)")
        tester_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        main_layout.addWidget(tester_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Левая панель
        self.input_text = QPlainTextEdit()
        self.input_text.setFont(QFont("Consolas", 10))
        self.input_text.setPlaceholderText('Вставьте грязный HTML сюда...')
        splitter.addWidget(self.input_text)

        # Правая панель
        self.output_text = QPlainTextEdit()
        self.output_text.setFont(QFont("Consolas", 10))
        self.output_text.setReadOnly(True)
        self.output_text.setPlaceholderText('Здесь будет чистый результат...')
        splitter.addWidget(self.output_text)
        
        splitter.setSizes([500, 500])
        main_layout.addWidget(splitter, stretch=1)

        # Кнопка теста
        self.test_btn = QPushButton("Проверить фрагмент (prettify_html)")
        self.test_btn.clicked.connect(self.process_single_text)
        main_layout.addWidget(self.test_btn)

        # --- РАЗДЕЛИТЕЛЬ ---
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        main_layout.addWidget(line)

        # --- БЛОК 2: EPUB Doctor ---
        epub_label = QLabel("📚 ОПЕРАЦИОННАЯ (Лечение всей книги)")
        epub_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        main_layout.addWidget(epub_label)

        epub_layout = QHBoxLayout()
        
        self.epub_btn = QPushButton("📁 Выбрать EPUB и начать лечение...")
        self.epub_btn.setFixedHeight(45)
        self.epub_btn.setStyleSheet("background-color: #d4edda; border: 1px solid #c3e6cb; border-radius: 5px;")
        self.epub_btn.clicked.connect(self.start_epub_surgery)
        
        epub_layout.addWidget(self.epub_btn)
        main_layout.addLayout(epub_layout)

        # Прогресс бар
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.hide()
        main_layout.addWidget(self.progress_bar)
        
        # Лог
        self.status_label = QLabel("Ожидание пациента...")
        self.status_label.setStyleSheet("color: gray;")
        main_layout.addWidget(self.status_label)

        # Проверка импорта
        if not IMPORT_SUCCESS:
            self.input_text.setPlainText(IMPORT_ERROR_MESSAGE)
            self.test_btn.setEnabled(False)
            self.test_btn.setText("Ошибка импорта (см. текст)")
            self.epub_btn.setEnabled(False)

    def process_single_text(self):
        source = self.input_text.toPlainText()
        if not source: return
        try:
            res = prettify_html(source)
            self.output_text.setPlainText(res)
        except Exception as e:
            self.output_text.setPlainText(f"Ошибка: {e}")

    def start_epub_surgery(self):
        fname, _ = QFileDialog.getOpenFileName(self, "Выберите EPUB", "", "EPUB Books (*.epub)")
        if not fname:
            return

        self.epub_btn.setEnabled(False)
        self.test_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.status_label.setText("Подготовка инструментов...")
        self.status_label.setStyleSheet("color: black;")

        # Запуск потока
        self.worker = EpubWorker(fname)
        self.worker.progress.connect(self.update_progress)
        self.worker.log.connect(self.update_status)
        self.worker.finished.connect(self.surgery_complete)
        self.worker.start()

    def update_progress(self, val):
        self.progress_bar.setValue(val)

    def update_status(self, text):
        self.status_label.setText(text)

    def surgery_complete(self, success, msg):
        self.epub_btn.setEnabled(True)
        self.test_btn.setEnabled(True)
        self.progress_bar.hide()
        
        if success:
            self.status_label.setText("Операция прошла успешно.")
            self.status_label.setStyleSheet("color: green; font-weight: bold;")
            QMessageBox.information(self, "Выписка", msg)
        else:
            self.status_label.setText("Пациент умер на столе (Ошибка).")
            self.status_label.setStyleSheet("color: red; font-weight: bold;")
            QMessageBox.critical(self, "Ошибка", f"Сбой операции:\n{msg}")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    install_window_title_branding(app)
    window = TextEpubDoctor()
    window.show()
    sys.exit(app.exec())
