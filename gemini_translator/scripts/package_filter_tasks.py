# file: gemini_translator/scripts/package_filter_tasks.py

import math
import itertools  # <<< ДОБАВЛЕН ИМПОРТ
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QSpinBox, QCheckBox, QDialogButtonBox, QMessageBox
)
from PyQt6.QtCore import Qt
from ..utils.epub_tools import extract_number_from_path
from ..ui.widgets.common_widgets import NoScrollSpinBox

class FilterPackagingDialog(QDialog):
    SAVE_CHAPTERS_KEY = "save_chapters"
    FILTER_REPACK_KEY = "filter_repack"
    CONTEXT_CHAPTERS_KEY = "context_chapters"

    """
    Диалог для умной подготовки отфильтрованных глав к повторному переводу
    путем их "разбавления" успешно переведенными главами для пакетной обработки.
    """
    def __init__(self, filtered_chapters, successful_chapters, recommended_size, epub_path, real_chapter_sizes, parent=None): # <-- ДОБАВЛЕН real_chapter_sizes
        super().__init__(parent)
        self.filtered_chapters = sorted(filtered_chapters, key=extract_number_from_path)
        self.successful_chapters = sorted(successful_chapters, key=extract_number_from_path)
        self.recommended_size = recommended_size
        self.epub_path = epub_path
        self.real_chapter_sizes = real_chapter_sizes # <-- ДОБАВЛЕНА ЭТА СТРОКА
        self.result = None

        self.setWindowTitle("Подготовка пакетов для 'Фильтр'")
        self.setMinimumWidth(500)
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)

        info_label = QLabel(
            "Эта утилита поможет сформировать новый список глав для пересборки задач. "
            "Основная цель — «спрятать» отфильтрованную главу внутри пакета с успешно переведенными главами, чтобы обойти защиту модели."
        )
        info_label.setWordWrap(True)
        main_layout.addWidget(info_label)

        settings_group = QGroupBox("Настройки пакетирования")
        grid_layout = QGridLayout(settings_group)

        grid_layout.addWidget(QLabel("Рекомендуемый размер задачи:"), 0, 0, 1, 2)
        size_info_label = QLabel(f"<b>{self.recommended_size:,}</b> символов")
        size_info_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        grid_layout.addWidget(size_info_label, 0, 2)

        grid_layout.addWidget(QLabel("Глав в одном пакете:"), 1, 0, 1, 2)
        self.chapters_per_batch_spin = NoScrollSpinBox()
        self.chapters_per_batch_spin.setRange(2, 50)
        self.chapters_per_batch_spin.setValue(3)
        self.chapters_per_batch_spin.setToolTip(
            "Сколько всего глав (одна отфильтрованная + остальные успешные)\n"
            "должно быть в каждом пакете."
        )
        grid_layout.addWidget(self.chapters_per_batch_spin, 1, 2)
        
        self.dilute_checkbox = QCheckBox("«Разбавить» в пакетах (рекомендуется)")
        self.dilute_checkbox.setChecked(True)
        self.dilute_checkbox.setToolTip(
            "Если включено, будут сформированы только пакеты 'проблемная + хорошие главы'.\n"
            "Лишние 'хорошие' главы будут убраны из списка задач для экономии времени.\n\n"
            "Если выключено, отфильтрованные главы будут РАВНОМЕРНО распределены между всеми\n"
            "успешными главами, сохраняя их естественный порядок. Итоговый список\n"
            "будет содержать ВСЕ главы (и успешные, и отфильтрованные)."
        )
        grid_layout.addWidget(self.dilute_checkbox, 2, 0, 1, 3)

        main_layout.addWidget(settings_group)
        
        buttons = QDialogButtonBox()
        form_list_btn = buttons.addButton("Сформировать список", QDialogButtonBox.ButtonRole.AcceptRole)
        self.only_filter_btn = buttons.addButton("Оставить только фильтр", QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)

        buttons.accepted.connect(self.process_and_accept)
        buttons.rejected.connect(self.reject)
        self.only_filter_btn.clicked.connect(self._process_only_filter_and_accept)
        
        main_layout.addWidget(buttons)

    def _make_filter_repack_metadata(self, save_chapters, all_chapters):
        save_list = [str(chapter) for chapter in save_chapters if chapter]
        save_set = set(save_list)
        context_list = [
            str(chapter)
            for chapter in all_chapters
            if chapter and str(chapter) not in save_set
        ]
        return {
            self.FILTER_REPACK_KEY: True,
            self.SAVE_CHAPTERS_KEY: save_list,
            self.CONTEXT_CHAPTERS_KEY: context_list,
        }

    def _payload_chapters(self, payload):
        if not payload:
            return []
        task_type = payload[0]
        if task_type in ("epub", "epub_chunk") and len(payload) > 2:
            return [payload[2]]
        if task_type == "epub_batch" and len(payload) > 2:
            return list(payload[2])
        return []

    def _with_filter_save_targets(self, payload, filtered_set):
        chapters = self._payload_chapters(payload)
        save_targets = [chapter for chapter in chapters if chapter in filtered_set]
        if not save_targets:
            return None

        task_type = payload[0]
        if task_type != "epub_batch":
            return payload

        payload_parts = list(payload)
        metadata = {}
        if len(payload_parts) > 3 and isinstance(payload_parts[3], dict):
            metadata.update(payload_parts[3])
        metadata.update(self._make_filter_repack_metadata(save_targets, chapters))

        if len(payload_parts) > 3 and isinstance(payload_parts[3], dict):
            payload_parts[3] = metadata
        else:
            payload_parts.append(metadata)
        return tuple(payload_parts)

    def _build_filter_repack_payloads(self, chapter_list):
        from gemini_translator.utils.glossary_tools import TaskPreparer

        settings = {
            "file_path": self.epub_path,
            "use_batching": True,
            "chunking": False,
            "task_size_limit": self.recommended_size,
        }
        preparer = TaskPreparer(settings, self.real_chapter_sizes)
        filtered_set = set(self.filtered_chapters)
        payloads = []
        for payload in preparer.prepare_tasks(chapter_list):
            marked_payload = self._with_filter_save_targets(payload, filtered_set)
            if marked_payload:
                payloads.append(marked_payload)
        return payloads

    def process_and_accept(self):
        try:
            self.result = self._calculate_new_chapter_list() # <-- УБРАНО .result_chapters
            if self.result is not None:
                self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Произошла ошибка при формировании списка: {e}")
    
    def _update_summary(self):
        """
        Выполняет симуляцию сборки задач и обновляет информационную панель.
        """
        # --- ШАГ 1: Симулируем получение результата ---
        # Мы не можем вызвать _calculate_new_chapter_list, так как он возвращает
        # разные типы данных. Вместо этого мы симулируем логику TaskPreparer.
        from gemini_translator.utils.glossary_tools import TaskPreparer

        result = self._calculate_new_chapter_list()
        if not result:
            self.total_tasks_label.setText("N/A")
            self.avg_chapters_label.setText("N/A")
            self.lonely_chapters_label.setText("N/A")
            return

        result_type = result.get('type')
        data = result.get('data')
        
        final_payloads = []
        if result_type == 'payloads':
            final_payloads = data
        elif result_type == 'chapters':
            # Симулируем работу TaskPreparer на списке глав
            settings = {
                'file_path': self.epub_path,
                'use_batching': True, # В этом диалоге мы всегда готовим пакеты
                'chunking': False,
                'task_size_limit': self.recommended_size
            }
            preparer = TaskPreparer(settings, self.real_chapter_sizes)
            final_payloads = preparer.prepare_tasks(data)

        # --- ШАГ 2: Анализируем симулированные задачи ---
        if not final_payloads:
            self.total_tasks_label.setText("0")
            self.avg_chapters_label.setText("0.0")
            self.lonely_chapters_label.setText("0")
            return

        total_tasks = len(final_payloads)
        total_chapters_in_batches = 0
        batch_count = 0
        lonely_count = 0
        
        filtered_set = set(self.filtered_chapters)

        for payload in final_payloads:
            task_type = payload[0]
            chapters_in_task = []
            
            if task_type == 'epub_batch':
                chapters_in_task = payload[2]
                batch_count += 1
                total_chapters_in_batches += len(chapters_in_task)
            elif task_type == 'epub':
                chapters_in_task = [payload[2]]
            
            # Проверяем, не "одинок" ли наш пациент
            task_chapters_set = set(chapters_in_task)
            filtered_in_task = task_chapters_set.intersection(filtered_set)
            
            if len(filtered_in_task) > 0 and len(task_chapters_set) == 1:
                lonely_count += len(filtered_in_task)

        # --- ШАГ 3: Обновляем UI ---
        self.total_tasks_label.setText(f"<b>{total_tasks}</b>")
        
        avg_chapters = (total_chapters_in_batches / batch_count) if batch_count > 0 else 0
        self.avg_chapters_label.setText(f"~{avg_chapters:.1f}")

        if lonely_count > 0:
            self.lonely_chapters_label.setText(f"<b style='color: red;'>{lonely_count}</b>")
        else:
            self.lonely_chapters_label.setText(f"<b style='color: green;'>{lonely_count}</b>")
    
    def _process_only_filter_and_accept(self):
        # Возвращаем просто список глав, TaskPreparer справится
        self.result = {'type': 'chapters', 'data': self.filtered_chapters}
        self.accept()

    def _calculate_new_chapter_list(self):
        chapters_per_batch = self.chapters_per_batch_spin.value()
        is_dilute_mode = self.dilute_checkbox.isChecked()

        if not self.filtered_chapters:
            QMessageBox.information(self, "Нет данных", "Нет отфильтрованных глав для обработки.")
            return None

        if is_dilute_mode:
            # РЕЖИМ "РАЗБАВИТЬ": Формируем готовые пейлоады задач.
            final_payloads = []
            
            if not self.successful_chapters:
                QMessageBox.warning(self, "Предупреждение", "Нет успешно переведенных глав для 'разбавления'. Возвращаем только отфильтрованные главы.")
                return {'type': 'chapters', 'data': self.filtered_chapters}

            successful_cycler = itertools.cycle(self.successful_chapters)
            
            for filtered_chapter in self.filtered_chapters:
                batch = [filtered_chapter]
                num_needed_to_pad = chapters_per_batch - 1
                
                if num_needed_to_pad > 0:
                    for _ in range(num_needed_to_pad):
                        batch.append(next(successful_cycler))
                
                # Создаем готовый пейлоад, который TaskPreparer не будет трогать
                payload = (
                    'epub_batch',
                    self.epub_path,
                    tuple(batch),
                    self._make_filter_repack_metadata([filtered_chapter], batch),
                )
                final_payloads.append(payload)
            
            # Возвращаем специальный объект, чтобы вызывающий код понял, что это готовые задачи
            return {'type': 'payloads', 'data': final_payloads}
            
        else:
            # РЕЖИМ "ВНЕДРИТЬ": Возвращаем плоский список глав, как и раньше.
            # TaskPreparer разберет их по своему усмотрению.
            if not self.successful_chapters:
                return {'type': 'chapters', 'data': self.filtered_chapters}

            num_successful = len(self.successful_chapters)
            num_filtered = len(self.filtered_chapters)
            
            interval = math.ceil(num_successful / (num_filtered + 1))
            if interval == 0: interval = 1

            chapter_list = []
            successful_iter = iter(self.successful_chapters)
            filtered_iter = iter(self.filtered_chapters)

            for _ in range(num_filtered):
                for _ in range(int(interval)):
                    try:
                        chapter_list.append(next(successful_iter))
                    except StopIteration:
                        break 
                try:
                    chapter_list.append(next(filtered_iter))
                except StopIteration:
                    break
            
            chapter_list.extend(successful_iter)

            final_payloads = self._build_filter_repack_payloads(chapter_list)
            if not final_payloads:
                return {'type': 'chapters', 'data': self.filtered_chapters}

            return {'type': 'payloads', 'data': final_payloads}

    def get_result(self):
        return self.result
