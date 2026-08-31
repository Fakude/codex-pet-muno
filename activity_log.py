"""
The transparency panel.

Muno's own window is deliberately tiny, so the record of what it has done lives
in a separate resizable window. Every request becomes a heading, every action
becomes a row underneath it, and each row moves from pending to running to a
final state. Nothing is hidden: the arguments Muno used and the outcome it got
are both shown.
"""
import time

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout,
)

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_DENIED = "denied"

_STATUS_MARKS = {
    STATUS_PENDING: ("○", "#7a7a7a"),
    STATUS_RUNNING: ("▶", "#2997ff"),
    STATUS_DONE: ("✓", "#30d158"),
    STATUS_FAILED: ("✕", "#ff453a"),
    STATUS_DENIED: ("⊘", "#7a7a7a"),
}

# Apple on-dark palette, so the log reads as part of the same product.
_COLOUR_TIME = "#7a7a7a"
_COLOUR_ACCENT = "#2997ff"
_COLOUR_BODY = "#ffffff"
_COLOUR_MUTED = "#cccccc"
_COLOUR_FAINT = "#7a7a7a"
_COLOUR_NOTE = "#ffd60a"


class ActivityLogWindow(QDialog):
    """A running, human-readable record of everything Muno does."""

    def __init__(self, pet_name="Muno", parent=None):
        super().__init__(parent)
        self.pet_name = pet_name
        self.setWindowTitle(f"{pet_name} — Activity Log")
        self.setMinimumSize(560, 420)
        self.resize(680, 520)
        # Let the user keep this open beside the companion.
        self.setWindowFlags(Qt.WindowType.Window)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        heading = QLabel(
            f"Everything {pet_name} does appears here, including the details it used."
        )
        heading.setWordWrap(True)
        layout.addWidget(heading)

        self.view = QTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.view.setFont(QFont("Consolas", 9))
        layout.addWidget(self.view, 1)

        controls = QHBoxLayout()
        self.follow_checkbox = QCheckBox("Follow new activity", self)
        self.follow_checkbox.setChecked(True)
        controls.addWidget(self.follow_checkbox)
        controls.addStretch(1)

        copy_button = QPushButton("Copy all", self)
        copy_button.clicked.connect(self.copy_all)
        controls.addWidget(copy_button)

        clear_button = QPushButton("Clear", self)
        clear_button.clicked.connect(self.clear_log)
        controls.addWidget(clear_button)

        close_button = QPushButton("Close", self)
        close_button.clicked.connect(self.hide)
        controls.addWidget(close_button)
        layout.addLayout(controls)

        # Apple design tokens: black canvas, one Action Blue accent, hairline
        # rules, pill buttons, and no shadows or gradients on chrome.
        self.setStyleSheet("""
            QDialog { background-color: #000000; }
            QLabel {
                color: #7a7a7a;
                font-family: 'SF Pro Text', 'Segoe UI';
                font-size: 12px;
            }
            QCheckBox {
                color: #7a7a7a;
                font-family: 'SF Pro Text', 'Segoe UI';
                font-size: 12px;
            }
            QTextEdit {
                background-color: #1c1c1e;
                color: #ffffff;
                border: 1px solid rgba(255,255,255,26);
                border-radius: 11px;
                padding: 17px;
            }
            QPushButton {
                background-color: #0066cc;
                color: #ffffff;
                font-family: 'SF Pro Text', 'Segoe UI';
                font-size: 13px;
                border: none;
                border-radius: 14px;
                padding: 7px 18px;
            }
            QPushButton:hover { background-color: #0071e3; }
            QPushButton:pressed { background-color: #0071e3; }
            QScrollBar:vertical { background: transparent; width: 8px; margin: 4px; }
            QScrollBar::handle:vertical {
                background: rgba(255,255,255,40);
                border-radius: 4px;
                min-height: 24px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

        # step key -> True once rendered, so statuses can be updated in place.
        self._rows = {}
        self._entries = []

    # -- rendering ---------------------------------------------------------

    def _render(self):
        """Rebuild the document from entries.

        Rewriting is simpler and fast enough here: a session holds tens of rows,
        not thousands, and it keeps status updates trivially correct.
        """
        blocks = []
        for entry in self._entries:
            if entry["kind"] == "request":
                blocks.append(
                    f"<div style='margin-top:24px'>"
                    f"<span style='color:{_COLOUR_TIME};font-size:12px'>"
                    f"{entry['time']}</span>&nbsp;&nbsp;"
                    f"<span style='color:{_COLOUR_ACCENT};font-weight:600'>You</span>"
                    f"<br><span style='color:{_COLOUR_BODY}'>{entry['text']}</span></div>"
                )
            elif entry["kind"] == "plan":
                blocks.append(
                    f"<div style='margin:8px 0 4px 17px;color:{_COLOUR_MUTED}'>"
                    f"{entry['text']}</div>"
                )
            elif entry["kind"] == "step":
                mark, colour = _STATUS_MARKS.get(entry["status"], _STATUS_MARKS[STATUS_PENDING])
                detail = ""
                if entry.get("detail"):
                    detail = (
                        f"<div style='margin-left:38px;color:{_COLOUR_FAINT};"
                        f"font-size:12px'>{entry['detail']}</div>"
                    )
                outcome = ""
                if entry.get("outcome"):
                    outcome = (
                        f"<div style='margin-left:38px;color:{_COLOUR_MUTED};"
                        f"font-size:12px'>{entry['outcome']}</div>"
                    )
                blocks.append(
                    f"<div style='margin:6px 0 0 17px'>"
                    f"<span style='color:{colour};font-weight:600'>{mark}</span>&nbsp;&nbsp;"
                    f"<span style='color:{_COLOUR_BODY}'>{entry['text']}</span></div>"
                    f"{detail}{outcome}"
                )
            elif entry["kind"] == "reply":
                blocks.append(
                    f"<div style='margin:12px 0 4px 17px'>"
                    f"<span style='color:{_COLOUR_ACCENT};font-weight:600'>"
                    f"{self.pet_name}</span>"
                    f"<br><span style='color:{_COLOUR_BODY}'>{entry['text']}</span></div>"
                )
            elif entry["kind"] == "note":
                blocks.append(
                    f"<div style='margin:6px 0 0 17px;color:{_COLOUR_NOTE};"
                    f"font-size:12px'>{entry['text']}</div>"
                )
        self.view.setHtml("".join(blocks))
        if self.follow_checkbox.isChecked():
            self.view.moveCursor(QTextCursor.MoveOperation.End)

    @staticmethod
    def _escape(text):
        return (
            str(text or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    @staticmethod
    def _now():
        return time.strftime("%H:%M:%S")

    # -- public API --------------------------------------------------------

    def add_request(self, text):
        self._entries.append({
            "kind": "request", "time": self._now(), "text": self._escape(text)
        })
        self._render()

    def add_plan(self, text):
        if not text:
            return
        self._entries.append({"kind": "plan", "text": self._escape(text)})
        self._render()

    def add_step(self, key, text, detail="", status=STATUS_PENDING):
        entry = {
            "kind": "step",
            "key": key,
            "text": self._escape(text),
            "detail": self._escape(detail),
            "outcome": "",
            "status": status,
        }
        self._entries.append(entry)
        self._rows[key] = entry
        self._render()

    def update_step(self, key, status=None, outcome=None):
        entry = self._rows.get(key)
        if not entry:
            return
        if status:
            entry["status"] = status
        if outcome is not None:
            entry["outcome"] = self._escape(outcome)
        self._render()

    def add_reply(self, text):
        self._entries.append({"kind": "reply", "text": self._escape(text)})
        self._render()

    def add_note(self, text):
        self._entries.append({"kind": "note", "text": self._escape(text)})
        self._render()

    def copy_all(self):
        from PyQt6.QtWidgets import QApplication

        QApplication.clipboard().setText(self.view.toPlainText())

    def clear_log(self):
        self._entries.clear()
        self._rows.clear()
        self._render()

    def show_and_raise(self):
        self.show()
        self.raise_()
        self.activateWindow()
