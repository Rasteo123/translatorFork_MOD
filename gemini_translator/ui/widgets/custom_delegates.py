from PyQt6.QtWidgets import QStyledItemDelegate, QTableWidget, QStyle
from PyQt6.QtGui import QPainter, QPainterPath, QColor
from PyQt6.QtCore import Qt

class RoundedSelectionDelegate(QStyledItemDelegate):
    def __init__(self, parent=None, radius=6.0, bg_color="#30d87a3a", text_color=None):
        super().__init__(parent)
        self.radius = radius
        # Default to a soft accent color
        self.bg_color = QColor(bg_color)
        self.text_color = QColor(text_color) if text_color else None

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = option.rect
        path = QPainterPath()
        
        model = index.model()
        is_first = index.column() == 0
        is_last = index.column() == model.columnCount() - 1
        
        x, y, w, h = float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height())
        
        from PyQt6.QtCore import QRectF
        path.addRoundedRect(QRectF(rect), self.radius, self.radius)
        if not (is_first and is_last):
            if is_first:
                path.addRect(x + self.radius, y, w - self.radius, h)
            elif is_last:
                path.addRect(x, y, w - self.radius, h)
            else:
                path = QPainterPath()
                path.addRect(QRectF(rect))
                
        path = path.simplified()
        
        is_selected = option.state & QStyle.StateFlag.State_Selected

        if is_selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.bg_color)
            painter.drawPath(path)

            option.state &= ~QStyle.StateFlag.State_Selected

            if self.text_color:
                option.palette.setColor(option.palette.ColorRole.Text, self.text_color)
                option.palette.setColor(option.palette.ColorRole.HighlightedText, self.text_color)
        else:
            bg_brush = index.data(Qt.ItemDataRole.BackgroundRole)
            if bg_brush:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(bg_brush)
                
                # Draw the individual cell background fully rounded
                cell_path = QPainterPath()
                cell_path.addRoundedRect(QRectF(rect), self.radius, self.radius)
                painter.drawPath(cell_path)
                
                from PyQt6.QtGui import QBrush
                option.backgroundBrush = QBrush(Qt.BrushStyle.NoBrush)

        super().paint(painter, option, index)
        painter.restore()

def apply_rounded_selection(table: QTableWidget, bg_color="#30d87a3a", text_color=None):
    """Helper to apply the delegate to a table."""
    delegate = RoundedSelectionDelegate(table, bg_color=bg_color, text_color=text_color)
    table.setItemDelegate(delegate)

