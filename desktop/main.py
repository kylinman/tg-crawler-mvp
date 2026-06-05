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
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QTabWidget, QLabel, QPushButton, QStatusBar, QMessageBox,
    QLineEdit, QComboBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QFormLayout, QSplitter, QTextEdit, QFrame, QToolBar, QMenu
)
from PySide6.QtCore import Qt, QTimer, QSettings, QSize
from PySide6.QtGui import QAction, QIcon, QFont
from typing import Optional


# Make sure we can import from project root (common/, etc.)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.normalize import normalize_code
from common.extracted import has_meaningful_extracted

# Local modules
from desktop.db import DesktopDB

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TG Crawler Admin (Qt)")
        self.resize(1280, 800)

        self.settings = QSettings("tg-crawler", "desktop")

        # Load settings early
        self._load_connection_settings()

        # Modern Qt6 styling
        self._apply_modern_style()

        # Central tabs
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # Modern top toolbar
        self._create_toolbar()

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

    def _create_toolbar(self):
        """Modern Qt toolbar with common actions."""
        toolbar = QToolBar("Main Toolbar")
        toolbar.setIconSize(QSize(18, 18))
        self.addToolBar(toolbar)

        # Refresh action
        refresh_action = QAction("Refresh", self)
        refresh_action.triggered.connect(self._refresh_current_tab)
        toolbar.addAction(refresh_action)

        toolbar.addSeparator()

        # Quick ops actions (modern buttons in toolbar)
        start_action = QAction("▶ Start All", self)
        start_action.triggered.connect(self._stub_start_all)
        toolbar.addAction(start_action)

        stop_action = QAction("■ Stop Crawler", self)
        stop_action.triggered.connect(lambda: self.status_bar.showMessage("Stop action (extend with real controller)", 2000))
        toolbar.addAction(stop_action)

        toolbar.addSeparator()

        about_action = QAction("About", self)
        about_action.triggered.connect(self._show_about)
        toolbar.addAction(about_action)

    def _refresh_current_tab(self):
        if self.tabs.currentIndex() == 0:  # Messages
            self._refresh_messages()
        else:
            self.status_bar.showMessage("Refresh for current tab (stub)", 1500)

    def _save_settings(self):
        self.db_url = self.db_url_edit.text().strip()
        self.settings.setValue("db_url", self.db_url)
        self.status_bar.showMessage("Settings saved (reconnect on next action)", 2000)
        self._try_connect_db()

    def _apply_modern_style(self):
        """Apply a clean, modern Qt6 flat design stylesheet."""
        style = """
            QMainWindow {
                background-color: #f8f9fa;
            }
            QTabWidget::pane {
                border: 1px solid #dee2e6;
                background: white;
                border-radius: 4px;
            }
            QTabBar::tab {
                background: #e9ecef;
                color: #495057;
                padding: 10px 20px;
                border: 1px solid #dee2e6;
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: white;
                color: #212529;
                font-weight: 600;
            }
            QTabBar::tab:hover {
                background: #dee2e6;
            }
            QPushButton {
                background-color: #0d6efd;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #0b5ed7;
            }
            QPushButton:pressed {
                background-color: #0a58ca;
            }
            QPushButton#secondary {
                background-color: #6c757d;
            }
            QPushButton#secondary:hover {
                background-color: #5c636a;
            }
            QLineEdit, QComboBox {
                border: 1px solid #ced4da;
                border-radius: 4px;
                padding: 6px 10px;
                background: white;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #86b7fe;
                outline: none;
            }
            QTableWidget {
                border: 1px solid #dee2e6;
                gridline-color: #e9ecef;
                alternate-background-color: #f8f9fa;
                selection-background-color: #e7f1ff;
            }
            QTableWidget::item {
                padding: 6px;
            }
            QHeaderView::section {
                background-color: #e9ecef;
                color: #495057;
                padding: 8px;
                border: none;
                border-right: 1px solid #dee2e6;
                font-weight: 600;
            }
            QGroupBox {
                font-weight: 600;
                border: 1px solid #dee2e6;
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                color: #212529;
            }
            QLabel {
                color: #212529;
            }
            QStatusBar {
                background: #e9ecef;
                border-top: 1px solid #dee2e6;
            }
            QToolBar {
                background: #ffffff;
                border-bottom: 1px solid #dee2e6;
                spacing: 6px;
                padding: 4px;
            }
            QSplitter::handle {
                background: #dee2e6;
            }
            QFrame#preview {
                background: #ffffff;
                border: 1px solid #dee2e6;
                border-radius: 6px;
            }
        """
        self.setStyleSheet(style)

        # Use Fusion style for consistent modern look across platforms
        QApplication.setStyle("Fusion")

    def _setup_messages_tab(self):
        widget = QWidget()
        main_layout = QVBoxLayout(widget)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(8)

        # Header with title and stats
        header_layout = QHBoxLayout()
        title = QLabel("Messages")
        title.setFont(QFont("Segoe UI", 16, QFont.Bold))
        header_layout.addWidget(title)

        self.msg_stats_label = QLabel("Total: - | Pending: -")
        self.msg_stats_label.setStyleSheet("color: #6c757d; font-size: 12px;")
        header_layout.addStretch()
        header_layout.addWidget(self.msg_stats_label)
        main_layout.addLayout(header_layout)

        # Main content splitter: filters | (table + preview)
        content_splitter = QSplitter(Qt.Horizontal)

        # Left: Filters panel (modern group box + form)
        filter_box = QGroupBox("Filters")
        filter_box.setObjectName("filters")
        filter_form = QFormLayout(filter_box)
        filter_form.setContentsMargins(12, 12, 12, 12)
        filter_form.setSpacing(8)

        self.msg_keyword = QLineEdit()
        self.msg_keyword.setPlaceholderText("Search text or extracted fields...")
        self.msg_keyword.setClearButtonEnabled(True)
        self.msg_keyword.textChanged.connect(self._refresh_messages)
        filter_form.addRow("Search:", self.msg_keyword)

        self.msg_status = QComboBox()
        self.msg_status.addItems(["(any)", "pending", "approved", "rejected", "need_review"])
        self.msg_status.currentIndexChanged.connect(self._refresh_messages)
        filter_form.addRow("Review Status:", self.msg_status)

        # Additional modern filters
        self.msg_has_media = QComboBox()
        self.msg_has_media.addItems(["(any)", "Yes", "No"])
        self.msg_has_media.currentIndexChanged.connect(self._refresh_messages)
        filter_form.addRow("Has Media:", self.msg_has_media)

        self.msg_flagged = QComboBox()
        self.msg_flagged.addItems(["(any)", "Flagged", "Not Flagged"])
        self.msg_flagged.currentIndexChanged.connect(self._refresh_messages)
        filter_form.addRow("Flagged:", self.msg_flagged)

        # Action buttons inside filters
        filter_actions = QHBoxLayout()
        btn_refresh = QPushButton("Refresh")
        btn_refresh.setObjectName("secondary")
        btn_refresh.clicked.connect(self._refresh_messages)
        filter_actions.addWidget(btn_refresh)

        btn_clear = QPushButton("Clear Filters")
        btn_clear.setObjectName("secondary")
        btn_clear.clicked.connect(self._clear_message_filters)
        filter_actions.addWidget(btn_clear)
        filter_form.addRow("", filter_actions)

        content_splitter.addWidget(filter_box)

        # Right side: vertical splitter for table + live preview
        right_splitter = QSplitter(Qt.Vertical)

        # Table
        self.msg_table = QTableWidget(0, 8)
        self.msg_table.setHorizontalHeaderLabels([
            "ID", "Date", "Channel", "Nickname/Code", "Status", "Conf", "Media", "Text (preview)"
        ])
        self.msg_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.msg_table.horizontalHeader().setStretchLastSection(True)
        self.msg_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.msg_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.msg_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.msg_table.setAlternatingRowColors(True)
        self.msg_table.doubleClicked.connect(self._open_message_detail)
        self.msg_table.itemSelectionChanged.connect(self._update_preview)
        # Context menu for modern UX
        self.msg_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.msg_table.customContextMenuRequested.connect(self._show_message_context_menu)

        right_splitter.addWidget(self.msg_table)

        # Live preview panel (modern card)
        preview_frame = QFrame()
        preview_frame.setObjectName("preview")
        preview_layout = QVBoxLayout(preview_frame)
        preview_layout.setContentsMargins(12, 8, 12, 8)

        preview_title = QLabel("Selection Preview")
        preview_title.setFont(QFont("Segoe UI", 11, QFont.Bold))
        preview_layout.addWidget(preview_title)

        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setMaximumHeight(120)
        self.preview_text.setPlaceholderText("Select a row to see details...")
        preview_layout.addWidget(self.preview_text)

        self.preview_meta = QLabel("Extracted fields and metadata will appear here.")
        self.preview_meta.setWordWrap(True)
        self.preview_meta.setStyleSheet("color: #6c757d; font-size: 12px;")
        preview_layout.addWidget(self.preview_meta)

        preview_layout.addStretch()
        right_splitter.addWidget(preview_frame)

        # Give more space to table
        right_splitter.setSizes([400, 150])

        content_splitter.addWidget(right_splitter)
        content_splitter.setSizes([220, 700])  # filters vs content

        main_layout.addWidget(content_splitter)

        # Bottom action toolbar (modern buttons)
        action_bar = QHBoxLayout()
        action_bar.setContentsMargins(0, 8, 0, 0)

        self.btn_approve = QPushButton("✓ Approve Selected")
        self.btn_approve.clicked.connect(lambda: self._quick_review("approved"))
        action_bar.addWidget(self.btn_approve)

        self.btn_reject = QPushButton("✗ Reject Selected")
        self.btn_reject.setObjectName("secondary")
        self.btn_reject.clicked.connect(lambda: self._quick_review("rejected"))
        action_bar.addWidget(self.btn_reject)

        self.btn_flag = QPushButton("⚑ Toggle Flag")
        self.btn_flag.setObjectName("secondary")
        self.btn_flag.clicked.connect(self._toggle_flag)
        action_bar.addWidget(self.btn_flag)

        action_bar.addStretch()

        btn_detail = QPushButton("Open Detail...")
        btn_detail.setObjectName("secondary")
        btn_detail.clicked.connect(self._open_selected_detail)
        action_bar.addWidget(btn_detail)

        main_layout.addLayout(action_bar)

        self.tabs.addTab(widget, "Messages")

        # Initial load
        QTimer.singleShot(400, self._refresh_messages)

    def _setup_persons_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(12, 12, 12, 12)

        header = QLabel("Persons / Profiles")
        header.setFont(QFont("Segoe UI", 16, QFont.Bold))
        layout.addWidget(header)

        # Modern filter + list layout using splitter
        splitter = QSplitter(Qt.Horizontal)

        filter_box = QGroupBox("Search & Filter")
        form = QFormLayout(filter_box)
        self.person_keyword = QLineEdit()
        self.person_keyword.setPlaceholderText("Name or code...")
        form.addRow("Keyword:", self.person_keyword)
        self.person_code = QLineEdit()
        form.addRow("Code:", self.person_code)
        btn_search = QPushButton("Search Persons")
        btn_search.setObjectName("secondary")
        btn_search.clicked.connect(lambda: self.status_bar.showMessage("Persons search (extend with db.fetch_persons)", 2000))
        form.addRow(btn_search)

        splitter.addWidget(filter_box)

        list_area = QWidget()
        list_layout = QVBoxLayout(list_area)
        list_layout.addWidget(QLabel("Results (grouped by code/album)"))
        placeholder = QTableWidget(0, 5)
        placeholder.setHorizontalHeaderLabels(["Person", "Code", "Province", "Recent Date", "Media"])
        placeholder.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        list_layout.addWidget(placeholder)
        splitter.addWidget(list_area)

        splitter.setSizes([250, 600])
        layout.addWidget(splitter)

        self.tabs.addTab(widget, "Persons")

    def _setup_ops_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(12, 12, 12, 12)

        header = QLabel("Operations & Control")
        header.setFont(QFont("Segoe UI", 16, QFont.Bold))
        layout.addWidget(header)

        # Modern service control cards using grid
        grid = QGridLayout()

        # MinIO card
        minio_box = QGroupBox("MinIO Storage")
        minio_l = QVBoxLayout(minio_box)
        minio_l.addWidget(QLabel("Local object storage for media & thumbs"))
        minio_status = QLabel("Status: (check via polling)")
        minio_l.addWidget(minio_status)
        btn_minio = QPushButton("Start MinIO")
        btn_minio.setObjectName("secondary")
        btn_minio.clicked.connect(lambda: self.status_bar.showMessage("Start MinIO via script (see web ops logic)", 2000))
        minio_l.addWidget(btn_minio)
        grid.addWidget(minio_box, 0, 0)

        # Crawler card
        crawler_box = QGroupBox("Telegram Crawler")
        crawler_l = QVBoxLayout(crawler_box)
        crawler_l.addWidget(QLabel("Incremental fetch + LLM dedupe + profile extraction"))
        crawler_status = QLabel("Status: (check via polling)")
        crawler_l.addWidget(crawler_status)
        btn_crawler = QPushButton("Start Crawler")
        btn_crawler.clicked.connect(self._stub_start_all)
        crawler_l.addWidget(btn_crawler)
        btn_stop = QPushButton("Stop Crawler")
        btn_stop.setObjectName("secondary")
        btn_stop.clicked.connect(lambda: self.status_bar.showMessage("Stop (extend)", 1500))
        crawler_l.addWidget(btn_stop)
        grid.addWidget(crawler_box, 0, 1)

        layout.addLayout(grid)

        self.service_status = QLabel("Live service status will update here (every 5s)")
        layout.addWidget(self.service_status)

        layout.addStretch()
        self.tabs.addTab(widget, "Operations")

    def _setup_settings_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(12, 12, 12, 12)

        header = QLabel("Settings & Connection")
        header.setFont(QFont("Segoe UI", 16, QFont.Bold))
        layout.addWidget(header)

        # Connection form (modern)
        conn_box = QGroupBox("Database & Storage")
        form = QFormLayout(conn_box)
        self.db_url_edit = QLineEdit(self.db_url if hasattr(self, 'db_url') else "")
        form.addRow("DATABASE_URL:", self.db_url_edit)

        self.s3_endpoint_edit = QLineEdit("http://localhost:9000")
        form.addRow("S3_ENDPOINT:", self.s3_endpoint_edit)

        save_btn = QPushButton("Save Settings")
        save_btn.clicked.connect(self._save_settings)
        form.addRow(save_btn)

        layout.addWidget(conn_box)

        info = QLabel("Changes take effect on next refresh. Use .env files for persistence across runs.")
        info.setWordWrap(True)
        layout.addWidget(info)
        layout.addStretch()
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
            # Map UI filters to DB query (basic support in DesktopDB)
            status = self.msg_status.currentText()
            if status == "(any)":
                status = None
            keyword = self.msg_keyword.text().strip() or None

            rows = self.db.fetch_messages(
                status=status,
                keyword=keyword,
                limit=300,
            )

            # Client-side filter for additional modern filters (has_media, flagged)
            has_media_filter = self.msg_has_media.currentText()
            flagged_filter = self.msg_flagged.currentText()

            filtered_rows = []
            for r in rows:
                keep = True
                if has_media_filter == "Yes" and not r.get("has_media"):
                    keep = False
                if has_media_filter == "No" and r.get("has_media"):
                    keep = False
                if flagged_filter == "Flagged" and not r.get("is_flagged"):
                    keep = False
                if flagged_filter == "Not Flagged" and r.get("is_flagged"):
                    keep = False
                if keep:
                    filtered_rows.append(r)

            self.msg_table.setRowCount(len(filtered_rows))
            for i, r in enumerate(filtered_rows):
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
                self.msg_table.item(i, 0).setData(Qt.UserRole, dict(r))

            # Update stats in header
            self.msg_stats_label.setText(
                f"Showing: {len(filtered_rows)} | DB Total: {len(rows)}"
            )
        except Exception as e:
            self.status_bar.showMessage(f"DB error: {e}", 4000)

        # Update header stats
        if self.db:
            try:
                stats = self.db.get_runtime_stats()
                self.msg_stats_label.setText(
                    f"Total: {stats.get('total_messages', 0)} | "
                    f"Pending: {stats.get('pending', 0)} | "
                    f"Approved: {stats.get('approved', 0)}"
                )
            except Exception:
                pass

    def _clear_message_filters(self):
        self.msg_keyword.clear()
        self.msg_status.setCurrentIndex(0)
        self.msg_has_media.setCurrentIndex(0)
        self.msg_flagged.setCurrentIndex(0)
        self._refresh_messages()

    def _update_preview(self):
        """Live preview for selected row (modern side panel)."""
        selected = self.msg_table.selectedItems()
        if not selected:
            self.preview_text.clear()
            self.preview_meta.setText("Select a row above to preview extracted data and content.")
            return

        # Get data from first column of first selected row
        row = selected[0].row()
        item0 = self.msg_table.item(row, 0)
        if not item0:
            return
        data = item0.data(Qt.UserRole) or {}

        text = data.get("text_content", "") or ""
        self.preview_text.setPlainText(text[:800] + ("..." if len(text) > 800 else ""))

        # Build nice meta
        nick = data.get("nickname") or data.get("extracted_json", {}).get("nickname", "-")
        code = data.get("code") or data.get("extracted_json", {}).get("code", "-")
        status = data.get("review_status", "-")
        conf = data.get("extract_confidence")
        conf_str = f"{conf:.2f}" if isinstance(conf, (int, float)) else "-"

        meta = f"<b>Nickname:</b> {nick} &nbsp;&nbsp; <b>Code:</b> {code}<br/>"
        meta += f"<b>Status:</b> {status} &nbsp;&nbsp; <b>Confidence:</b> {conf_str}<br/>"
        meta += f"<b>Channel:</b> {data.get('channel_name', '-')} &nbsp;&nbsp; <b>Media:</b> {'Yes' if data.get('has_media') else 'No'}"
        self.preview_meta.setText(meta)

    def _show_message_context_menu(self, pos):
        """Modern context menu for table rows (right-click actions)."""
        menu = QMenu(self)
        menu.addAction("Approve", lambda: self._quick_review("approved"))
        menu.addAction("Reject", lambda: self._quick_review("rejected"))
        menu.addSeparator()
        menu.addAction("Toggle Flag", self._toggle_flag)
        menu.addAction("Open Detail...", self._open_selected_detail)
        menu.exec(self.msg_table.mapToGlobal(pos))

    def _open_selected_detail(self):
        selected = self.msg_table.selectedItems()
        if selected:
            row = selected[0].row()
            # Simulate double click on first column
            idx = self.msg_table.model().index(row, 0)
            self._open_message_detail(idx)
        else:
            self.status_bar.showMessage("Select a row first", 1500)

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
