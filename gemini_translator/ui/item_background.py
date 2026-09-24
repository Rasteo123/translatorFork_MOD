"""Фон элемента из модели под таблицей стилей темы.

Тема задаёт правило ``QTableWidget::item, QListWidget::item`` со скруглением и
полями (themes.py). С ним QStyleSheetStyle рисует элемент сам и не смотрит на
кисть из модели: ``setBackground`` у QTableWidgetItem и QListWidgetItem,
``BackgroundRole`` у модели молча пропадают. Делегат заливает эту кисть сам,
плашкой той же формы, что у наведения и выделения, и снимает её с опции, чтобы
стиль без таблицы стилей не залил ячейку второй раз.

Модуль лежит вне ui/widgets: пакет ui/widgets при импорте тянет glossary_widget,
а тот — делегаты глоссария, которые наследуются отсюда (см. delegate_utils.py).
"""

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from .themes import ITEM_MARGIN_X, ITEM_MARGIN_Y, ITEM_RADIUS


def paint_item_background(painter: QPainter, option: QStyleOptionViewItem) -> None:
    """Заливает фон элемента плашкой темы и снимает кисть с ``option``.

    ``option`` уже заполнена ``initStyleOption``: кисть берётся из неё.
    """
    brush = option.backgroundBrush
    if brush.style() == Qt.BrushStyle.NoBrush:
        return
    painter.save()
    # Без сглаживания дуги плашек дают «лесенки» на экранах с DPR=1.
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(brush)
    plate = QRectF(option.rect).adjusted(
        ITEM_MARGIN_X, ITEM_MARGIN_Y, -ITEM_MARGIN_X, -ITEM_MARGIN_Y
    )
    painter.drawRoundedRect(plate, ITEM_RADIUS, ITEM_RADIUS)
    painter.restore()
    option.backgroundBrush = QBrush()


class ItemBackgroundDelegate(QStyledItemDelegate):
    """QStyledItemDelegate, у которого фон из модели виден и под темой."""

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        paint_item_background(painter, opt)
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)


class CellWidgetDelegate(ItemBackgroundDelegate):
    """Ставит виджет ячейки (``setCellWidget``) на плашку элемента.

    QStyledItemDelegate кладёт его в поле текста, а правило темы ``::item``
    сужает это поле полями, рамкой и отступами: под темой macOS комбобокс в
    строке 30 px сдвигался на 13 px вбок, на 5 px вниз и получал 19 px высоты.
    Строка должна вмещать виджет вместе с полями плашки, это задаёт таблица.
    """

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(
            option.rect.adjusted(ITEM_MARGIN_X, ITEM_MARGIN_Y, -ITEM_MARGIN_X, -ITEM_MARGIN_Y)
        )
