"""
Choosing what Muno looks like.

Browses the community gallery, downloads a chosen pet, and reports the creator.
Fetching happens on a worker thread so the list never blocks the interface, and
whatever is already downloaded is shown immediately so the picker is useful with
no network at all.
"""
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout, QWidget,
)

import pets

BUILT_IN_ID = "__muno__"


class _CatalogueWorker(QThread):
    """Fetches the gallery listing without blocking the dialog."""

    ready = pyqtSignal(object, object)

    def run(self):
        try:
            listing = pets.fetch_catalogue()
            self.ready.emit(listing, pets.catalogue_error())
        except Exception as exc:
            self.ready.emit([], str(exc))


class _PreviewWorker(QThread):
    """Downloads one preview image."""

    ready = pyqtSignal(str, object)

    def __init__(self, pet_id, url, parent=None):
        super().__init__(parent)
        self.pet_id = pet_id
        self.url = url

    def run(self):
        try:
            data = pets._request(self.url)
            self.ready.emit(self.pet_id, data)
        except Exception:
            self.ready.emit(self.pet_id, None)


class _InstallWorker(QThread):
    """Downloads a pet's sprite atlas."""

    done = pyqtSignal(bool, str, object)

    def __init__(self, pet, parent=None):
        super().__init__(parent)
        self.pet = pet

    def run(self):
        ok, message = pets.install(self.pet)
        self.done.emit(ok, message, self.pet)


