# gemini_translator/ui/widgets/common_widgets.py

from PyQt6 import QtCore, QtGui
from PyQt6.QtWidgets import QSpinBox, QDoubleSpinBox, QComboBox


# --- БАЗОВЫЙ КЛАСС С НОВОЙ ЛОГИКОЙ ---
class HoverControlMixin:
    """
    Миксин (примесь), который добавляет логику активации прокрутки
    колесом мыши при наведении курсора на виджет.
    """
    def __init__(self, *args, **kwargs):
        # Важно вызвать __init__ родительского класса (QSpinBox, QComboBox и т.д.)
        super().__init__(*args, **kwargs)
        
        # Флаг, разрешающий прокрутку
        self._wheel_scroll_enabled = False
        
        # Одноразовый таймер для активации
        self._hover_timer = QtCore.QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(500)
        self._hover_timer.timeout.connect(self._enable_wheel_scroll)
        
        # Устанавливаем политику фокуса, чтобы клик тоже работал
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

    def _enable_wheel_scroll(self):
        """Слот, который вызывается по таймеру. Разрешает прокрутку."""
        self._wheel_scroll_enabled = True

    def enterEvent(self, event: QtGui.QEnterEvent):
        """Вызывается, когда курсор входит в область виджета."""
        super().enterEvent(event)
        # Запускаем таймер активации
        self._hover_timer.start()

    def leaveEvent(self, event: QtCore.QEvent):
        """Вызывается, когда курсор покидает область виджета."""
        super().leaveEvent(event)
        # Останавливаем таймер и сбрасываем флаг
        self._hover_timer.stop()
        self._wheel_scroll_enabled = False
        
    def wheelEvent(self, event: QtGui.QWheelEvent):
        # Проверяем условия: прокрутка разрешена, если виджет в фокусе
        # ИЛИ если сработал таймер наведения.
        if self.hasFocus() or self._wheel_scroll_enabled:
            # Если да, передаем событие родительскому классу для стандартной обработки
            super().wheelEvent(event)
        else:
            # Если нет, игнорируем событие
            event.ignore()

# --- ОБНОВЛЕННЫЕ КЛАССЫ ВИДЖЕТОВ ---



class NoScrollSpinBox(HoverControlMixin, QSpinBox):
    """
    Улучшенный QSpinBox, который игнорирует прокрутку колеса мыши,
    если он не находится в фокусе. Это предотвращает случайное изменение
    значения при прокрутке всего окна.
    """
    pass
class NoScrollDoubleSpinBox(HoverControlMixin, QDoubleSpinBox):
    """
    Аналогичная версия для QDoubleSpinBox (например, для настройки температуры).
    """
    pass
class NoScrollComboBox(HoverControlMixin, QComboBox):
    """
    Улучшенный QComboBox, который игнорирует прокрутку колеса мыши,
    если он не в фокусе И его выпадающий список закрыт.
    Это предотвращает случайное изменение значения при прокрутке.
    """

    def wheelEvent(self, event: QtGui.QWheelEvent):
        # Для комбобокса добавляем еще одно условие:
        # прокрутка всегда должна работать, если выпадающий список открыт.
        if self.hasFocus() or self._wheel_scroll_enabled or self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()