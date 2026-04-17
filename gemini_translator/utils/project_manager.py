# gemini_translator/utils/project_manager.py

import os
import json
# --- ИЗМЕНЕНИЕ 1: Импортируем threading целиком ---
import threading

try:
    # Попытка абсолютного импорта от корня (предпочтительно)
    import os_patch
    PatientLock = os_patch.PatientLock
except (ImportError, AttributeError):
    # Запасной вариант, если PatientLock не найден, используем RLock как менее строгое, но безопасное решение
    print("[ProjectManager WARN] PatientLock не найден. Используется стандартный RLock.")
    from threading import RLock as PatientLock

import zipfile
import re
from ..api import config as api_config

class TranslationProjectManager:
    """
    Управляет индексным файлом 'translation_map.json' для проекта.
    Версия 8.2: Использует RLock для предотвращения deadlock'ов.
    """
    def __init__(self, project_folder):
        self.project_folder = project_folder
        self.map_file_path = os.path.join(project_folder, 'translation_map.json')
        self.glossary_map_path = os.path.join(project_folder, 'glossary_generation_map.json')
        self.user_problem_terms_path = os.path.join(project_folder, 'user_problem_terms.json')
        self.validation_cache_path = os.path.join(project_folder, 'validation_analysis_cache.json')
        self.term_frequency_cache_path = os.path.join(project_folder, 'term_frequency_cache.json')
        self.lock = PatientLock()
        self.data = self._load()

    def _load(self):
        with self.lock:
            if os.path.exists(self.map_file_path):
                try:
                    with open(self.map_file_path, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except (json.JSONDecodeError, IOError): return {}
            return {}

    def _save_internal(self, data_to_save):
        with self.lock:
            os.makedirs(os.path.dirname(self.map_file_path), exist_ok=True)
            with open(self.map_file_path, 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2, sort_keys=True)
            self.data = data_to_save
    
    def _save_unsafe(self, data_to_save):
        """Внутренний метод, вызывается, когда блокировка уже установлена."""
        os.makedirs(os.path.dirname(self.map_file_path), exist_ok=True)
        with open(self.map_file_path, 'w', encoding='utf-8') as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=2, sort_keys=True)
        self.data = data_to_save
    
    def _load_unsafe(self):
        """Внутренний метод, вызывается, когда блокировка уже установлена или не нужна."""
        if os.path.exists(self.map_file_path):
            try:
                with open(self.map_file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if not content: return {} # Обработка пустого файла
                    
                    data_to_load = json.loads(content)
                    self.data = data_to_load
                    return data_to_load

            except (json.JSONDecodeError, IOError):
                print(f"[WARN] Не удалось прочитать или распарсить файл карты проекта: {self.map_file_path}. Файл может быть поврежден.")
                return {}
        return {}

    def register_translation(self, original_internal_path, version_suffix, translated_relative_path):
        """Атомарно регистрирует одну версию перевода."""
        with self.lock:
            current_data = self._load_unsafe() # 1. Читаем свежие данные
            
            path1 = original_internal_path.replace('\\', '/')
            path2 = translated_relative_path.replace('\\', '/')
            if path1 not in current_data:
                current_data[path1] = {}
            current_data[path1][version_suffix] = path2
            
            self._save_unsafe(current_data) # 2. Сохраняем измененные данные

    def load_version_map(self):
        """
        Загружает карту версий терминов (glossary_versions.json).
        Возвращает dict: { 'Original Term': [ {scope: [], override: {}}, ... ] }
        """
        version_file = os.path.join(self.project_folder, 'glossary_versions.json')
        with self.lock:
            if os.path.exists(version_file):
                try:
                    with open(version_file, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except Exception:
                    return {}
            return {}

    def save_version_map(self, version_map):
        """Сохраняет карту версий."""
        version_file = os.path.join(self.project_folder, 'glossary_versions.json')
        with self.lock:
            try:
                with open(version_file, 'w', encoding='utf-8') as f:
                    json.dump(version_map, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"[ProjectManager] Ошибка сохранения версий: {e}")
    
    def remove_translation(self, original_internal_path, version_suffix):
        """Атомарно удаляет одну версию перевода."""
        with self.lock:
            current_data = self._load_unsafe()
            
            path1 = original_internal_path.replace('\\', '/')
            if path1 in current_data and version_suffix in current_data[path1]:
                del current_data[path1][version_suffix]
                if not current_data[path1]:
                    del current_data[path1]
            
            self._save_unsafe(current_data)

    def cleanup_dead_entries(self, dead_entries: list):
        """Атомарно удаляет СПИСОК "мертвых" записей за одну операцию."""
        with self.lock:
            current_data = self._load_unsafe()
            for original_path, suffix, _ in dead_entries:
                original_path_norm = original_path.replace('\\', '/')
                if original_path_norm in current_data and suffix in current_data[original_path_norm]:
                    del current_data[original_path_norm][suffix]
                    if not current_data[original_path_norm]:
                        del current_data[original_path_norm]
            self._save_unsafe(current_data)

    def find_stale_translations(self, original_epub_path):
        """
        Находит файлы переводов, привязанные к главам, которых уже нет
        в текущем исходном EPUB.
        """
        if not original_epub_path or not os.path.exists(original_epub_path):
            return []

        epub_structure = self._build_epub_structure_map(original_epub_path)
        if not epub_structure:
            return []

        stale_entries = []
        with self.lock:
            current_data = self.data.copy()

        for original_path, versions in current_data.items():
            original_path_norm = original_path.replace('\\', '/')
            if original_path_norm in epub_structure:
                continue

            for suffix, rel_path in versions.items():
                rel_path_norm = rel_path.replace('\\', '/')
                full_path = os.path.join(
                    self.project_folder,
                    rel_path_norm.replace('/', os.sep)
                )
                stale_entries.append({
                    'original_path': original_path_norm,
                    'suffix': suffix,
                    'rel_path': rel_path_norm,
                    'full_path': full_path,
                })

        return stale_entries

    def cleanup_stale_translations(self, stale_entries):
        """
        Удаляет устаревшие файлы переводов с диска и очищает карту проекта
        только для тех записей, которые удалось безопасно убрать.
        """
        if not stale_entries:
            return {"removed": 0, "failed": []}

        removable_entries = []
        failed = []

        for entry in stale_entries:
            full_path = entry['full_path']
            can_remove_entry = True

            if os.path.exists(full_path):
                try:
                    os.remove(full_path)
                    self._cleanup_empty_parent_dirs(os.path.dirname(full_path))
                except OSError as exc:
                    can_remove_entry = False
                    failed.append((full_path, str(exc)))

            if can_remove_entry:
                removable_entries.append(entry)

        if removable_entries:
            with self.lock:
                current_data = self._load_unsafe()
                for entry in removable_entries:
                    original_path = entry['original_path']
                    suffix = entry['suffix']
                    if original_path in current_data and suffix in current_data[original_path]:
                        del current_data[original_path][suffix]
                        if not current_data[original_path]:
                            del current_data[original_path]
                self._save_unsafe(current_data)

        return {"removed": len(removable_entries), "failed": failed}

    def cleanup_translations_in_subtree(self, relative_folder):
        """
        Удаляет все файлы переводов внутри указанной подпапки проекта и
        очищает карту проекта от записей, которые на них ссылаются.
        """
        if not relative_folder:
            return {"removed_files": 0, "removed_entries": 0, "failed": []}

        subtree = relative_folder.replace('\\', '/').strip('/')
        subtree_prefix = f"{subtree}/"
        subtree_abs = os.path.abspath(
            os.path.join(self.project_folder, subtree.replace('/', os.sep))
        )
        project_root = os.path.abspath(self.project_folder)

        try:
            is_inside_project = os.path.commonpath([project_root, subtree_abs]) == project_root
        except ValueError:
            is_inside_project = False

        if not is_inside_project:
            return {
                "removed_files": 0,
                "removed_entries": 0,
                "failed": [(subtree_abs, "path escapes project root")],
            }

        tracked_entries = []
        with self.lock:
            current_data = self._load_unsafe()
            for original_path, versions in current_data.items():
                for suffix, rel_path in versions.items():
                    rel_path_norm = rel_path.replace('\\', '/')
                    if rel_path_norm == subtree or rel_path_norm.startswith(subtree_prefix):
                        tracked_entries.append({
                            'original_path': original_path,
                            'suffix': suffix,
                            'full_path': os.path.join(
                                self.project_folder,
                                rel_path_norm.replace('/', os.sep)
                            ),
                        })

        removable_entries = []
        failed = []
        removed_files = 0
        tracked_paths = {entry['full_path'] for entry in tracked_entries}

        for entry in tracked_entries:
            full_path = entry['full_path']
            can_remove_entry = True

            if os.path.exists(full_path):
                try:
                    os.remove(full_path)
                    removed_files += 1
                    self._cleanup_empty_parent_dirs(os.path.dirname(full_path))
                except OSError as exc:
                    can_remove_entry = False
                    failed.append((full_path, str(exc)))

            if can_remove_entry:
                removable_entries.append(entry)

        if os.path.isdir(subtree_abs):
            for root, dirs, files in os.walk(subtree_abs, topdown=False):
                for filename in files:
                    full_path = os.path.join(root, filename)
                    if full_path in tracked_paths and not os.path.exists(full_path):
                        continue
                    try:
                        os.remove(full_path)
                        removed_files += 1
                    except OSError as exc:
                        failed.append((full_path, str(exc)))

                for dirname in dirs:
                    dir_path = os.path.join(root, dirname)
                    try:
                        os.rmdir(dir_path)
                    except OSError:
                        pass

            try:
                os.rmdir(subtree_abs)
            except OSError:
                pass

            self._cleanup_empty_parent_dirs(os.path.dirname(subtree_abs))

        removed_entries = 0
        if removable_entries:
            with self.lock:
                current_data = self._load_unsafe()
                for entry in removable_entries:
                    original_path = entry['original_path']
                    suffix = entry['suffix']
                    if original_path in current_data and suffix in current_data[original_path]:
                        del current_data[original_path][suffix]
                        removed_entries += 1
                        if not current_data[original_path]:
                            del current_data[original_path]
                self._save_unsafe(current_data)

        return {
            "removed_files": removed_files,
            "removed_entries": removed_entries,
            "failed": failed,
        }

    def _cleanup_empty_parent_dirs(self, start_dir):
        """Удаляет пустые родительские папки внутри каталога проекта."""
        project_root = os.path.abspath(self.project_folder)
        current_dir = os.path.abspath(start_dir)

        while current_dir.startswith(project_root) and current_dir != project_root:
            try:
                os.rmdir(current_dir)
            except OSError:
                break
            current_dir = os.path.dirname(current_dir)

    def register_multiple_translations(self, entries_to_add: list):
        """Атомарно добавляет СПИСОК записей за одну операцию."""
        with self.lock:
            current_data = self._load_unsafe()
            for original_path, suffix, rel_path in entries_to_add:
                original_path = original_path.replace('\\', '/')
                rel_path = rel_path.replace('\\', '/')
                if original_path not in current_data:
                    current_data[original_path] = {}
                current_data[original_path][suffix] = rel_path
            self._save_unsafe(current_data)

    # --- ПУБЛИЧНЫЕ ПОТОКОБЕЗОПАСНЫЕ МЕТОДЫ ---
    
    def get_full_map(self):
        """
        Возвращает полную карту переводов.
        Если карта еще не загружена, загружает ее.
        Потокобезопасно.
        """
        with self.lock:
            # self.data уже содержит загруженную карту.
            # Просто возвращаем ее копию, чтобы избежать случайных изменений извне.
            return self.data.copy()
    
    def reload_data_from_disk(self):
        """Принудительно и безопасно перезагружает данные из файла."""
        with self.lock:
            self.data = self._load_unsafe()
        print("[PROJECT_MAP] Данные карты проекта были принудительно перезагружены с диска.")

    def save(self):
        """Безопасно сохраняет текущее состояние данных из памяти на диск."""
        with self.lock:
            self._save_unsafe(self.data)

    def load_validation_cache(self):
        with self.lock:
            if not os.path.exists(self.validation_cache_path):
                return {}

            try:
                with open(self.validation_cache_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}

    def save_validation_cache(self, payload):
        with self.lock:
            os.makedirs(os.path.dirname(self.validation_cache_path), exist_ok=True)
            with open(self.validation_cache_path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)

    def load_term_frequency_cache(self):
        with self.lock:
            if not os.path.exists(self.term_frequency_cache_path):
                return {}

            try:
                with open(self.term_frequency_cache_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}

    def save_term_frequency_cache(self, payload):
        with self.lock:
            os.makedirs(os.path.dirname(self.term_frequency_cache_path), exist_ok=True)
            with open(self.term_frequency_cache_path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)

    def get_all_originals(self):
        from .epub_tools import extract_number_from_path
        with self.lock: return sorted(list(self.data.keys()), key=extract_number_from_path)

    def get_versions_for_original(self, original_internal_path: str):
        with self.lock:
            path1 = original_internal_path.replace('\\', '/')
            return self.data.get(path1, {}).copy()

    def validate_map_with_filesystem(self):
        dead_entries = []
        with self.lock:
            data_to_check = self.data
            for original_path, versions in data_to_check.items():
                for suffix, rel_path in versions.items():
                    full_path = os.path.join(self.project_folder, rel_path)
                    if not os.path.exists(full_path):
                        dead_entries.append((original_path, suffix, rel_path))
        return dead_entries

    def _build_epub_structure_map(self, epub_path):
        """Читает EPUB и возвращает set полных внутренних путей ко всем HTML-файлам."""
        structure_set = set()
        try:
            with zipfile.ZipFile(open(epub_path, 'rb'), 'r') as epub_zip:
                for name in epub_zip.namelist():
                    if name.lower().endswith(('.html', '.xhtml', '.htm')) and not name.startswith('__MACOSX'):
                        structure_set.add(name.replace('\\', '/'))
        except Exception as e:
            print(f"[ERROR] Не удалось прочитать EPUB для построения карты: {e}")
        return structure_set

    # --- ИЗМЕНЕНИЕ: _build_filesystem_map теперь возвращает простой set ---
    def _build_filesystem_map(self):
        """
        Сканирует файловую систему проекта и возвращает простой set
        относительных путей ко всем найденным файлам-переводам.
        """
        from ..api import config as api_config
        all_possible_suffixes = api_config.all_translated_suffixes() + ['_validated.html']
        files_set = set()

        for root, _, files in os.walk(self.project_folder):
            for filename in files:
                if any(filename.endswith(s) for s in all_possible_suffixes):
                    relative_path = os.path.relpath(os.path.join(root, filename), self.project_folder)
                    files_set.add(relative_path.replace('\\', '/'))
        
        return files_set

    # --- ИЗМЕНЕНИЕ: Вся логика сопоставления теперь в find_untracked_files ---
    def find_untracked_files(self, original_epub_path):
        """
        Находит незарегистрированные файлы, сравнивая set'ы файлов из EPUB и ФС.
        Версия 14.0: Симметричные сборщики данных.
        """
        if not os.path.exists(original_epub_path): return []
        
        from ..api import config as api_config
        all_possible_suffixes = api_config.all_translated_suffixes() + ['_validated.html']
        untracked_files_list = []

        # 1. Получаем два простых set'а
        epub_files = self._build_epub_structure_map(original_epub_path)
        fs_files = self._build_filesystem_map()

        with self.lock:
            current_project_map = self.data

        # 2. Создаем карту всех зарегистрированных файлов для быстрой проверки
        registered_files = set()
        for versions in current_project_map.values():
            for rel_path in versions.values():
                registered_files.add(rel_path.replace('\\', '/'))
        
        # 3. Находим файлы, которые есть на диске, но не зарегистрированы
        unregistered_files_on_disk = fs_files - registered_files

        if not unregistered_files_on_disk:
            return [] # Оптимизация: если все файлы на диске учтены, дальше не идем

        # 4. Теперь самая сложная часть: для каждого "беспризорника" найти его "родителя" в EPUB
        # Создаем удобную структуру для поиска оригиналов
        # {'OEBPS/Text/chapter1': 'OEBPS/Text/chapter1.xhtml', …}
        epub_base_map = {os.path.splitext(f)[0]: f for f in epub_files}

        for file_path in unregistered_files_on_disk:
            # `file_path` - это, например, "OEBPS/Text/chapter1_translated.html"
            
            # Ищем, каким суффиксом он заканчивается
            file_suffix = next((s for s in all_possible_suffixes if file_path.endswith(s)), None)
            if not file_suffix:
                continue # На всякий случай

            # Получаем базовый путь без суффикса
            # "OEBPS/Text/chapter1_translated.html" -> "OEBPS/Text/chapter1"
            base_path = file_path[:-len(file_suffix)]
            
            # Ищем этот базовый путь в нашей карте оригиналов
            original_internal_path = epub_base_map.get(base_path)

            if original_internal_path:
                # Нашли!
                untracked_files_list.append((original_internal_path, file_suffix, file_path))

        return untracked_files_list
        
        

    
    
    def load_glossary_generation_map(self) -> set:
        """
        Потокобезопасно загружает список глав, для которых уже был сгенерирован глоссарий.
        Возвращает set для быстрой проверки.
        """
        with self.lock:
            if not os.path.exists(self.glossary_map_path):
                return set()
            try:
                with open(self.glossary_map_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if not content.strip():
                        return set()
                    # JSON хранит список, мы конвертируем в set для эффективности
                    return set(json.loads(content))
            except (json.JSONDecodeError, IOError, TypeError):
                # В случае ошибки возвращаем пустой set, чтобы не прерывать работу
                print(f"[WARN] Не удалось прочитать или обработать файл карты глоссария: {self.glossary_map_path}")
                return set()
    
    def save_glossary_generation_map(self, generated_chapters_set: set):
        """
        Потокобезопасно сохраняет обновленный список глав, для которых сгенерирован глоссарий.
        """
        with self.lock:
            # Конвертируем set в отсортированный список для стабильного и читаемого вывода
            data_to_save = sorted(list(generated_chapters_set))
            try:
                os.makedirs(os.path.dirname(self.glossary_map_path), exist_ok=True)
                with open(self.glossary_map_path, 'w', encoding='utf-8') as f:
                    json.dump(data_to_save, f, ensure_ascii=False, indent=2)
                print(f"[INFO] Карта сгенерированного глоссария ({len(data_to_save)} глав) сохранена.")
            except IOError as e:
                print(f"[ERROR] Не удалось сохранить файл карты глоссария: {e}")

    def _load_user_problem_terms_unsafe(self):
        if not os.path.exists(self.user_problem_terms_path):
            return []

        try:
            with open(self.user_problem_terms_path, 'r', encoding='utf-8') as f:
                content = f.read()
                if not content.strip():
                    return []

                data = json.loads(content)
                if isinstance(data, list):
                    return [item for item in data if isinstance(item, dict)]
        except (json.JSONDecodeError, IOError, TypeError):
            print(f"[WARN] Не удалось прочитать файл пользовательских пометок: {self.user_problem_terms_path}")

        return []

    def load_user_problem_terms(self) -> list:
        with self.lock:
            return self._load_user_problem_terms_unsafe()

    def save_user_problem_terms(self, items: list):
        with self.lock:
            os.makedirs(os.path.dirname(self.user_problem_terms_path), exist_ok=True)
            with open(self.user_problem_terms_path, 'w', encoding='utf-8') as f:
                json.dump(items, f, ensure_ascii=False, indent=2)

    def upsert_user_problem_terms(self, new_items: list):
        with self.lock:
            current_items = self._load_user_problem_terms_unsafe()
            items_by_id = {}

            for item in current_items:
                item_id = str(item.get('id', '')).strip()
                if item_id:
                    items_by_id[item_id] = item

            added = 0
            updated = 0

            for item in new_items:
                if not isinstance(item, dict):
                    continue

                item_id = str(item.get('id', '')).strip()
                if not item_id:
                    continue

                if item_id in items_by_id:
                    merged_item = items_by_id[item_id].copy()
                    merged_item.update(item)
                    items_by_id[item_id] = merged_item
                    updated += 1
                else:
                    items_by_id[item_id] = item
                    added += 1

            data_to_save = sorted(
                items_by_id.values(),
                key=lambda item: (
                    str(item.get('internal_html_path', '')),
                    str(item.get('term', '')).lower(),
                    str(item.get('id', '')),
                )
            )
            os.makedirs(os.path.dirname(self.user_problem_terms_path), exist_ok=True)
            with open(self.user_problem_terms_path, 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)

            return {
                'added': added,
                'updated': updated,
                'total': len(data_to_save),
            }

    def remove_user_problem_terms(self, item_ids) -> int:
        with self.lock:
            target_ids = {
                str(item_id).strip()
                for item_id in (item_ids or [])
                if str(item_id).strip()
            }
            if not target_ids:
                return 0

            current_items = self._load_user_problem_terms_unsafe()
            filtered_items = [
                item for item in current_items
                if str(item.get('id', '')).strip() not in target_ids
            ]
            removed_count = len(current_items) - len(filtered_items)
            if removed_count == 0:
                return 0

            os.makedirs(os.path.dirname(self.user_problem_terms_path), exist_ok=True)
            with open(self.user_problem_terms_path, 'w', encoding='utf-8') as f:
                json.dump(filtered_items, f, ensure_ascii=False, indent=2)

            return removed_count
    
    def _get_size_cache_path(self):
        """Возвращает путь к файлу кэша размеров глав."""
        return os.path.join(self.project_folder, 'chapter_size_cache.json')

    def load_size_cache(self):
        """Потокобезопасно загружает кэш размеров глав."""
        with self.lock:
            cache_path = self._get_size_cache_path()
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except (json.JSONDecodeError, IOError):
                    return None # Возвращаем None при ошибке, чтобы инициировать пересчет
            return None

    def save_size_cache(self, cache_data):
        """Потокобезопасно сохраняет кэш размеров глав."""
        with self.lock:
            cache_path = self._get_size_cache_path()
            try:
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                with open(cache_path, 'w', encoding='utf-8') as f:
                    json.dump(cache_data, f, ensure_ascii=False, indent=2)
            except IOError as e:
                print(f"[ERROR] Не удалось сохранить кэш размеров глав: {e}")
