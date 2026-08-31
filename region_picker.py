"""
Choosing a screen region, and setting up a repeating watch over it.

Two pieces: a full-screen overlay for dragging out a rectangle, and a dialog for
saying what should happen when the chosen thing reappears there.
"""
from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

import watcher


class RegionOverlay(QWidget):
    """A dim full-screen sheet the user drags a rectangle on."""

    chosen = pyqtSignal(object)

    def __init__(self, prompt="Drag around the area to watch"):
        super().__init__()
        self.prompt = prompt
        self.origin = None
        self.current = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        # Cover every monitor so a region can be picked on any of them.
        geometry = QRect()
        for screen in QApplication.screens():
            geometry = geometry.united(screen.geometry())
        self.setGeometry(geometry)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 110))

        if self.origin and self.current:
            box = QRect(self.origin, self.current).normalized()
            # Punch the selection back to clear so the content shows through.
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Clear
            )
            painter.fillRect(box, Qt.GlobalColor.transparent)
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceOver
            )
            painter.setPen(QPen(QColor("#89b4fa"), 2))
            painter.drawRect(box)
            painter.setPen(QPen(QColor("#ffffff")))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(
                box.left(), max(14, box.top() - 6),
                f"{box.width()} x {box.height()}",
            )

        painter.setPen(QPen(QColor("#ffffff")))
        painter.setFont(QFont("Segoe UI", 13))
        painter.drawText(
            QRect(0, 40, self.width(), 40),
            Qt.AlignmentFlag.AlignHCenter,
            f"{self.prompt}     (Esc to cancel)",
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.position().toPoint()
            self.current = self.origin
            self.update()

    def mouseMoveEvent(self, event):
        if self.origin:
            self.current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if not self.origin:
            return
        box = QRect(self.origin, event.position().toPoint()).normalized()
        self.hide()
        if box.width() < 12 or box.height() < 12:
            self.chosen.emit(None)   # Too small to be deliberate.
        else:
            offset = self.geometry().topLeft()
            self.chosen.emit((
                box.left() + offset.x(), box.top() + offset.y(),
                box.width(), box.height(),
            ))
        self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            self.chosen.emit(None)
            self.close()


class PointOverlay(QWidget):
    """A full-screen sheet for picking the single spot to click."""

    chosen = pyqtSignal(object)

    def __init__(self, watch_region=None,
                 prompt="Now click exactly where I should click"):
        super().__init__()
        self.prompt = prompt
        self.watch_region = watch_region
        self.hover = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)

        geometry = QRect()
        for screen in QApplication.screens():
            geometry = geometry.united(screen.geometry())
        self.setGeometry(geometry)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 110))
        offset = self.geometry().topLeft()

        # Keep the already-chosen watch area visible for context.
        if self.watch_region:
            left, top, width, height = self.watch_region
            box = QRect(left - offset.x(), top - offset.y(), width, height)
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Clear
            )
            painter.fillRect(box, Qt.GlobalColor.transparent)
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceOver
            )
            painter.setPen(QPen(QColor("#a6e3a1"), 2))
            painter.drawRect(box)
            painter.setPen(QPen(QColor("#a6e3a1")))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(box.left(), max(12, box.top() - 6), "watching this")

        if self.hover:
            painter.setPen(QPen(QColor("#f38ba8"), 2))
            painter.drawLine(self.hover.x() - 14, self.hover.y(),
                             self.hover.x() + 14, self.hover.y())
            painter.drawLine(self.hover.x(), self.hover.y() - 14,
                             self.hover.x(), self.hover.y() + 14)
            painter.drawEllipse(self.hover, 16, 16)
            painter.setPen(QPen(QColor("#ffffff")))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(
                self.hover.x() + 22, self.hover.y() - 8,
                f"{self.hover.x() + offset.x()}, {self.hover.y() + offset.y()}",
            )

        painter.setPen(QPen(QColor("#ffffff")))
        painter.setFont(QFont("Segoe UI", 13))
        painter.drawText(
            QRect(0, 40, self.width(), 40),
            Qt.AlignmentFlag.AlignHCenter,
            f"{self.prompt}     (Esc to cancel)",
        )

    def mouseMoveEvent(self, event):
        self.hover = event.position().toPoint()
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = event.position().toPoint()
        offset = self.geometry().topLeft()
        self.hide()
        self.chosen.emit((point.x() + offset.x(), point.y() + offset.y()))
        self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            self.chosen.emit(None)
            self.close()


