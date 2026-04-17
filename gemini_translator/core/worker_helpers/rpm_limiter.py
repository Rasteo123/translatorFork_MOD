import time
import threading

class RPMLimiter:
    """
    Потокобезопасный класс для РАВНОМЕРНОГО контроля скорости запросов (RPM).
    Версия 2.0: Добавлены методы для сброса и принудительного ожидания.
    """
    def __init__(self, rpm_limit: int):
        if rpm_limit <= 0:
            self.rpm_limit, self.interval = 0, 0

            self.can_proceed = lambda: True
            self.reset = lambda: None
            self.update_last_request_time = lambda: None
            return
        

        self.rpm_limit = rpm_limit
        self.interval = 60.0 / self.rpm_limit
        self.lock = threading.Lock()
        self.last_request_time = 0

    def can_proceed(self) -> bool:

        with self.lock:
            now = time.time()
            elapsed = now - self.last_request_time
            if elapsed >= self.interval:
                self.last_request_time = now
                return True
            return False

    # --- НАЧАЛО НОВЫХ МЕТОДОВ ---
    def reset(self):
        """
        Обнуляет таймер. Следующий вызов can_proceed() гарантированно пройдет.
        """
        with self.lock:
            self.last_request_time = 0
    
    def get_rpm(self) -> int:
        """Возвращает текущее значение RPM."""
        with self.lock:
            return self.rpm_limit
    
    def decrease_rpm(self, percentage=25):
        """
        Динамически снижает RPM лимит на заданный процент, но не ниже 1.
        Пересчитывает интервал.
        """
        with self.lock:
            # Считаем, на сколько нужно уменьшить
            reduction = int(self.rpm_limit * (percentage / 100.0))
            # Уменьшаем, но гарантируем, что останется хотя бы 1
            self.rpm_limit = max(1, self.rpm_limit - max(1, reduction)) # Уменьшаем минимум на 1
            self.interval = 60.0 / self.rpm_limit
    
    def set_rpm(self, new_rpm):
        """Принудительно устанавливает новое значение RPM."""
        with self.lock:
            if new_rpm > 0:
                self.rpm_limit = new_rpm
                self.interval = 60.0 / self.rpm_limit
    
    def update_last_request_time(self, delay=0):
        """
        Устанавливает "точку отсчета" так, чтобы следующий запрос
        был разрешен ровно через `delay` секунд, не добавляя
        дополнительного интервала RPM.
        """
        with self.lock:
            # T_next = Момент в будущем, когда мы хотим разрешить следующий запрос
            next_allowed_time = time.time() + delay
            
            # T_last_request = T_next - I
            # "Обманываем" лимитер, говоря ему, что последний запрос был сделан
            # ровно `interval` секунд назад от желаемого времени следующего запуска.
            self.last_request_time = next_allowed_time - self.interval

    def sync_last_request_time(self, timestamp):
        """
        Принудительно устанавливает время последнего запроса.
        Используется для синхронизации с внешним источником времени.
        """
        with self.lock:
            self.last_request_time = timestamp

