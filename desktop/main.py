#!/usr/bin/env python3
"""Qt cross-platform desktop admin UI for TG Crawler MVP.

Replaces or complements the web interface for local/desktop users.
Cross-platform (Windows/macOS/Linux) via PySide6.

Run:
  cd desktop
  pip install -r requirements.txt
  python main.py

Requires the database (postgres) and optionally MinIO running.
Reuses the project's common/ package for normalize/extracted logic.
"""

import os
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QPushButton, QStatusBar, QMessageBox,
    QLineEdit, QComboBox, QTableWidget, QTableWidgetItem, QHeaderView
)
from PySide6.QtCore import Qt, QTimer, QSettings
from typing import Optional
from PySide6.QtGui import QAction

# Make sure we can import from project root (common/, etc.)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.normalize import normalize_code
from common.extracted import has_meaningful_extracted

# Local modules
from .db import DesktopDB

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TG Crawler Admin (Qt)")
        self.resize(1280, 800)

        self.settings = QSettings("tg-crawler", "desktop")

        # Central tabs
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # Placeholder tabs - will be replaced with real widgets
        self._setup_messages_tab()
        self._setup_persons_tab()
        self._setup_ops_tab()
        self._setup_settings_tab()

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel("Ready | DB: not connected")
        self.status_bar.addPermanentWidget(self.status_label)

        # Menu
        self._create_menu()

        # Timer for periodic status refresh (ops, etc.)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._periodic_refresh)
        self.refresh_timer.start(5000)  # 5s

        self.db: Optional[DesktopDB] = None
        self._load_connection_settings()
        self._try_connect_db()

    def _create_menu(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        exit_action = QAction("E&xit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        help_menu = menubar.addMenu("&Help")
        about_action = QAction("&About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _setup_messages_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        header = QLabel("<b>Messages</b> — Filter, review and manage extracted Telegram posts (real DB)")
        layout.addWidget(header)

        # Simple filter row
        filter_layout = QHBoxLayout()
        self.msg_keyword = QLineEdit()
        self.msg_keyword.setPlaceholderText("Keyword (text or extracted)...")
        self.msg_keyword.textChanged.connect(self._refresh_messages)
        filter_layout.addWidget(QLabel("Search:"))
        filter_layout.addWidget(self.msg_keyword)

        self.msg_status = QComboBox()
        self.msg_status.addItems(["", "pending", "approved", "rejected", "need_review"])
        self.msg_status.currentTextChanged.connect(self._refresh_messages)
        filter_layout.addWidget(QLabel("Status:"))
        filter_layout.addWidget(self.msg_status)

        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._refresh_messages)
        filter_layout.addWidget(btn_refresh)
        filter_layout.addStretch()
        layout.addLayout(filter_layout)

        # Real data table (QTableWidget for simplicity in v1)
        self.msg_table = QTableWidget(0, 8)
        self.msg_table.setHorizontalHeaderLabels([
            "ID", "Date", "Channel", "Nickname/Code", "Status", "Conf", "Media", "Text (preview)"
        ])
        self.msg_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.msg_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.msg_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.msg_table.doubleClicked.connect(self._open_message_detail)
        layout.addWidget(self.msg_table)

        # Quick action row
        action_layout = QHBoxLayout()
        self.btn_approve = QPushButton("Approve Selected")
        self.btn_approve.clicked.connect(lambda: self._quick_review("approved"))
        action_layout.addWidget(self.btn_approve)

        self.btn_reject = QPushButton("Reject Selected")
        self.btn_reject.clicked.connect(lambda: self._quick_review("rejected"))
        action_layout.addWidget(self.btn_reject)

        self.btn_flag = QPushButton("Toggle Flag")
        self.btn_flag.clicked.connect(self._toggle_flag)
        action_layout.addWidget(self.btn_flag)
        action_layout.addStretch()
        layout.addLayout(action_layout)

        self.tabs.addTab(widget, "Messages")

        # Initial load
        QTimer.singleShot(300, self._refresh_messages)

    def _setup_persons_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("<b>Persons / Profiles</b> (grouped by code or media_group)"))
        placeholder = QLabel("Persons search, grouping (code:/album:/msg:), media preview coming soon.")
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setStyleSheet("color: gray;")
        layout.addWidget(placeholder)
        self.tabs.addTab(widget, "Persons")

    def _setup_ops_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("<b>Operations & Control</b>"))

        info = QLabel(
            "One-click start MinIO + Crawler (using local scripts or direct process management).\n"
            "Status polling for running services.\n"
            "Ported/adapted from web/main.py ops logic for cross-platform consistency."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        h = QHBoxLayout()
        self.btn_start_all = QPushButton("▶ Start All (MinIO + Crawler)")
        self.btn_start_all.clicked.connect(self._stub_start_all)
        h.addWidget(self.btn_start_all)

        self.btn_stop_crawler = QPushButton("■ Stop Crawler")
        self.btn_stop_crawler.clicked.connect(lambda: self.status_bar.showMessage("Stop stub", 1500))
        h.addWidget(self.btn_stop_crawler)

        layout.addLayout(h)

        self.service_status = QLabel("Service status: (polling every 5s)")
        layout.addWidget(self.service_status)

        self.tabs.addTab(widget, "Operations")

    def _setup_settings_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("<b>Settings & Connection</b>"))
        layout.addWidget(QLabel(
            "DB URL, S3/MinIO endpoints, crawler owner settings.\n"
            "Per-user crawler config (from user_crawler_settings).\n\n"
            "Will load from .env / .env.local + allow editing + save to QSettings."
        ))
        self.tabs.addTab(widget, "Settings")

    def _load_connection_settings(self):
        # Example: load last used DB
        db_url = self.settings.value("db_url", "postgresql://tguser:tgpwd@localhost:5433/tg_crawler")
        self.db_url = db_url

    def _try_connect_db(self):
        try:
            self.db = DesktopDB(self.db_url)
            self.status_label.setText(f"Ready | DB: {self.db_url.split('@')[-1] if '@' in self.db_url else 'configured'}")
            self.status_bar.showMessage("Connected to DB", 2000)
            self._refresh_messages()
        except Exception as e:
            self.db = None
            self.status_label.setText("DB: connection failed")
            QMessageBox.warning(self, "DB Error", f"Could not connect to {self.db_url}:\n{e}\n\nMake sure postgres is running (port 5433 in compose).")

    def _periodic_refresh(self):
        # Update service status + quick stats from DB
        if self.db:
            try:
                stats = self.db.get_runtime_stats()
                self.service_status.setText(
                    f"Messages: {stats.get('total_messages',0)} | Pending: {stats.get('pending',0)} | With media: {stats.get('with_media',0)}"
                )
            except Exception:
                pass
        else:
            self.service_status.setText("Service status: DB not connected")

    def _stub_start_all(self):
        self.status_bar.showMessage("Start All clicked — will call local scripts or direct launcher (Phase 0 ops logic)", 3000)
        QMessageBox.information(self, "Ops", "This will eventually reuse the robust start logic from web/main.py (platform aware, lock, logs).")

    # --- Messages real implementation (wired to DesktopDB) ---

    def _refresh_messages(self):
        if not self.db:
            self._try_connect_db()
            if not self.db:
                return
        try:
            rows = self.db.fetch_messages(
                status=self.msg_status.currentText() or None,
                keyword=self.msg_keyword.text().strip() or None,
                limit=200,
            )
            self.msg_table.setRowCount(len(rows))
            for i, r in enumerate(rows):
                self.msg_table.setItem(i, 0, QTableWidgetItem(str(r.get("id", ""))))
                self.msg_table.setItem(i, 1, QTableWidgetItem(str(r.get("telegram_date", ""))[:19]))
                self.msg_table.setItem(i, 2, QTableWidgetItem(str(r.get("channel_name", ""))))
                nick = r.get("nickname") or ""
                code = r.get("code") or ""
                self.msg_table.setItem(i, 3, QTableWidgetItem(f"{nick} / {code}".strip(" /")))
                self.msg_table.setItem(i, 4, QTableWidgetItem(str(r.get("review_status", ""))))
                conf = r.get("extract_confidence")
                self.msg_table.setItem(i, 5, QTableWidgetItem(f"{conf:.2f}" if conf is not None else ""))
                self.msg_table.setItem(i, 6, QTableWidgetItem("✓" if r.get("has_media") else ""))
                text_preview = (r.get("text_content") or "")[:80].replace("\n", " ")
                self.msg_table.setItem(i, 7, QTableWidgetItem(text_preview))
                # Store full row data
                self.msg_table.item(i, 0).setData(Qt.UserRole, dict(r))
        except Exception as e:
            self.status_bar.showMessage(f"DB error: {e}", 4000)

    def _open_message_detail(self, index):
        row = index.row()
        item = self.msg_table.item(row, 0)
        if not item:
            return
        data = item.data(Qt.UserRole) or {}
        msg_id = data.get("id")
        text = data.get("text_content", "")
        extracted = data.get("extracted_json") or {}
        status = data.get("review_status", "")

        detail = QMessageBox(self)
        detail.setWindowTitle(f"Message #{msg_id}")
        detail.setText(f"Status: {status}\n\n{text[:500]}")
        detail.setDetailedText(str(extracted)[:2000])
        detail.setStandardButtons(QMessageBox.Ok | QMessageBox.Apply)
        # For demo, Apply does approve
        if detail.exec() == QMessageBox.Apply:
            self._quick_review("approved", specific_id=msg_id)

    def _quick_review(self, new_status: str, specific_id: Optional[int] = None):
        if not self.db:
            return
        selected = []
        if specific_id:
            selected = [specific_id]
        else:
            for item in self.msg_table.selectedItems():
                if item.column() == 0:
                    try:
                        selected.append(int(item.text()))
                    except:
                        pass
        if not selected:
            self.status_bar.showMessage("No rows selected", 1500)
            return

        for mid in selected:
            try:
                self.db.update_review(mid, new_status, reviewer_id=1)  # demo reviewer
            except Exception as e:
                self.status_bar.showMessage(f"Update failed for {mid}: {e}", 2000)
                return
        self.status_bar.showMessage(f"Updated {len(selected)} to {new_status}", 2000)
        self._refresh_messages()

    def _toggle_flag(self):
        # Demo: just refresh for now (real impl would read current flag and flip)
        self.status_bar.showMessage("Toggle flag (demo - extend in _quick_review style)", 2000)
        self._refresh_messages()

    # --- end messages ---

    def _show_about(self):
        QMessageBox.about(
            self,
            "About TG Crawler Qt",
            "TG Crawler MVP Desktop Admin\n\n"
            "Qt6 (PySide6) cross-platform UI.\n"
            "Reuses project's common/ package and DB schema.\n\n"
            "Part of global optimization + new desktop interface work."
        )

    def closeEvent(self, event):
        self.refresh_timer.stop()
        self.settings.setValue("db_url", self.db_url)
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("TG Crawler Admin")
    app.setOrganizationName("tg-crawler-mvp")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
