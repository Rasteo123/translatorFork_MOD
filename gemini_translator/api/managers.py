# -*- coding: utf-8 -*-

# ---------------------------------------------------------------------------
# Менеджеры API
# ---------------------------------------------------------------------------
# Этот файл содержит классы для управления взаимодействием с API:
# - ApiKeyManager: управляет пулом API ключей, их ротацией и отслеживанием.
# - RateLimitTracker: отслеживает лимиты запросов на основе заголовков ответа.
# ---------------------------------------------------------------------------

import threading
import time

try:
    # Попытка абсолютного импорта от корня (предпочтительно)
    import os_patch
    PatientLock = os_patch.PatientLock
except (ImportError, AttributeError):
    # Запасной вариант, если PatientLock не найден, используем RLock как менее строгое, но безопасное решение
    print("[API KeyManager WARN] PatientLock не найден. Используется стандартный RLock.")
    PatientLock = threading.RLock
    
class ApiKeyManager:
    """Управляет пулом API ключей с отслеживанием использования и ротацией."""
    def __init__(self, api_keys):
        if not api_keys:
            raise ValueError("Список API ключей не может быть пустым.")
        self.api_keys = list(dict.fromkeys(api_keys)) 
        self.current_index = 0
        self.exhausted_keys = set()
        self.active_keys = set()
        self.paused_keys = set()
        self.keys_map = {}
        self.lock = PatientLock()
    
    def get_next_available_key(self):
        """
        Находит следующий ключ, готовый к работе (не активный, не на паузе, не исчерпан),
        и помечает его как активный. Возвращает None, если таких ключей нет.
        """
        with self.lock:
            # Пройдемся по всем ключам не более одного раза
            for _ in range(len(self.api_keys)):
                key = self.api_keys[self.current_index]
                self.current_index = (self.current_index + 1) % len(self.api_keys)

                if key not in self.exhausted_keys and key not in self.paused_keys and key not in self.active_keys:
                    self.active_keys.add(key)
                    return key
            return None
            
    
    def update_active(self, active_source):
        """Принудительно синхронизирует множество активных ключей с реальным состоянием воркеров."""
        with self.lock:
            if isinstance(active_source, dict):
                # Если передан словарь (старая логика или map воркеров), берем его ключи
                self.active_keys = set(active_source.keys())
            else:
                # Если передан список/множество (новая логика), используем как есть
                self.active_keys = set(active_source)
    
    def update_map(self, keys_map):
        """Синхронизирует карту воркер-ключ."""
        with self.lock:
            self.keys_map = keys_map
    
    def get_map(self):
        """Возвращает карту воркер-ключ."""
        with self.lock:
            keys_map = self.keys_map
        return keys_map
    
    def check_key(self, api_key):
        """Проверяет ключ на право работать в данный момент."""
        with self.lock:
            stop_keys = self.paused_keys | self.exhausted_keys
            if api_key in stop_keys:
                return False
        return True
    
    def pause_key(self, key):
        """Ставит ключ на временную паузу."""
        with self.lock:
            if key in self.api_keys:
                self.paused_keys.add(key)
                print(f"[API KEY] Ключ …{key[-4:]} поставлен на паузу.")
                
    def resume_key(self, key):
        """Снимает ключ с паузы, делая его снова доступным."""
        with self.lock:
            if key in self.paused_keys:
                self.paused_keys.discard(key)
                print(f"[API KEY] Ключ …{key[-4:]} снят с паузы и возвращен в ротацию.")


    def mark_key_exhausted(self, key):
        """Помечает ключ как исчерпанный"""
        with self.lock:
            if key in self.api_keys:
                self.exhausted_keys.add(key)
                print(f"[API KEY] Ключ …{key[-4:]} помечен как исчерпанный")

    
    def has_idle_keys(self) -> bool:
        """
        ОТВЕЧАЕТ НА ВОПРОС: "Есть ли у нас свободные 'руки' для новой работы прямо сейчас?"
        
        Проверяет, есть ли хотя бы один ключ, который не активен, не на паузе и не исчерпан.
        Используется для принятия решения о запуске нового воркера или замене уволенного.
        """
        with self.lock:
            unavailable_keys = self.exhausted_keys | self.paused_keys | self.active_keys
            return len(unavailable_keys) < len(self.api_keys)

    def has_non_exhausted_keys(self) -> bool:
        """
        ОТВЕЧАЕТ НА ВОПРОС: "Есть ли вообще надежда на продолжение сессии?"

        Проверяет, есть ли в пуле хотя бы один ключ, который НЕ является
        окончательно исчерпанным. Ключ может быть активен или на паузе.
        Используется для определения полной безысходности (stalemate).
        """
        with self.lock:
            return len(self.exhausted_keys) < len(self.api_keys)
    
    def get_status_counts(self) -> dict:
        """
        Возвращает текущую статистику пула ключей.
        'reserve' — ключи, готовые к выдаче немедленно.
        'active' — ключи, которые сейчас в работе.
        """
        with self.lock:
            active = len(self.active_keys)
            exhausted = len(self.exhausted_keys)
            paused = len(self.paused_keys)
            total = len(self.api_keys)
            
            # Резерв — это те, кто не занят, не отдыхает и не исчерпан
            reserve = total - (active + exhausted + paused)
            
            return {
                'active': active,
                'reserve': reserve,
                'exhausted': exhausted,
                'paused': paused,
                'total': total
            }
            
    def release_key(self, key):
        """
        Возвращает ключ в пул доступных, когда воркер завершил работу.
        """
        with self.lock:
            self.active_keys.discard(key)
            print(f"[API KEY] Ключ …{key[-4:]} освобожден и возвращен в пул.")
