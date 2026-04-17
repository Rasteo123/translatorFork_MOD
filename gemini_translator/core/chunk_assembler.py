import os
import threading
import zipfile
import re
import json
from collections import defaultdict, Counter
from PyQt6 import QtWidgets
from PyQt6 import QtCore
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot
from ..api import config as api_config
from ..utils.text import prettify_html, process_body_tag
class ChunkAssembler(QObject):
    """
    Отслеживает и собирает переведенные чанки в финальные файлы глав.
    Класс является потокобезопасным.
    """
    
    def __init__(self, output_folder, project_manager=None, settings=None):
        super().__init__()
        self.bus = QtWidgets.QApplication.instance().event_bus
        self.bus.event_posted.connect(self.on_event)
        
        if settings:
            self.settings = settings
        
        self.output_folder = output_folder
        self.project_manager = project_manager
        
        # self.assembly_bay и self.lock больше не нужны
        self.session_id = None


    @pyqtSlot(dict)
    def on_event(self, event: dict):
        """Принимает события из общей шины."""
        event_name = event.get('event')
        
        if event_name in ['task_state_changed', 'session_started']:
            if self.session_id is None and event.get('session_id'):
                self.session_id = event.get('session_id')
            # Запускаем проверку асинхронно, чтобы не блокировать поток событий
            QtCore.QTimer.singleShot(0, self.run_final_assembly_check)

    def _post_event(self, name: str, data: dict = None):
        event = {
            'event': name, 'source': 'ChunkAssembler', 
            'session_id': self.session_id, 'data': data or {}
        }
        self.bus.event_posted.emit(event)

    def _assemble_chapter_from_db(self, task_ids: list, original_chapter_path: str):
        """
        Атомарно извлекает результаты чанков из БД, удаляет их, а затем собирает главу.
        Версия 3.0: Использует ОДНУ транзакцию для всех операций с БД.
        """
        app = QtWidgets.QApplication.instance()
        if not hasattr(app, 'task_manager'): return

        try:
            placeholders = ','.join('?' for _ in task_ids)
            
            # --- НАЧАЛО ЕДИНОЙ АТОМАРНОЙ ОПЕРАЦИИ ---
            with app.task_manager._get_write_conn() as conn:
                # 1. Проверяем наличие всех результатов
                cursor = conn.execute(f"SELECT task_id, translated_content, provider_id FROM chunk_results WHERE task_id IN ({placeholders})", task_ids)
                results = cursor.fetchall()

                if len(results) != len(task_ids):
                    # Если результатов не хватает, ничего не делаем. Транзакция просто завершится.
                    print(f"[ASSEMBLER_RACE_CONDITION] Сборка для '{os.path.basename(original_chapter_path)}' отменена: другой поток уже забрал эти чанки.")
                    return

                # 2. Если все на месте, удаляем их, чтобы никто больше не смог их забрать
                conn.execute(f"DELETE FROM chunk_results WHERE task_id IN ({placeholders})", task_ids)

                # 3. Получаем все необходимые payload'ы в этой же транзакции
                cursor = conn.execute(f"SELECT task_id, payload FROM tasks WHERE task_id IN ({placeholders})", task_ids)
                chunk_infos_rows = cursor.fetchall()
                if not chunk_infos_rows:
                     raise RuntimeError(f"Не удалось найти payload'ы для чанков главы {original_chapter_path}")
            # --- КОНЕЦ ЕДИНОЙ АТОМАРНОЙ ОПЕРАЦИИ. conn.commit() вызван автоматически ---

            # --- Теперь мы эксклюзивно владеем данными и можем спокойно работать вне транзакции ---
            results_map = {row['task_id']: row['translated_content'] for row in results}
            chunk_infos = [{'task_id': row['task_id'], 'payload': json.loads(row['payload'])} for row in chunk_infos_rows]
            
            first_payload = chunk_infos[0]['payload']
            epub_path, total_chunks, prefix, suffix = first_payload[1], first_payload[5], first_payload[6], first_payload[7]
            
            self._post_event('log_message', {'message': f"[ASSEMBLER] Комплект из {total_chunks} чанков для '{os.path.basename(original_chapter_path)}' захвачен для сборки…"})

            provider_id = Counter(row['provider_id'] for row in results).most_common(1)[0][0] if results else 'gemini'
            
            sorted_chunks = [process_body_tag(results_map[info['task_id']], return_parts=False, body_content_only=True) for info in sorted(chunk_infos, key=lambda x: x['payload'][4])]
            
            full_content = "".join(sorted_chunks)
            final_html = prefix + full_content + suffix
            if self.settings:
                if self.settings.get("use_prettify", False):
                    final_html = prettify_html(final_html)
            else:
                final_html = prettify_html(final_html)
            
            provider_config = api_config.api_providers().get(provider_id, {})
            file_suffix = provider_config.get('file_suffix', '_translated.html')

            new_filename = f"{os.path.splitext(os.path.basename(original_chapter_path))[0]}{file_suffix}"
            destination_dir = os.path.join(self.output_folder, os.path.dirname(original_chapter_path))
            os.makedirs(destination_dir, exist_ok=True)
            final_path = os.path.join(destination_dir, new_filename)
            with open(final_path, "w", encoding="utf-8") as f: f.write(final_html)

            if self.project_manager:
                relative_path = os.path.relpath(final_path, self.output_folder)
                self.project_manager.register_translation(original_chapter_path, file_suffix, relative_path)

            app.task_manager.replace_chunks_with_chapter(
                chunk_task_ids=task_ids, epub_path=epub_path, original_chapter_path=original_chapter_path
            )
            
            self._post_event('log_message', {'message': f"[ASSEMBLER] ✅ Глава '{os.path.basename(original_chapter_path)}' успешно собрана из комплекта."})
            self._post_event('assembly_finished', {'original_chapter_path': original_chapter_path, 'chunk_count': total_chunks})

        except Exception as e:
            self._post_event('log_message', {'message': f"[ASSEMBLER_ERROR] КРИТИЧЕСКАЯ ОШИБКА при сборке главы '{os.path.basename(original_chapter_path)}': {e}"})

    def run_final_assembly_check(self):
        """
        Находит в БД все успешные чанки и запускает сборку для КАЖДОГО
        найденного полного комплекта.
        """
        if not self.project_manager: return
        app = QtWidgets.QApplication.instance()
        if not hasattr(app, 'task_manager'): return

        with app.task_manager._get_read_only_conn() as conn: # Используем 'with conn' для автоматических транзакций при чтении
            cursor = conn.execute("SELECT task_id, payload FROM tasks WHERE status = 'completed' AND payload LIKE '%\"epub_chunk\"%'")
            completed_chunks = cursor.fetchall()
        
        if not completed_chunks: return

        chunks_by_chapter_and_index = defaultdict(lambda: defaultdict(list))
        for row in completed_chunks:
            try:
                payload = json.loads(row['payload'])
                if payload[0] == 'epub_chunk' and len(payload) >= 6:
                    chunks_by_chapter_and_index[payload[2]][payload[4]].append(
                        {'task_id': row['task_id'], 'payload': payload}
                    )
            except (json.JSONDecodeError, IndexError):
                continue
        
        for chapter_path, grouped_chunks in chunks_by_chapter_and_index.items():
            first_index_group = next(iter(grouped_chunks.values()), [])
            if not first_index_group: continue
            
            total_chunks_needed = first_index_group[0]['payload'][5]
            
            if not all(i in grouped_chunks for i in range(total_chunks_needed)):
                continue

            num_possible_assemblies = min(len(grouped_chunks[i]) for i in range(total_chunks_needed))

            for i in range(num_possible_assemblies):
                complete_set_of_infos = [grouped_chunks[idx][i] for idx in range(total_chunks_needed)]
                task_ids_for_assembly = [info['task_id'] for info in complete_set_of_infos]
                
                # Просто запускаем сборку, передавая ей ID задач и путь.
                # Атомарная операция внутри _assemble_chapter_from_db предотвратит двойную сборку.
                QtCore.QTimer.singleShot(0, lambda ids=task_ids_for_assembly, path=chapter_path: self._assemble_chapter_from_db(ids, path))