class PetPickerDialog(QDialog):
    """Pick an appearance for the companion."""

    def __init__(self, current_pet_id=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose a pet")
        self.setMinimumSize(660, 460)
        self.resize(760, 540)

        self.selected_pet_id = current_pet_id or BUILT_IN_ID
        self._entries = {}
        self._preview_workers = []
        self._install_worker = None
        self._catalogue_worker = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(10)

        heading = QLabel(
            "Pets are pixel art shared by their creators at codex-pets.net. "
            "Pick one and Muno takes its shape, keeping all the same abilities."
        )
        heading.setWordWrap(True)
        outer.addWidget(heading)

        body = QHBoxLayout()
        body.setSpacing(12)

        self.list_widget = QListWidget(self)
        self.list_widget.setMinimumWidth(260)
        self.list_widget.currentItemChanged.connect(self._on_selection_changed)
        body.addWidget(self.list_widget, 1)

        side = QWidget(self)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(8)

        self.preview_label = QLabel(self)
        self.preview_label.setFixedSize(240, 260)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setObjectName("preview")
        self.preview_label.setText("Select a pet")
        side_layout.addWidget(self.preview_label, 0, Qt.AlignmentFlag.AlignHCenter)

        self.detail_label = QLabel(self)
        self.detail_label.setWordWrap(True)
        self.detail_label.setMinimumHeight(90)
        self.detail_label.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        side_layout.addWidget(self.detail_label, 1)
        body.addWidget(side, 0)
        outer.addLayout(body)

        self.status_label = QLabel("Loading the gallery…", self)
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.clicked.connect(lambda: self._load_catalogue(force=True))
        buttons.addWidget(self.refresh_button)
        buttons.addStretch(1)

        self.use_button = QPushButton("Use this pet", self)
        self.use_button.setDefault(True)
        self.use_button.clicked.connect(self._use_selected)
        buttons.addWidget(self.use_button)

        cancel = QPushButton("Cancel", self)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        outer.addLayout(buttons)

        self.setStyleSheet("""
            QDialog { background-color: #1e1e2e; }
            QLabel { color: #cdd6f4; font-family: 'Segoe UI'; font-size: 12px; }
            QLabel#preview {
                background-color: #11111b;
                border: 1px solid #45475a;
                border-radius: 8px;
                color: #6c7086;
            }
            QListWidget {
                background-color: #11111b;
                color: #cdd6f4;
                border: 1px solid #313244;
                border-radius: 8px;
                font-size: 12px;
            }
            QListWidget::item { padding: 7px 8px; }
            QListWidget::item:selected { background-color: #89b4fa; color: #11111b; }
            QPushButton {
                background-color: #89b4fa; color: #11111b; font-weight: bold;
                border-radius: 5px; padding: 6px 14px;
            }
            QPushButton:hover { background-color: #b5c7ff; }
        """)

        self._populate_local()
        self._load_catalogue()

    # -- population --------------------------------------------------------

    def _add_entry(self, entry, label=None):
        pet_id = entry["id"]
        if pet_id in self._entries:
            return
        self._entries[pet_id] = entry
        item = QListWidgetItem(label or self._label_for(entry))
        item.setData(Qt.ItemDataRole.UserRole, pet_id)
        self.list_widget.addItem(item)
        if pet_id == self.selected_pet_id:
            self.list_widget.setCurrentItem(item)

    @staticmethod
    def _label_for(entry):
        mark = "⤓ " if pets.is_installed(entry["id"]) else ""
        return f"{mark}{entry['name']}  ·  {entry.get('kind', '')}"

    def _populate_local(self):
        """Built-in look first, then anything already downloaded."""
        self._add_entry(
            {
                "id": BUILT_IN_ID,
                "name": "Muno (built-in)",
                "description": "The original hand-drawn Muno, animated in code.",
                "kind": "built-in",
                "owner": "this app",
                "spritesheet_url": "",
                "preview_url": "",
                "tags": [],
            },
            label="Muno (built-in)",
        )
        for entry in pets.installed_pets():
            self._add_entry(entry)

    def _load_catalogue(self, force=False):
        self.refresh_button.setEnabled(False)
        self.status_label.setText("Loading the gallery…")
        if force:
            pets._catalogue_cache["pets"] = None
        self._catalogue_worker = _CatalogueWorker(self)
        self._catalogue_worker.ready.connect(self._on_catalogue)
        self._catalogue_worker.start()

    def _on_catalogue(self, listing, error):
        self.refresh_button.setEnabled(True)
        for entry in listing or []:
            self._add_entry(entry)
        downloaded = sum(1 for pet_id in self._entries if pets.is_installed(pet_id))
        if error:
            self.status_label.setText(
                f"Couldn't reach the gallery, showing {downloaded} downloaded pet(s). "
                f"({str(error)[:70]})"
            )
        else:
            self.status_label.setText(
                f"{len(listing or [])} pets in the gallery, {downloaded} downloaded."
            )
        if self.list_widget.currentItem() is None and self.list_widget.count():
            self.list_widget.setCurrentRow(0)

    # -- selection ---------------------------------------------------------

    def _on_selection_changed(self, current, _previous):
        if current is None:
            return
        pet_id = current.data(Qt.ItemDataRole.UserRole)
        entry = self._entries.get(pet_id)
        if not entry:
            return

        if pet_id == BUILT_IN_ID:
            self.preview_label.setText("Muno")
            self.preview_label.setPixmap(QPixmap())
            self.detail_label.setText(entry["description"])
            self.use_button.setText("Use built-in Muno")
            return

        tags = ", ".join(entry.get("tags") or []) or "none"
        state = "downloaded" if pets.is_installed(pet_id) else "will be downloaded"
        self.detail_label.setText(
            f"<b>{entry['name']}</b><br>"
            f"<span style='color:#a6adc8'>by {entry.get('owner', 'unknown')}</span><br><br>"
            f"{entry.get('description', '')[:260]}<br><br>"
            f"<span style='color:#6c7086'>kind: {entry.get('kind', '?')} · "
            f"tags: {tags} · animations: {max(0, entry.get('rows', 0))} · {state}</span>"
        )
        self.use_button.setText(
            "Use this pet" if pets.is_installed(pet_id) else "Download and use"
        )

        # Show the cached sheet if we have it, otherwise fetch the preview.
        local = pets.spritesheet_path(pet_id)
        if local:
            sheet = QPixmap(local)
            if not sheet.isNull():
                first = sheet.copy(0, 0, pets.CELL_WIDTH, pets.CELL_HEIGHT)
                self._show_preview_pixmap(first)
                return
        self.preview_label.setText("loading…")
        self.preview_label.setPixmap(QPixmap())
        url = entry.get("preview_url")
        if url:
            worker = _PreviewWorker(pet_id, url, self)
            worker.ready.connect(self._on_preview)
            self._preview_workers.append(worker)
            worker.start()

    def _show_preview_pixmap(self, pixmap):
        scaled = pixmap.scaled(
            self.preview_label.width() - 12,
            self.preview_label.height() - 12,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setPixmap(scaled)

    def _on_preview(self, pet_id, data):
        current = self.list_widget.currentItem()
        if current is None or current.data(Qt.ItemDataRole.UserRole) != pet_id:
            return
        if not data:
            self.preview_label.setText("no preview")
            return
        pixmap = QPixmap()
        if pixmap.loadFromData(data) and not pixmap.isNull():
            self._show_preview_pixmap(pixmap)
        else:
            self.preview_label.setText("no preview")

    # -- confirmation ------------------------------------------------------

    def _use_selected(self):
        current = self.list_widget.currentItem()
        if current is None:
            return
        pet_id = current.data(Qt.ItemDataRole.UserRole)
        entry = self._entries.get(pet_id)
        if not entry:
            return

        if pet_id == BUILT_IN_ID or pets.is_installed(pet_id):
            self.selected_pet_id = pet_id
            self.accept()
            return

        self.use_button.setEnabled(False)
        self.status_label.setText(f"Downloading {entry['name']}…")
        self._install_worker = _InstallWorker(entry, self)
        self._install_worker.done.connect(self._on_installed)
        self._install_worker.start()

    def _on_installed(self, ok, message, entry):
        self.use_button.setEnabled(True)
        self.status_label.setText(message)
        if ok:
            self.selected_pet_id = entry["id"]
            self.accept()

    def chosen_pet_id(self):
        """Return the chosen id, or None for the built-in look."""
        return None if self.selected_pet_id == BUILT_IN_ID else self.selected_pet_id