class WatchSetupDialog(QDialog):
    """Confirm what to watch for and what to do about it."""

    def __init__(self, region, preview_image, click_point=None, parent=None):
        super().__init__(parent)
        self.region = region
        self.click_point = click_point
        self.setWindowTitle("Set up a repeating watch")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        left, top, width, height = region
        where = (
            f"and click at {click_point[0]}, {click_point[1]}"
            if click_point else "and click the middle of it"
        )
        heading = QLabel(
            f"I'll watch the {width}x{height} area at {left}, {top} {where} "
            "whenever what's in it right now appears again."
        )
        heading.setWordWrap(True)
        layout.addWidget(heading)

        # Show exactly what was captured, so there is no doubt what will match.
        self.preview = QLabel(self)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet(
            "background:#11111b; border:1px solid #45475a; border-radius:6px; padding:4px;"
        )
        if preview_image is not None:
            self.preview.setPixmap(preview_image.scaled(
                420, 150, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        layout.addWidget(self.preview)

        form = QFormLayout()

        self.name_input = QLineEdit(self)
        self.name_input.setPlaceholderText("e.g. Kiro approvals")
        form.addRow("Call it:", self.name_input)

        # A preset fills in the trigger, question and reaction together, since
        # those three choices only make sense as a set.
        self.preset_choice = QComboBox(self)
        for label, key in (
            ("Click a button each time it appears", "autoclick"),
            ("Tell me when this changes", "notify"),
            ("Watch for something specific and tell me", "ask"),
            ("Check whether something here looks unsafe", "security"),
            ("Keep track of this figure and log it", "track"),
            ("Watch a video and tell me when something happens", "video"),
        ):
            self.preset_choice.addItem(label, key)
        self.preset_choice.currentIndexChanged.connect(self._apply_preset)
        form.addRow("What should I do:", self.preset_choice)

        self.question_input = QLineEdit(self)
        self.question_input.setPlaceholderText(
            "e.g. Does this show an error? Is this link suspicious?"
        )
        form.addRow("Look for:", self.question_input)

        self.action_choice = QComboBox(self)
        if click_point:
            self.action_choice.addItem(
                f"Click {click_point[0]}, {click_point[1]}", "point"
            )
        self.action_choice.addItem("Click the middle of the watched area", "click")
        self.action_choice.addItem("Press Enter", "enter")
        self.action_choice.addItem("Tell me about it", "notify")
        self.action_choice.addItem("Tell me and save a picture", "report")
        form.addRow("Then:", self.action_choice)

        self.interval = QSpinBox(self)
        self.interval.setRange(1, 60)
        self.interval.setValue(2)
        self.interval.setSuffix(" s")
        form.addRow("Check every:", self.interval)

        self.max_actions = QSpinBox(self)
        self.max_actions.setRange(1, 500)
        self.max_actions.setValue(25)
        form.addRow("Stop after:", self.max_actions)

        self.expiry = QSpinBox(self)
        self.expiry.setRange(1, 600)
        self.expiry.setValue(60)
        self.expiry.setSuffix(" min")
        form.addRow("Or after:", self.expiry)

        self.sensitivity = QSpinBox(self)
        self.sensitivity.setRange(70, 99)
        self.sensitivity.setValue(92)
        self.sensitivity.setSuffix(" %")
        form.addRow("Match closeness:", self.sensitivity)
        layout.addLayout(form)

        caution = QLabel(
            "This clicks for you while you're working elsewhere. It only ever "
            "clicks inside the area above, stops at whichever limit comes first, "
            "and logs every click. If that area starts showing something "
            "different, it will not match and nothing will happen."
        )
        caution.setWordWrap(True)
        caution.setStyleSheet("color:#f9e2af; font-size:11px;")
        layout.addWidget(caution)

        self._apply_preset()

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel", self)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        start = QPushButton("Start watching", self)
        start.setDefault(True)
        start.clicked.connect(self.accept)
        buttons.addWidget(start)
        layout.addLayout(buttons)

        self.setStyleSheet("""
            QDialog { background-color: #1e1e2e; }
            QLabel { color: #cdd6f4; font-family: 'Segoe UI'; font-size: 12px; }
            QComboBox, QSpinBox {
                background-color: #313244; color: #cdd6f4;
                border: 1px solid #45475a; border-radius: 4px; padding: 3px;
            }
            QPushButton {
                background-color: #89b4fa; color: #11111b; font-weight: bold;
                border-radius: 5px; padding: 6px 14px;
            }
            QPushButton:hover { background-color: #b5c7ff; }
        """)

    # preset -> (trigger, suggested question, reaction, seconds between checks)
    PRESETS = {
        "autoclick": ("appears", "", None, 2),
        "notify": ("changes", "", "notify", 3),
        "ask": ("ask", "Has anything important appeared here? Answer Yes or No.",
                "notify", 5),
        "security": ("ask",
                     "Does anything here look unsafe, such as a suspicious link, "
                     "a warning, or a request for credentials? Answer Yes or No, "
                     "then say why in a few words.",
                     "report", 5),
        "track": ("changes", "What value or figure is shown here now?", "report", 10),
        "video": ("ask",
                  "Describe what is happening in this video frame in a few words, "
                  "and start with Yes if something notable just happened.",
                  "notify", 5),
    }

    def _apply_preset(self):
        """Fill the other fields to match the chosen use case."""
        key = self.preset_choice.currentData()
        trigger, question, reaction, interval = self.PRESETS.get(
            key, ("appears", "", None, 2)
        )
        self.question_input.setText(question)
        self.question_input.setEnabled(trigger in ("ask", "changes") and bool(question) or trigger == "ask")
        self.interval.setValue(interval)
        if reaction:
            for index in range(self.action_choice.count()):
                if self.action_choice.itemData(index) == reaction:
                    self.action_choice.setCurrentIndex(index)
                    break
        elif self.click_point:
            for index in range(self.action_choice.count()):
                if self.action_choice.itemData(index) == "point":
                    self.action_choice.setCurrentIndex(index)
                    break

    def settings(self):
        preset = self.preset_choice.currentData()
        trigger = self.PRESETS.get(preset, ("appears",))[0]
        return {
            "name": self.name_input.text().strip(),
            "preset": preset,
            "trigger": trigger,
            "question": self.question_input.text().strip(),
            "action": self.action_choice.currentData(),
            "click_point": self.click_point,
            "interval": float(self.interval.value()),
            "max_actions": int(self.max_actions.value()),
            "expiry_minutes": int(self.expiry.value()),
            "threshold": self.sensitivity.value() / 100.0,
        }
