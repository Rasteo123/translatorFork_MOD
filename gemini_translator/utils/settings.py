import os
import json
import time
from datetime import datetime, timedelta, timezone
from PyQt6 import QtWidgets, QtCore
from PyQt6.QtCore import QObject, pyqtSlot, pyqtSignal
import threading

try:
    import pytz
    PYTZ_AVAILABLE = True
except ImportError:
    PYTZ_AVAILABLE = False

from ..api import config as api_config

# --- ПРЕДПОЛАГАЕМЫЙ ИМПОРТ, МОЖЕТ ПОТРЕБОВАТЬ КОРРЕКТИРОВКИ ---
try:
    # Попытка абсолютного импорта от корня (предпочтительно)
    import os_patch
    PatientLock = os_patch.PatientLock
except (ImportError, AttributeError):
    # Запасной вариант, если PatientLock не найден, используем RLock как менее строгое, но безопасное решение
    print("[SettingsManager WARN] PatientLock не найден. Используется стандартный RLock.")
    PatientLock = threading.RLock


class SettingsManager(QObject):
    _save_requested = pyqtSignal()
    _request_count_changed = pyqtSignal(str, str, int)
    def __init__(self, event_bus=None, config_file=None):
        super().__init__()
        
        # --- Гибридный подход к получению зависимостей ---
        app = QtWidgets.QApplication.instance()
        self.bus = event_bus or getattr(app, 'event_bus', None)
        if self.bus:
            self.bus.event_posted.connect(self.on_event)
        else:
            print("[WARN] SettingsManager не получил event_bus и не нашел его в QApplication.")

        # --- Гибкая логика для пути к файлу ---
        if config_file:
            self.config_file = config_file
            self.config_dir = os.path.dirname(config_file)
        else:
            self.config_dir = os.path.expanduser("~/.epub_translator")
            self.config_file = os.path.join(self.config_dir, "settings.json")
        
        self.ensure_config_dir()
        
        # --- КЛЮЧЕВЫЕ КОМПОНЕНТЫ КЭШИРУЮЩЕЙ АРХИТЕКТУРЫ ---
        self.file_lock = PatientLock()
        self._cache = {}
        self._is_dirty = False
        
        # Таймер для отложенной записи (debouncing)
        self._save_timer = QtCore.QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(2500) # Задержка в 2.5 секунды
        self._save_timer.timeout.connect(self._perform_save)
        self._save_requested.connect(self._schedule_save)
        self._request_count_changed.connect(self._emit_request_count_updated)
        # Первоначальная загрузка кэша
        with self.file_lock:
            self._load_from_disk_unsafe()

        # Гарантированное сохранение при выходе из приложения
        if app:
            app.aboutToQuit.connect(self.flush)

    def _post_event(self, name: str, data: dict = None):
        if not self.bus: return
        event = {'event': name, 'source': 'SettingsManager', 'data': data or {}}
        self.bus.event_posted.emit(event)

    # --- ВНУТРЕННИЕ МЕТОДЫ УПРАВЛЕНИЯ КЭШЕМ И ФАЙЛОМ ---

    def _load_from_disk_unsafe(self):
        """[Под замком] Читает файл с диска и обновляет кэш."""
        self._cache = self._load_unsafe()
        self._migrate_keys_in_cache()

    def _save_to_disk_unsafe(self):
        """
        [Под замком] Читает диск, объединяет статистику запросов и записывает.
        Это предотвращает потерю данных о квотах при работе нескольких экземпляров.
        """
        # 1. Сначала пытаемся прочитать актуальное состояние файла
        disk_data = self._load_unsafe()
        
        # 2. Если файл существует и валиден, подтягиваем из него чужие запросы
        if disk_data:
            self._merge_disk_timestamps(disk_data)

        self._check_and_reset_limits_in_cache()

        # 3. Записываем итоговый результат (наши настройки + общая история)
        self._save_unsafe(self._cache)
        self._is_dirty = False
    
    def _merge_disk_timestamps(self, disk_data):
        """Вспомогательный метод: объединяет таймстампы из файла с текущим кэшем."""
        disk_keys = disk_data.get('api_keys_with_status', [])
        mem_keys = self._cache.get('api_keys_with_status', [])
        
        # Создаем карту для быстрого сопоставления ключей в памяти
        mem_key_map = {k['key']: k for k in mem_keys}

        for disk_k in disk_keys:
            key_str = disk_k.get('key')
            # Если такой ключ есть у нас в памяти
            if key_str in mem_key_map:
                mem_k = mem_key_map[key_str]
                
                disk_models = disk_k.get('status_by_model', {})
                mem_models = mem_k.get('status_by_model', {})
                
                # Проходим по моделям внутри ключа
                for model_id, disk_stats in disk_models.items():
                    # Если модель есть и у нас, мержим списки
                    if model_id in mem_models:
                        disk_reqs = set(disk_stats.get('requests', []))
                        mem_stats = mem_models[model_id]
                        mem_reqs = set(mem_stats.get('requests', []))
                        
                        # Объединение множеств (исключает дубликаты)
                        if not disk_reqs.issubset(mem_reqs):
                            # Сортируем, чтобы хронология была красивой
                            mem_stats['requests'] = sorted(list(mem_reqs | disk_reqs))
                    
                    # Если в памяти этой модели еще нет (например, использовалась в другом окне), 
                    # можно теоретически добавить, но для безопасности лучше не трогать структуру кэша,
                    # так как мы только "обогащаем" существующие данные.
                    
    def _request_save(self):
        """
        [Потокобезопасно] Помечает кэш как 'грязный' и ИСПУСКАЕТ СИГНАЛ
        для запуска таймера в главном потоке.
        """
        self._is_dirty = True
        # --- ИЗМЕНЕНИЕ: Вместо прямого вызова .start() испускаем сигнал ---
        self._save_requested.emit()

    @pyqtSlot()
    def _perform_save(self):
        """[Слот, GUI-поток] Если кэш 'грязный', атомарно сохраняет его на диск."""
        if self._is_dirty:
            with self.file_lock:
                self._save_to_disk_unsafe()

    @pyqtSlot()
    def _schedule_save(self):
        self._save_timer.start()

    @pyqtSlot()
    def flush(self):
        """[Слот, GUI-поток] Принудительно сохраняет кэш. Вызывается при выходе."""
        if self._save_timer.isActive():
            self._save_timer.stop()
        self._perform_save()

    @pyqtSlot(str, str, int)
    def _emit_request_count_updated(self, key_to_update, model_id, new_count):
        self._post_event('request_count_updated', {
            'key': key_to_update,
            'model_id': model_id,
            'count': new_count
        })

    def _migrate_keys_in_cache(self):
        """[Под замком] Выполняет миграцию старого формата ключей прямо в кэше."""
        migrated = False
        key_statuses = self._cache.get('api_keys_with_status', [])
        
        for key_info in key_statuses:
            if 'exhausted_at' in key_info or 'requests' in key_info:
                if 'status_by_model' not in key_info: key_info['status_by_model'] = {}
                provider_id = key_info.get('provider', 'gemini')
                provider_cfg = api_config.api_providers().get(provider_id, {})
                default_model_id = next(iter(provider_cfg.get('models', {}).values()), {}).get('id')
                if default_model_id and default_model_id not in key_info['status_by_model']:
                    key_info['status_by_model'][default_model_id] = {
                        "exhausted_at": key_info.pop('exhausted_at', None),
                        "exhausted_level": key_info.pop('exhausted_level', 0),
                        "requests": key_info.pop('requests', [])
                    }
                    migrated = True
        
        if not key_statuses and 'api_keys' in self._cache:
            old_keys = self._cache.pop('api_keys', [])
            self._cache['api_keys_with_status'] = [{"key": key, "provider": "gemini", "status_by_model": {}} for key in old_keys]
            migrated = True
            
        if migrated:
            print("[SettingsManager] Обнаружен и мигрирован старый формат хранения ключей.")
            self._save_to_disk_unsafe()

    # --- ПУБЛИЧНЫЕ МЕТОДЫ: Адаптированы для работы с кэшем ---

    def load_settings(self):
        with self.file_lock:
            return self._cache.copy()

    def save_settings(self, settings_dict):
        with self.file_lock:
            self._cache = settings_dict
            self._save_to_disk_unsafe()
        return True

    @pyqtSlot(dict)
    def on_event(self, event_data: dict):
        event_name = event_data.get('event')
        data = event_data.get('data', {})
        if event_name == 'fatal_error':
            payload = data.get('payload', {})
            if payload.get('type') == 'quota_exceeded':
                source = event_data.get('source', '')
                if source.startswith('worker_'):
                    key = event_data.get('worker_key', '')
                    model_id = payload.get('model_id') 
                    if key and model_id:
                        self.mark_key_as_exhausted(key, model_id)

    def ensure_config_dir(self):
        if not os.path.exists(self.config_dir):
            os.makedirs(self.config_dir, exist_ok=True)

    def _get_status_for_model(self, key_info, model_id):
        if 'status_by_model' not in key_info:
            key_info['status_by_model'] = {}
        if model_id not in key_info['status_by_model']:
            key_info['status_by_model'][model_id] = {"exhausted_at": None, "exhausted_level": 0, "requests": []}
        return key_info['status_by_model'][model_id]

    def is_key_limit_active(self, key_info, model_id):
        if not model_id: return False
        model_status = self._get_status_for_model(key_info, model_id)
        timestamp = model_status.get("exhausted_at")
        level = model_status.get("exhausted_level", 0)
        if not timestamp or level < 2: return False
        provider = key_info.get("provider", "default")
        policy = api_config.api_providers().get(provider, {}).get('reset_policy', api_config.default_reset_policy())
        exhausted_time_utc = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        now_utc = datetime.now(timezone.utc)
        if policy["type"] == "rolling":
            return now_utc < exhausted_time_utc + timedelta(hours=policy["duration_hours"])
        elif policy["type"] == "daily" and PYTZ_AVAILABLE:
            try:
                tz = pytz.timezone(policy["timezone"])
                now_in_tz = now_utc.astimezone(tz)
                last_reset = now_in_tz.replace(hour=policy.get("reset_hour",0), minute=policy.get("reset_minute",1), second=0, microsecond=0)
                if last_reset > now_in_tz: last_reset -= timedelta(days=1)
                return exhausted_time_utc.astimezone(tz) > last_reset
            except Exception: return now_utc < exhausted_time_utc + timedelta(hours=24)
        else: return now_utc < exhausted_time_utc + timedelta(hours=24)
    
    def get_key_info(self, key_to_find):
        with self.file_lock:
            for key_info in self._cache.get('api_keys_with_status', []):
                if key_info['key'] == key_to_find:
                    return key_info.copy()
        return None

    def get_key_reset_time_str(self, key_info, model_id):
        if not model_id: return "Модель не выбрана"
        model_status = self._get_status_for_model(key_info, model_id)
        timestamp = model_status.get("exhausted_at")
        if not timestamp: return "Активен"
        provider = key_info.get("provider", "default")
        policy = api_config.api_providers().get(provider, {}).get('reset_policy', api_config.default_reset_policy())
        exhausted_time_utc = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        reset_time_utc = None
        if policy["type"] == "rolling":
            reset_time_utc = exhausted_time_utc + timedelta(hours=policy["duration_hours"])
        elif policy["type"] == "daily" and PYTZ_AVAILABLE:
            try:
                tz = pytz.timezone(policy["timezone"])
                now_in_tz = datetime.now(tz)
                next_reset_in_tz = now_in_tz.replace(hour=policy.get("reset_hour", 0), minute=policy.get("reset_minute", 1), second=0, microsecond=0)
                if next_reset_in_tz <= now_in_tz: next_reset_in_tz += timedelta(days=1)
                reset_time_utc = next_reset_in_tz.astimezone(timezone.utc)
            except Exception as e:
                print(f"[ERROR] Ошибка расчета времени для ключа (daily policy): {e}")
                reset_time_utc = exhausted_time_utc + timedelta(hours=24)
        else:
            reset_time_utc = exhausted_time_utc + timedelta(hours=24)
        if reset_time_utc:
            local_reset_time = reset_time_utc.astimezone()
            return f"Сброс ~{local_reset_time.strftime('%H:%M (%d.%m)')}"
        return "Сброс в течение 24ч"

    def _get_request_policy(self, key_info):
        provider = key_info.get("provider", "default")
        return api_config.api_providers().get(provider, {}).get('reset_policy', api_config.default_reset_policy())

    def _filter_request_timestamps_in_window(self, timestamps, policy, now_ts=None):
        if not timestamps:
            return []

        if now_ts is None:
            now_ts = int(time.time())

        normalized_timestamps = [int(ts) for ts in timestamps if isinstance(ts, (int, float))]
        if not normalized_timestamps:
            return []

        if policy['type'] == 'rolling':
            cutoff = now_ts - (int(policy.get('duration_hours', 24)) * 3600)
            return sorted(ts for ts in normalized_timestamps if ts > cutoff)

        if policy['type'] == 'daily' and PYTZ_AVAILABLE:
            try:
                tz = pytz.timezone(policy["timezone"])
                now_in_tz = datetime.fromtimestamp(now_ts, tz=timezone.utc).astimezone(tz)
                last_reset = now_in_tz.replace(
                    hour=policy.get("reset_hour", 0),
                    minute=policy.get("reset_minute", 1),
                    second=0,
                    microsecond=0,
                )
                if last_reset > now_in_tz:
                    last_reset -= timedelta(days=1)
                cutoff = int(last_reset.timestamp())
                return sorted(ts for ts in normalized_timestamps if ts > cutoff)
            except Exception:
                pass

        cutoff = now_ts - (24 * 3600)
        return sorted(ts for ts in normalized_timestamps if ts > cutoff)

    def _prune_request_history_for_model(self, key_info, model_id, now_ts=None):
        model_status = self._get_status_for_model(key_info, model_id)
        current_requests = model_status.get('requests', [])
        filtered_requests = self._filter_request_timestamps_in_window(
            current_requests,
            self._get_request_policy(key_info),
            now_ts=now_ts,
        )
        was_changed = filtered_requests != current_requests
        if was_changed:
            model_status['requests'] = filtered_requests
        return filtered_requests, was_changed

    def increment_request_count(self, key_to_update, model_id):
        if not model_id: return False
        new_count = 0
        updated = False
        with self.file_lock:
            now = int(time.time())
            for key_info in self._cache.get('api_keys_with_status', []):
                if key_info['key'] == key_to_update:
                    model_status = self._get_status_for_model(key_info, model_id)
                    model_status['requests'].append(now)
                    valid_requests, _ = self._prune_request_history_for_model(
                        key_info, model_id, now_ts=now)
                    new_count = len(valid_requests)
                    updated = True
                    break
            if updated:
                self._request_save()
        # print(f"key_to_update {key_to_update} model_id {model_id} new_count {new_count}")
        if updated:
            self._request_count_changed.emit(key_to_update, model_id, new_count)
        return updated

    def decrement_request_count(self, key_to_update, model_id):
        if not model_id: return False
        new_count = 0
        updated = False
        with self.file_lock:
            for key_info in self._cache.get('api_keys_with_status', []):
                if key_info['key'] == key_to_update:
                    valid_requests, was_pruned = self._prune_request_history_for_model(
                        key_info, model_id)
                    if valid_requests:
                        valid_requests.pop()
                        self._get_status_for_model(key_info, model_id)['requests'] = valid_requests
                        was_pruned = True
                    new_count = len(valid_requests)
                    updated = was_pruned
                    break
            if updated:
                self._request_save()
        # print(f"key_to_update {key_to_update} model_id {model_id} new_count {new_count}")
        if updated:
            self._request_count_changed.emit(key_to_update, model_id, new_count)
        return updated

    def load_key_statuses(self):
        with self.file_lock:
            if self._check_and_reset_limits_in_cache():
                self._save_to_disk_unsafe() # Сохраняем, если были изменения
            return self._cache.get('api_keys_with_status', []).copy()

    def save_key_statuses(self, key_statuses):
        with self.file_lock:
            self._cache['api_keys_with_status'] = key_statuses
            if 'api_keys' in self._cache:
                del self._cache['api_keys']
            self._save_to_disk_unsafe()
        self._post_event('key_statuses_updated')
        return True

    def remove_keys_atomically(self, keys_to_remove: set):
        removed_count = -1
        with self.file_lock: 
            key_statuses = self._cache.get('api_keys_with_status', [])
            initial_count = len(key_statuses)
            updated_statuses = [ki for ki in key_statuses if ki['key'] not in keys_to_remove]
            removed_count = initial_count - len(updated_statuses)
            if removed_count > 0:
                self._cache['api_keys_with_status'] = updated_statuses
                self._save_to_disk_unsafe()
        if removed_count > 0:
            self._post_event('key_statuses_updated')
        return removed_count
    
    def mark_key_as_exhausted(self, key_to_mark, model_id):
        """
        [Потокобезопасно] Помечает ключ как исчерпанный для конкретной модели.
        Адаптировано для работы с in-memory кэшем.
        """
        if not model_id: return False
        
        updated = False
        with self.file_lock:
            # Итерируемся прямо по списку в кэше
            for key_info in self._cache.get('api_keys_with_status', []):
                if key_info['key'] == key_to_mark:
                    model_status = self._get_status_for_model(key_info, model_id)
                    model_status['exhausted_at'] = time.time()
                    model_status['exhausted_level'] = 2
                    updated = True
                    break
            
            if updated:
                # Используем механизм отложенного сохранения, чтобы не фризить GUI при ошибке
                self._request_save()

        # Уведомление отправляем вне блокировки
        if updated:
            self._post_event('key_statuses_updated')
            
        return updated
        
    def add_keys_atomically(self, new_keys: set, provider_id: str):
        added_count = -1
        with self.file_lock:
            key_statuses = self._cache.get('api_keys_with_status', [])
            existing_keys = {ki['key'] for ki in key_statuses}
            added_count_internal = 0
            for key in new_keys:
                if key not in existing_keys:
                    key_statuses.append({"key": key, "provider": provider_id, "status_by_model": {}})
                    added_count_internal += 1
            if added_count_internal > 0:
                self._cache['api_keys_with_status'] = key_statuses
                self._save_to_disk_unsafe()
                added_count = added_count_internal
            else:
                added_count = 0
        if added_count > 0:
            self._post_event('key_statuses_updated')
        return added_count
                
    def get_api_keys(self):
        with self.file_lock:
            return [item['key'] for item in self._cache.get('api_keys_with_status', [])]

    def save_api_keys(self, keys_list):
        key_statuses = [{"key": key, "provider": "gemini", "status_by_model": {}} for key in keys_list]
        return self.save_key_statuses(key_statuses)

    def save_ui_state(self, ui_state_dict):
        with self.file_lock:
            self._cache.update(ui_state_dict)
            self._save_to_disk_unsafe()
        return True
            
    # --- ОСТАЛЬНЫЕ МЕТОДЫ, РАБОТАЮЩИЕ С КЭШЕМ ---
    def _generic_loader(self, key, default=None):
        with self.file_lock: return self._cache.get(key, default)
    def _generic_saver(self, key, value):
        with self.file_lock:
            self._cache[key] = value
            self._save_to_disk_unsafe()
        return True
    
    def _check_and_reset_limits_in_cache(self):
        """[Под замком] Проверяет и сбрасывает лимиты прямо в кэше."""
        changed = False
        # Работаем с кэшем напрямую, так как мы под замком
        for key_info in self._cache.get('api_keys_with_status', []):
            if 'status_by_model' in key_info:
                # list() для создания копии, чтобы избежать ошибки изменения размера во время итерации
                for model_id in list(key_info['status_by_model'].keys()):
                    _, was_pruned = self._prune_request_history_for_model(key_info, model_id)
                    if was_pruned:
                        changed = True
                    # ВЫЗЫВАЕМ МЕТОД У SELF, А НЕ У SELF.SETTINGS_MANAGER
                    if not self.is_key_limit_active(key_info, model_id):
                        # Проверяем, есть ли что сбрасывать
                        if key_info['status_by_model'][model_id].get("exhausted_at") is not None:
                            changed = True
                            key_info['status_by_model'][model_id]["exhausted_at"] = None
                            key_info['status_by_model'][model_id]["exhausted_level"] = 0
        return changed
    
    def get_custom_prompt(self): return self._generic_loader('custom_prompt', '')
    def save_custom_prompt(self, prompt): return self._generic_saver('custom_prompt', prompt)
    def get_last_settings(self):
        with self.file_lock:
            return {
                'output_folder': self._cache.get('last_output_folder', ''),
                'model': self._cache.get('last_model', api_config.default_model_name()),
                'temperature': self._cache.get('last_temperature', 1.0),
                'temperature_override_enabled': self._cache.get('last_temperature_override_enabled', False),
                'rpm_limit': self._cache.get('last_concurrent_requests', 10),
                'chunking': self._cache.get('last_chunking', False),
                'dynamic_glossary': self._cache.get('last_dynamic_glossary', True),
                'system_instruction': self._cache.get('last_system_instruction', False),
                'thinking_enabled': self._cache.get('last_thinking_enabled', False),
                'thinking_budget': self._cache.get('last_thinking_budget', -1),
                'use_json_epub_pipeline': self._cache.get('last_use_json_epub_pipeline', False),
            }
    def save_last_settings(self, **kwargs):
        with self.file_lock:
            for key, value in kwargs.items():
                self._cache[f'last_{key}'] = value
            self._save_to_disk_unsafe()
        return True
        
    def clear_key_exhaustion_status(self, key_to_clear, model_id):
        if not model_id: return False
        was_cleared = False
        with self.file_lock:
            for key_info in self._cache.get('api_keys_with_status', []):
                if key_info['key'] == key_to_clear:
                    model_status = self._get_status_for_model(key_info, model_id)
                    if model_status.get("exhausted_at") is not None:
                        model_status["exhausted_at"] = None
                        model_status["exhausted_level"] = 0
                        was_cleared = True
                    break
            if was_cleared:
                self._save_to_disk_unsafe()
        if was_cleared:
            self._post_event('key_statuses_updated')
        return was_cleared
        
    def _normalize_project_path(self, path):
        return os.path.normpath(path) if path else ''

    def _project_history_key(self, output_folder='', epub_path=''):
        path = output_folder or epub_path
        return os.path.normcase(self._normalize_project_path(path))

    def _prepare_project_history(self, history_list):
        prepared_history = []
        seen_keys = set()

        for project in history_list or []:
            if not isinstance(project, dict):
                continue

            normalized_project = dict(project)
            normalized_project['epub_path'] = self._normalize_project_path(
                normalized_project.get('epub_path')
            )
            normalized_project['output_folder'] = self._normalize_project_path(
                normalized_project.get('output_folder')
            )

            project_key = self._project_history_key(
                normalized_project.get('output_folder'),
                normalized_project.get('epub_path'),
            )
            if not project_key or project_key in seen_keys:
                continue

            seen_keys.add(project_key)
            prepared_history.append(normalized_project)

        return prepared_history[:30]

    def load_project_history(self):
        with self.file_lock:
            history = self._prepare_project_history(self._cache.get('project_history', []))
            if history != self._cache.get('project_history', []):
                self._cache['project_history'] = history
                self._save_to_disk_unsafe()
            return history.copy()

    def save_project_history(self, history_list):
        return self._generic_saver('project_history', self._prepare_project_history(history_list))
    def get_last_project_folder(self): return self._generic_loader('last_project_folder', '')
    def save_last_project_folder(self, folder_path): return self._generic_saver('last_project_folder', folder_path or '')
    def get_last_projects_root_folder(self): return self._generic_loader('last_projects_root_folder', '')
    def save_last_projects_root_folder(self, folder_path): return self._generic_saver('last_projects_root_folder', folder_path or '')

    def add_to_project_history(self, epub_path, output_folder):
        normalized_epub_path = self._normalize_project_path(epub_path)
        normalized_output_folder = self._normalize_project_path(output_folder)
        if not normalized_epub_path or not normalized_output_folder:
            return

        with self.file_lock:
            history = self._prepare_project_history(self._cache.get('project_history', []))
            output_folder_key = self._project_history_key(normalized_output_folder)
            project_to_move = next(
                (
                    p for p in history
                    if self._project_history_key(
                        p.get('output_folder'),
                        p.get('epub_path'),
                    ) == output_folder_key
                ),
                None
            )
            if project_to_move:
                history.remove(project_to_move)
                new_project_entry = project_to_move
                epub_name = os.path.splitext(os.path.basename(normalized_epub_path))[0]
                folder_name = os.path.basename(os.path.normpath(normalized_output_folder))
                new_project_entry['name'] = f"{epub_name} - {folder_name}"
                new_project_entry['epub_path'] = normalized_epub_path
                new_project_entry['output_folder'] = normalized_output_folder
            else:
                epub_name = os.path.splitext(os.path.basename(normalized_epub_path))[0]
                folder_name = os.path.basename(os.path.normpath(normalized_output_folder))
                project_name = f"{epub_name} - {folder_name}"
                new_project_entry = {
                    "name": project_name,
                    "epub_path": normalized_epub_path,
                    "output_folder": normalized_output_folder,
                }
            history.insert(0, new_project_entry)
            self._cache['project_history'] = self._prepare_project_history(history)
            self._cache['last_project_folder'] = normalized_output_folder
            self._save_to_disk_unsafe()

    def get_request_count(self, key_info, model_id):
        provider_id = key_info.get("provider", "default")
        provider_config = api_config.api_providers().get(provider_id, {})
        use_shared_counter = provider_config.get("shared_request_counter", False)
        policy = provider_config.get('reset_policy', api_config.default_reset_policy())
        if not use_shared_counter:
            if not model_id: return 0
            model_status = self._get_status_for_model(key_info, model_id)
            timestamps = model_status.get('requests', [])
            return self._count_valid_requests_in_window(timestamps, policy)
        else:
            total_count = 0
            provider_model_ids = { model_data['id'] for model_data in provider_config.get('models', {}).values() }
            for model_id_in_key, model_status in key_info.get('status_by_model', {}).items():
                if model_id_in_key in provider_model_ids:
                    timestamps = model_status.get('requests', [])
                    total_count += self._count_valid_requests_in_window(timestamps, policy)
            return total_count

    def get_request_timestamps(self, key_info, model_id):
        if not model_id: return []
        model_status = self._get_status_for_model(key_info, model_id)
        return self._filter_request_timestamps_in_window(
            model_status.get('requests', []),
            self._get_request_policy(key_info),
        )
    
    def _count_valid_requests_in_window(self, timestamps, policy):
        return len(self._filter_request_timestamps_in_window(timestamps, policy))

    def _get_path_for_preset_type(self, preset_type: str) -> str:
        """Вспомогательный метод для получения пути к файлу пресетов."""
        filename_map = {
            "prompt": "prompts.json", "glossary": "glossary_prompts.json",
            "correction": "correction_prompts.json", "untranslated": "untranslated_prompts.json",
            "manual_translation": "manual_translation_prompts.json",
            "system": "system_prompts.json", "exceptions": "word_exceptions.json",
            "auto_translation": "auto_translation_presets.json",
            "glossary_pipeline": "glossary_pipeline_templates.json",
        }
        filename = filename_map.get(preset_type, "prompts.json")
        return os.path.join(self.config_dir, filename)

    def load_presets_by_type(self, preset_type: str):
        path = self._get_path_for_preset_type(preset_type)
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f: return json.load(f)
            except (json.JSONDecodeError, IOError): return {}
        return {}
    
    def save_presets_by_type(self, preset_type: str, presets_dict: dict):
        path = self._get_path_for_preset_type(preset_type)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(presets_dict, f, ensure_ascii=False, indent=2, sort_keys=True)
            return True
        except IOError: return False

    def load_named_prompts(self): return self.load_presets_by_type('prompt')
    def save_named_prompts(self, d): return self.save_presets_by_type('prompt', d)
    def load_glossary_prompts(self): return self.load_presets_by_type('glossary')
    def save_glossary_prompts(self, d): return self.save_presets_by_type('glossary', d)
    def load_correction_prompts(self): return self.load_presets_by_type('correction')
    def save_correction_prompts(self, d): return self.save_presets_by_type('correction', d)
    def load_untranslated_prompts(self): return self.load_presets_by_type('untranslated')
    def save_untranslated_prompts(self, d): return self.save_presets_by_type('untranslated', d)
    def load_manual_translation_prompts(self): return self.load_presets_by_type('manual_translation')
    def save_manual_translation_prompts(self, d): return self.save_presets_by_type('manual_translation', d)
    def load_system_prompts(self): return self.load_presets_by_type('system')
    def save_system_prompts(self, d): return self.save_presets_by_type('system', d)
    def load_word_exceptions_presets(self): return self.load_presets_by_type('exceptions')
    def save_word_exceptions_presets(self, d): return self.save_presets_by_type('exceptions', d)
    def load_auto_translation_presets(self): return self.load_presets_by_type('auto_translation')
    def save_auto_translation_presets(self, d): return self.save_presets_by_type('auto_translation', d)
    def load_glossary_pipeline_templates(self): return self.load_presets_by_type('glossary_pipeline')
    def save_glossary_pipeline_templates(self, d): return self.save_presets_by_type('glossary_pipeline', d)
    
    def get_last_prompt_preset_name(self): return self._generic_loader('last_prompt_preset', None)
    def save_last_prompt_preset_name(self, name): return self._generic_saver('last_prompt_preset', name)
    def get_last_glossary_prompt_text(self): return self._generic_loader('last_glossary_prompt_text', api_config.default_glossary_prompt())
    def save_last_glossary_prompt_text(self, text): return self._generic_saver('last_glossary_prompt_text', text)
    def get_last_glossary_prompt_preset_name(self): return self._generic_loader('last_glossary_prompt_preset', None)
    def save_last_glossary_prompt_preset_name(self, name): return self._generic_saver('last_glossary_prompt_preset', name)
    def get_last_word_exceptions_text(self): return self._generic_loader('last_word_exceptions_text', "")
    def save_last_word_exceptions_text(self, text): return self._generic_saver('last_word_exceptions_text', text)
    def load_proxy_settings(self): return self._generic_loader("proxy_settings", {})
    def get_last_correction_prompt_text(self): return self._generic_loader('last_correction_prompt_text', api_config.default_correction_prompt())
    def save_last_correction_prompt_text(self, text): return self._generic_saver('last_correction_prompt_text', text)
    def get_last_correction_prompt_preset_name(self): return self._generic_loader('last_correction_prompt_preset', None)
    def save_last_correction_prompt_preset_name(self, name): return self._generic_saver('last_correction_prompt_preset', name)
    def get_last_untranslated_prompt_text(self): return self._generic_loader('last_untranslated_prompt_text', api_config.default_untranslated_prompt())
    def save_last_untranslated_prompt_text(self, text): return self._generic_saver('last_untranslated_prompt_text', text)
    def get_last_untranslated_prompt_preset_name(self): return self._generic_loader('last_untranslated_prompt_preset', None)
    def save_last_untranslated_prompt_preset_name(self, name): return self._generic_saver('last_untranslated_prompt_preset', name)
    def get_last_manual_translation_prompt_text(self): return self._generic_loader('last_manual_translation_prompt_text', api_config.default_manual_translation_prompt())
    def save_last_manual_translation_prompt_text(self, text): return self._generic_saver('last_manual_translation_prompt_text', text)
    def get_last_manual_translation_prompt_preset_name(self): return self._generic_loader('last_manual_translation_prompt_preset', None)
    def save_last_manual_translation_prompt_preset_name(self, name): return self._generic_saver('last_manual_translation_prompt_preset', name)
    def get_last_system_prompt_text(self): return self._generic_loader('last_system_prompt_text', "")
    def save_last_system_prompt_text(self, text): return self._generic_saver('last_system_prompt_text', text)
    def get_last_system_prompt_preset_name(self): return self._generic_loader('last_system_prompt_preset', None)
    def save_last_system_prompt_preset_name(self, name): return self._generic_saver('last_system_prompt_preset', name)
    def get_last_auto_translation_preset_name(self): return self._generic_loader('last_auto_translation_preset', None)
    def save_last_auto_translation_preset_name(self, name): return self._generic_saver('last_auto_translation_preset', name)
    def get_last_glossary_pipeline_template_name(self): return self._generic_loader('last_glossary_pipeline_template', None)
    def save_last_glossary_pipeline_template_name(self, name): return self._generic_saver('last_glossary_pipeline_template', name)
    def get_last_auto_translation_settings(self): return self._generic_loader('last_auto_translation_settings', {})
    def save_last_auto_translation_settings(self, settings_dict): return self._generic_saver('last_auto_translation_settings', settings_dict or {})
    def get_last_glossary_generation_settings(self):
        settings = self._generic_loader('last_glossary_generation_settings', {})
        return settings.copy() if isinstance(settings, dict) else {}
    def save_last_glossary_generation_settings(self, settings_dict): return self._generic_saver('last_glossary_generation_settings', settings_dict or {})
    def get_ai_correction_review_settings(self): return self._generic_loader('ai_correction_review_settings', {})
    def save_ai_correction_review_settings(self, settings_dict): return self._generic_saver('ai_correction_review_settings', settings_dict or {})
    
    def save_proxy_settings(self, proxy_settings_dict):
        with self.file_lock:
            self._cache["proxy_settings"] = proxy_settings_dict
            # Настройки прокси лучше сохранить сразу (unsafe запись), чтобы они точно применились
            self._save_to_disk_unsafe()
        
        # Важно: отправляем событие, на которое реагирует сетевой менеджер
        self._post_event('proxy_settings_changed', proxy_settings_dict)
        return True
    
    def load_full_session_settings(self):
        return self._generic_loader('last_full_session', {})

    def save_full_session_settings(self, session_dict):
        return self._generic_saver('last_full_session', session_dict)
        
    def _save_unsafe(self, data_to_save):
        """
        Сохраняет настройки. 
        1. Санитизирует ключи (предотвращает ошибку сортировки NoneType).
        2. Генерирует JSON в памяти (быстро, проверка ошибок до записи).
        3. Пишет во временный файл и делает атомарную подмену.
        """
        
        # --- 1. Санитизация (лечим ошибку '<' not supported) ---
        def sanitize_keys(data):
            if isinstance(data, dict):
                # Превращаем ключи в строки, рекурсивно идем вглубь
                return {str(k): sanitize_keys(v) for k, v in data.items()}
            elif isinstance(data, list):
                return [sanitize_keys(i) for i in data]
            else:
                return data

        try:
            safe_data = sanitize_keys(data_to_save)
        except Exception as e:
            print(f"[SettingsManager] Ошибка санитизации: {e}")
            safe_data = data_to_save

        # --- 2. Генерация в памяти (RAM) ---
        try:
            # Формируем весь JSON-строку в оперативной памяти.
            # Если тут будет ошибка (как ваша TypeError), она вылетит СЕЙЧАС,
            # и мы даже не притронемся к файлу на диске. Файл будет спасен.
            json_content = json.dumps(safe_data, ensure_ascii=False, indent=2, sort_keys=True)
        except Exception as e:
            print(f"[SettingsManager CRITICAL] Ошибка генерации JSON: {e}")
            # Не пишем ничего, чтобы не испортить файл
            raise e

        os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
        
        # --- 3. Атомарная запись (Disk) ---
        # Пишем во временный файл, который находится в ТОЙ ЖЕ папке.
        # Это обязательно для работы os.replace (атомарного переноса).
        temp_file = self.config_file + ".tmp"
        
        try:
            # Записываем подготовленную строку
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write(json_content)
                f.flush()
                os.fsync(f.fileno()) # Принудительно сбрасываем буфер ОС на диск

            # МОМЕНТ ИСТИНЫ: Мгновенная подмена. 
            # Либо старый файл, либо новый. Никаких промежуточных состояний.
            os.replace(temp_file, self.config_file)

        except Exception as e:
            # Если что-то пошло не так (место на диске кончилось), удаляем мусор
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass
            print(f"[SettingsManager CRITICAL] Не удалось записать файл настроек: {e}")
            raise e
    
    def _load_unsafe(self):
        if not os.path.exists(self.config_file): return {}
        encodings_to_try = ['utf-8', 'cp1251', 'cp866']
        for encoding in encodings_to_try:
            try:
                with open(self.config_file, 'r', encoding=encoding) as f: content = f.read()
                if not content.strip(): return {}
                data_to_load = json.loads(content)
                if encoding != 'utf-8':
                    print(f"[SettingsManager] ВНИМАНИЕ: Файл настроек был в кодировке {encoding}. Конвертирую в UTF-8...")
                    try:
                        self._save_unsafe(data_to_load)
                        print("[SettingsManager] Файл настроек успешно вылечен.")
                    except Exception as e:
                        print(f"[SettingsManager] ОШИБКА: Не удалось пересохранить файл в UTF-8: {e}")
                return data_to_load
            except (UnicodeDecodeError, json.JSONDecodeError): continue
            except Exception as e:
                print(f"[SettingsManager WARN] Не удалось прочитать файл настроек с кодировкой {encoding}: {e}")
                continue
        print(f"[SettingsManager CRITICAL] Не удалось прочитать или распарсить файл {self.config_file}. Файл может быть поврежден. Возвращаю пустые настройки.")
        return {}
