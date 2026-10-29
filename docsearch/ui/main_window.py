"""Fereastra principală. Doar afișare + comenzi; zero SQL, zero citire de fișiere.

Așezare (v2.1):
  ┌ bară de sus: căutare compactă · sortare · nr. rezultate · panou · setări ┐
  │ rezultate (Fișier | Modificat) — zona principală │ panou lateral        │
  │                                                   │  stare index (semafor)│
  │                                                   │  conținut (cifre)     │
  │                                                   │  butoane de indexare  │
  └ jos: ▸ Jurnal (pliabil) · status pe un rând · progres doar cât lucrează  ┘
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime
from typing import List, Optional

from PyQt5.QtCore import QByteArray, QEvent, Qt, QThreadPool, QTimer
from PyQt5.QtGui import QCursor, QFont, QIcon
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QButtonGroup, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSplitter, QStyledItemDelegate,
    QToolButton, QToolTip, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..config import AppConfig
from ..indexing import scanner
from ..indexing.doc_meta import read_doc_authors
from ..indexing.health import IndexHealth, compute_health, humanize_ago
from ..ro_format import fmt_int, fmt_size, plural
from ..storage import Database, IndexReport, SearchHit
from .. import paths
from .settings_dialog import SettingsDialog
from .workers import SEARCH_LIMIT, CallWorker, IndexWorker, SearchWorker, format_duration

logger = logging.getLogger(__name__)


HEALTH_COLORS = {"ok": "#2e7d32", "warn": "#d98a00", "bad": "#c62828"}

# Coloanele listei de rezultate: Folder | Document | Modificat
COL_FOLDER, COL_NAME, COL_DATE = 0, 1, 2
DATA_COL = 0   # coloana pe care stau datele rândului (cale, tooltip, dată…)

# Sortare din capul coloanei; valorile din config rămân cele vechi (compatibile).
SORT_BY_COLUMN = {
    COL_FOLDER: "Sortare după folder",
    COL_NAME: "Sortare după nume",
    COL_DATE: "Sortare după data modificării",
}
COLUMN_BY_SORT = {v: k for k, v in SORT_BY_COLUMN.items()}

# Date atașate fiecărui rând din rezultate
ROLE_TOOLTIP = Qt.UserRole + 1
ROLE_MTIME = Qt.UserRole + 2
ROLE_EXISTS = Qt.UserRole + 3
AUTHOR_CACHE_MAX = 5000

# Date de modificare "suspecte": anterioare lui 1990 (pierdute la copiere,
# de ex. de pe un CD) sau în viitor (ceas greșit pe PC-ul care a salvat).
SUSPECT_BEFORE = datetime(1990, 1, 1).timestamp()
SUSPECT_COLOR = QColor("#b26a00")

STYLE = """
QFrame#sidebar { background: #f5f6f8; border-left: 1px solid #d9dce1; }
QLabel[role="section"] { color: #6b7280; font-weight: bold; padding-top: 6px; }
QLabel[role="muted"] { color: #6b7280; }
QFrame[role="separator"] { color: #d9dce1; }
QPushButton[recommended="true"] { border: 2px solid #d98a00; font-weight: bold; }
QToolButton#logToggle { border: none; color: #4b5563; }
QScrollArea#sidebarScroll { border: none; border-left: 1px solid #d9dce1; background: #f5f6f8; }
QLabel#emptyState { color: #6b7280; }
QLabel[stale="true"] { color: #9aa0a6; }
QPushButton#quickButton { font-weight: bold; padding: 8px 6px; }
QLabel#recentLink { padding: 4px 2px 6px 2px; }
QPushButton#segment { padding: 4px 6px; }
QPushButton#segment:checked { background: #1f6feb; color: white; font-weight: bold; border: 1px solid #1a5fd0; }
"""

_fmt_int = fmt_int   # nume vechi, păstrat pentru restul fișierului


def _windows_show_in_folder(path: str, folder: str) -> None:
    """Explorer în folderul documentului, cu documentul selectat.

    Folosim direct API-ul Windows (SHOpenFolderAndSelectItems) — același pe
    care îl folosește Explorer la "Open file location". Varianta cu linia de
    comandă `explorer /select,"cale"` e capricioasă: Python pune ghilimele în
    jurul întregului argument când calea are spații, Explorer nu-l mai
    recunoaște și deschide "Documents". Rămâne doar ca rezervă, ca text brut.
    """
    import ctypes
    from ctypes import wintypes

    path = os.path.normpath(path)
    folder = os.path.normpath(folder)
    try:
        shell32 = ctypes.windll.shell32
        ole32 = ctypes.windll.ole32
        shell32.ILCreateFromPathW.restype = ctypes.c_void_p
        shell32.ILCreateFromPathW.argtypes = [wintypes.LPCWSTR]
        shell32.ILFree.argtypes = [ctypes.c_void_p]
        shell32.SHOpenFolderAndSelectItems.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD,
        ]
        ole32.CoInitialize(None)
        folder_pidl = shell32.ILCreateFromPathW(folder)
        if not folder_pidl:
            raise OSError("folderul nu a putut fi localizat")
        item_pidl = shell32.ILCreateFromPathW(path) if os.path.exists(path) else None
        try:
            if item_pidl:
                items = (ctypes.c_void_p * 1)(item_pidl)
                hr = shell32.SHOpenFolderAndSelectItems(folder_pidl, 1, items, 0)
            else:
                hr = shell32.SHOpenFolderAndSelectItems(folder_pidl, 0, None, 0)
            if hr != 0:
                raise OSError(f"SHOpenFolderAndSelectItems a întors {hr:#x}")
        finally:
            if item_pidl:
                shell32.ILFree(item_pidl)
            shell32.ILFree(folder_pidl)
    except Exception as exc:
        logger.warning("Deschiderea prin API a eșuat (%s); încerc explorer /select", exc)
        # Text brut, fără lista de argumente: ghilimelele ajung exact unde trebuie.
        if os.path.exists(path):
            subprocess.Popen(f'explorer.exe /select,"{path}"')
        else:
            subprocess.Popen(f'explorer.exe "{folder}"')


class _ElideLeftDelegate(QStyledItemDelegate):
    """Coloana Folder se scurtează din STÂNGA: rămân vizibile folderele cele
    mai apropiate de document ("…\\Cereri\\2021 - 2030\\2024") — exact partea
    care deosebește documente cu același nume."""

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        option.textElideMode = Qt.ElideLeft


def _wrappable_path(path: str) -> str:
    """Inserează puncte de rupere invizibile după separatori, ca o cale lungă
    să se poată împărți pe mai multe rânduri într-un QLabel îngust."""
    return path.replace("\\", "\\\u200b").replace("/", "/\u200b")


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig, db: Database):
        super().__init__()
        self.config = config
        self.db = db
        self.thread_pool = QThreadPool()
        self.current_index_worker: Optional[IndexWorker] = None
        self._hits: List[SearchHit] = []
        self._showing_recent = False        # lista afișează "modificate recent"?
        self._running_index_quick = False   # indexarea în curs e rapidă (nu completă)?
        # Sortarea: coloană necunoscută -> data; ordine nesalvată -> cea firească
        # a coloanei (data: cele mai recente sus; text: A→Z).
        if self.config.sort_method not in COLUMN_BY_SORT:
            self.config.sort_method = SORT_BY_COLUMN[COL_DATE]
        if self.config.sort_descending is None:
            self.config.sort_descending = (
                self.config.sort_method == SORT_BY_COLUMN[COL_DATE]
            )
        self._stats_running = False
        self._health_running = False
        # Cine a salvat ultima dată: citit la hover, pe fundal, ținut minte.
        self._author_cache: dict = {}        # (cale, mtime) -> {last_modified_by, creator}
        self._author_pending: set = set()
        self._hover_path: Optional[str] = None
        # Unde e baza de date — dacă e pe disc de rețea, semaforul avertizează.
        self._db_location = os.path.dirname(os.path.abspath(self.db.db_path))
        self._db_on_network = paths.is_network_path(self._db_location)

        # Salvare config "debounced": multe evenimente -> o singură scriere.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(1000)
        self._save_timer.timeout.connect(self._save_config_now)

        # Starea indexului se reîmprospătează periodic ("acum 47 minute" curge).
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(60_000)
        self._health_timer.timeout.connect(self.refresh_health)
        self._health_timer.start()

        self._build_ui()
        self._restore_geometry()
        self._apply_font(self.config.font_size)
        self._set_app_icon()
        self.log(
            f"Baza de date: {self._db_location} "
            f"({'DISC DE REȚEA — lent și riscant' if self._db_on_network else 'disc local'})."
        )
        self._show_cached_state()      # instant, din memorie
        self.refresh_stats()           # proaspăt, pe fundal

        # Pornirea automată: întâi trezim discul de rețea (pe fundal), apoi
        # decidem dacă e nevoie de indexare. Fereastra e deja vizibilă.
        if self.config.last_folder:
            QTimer.singleShot(300, self._startup_wake_drive)

    def _startup_wake_drive(self) -> None:
        folder = self.config.last_folder
        if paths.is_network_path(folder):
            self._set_status(f"Mă conectez la discul de rețea {os.path.splitdrive(folder)[0]}…")
        worker = CallWorker(paths.wake_network_drive, folder)
        worker.signals.finished.connect(self._on_drive_awake)
        worker.signals.error.connect(lambda _m: self._on_drive_awake("eroare la trezirea discului"))
        self.thread_pool.start(worker)

    def _on_drive_awake(self, message: str) -> None:
        if message != "disc local":
            self.log(f"Disc de rețea: {message}.")
        self.refresh_health()          # acum folderul e (probabil) accesibil
        if self.config.refresh_on_startup and self.config.folder:
            self._startup_quick_index()
        elif self.status_label.text().startswith("Mă conectez"):
            self._set_status("Gata.")

    def _startup_quick_index(self) -> None:
        """Reindexarea automată de la pornire — DOAR dacă a trecut destul timp.

        Dacă ultima indexare reușită e mai nouă de ``startup_skip_minutes``,
        nu mai pornim una: tocmai am verificat, suntem siguri pe perioada aia.
        """
        last = self.db.get_last_index_completed()
        if last is not None and self.config.startup_skip_minutes > 0:
            elapsed_min = (datetime.now() - last).total_seconds() / 60.0
            if elapsed_min < self.config.startup_skip_minutes:
                self.log(
                    f"Sar reindexarea automată: ultima indexare a fost {humanize_ago(last)} "
                    f"(pragul e de {plural(self.config.startup_skip_minutes, 'minut', 'minute')}). "
                    f"Poți porni oricând una manual."
                )
                self._set_status(
                    f"Index la zi (ultima indexare {humanize_ago(last)}) — gata de căutare."
                )
                return
        self.start_quick_index()

    # ================================================================== #
    # Construcția interfeței
    # ================================================================== #

    def _build_ui(self) -> None:
        self.setWindowTitle("Document Search System")
        self.setGeometry(100, 100, 1300, 820)
        self.setStyleSheet(STYLE)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 4)
        layout.setSpacing(6)

        layout.addLayout(self._build_top_bar())

        # --- Zona centrală: rezultate | panou lateral ----------------- #
        self.horizontal_splitter = QSplitter(Qt.Horizontal)
        self.horizontal_splitter.addWidget(self._build_results())
        # Panoul stă într-o zonă cu derulare: la fonturi mari sau ferestre
        # scunde, conținutul derulează în loc să se suprapună și să se taie.
        self.sidebar = QScrollArea()
        self.sidebar.setObjectName("sidebarScroll")
        self.sidebar.setWidgetResizable(True)
        self.sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.sidebar.setFrameShape(QFrame.NoFrame)
        self.sidebar_content = self._build_sidebar()
        self.sidebar.setWidget(self.sidebar_content)
        self.horizontal_splitter.addWidget(self.sidebar)
        self.horizontal_splitter.setStretchFactor(0, 1)   # rezultatele cresc
        self.horizontal_splitter.setStretchFactor(1, 0)   # panoul rămâne îngust
        self.horizontal_splitter.setCollapsible(0, False)
        self.horizontal_splitter.setSizes([1000, 280])
        self.horizontal_splitter.splitterMoved.connect(lambda *_: self.schedule_save())
        layout.addWidget(self.horizontal_splitter, stretch=1)

        layout.addWidget(self._build_bottom())

        self.sidebar.setVisible(self.config.sidebar_visible)
        self.sidebar_toggle.setChecked(self.config.sidebar_visible)
        self._set_log_visible(self.config.log_visible)

    def _build_top_bar(self) -> QHBoxLayout:
        top = QHBoxLayout()
        top.setSpacing(6)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Caută: CNP, nume, text…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumWidth(260)
        self.search_input.setMaximumWidth(520)     # compact: nu mai ocupă tot rândul
        self.search_input.returnPressed.connect(self.start_search)
        top.addWidget(self.search_input, stretch=3)

        self.search_button = QPushButton("Caută")
        self.search_button.setDefault(True)
        self.search_button.clicked.connect(self.start_search)
        top.addWidget(self.search_button)

        top.addSpacing(14)

        top.addSpacing(14)
        self.results_info = QLabel("")
        self.results_info.setProperty("role", "muted")
        self.results_info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        top.addWidget(self.results_info, stretch=2)

        self.sidebar_toggle = QToolButton()
        self.sidebar_toggle.setText("Panou")
        self.sidebar_toggle.setCheckable(True)
        self.sidebar_toggle.setToolTip("Arată / ascunde panoul lateral (stare index, cifre, indexare)")
        self.sidebar_toggle.toggled.connect(self._on_sidebar_toggled)
        top.addWidget(self.sidebar_toggle)

        self.settings_button = QPushButton("⚙  Setări")
        self.settings_button.clicked.connect(self.show_settings)
        top.addWidget(self.settings_button)
        return top

    def _build_results(self) -> QTreeWidget:
        self.results_list = QTreeWidget()
        self.results_list.setColumnCount(3)
        self.results_list.setHeaderLabels(["Folder", "Document", "Modificat"])
        self.results_list.setRootIsDecorated(False)
        self.results_list.setAlternatingRowColors(True)
        self.results_list.setUniformRowHeights(True)   # rapid la mii de rezultate
        # Numele documentului se scurtează la MIJLOC (diferențele sunt de obicei
        # la final: "…v2.docx", "… - copie.docx"); folderul din STÂNGA (delegat).
        self.results_list.setTextElideMode(Qt.ElideMiddle)
        self._folder_delegate = _ElideLeftDelegate(self.results_list)
        self.results_list.setItemDelegateForColumn(COL_FOLDER, self._folder_delegate)
        header = self.results_list.header()
        # Document și Folder împart spațiul; data stă fixă, exact cât îi trebuie.
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_FOLDER, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_DATE, QHeaderView.Fixed)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.results_list.headerItem().setToolTip(COL_FOLDER, "Dublu-clic pe un folder îl deschide în Explorer")
        self.results_list.headerItem().setToolTip(COL_NAME, "Dublu-clic pe un document îl deschide")
        # Sortare directă: clic pe capul coloanei. Sortăm NOI datele (nu Qt pe
        # text), ca data să se ordoneze după valoare, nu după "zz-ll-aaaa".
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.sectionClicked.connect(self._on_header_clicked)
        self._update_sort_indicator()
        self._fit_date_column()

        # Mesaj când căutarea nu găsește nimic (în locul unei liste albe).
        self.empty_label = QLabel(self.results_list.viewport())
        self.empty_label.setObjectName("emptyState")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.setTextFormat(Qt.PlainText)
        self.empty_label.hide()
        self.results_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.results_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.results_list.customContextMenuRequested.connect(self._show_context_menu)
        self.results_list.viewport().installEventFilter(self)   # tooltip-uri la hover
        return self.results_list

    @staticmethod
    def _section(text: str) -> QLabel:
        label = QLabel(text.upper())
        label.setProperty("role", "section")
        return label

    @staticmethod
    def _separator() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setProperty("role", "separator")
        return line

    @staticmethod
    def _value_label() -> QLabel:
        label = QLabel("—")
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return label

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        v = QVBoxLayout(sidebar)
        v.setContentsMargins(12, 4, 12, 10)
        v.setSpacing(4)

        # --- Starea indexului ---------------------------------------- #
        v.addWidget(self._section("Starea indexului"))
        self.health_label = QLabel()
        self.health_label.setTextFormat(Qt.RichText)
        self.health_label.setWordWrap(True)   # titlul se rupe pe 2 rânduri, nu se taie
        v.addWidget(self.health_label)
        self.health_reasons = QLabel()
        self.health_reasons.setWordWrap(True)
        self.health_reasons.setProperty("role", "muted")
        self.health_reasons.setTextFormat(Qt.RichText)
        v.addWidget(self.health_reasons)

        state_form = QFormLayout()
        state_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        state_form.setLabelAlignment(Qt.AlignLeft)
        state_form.setHorizontalSpacing(8)
        # Folderul pe rândul lui, pe toată lățimea, rupt pe mai multe rânduri
        # dacă e lung (pe un rând comun cu eticheta se tăia la "/home/").
        self.folder_value = QLabel()
        self.folder_value.setWordWrap(True)
        self.folder_value.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.folder_value.setTextFormat(Qt.PlainText)
        self.last_check_value = self._value_label()
        self.last_full_value = self._value_label()
        self.tracked_value = self._value_label()
        state_form.addRow(QLabel("Folder:"))
        state_form.addRow(self.folder_value)
        state_form.addRow("Foldere verificate:", self.last_check_value)
        state_form.addRow("Indexare completă:", self.last_full_value)
        state_form.addRow("Foldere urmărite:", self.tracked_value)
        v.addLayout(state_form)

        # Configurare (folosită rar): alegerea folderului + indexarea completă.
        self.choose_folder_button = QPushButton("Alege folder…")
        self.choose_folder_button.setToolTip(
            "Folderul cu documentele de indexat. Dacă arhiva a fost mutată,\n"
            "programul îți oferă să reconecteze indexul la noua cale, fără reindexare."
        )
        self.choose_folder_button.clicked.connect(self.choose_folder)
        v.addWidget(self.choose_folder_button)

        self.full_index_button = QPushButton("Indexare completă")
        self.full_index_button.setToolTip(
            "Relistează toate folderele și verifică fiecare fișier.\n"
            "Recuperează orice modificare scăpată de indexarea rapidă."
        )
        self.full_index_button.clicked.connect(self.start_full_index)
        v.addWidget(self.full_index_button)

        v.addWidget(self._separator())

        # --- Lucru zilnic: indexarea rapidă + fișierele modificate recent --- #
        # Cele două merg împreună: indexarea rapidă aduce ultimele modificări,
        # iar linkul de dedesubt le arată.
        v.addWidget(self._section("Lucru zilnic"))
        self.quick_index_button = QPushButton("Indexare rapidă\naduce ultimele modificări")
        self.quick_index_button.setObjectName("quickButton")
        self.quick_index_button.setToolTip(
            "Verifică doar ce s-a schimbat de la ultima indexare (folderele\n"
            "cu data neschimbată sunt sărite)."
        )
        self.quick_index_button.clicked.connect(self.start_quick_index)
        v.addWidget(self.quick_index_button)

        # Comutator cu 3 poziții pentru "modificate în ultimele N ore"
        # (valorile se aleg în Setări; implicit 12 / 16 / 24).
        hours_row = QHBoxLayout()
        hours_row.setSpacing(0)
        hours_label = QLabel("Interval:")
        hours_label.setProperty("role", "muted")
        hours_row.addWidget(hours_label)
        hours_row.addSpacing(6)
        self.hours_group = QButtonGroup(self)
        self.hours_group.setExclusive(True)
        self.hours_buttons = []
        for i in range(3):
            button = QPushButton()
            button.setObjectName("segment")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, i=i: self._on_hours_segment(i))
            self.hours_group.addButton(button, i)
            hours_row.addWidget(button, 1)
            self.hours_buttons.append(button)
        v.addLayout(hours_row)
        self._sync_hours_toggle()

        self.recent_value = QLabel()
        self.recent_value.setObjectName("recentLink")
        self.recent_value.setTextFormat(Qt.RichText)
        self.recent_value.setWordWrap(True)
        self.recent_value.setTextInteractionFlags(Qt.LinksAccessibleByMouse)
        self.recent_value.linkActivated.connect(self._show_recent_files)
        self.recent_value.setToolTip("Click pentru a vedea fișierele în listă")
        v.addWidget(self.recent_value)

        self.cancel_button = QPushButton("Oprește indexarea")
        self.cancel_button.clicked.connect(self.cancel_indexing)
        self.cancel_button.setVisible(False)       # apare doar cât rulează o indexare
        v.addWidget(self.cancel_button)

        v.addWidget(self._separator())

        # --- Conținutul indexului ------------------------------------ #
        v.addWidget(self._section("Conținut"))
        stats_form = QFormLayout()
        stats_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        stats_form.setHorizontalSpacing(8)
        self.docs_value = self._value_label()
        self.cnp_value = self._value_label()
        self.unreadable_value = self._value_label()
        self.size_value = self._value_label()
        stats_form.addRow("Documente:", self.docs_value)
        stats_form.addRow("CNP-uri unice:", self.cnp_value)
        stats_form.addRow("Necitibile:", self.unreadable_value)
        stats_form.addRow("Dimensiune:", self.size_value)
        v.addLayout(stats_form)

        v.addStretch()
        return sidebar

    def _build_bottom(self) -> QWidget:
        bottom = QWidget()
        b = QVBoxLayout(bottom)
        b.setContentsMargins(0, 0, 0, 0)
        b.setSpacing(4)

        row = QHBoxLayout()
        self.log_toggle = QToolButton()
        self.log_toggle.setObjectName("logToggle")
        self.log_toggle.setCheckable(True)
        self.log_toggle.toggled.connect(self._on_log_toggled)
        row.addWidget(self.log_toggle)

        self.status_label = QLabel("Gata.")
        # Ignored => un mesaj lung nu mai lățește fereastra; se taie, iar
        # textul complet rămâne în tooltip și în jurnal.
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self.status_label, stretch=1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(240)
        self.progress_bar.setAlignment(Qt.AlignCenter)
        self.progress_bar.setVisible(False)       # apare doar cât lucrează
        row.addWidget(self.progress_bar)
        b.addLayout(row)

        # Jurnal ("consola"): ce face aplicația în spate, cu oră.
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)   # nu creștem la infinit
        self.log_view.setFixedHeight(120)
        self.log_view.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")
        b.addWidget(self.log_view)
        return bottom

    # ------------------------------------------------------------------ #
    # Mici utilitare de interfață
    # ------------------------------------------------------------------ #

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setToolTip(text)

    def _set_busy(self, busy: bool, indeterminate: bool = True) -> None:
        self.progress_bar.setVisible(busy)
        if busy and indeterminate:
            self.progress_bar.setRange(0, 0)
        elif not busy:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)

    def _on_sidebar_toggled(self, visible: bool) -> None:
        self.sidebar.setVisible(visible)
        self.config.sidebar_visible = visible
        self.schedule_save()

    def _set_log_visible(self, visible: bool) -> None:
        self.log_toggle.blockSignals(True)
        self.log_toggle.setChecked(visible)
        self.log_toggle.blockSignals(False)
        self.log_view.setVisible(visible)
        self.log_toggle.setText(("▾" if visible else "▸") + " Jurnal")

    def _on_log_toggled(self, visible: bool) -> None:
        self._set_log_visible(visible)
        self.config.log_visible = visible
        self.schedule_save()

    def _fit_date_column(self) -> None:
        """Lățimea coloanei de dată: măsurată după fontul REAL al listei,
        pe textul cel mai lat posibil ("28-00-2000  20:00" + aer), ca să nu
        fie nici tăiată la font mare, nici lăbărțată la font mic."""
        metrics = self.results_list.fontMetrics()
        width = metrics.horizontalAdvance("⚠ 00-00-0000  00:00") + 28
        self.results_list.header().resizeSection(COL_DATE, width)

    def _place_empty_label(self) -> None:
        viewport = self.results_list.viewport()
        margin = 24
        self.empty_label.setGeometry(
            margin, margin, max(50, viewport.width() - 2 * margin),
            max(50, viewport.height() // 2),
        )

    def _show_empty_state(self, query: str) -> None:
        shown = query if len(query) <= 60 else query[:57] + "…"
        self.empty_label.setText(
            f"Niciun document indexat nu conține «{shown}».\n\n"
            "Verifică ortografia, caută mai puține cuvinte sau doar o parte dintr-un "
            "număr. Documentele salvate foarte recent apar după următoarea indexare."
        )
        self._place_empty_label()
        self.empty_label.show()

    def log(self, message: str) -> None:
        """O linie în jurnalul din fereastră + în fișierul de log."""
        self.log_view.appendPlainText(
            f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
        )
        logger.info(message)

    # ================================================================== #
    # Căutare
    # ================================================================== #

    def start_search(self) -> None:
        query = self.search_input.text().strip()
        if not query:
            self.search_input.setFocus()
            self._set_status("Scrie ceva de căutat: un CNP, un nume sau o bucată de text.")
            return
        self.search_button.setEnabled(False)
        self._showing_recent = False
        self.results_list.clear()
        self.empty_label.hide()
        self.results_info.setText("Caut…")
        self._last_query = query
        if self.current_index_worker is None:
            self._set_busy(True)
            self._set_status("Căutare în progres…")
        else:
            # Bara de progres aparține indexării; căutarea merge în paralel.
            self._set_status("Caut în indexul existent — indexarea continuă în fundal…")

        worker = SearchWorker(self.db, query)
        worker.signals.finished.connect(self._on_search_done)
        worker.signals.error.connect(self._on_error)
        self.thread_pool.start(worker)

    @staticmethod
    def _count_text(shown: int, total: int) -> str:
        """'1 rezultat', '20 de rezultate' sau, la limită, 'primele 2.000 din 2.600 de rezultate'."""
        if total > shown:
            return f"primele {fmt_int(shown)} din {plural(total, 'rezultat', 'rezultate')}"
        return plural(shown, "rezultat", "rezultate")

    def _on_search_done(self, hits: List[SearchHit], duration: float, total: int = -1) -> None:
        self._hits = hits
        total = max(total, len(hits))
        self.search_button.setEnabled(True)
        if self.current_index_worker is None:
            self._set_busy(False)
        count = self._count_text(len(hits), total)
        # Sus doar numărul (esențialul, încape și la 1024 px); durata e jos și în tooltip.
        self.results_info.setText(count)
        self.results_info.setToolTip(f"{count} · găsite în {format_duration(duration)}")
        status = f"Găsite {count} în {format_duration(duration)}."
        if total > len(hits):
            status += " Lista e limitată la primele " + fmt_int(len(hits)) + \
                " (cele mai recente) — adaugă cuvinte ca să restrângi căutarea."
        if self.current_index_worker is not None:
            status += (" Indexarea continuă în fundal; documentele noi apar la o "
                       "căutare ulterioară.")
        self._set_status(status)
        self._render_results()
        if not hits:
            self._show_empty_state(getattr(self, "_last_query", ""))
        self.log(f"Căutare încheiată: {count} în {format_duration(duration)}.")

    def _on_header_clicked(self, column: int) -> None:
        """Clic pe capul coloanei: același cap inversează ordinea; altul pornește
        cu ordinea firească (dată: cele mai recente sus; text: A→Z)."""
        method = SORT_BY_COLUMN.get(column)
        if method is None:
            return
        if method == self.config.sort_method:
            self.config.sort_descending = not self.config.sort_descending
        else:
            self.config.sort_method = method
            self.config.sort_descending = (column == COL_DATE)
        self._update_sort_indicator()
        self.schedule_save()          # salvare debounced în config.json
        self._render_results()

    def _update_sort_indicator(self) -> None:
        column = COLUMN_BY_SORT.get(self.config.sort_method, COL_DATE)
        # Stilul Fusion desenează săgeata invers față de Windows Explorer
        # (descrescător = săgeată în sus). O inversăm, ca utilizatorul să vadă
        # ce știe din Explorer: săgeată în jos = cele mai noi / Z→A sus.
        # Ordinea reală o ține config.sort_descending, nu indicatorul.
        order = Qt.AscendingOrder if self.config.sort_descending else Qt.DescendingOrder
        self.results_list.header().setSortIndicator(column, order)

    def _display_folder(self, path: str) -> str:
        """Folderul documentului, RELATIV la folderul indexat (economisește
        spațiu: "SALARIATI\\Cereri\\2024" în loc de "M:\\SALARIATI\\Cereri\\2024")."""
        folder = os.path.dirname(path)
        root = self.config.last_folder or ""
        if root:
            root_norm = os.path.normpath(root).rstrip("\\/")
            folder_norm = os.path.normpath(folder)
            if folder_norm.lower() == root_norm.lower():
                return "(rădăcină)"
            if folder_norm.lower().startswith(root_norm.lower() + os.sep):
                return folder_norm[len(root_norm) + 1:]
        return folder

    def _render_results(self) -> None:
        """(Re)afișează self._hits conform criteriului de sortare. Nu re-citește discul."""
        method = self.config.sort_method
        descending = self.config.sort_descending
        hits = list(self._hits)
        if method == "Sortare după nume":
            hits.sort(key=lambda h: os.path.basename(h.path).lower(), reverse=descending)
        elif method == "Sortare după folder":
            hits.sort(key=lambda h: (os.path.dirname(h.path).lower(),
                                     os.path.basename(h.path).lower()), reverse=descending)
        else:
            hits.sort(key=lambda h: h.mtime, reverse=descending)

        self.results_list.clear()
        self.empty_label.hide()
        missing_count = 0
        items = []
        now_ts = datetime.now().timestamp()
        for hit in hits:
            exists = getattr(hit, "exists", True)
            name = os.path.basename(hit.path)
            # Eticheta scurtă rămâne la început, deci nu e înghițită de scurtarea la mijloc.
            label = name if exists else f"[LIPSĂ] {name}"
            mtime = datetime.fromtimestamp(hit.mtime)
            date_text = mtime.strftime("%d-%m-%Y  %H:%M")
            suspect = ""
            if hit.mtime < SUSPECT_BEFORE:
                suspect = ("Dată suspectă: probabil pierdută la copiere "
                           "(de ex. de pe un CD sau dintr-o arhivă).")
            elif hit.mtime > now_ts + 86400:
                suspect = ("Dată suspectă: în viitor — ceasul calculatorului care "
                           "a salvat documentul era probabil greșit.")
            if suspect:
                date_text = "⚠ " + date_text
            row = [""] * 3
            row[COL_FOLDER] = self._display_folder(hit.path)
            row[COL_NAME] = label
            row[COL_DATE] = date_text
            item = QTreeWidgetItem(row)
            tooltip = (
                f"{hit.path}\n\n"
                f"Ultima modificare: {mtime.strftime('%d-%m-%Y %H:%M:%S')}\n"
                f"Dimensiune: {fmt_size(hit.size)}"
            )
            if suspect:
                tooltip += f"\n{suspect}"
                item.setForeground(COL_DATE, SUSPECT_COLOR)
            if not exists:
                missing_count += 1
                for col in (COL_NAME, COL_FOLDER, COL_DATE):
                    item.setForeground(col, Qt.gray)
                tooltip += (
                    "\n\nFișierul NU există la această cale pe acest PC.\n"
                    "Dacă arhiva a fost mutată, folosește Setări → «Verifică baza de date» →\n"
                    "«Reconectează la altă cale…»."
                )
            # Tooltip-ul se compune la hover (eventFilter), ca să putem adăuga
            # "cine a salvat ultima dată", citit din fișier pe fundal.
            item.setData(DATA_COL, Qt.UserRole, hit.path)
            item.setData(DATA_COL, ROLE_TOOLTIP, tooltip)
            item.setData(DATA_COL, ROLE_MTIME, float(hit.mtime))
            item.setData(DATA_COL, ROLE_EXISTS, bool(exists))
            items.append(item)
        self.results_list.addTopLevelItems(items)   # un singur update, nu N
        if missing_count:
            self._set_status(
                f"Atenție: {missing_count} din {plural(len(hits), 'rezultat', 'rezultate')} "
                f"indică fișiere care nu există la calea indexată — vezi Setări → "
                f"«Verifică baza de date»."
            )

    # ================================================================== #
    # Indexare
    # ================================================================== #

    def start_quick_index(self) -> None:
        from ..indexing.indexer import dynamic_window_days

        if self.config.dynamic_quick_index:
            days = dynamic_window_days(self.db, self.config.partial_index_days)
        else:
            days = self.config.partial_index_days
        self._start_index(newer_than_days=days)

    def start_full_index(self) -> None:
        folder = self._require_folder()
        if not folder:
            return
        reply = QMessageBox.question(
            self, "Confirmare",
            f"Se va reindexa COMPLET folderul:\n{folder}\n\n"
            "Acest proces poate dura mult timp. Continuați?",
        )
        if reply == QMessageBox.Yes:
            self._start_index(newer_than_days=None)

    def _require_folder(self) -> Optional[str]:
        if self.config.folder:
            return str(self.config.folder)
        QMessageBox.warning(
            self, "Eroare",
            "Nu este selectat niciun folder valid.\nAlegeți unul din Setări.",
        )
        return None

    def _start_index(self, newer_than_days: Optional[int]) -> None:
        folder = self._require_folder()
        if not folder:
            return
        if self.current_index_worker is not None:
            QMessageBox.information(self, "Info", "O indexare este deja în curs.")
            return

        # Capcană clasică: indexare rapidă peste un index GOL (prima rulare,
        # bază nouă sau proaspăt migrată). Fereastra rapidă ar sări toate
        # documentele vechi și utilizatorul n-ar afla niciodată. În acest caz
        # facem automat indexare completă.
        if newer_than_days is not None and self.db.count_documents() == 0:
            newer_than_days = None
            self.log("Indexul este gol — se face automat indexare COMPLETĂ.")

        year_cutoff = None
        if self.config.archive_year_folders:
            year_cutoff = scanner.archive_year_cutoff(self.config.archive_keep_years)
        worker = IndexWorker(
            self.db, folder, self.config.extensions, newer_than_days,
            excluded_folders=self.config.excluded_folders,
            use_dir_dates=self.config.use_dir_dates,
            archived_folders=self.config.archived_folders,
            archive_year_cutoff=year_cutoff,
        )
        if self.config.excluded_folders:
            self.log(f"Foldere excluse de la scanare: {', '.join(self.config.excluded_folders)}")
        if newer_than_days is not None:
            archive_bits = []
            if year_cutoff is not None:
                archive_bits.append(f"folderele cu an în nume mai vechi de {year_cutoff}")
            if self.config.archived_folders:
                archive_bits.append(", ".join(self.config.archived_folders))
            if archive_bits:
                self.log("Arhivă ocolită de indexarea rapidă: " + "; ".join(archive_bits) + ".")
        worker.signals.scan_progress.connect(self._on_scan_progress)
        worker.signals.progress.connect(self._on_index_progress)
        worker.signals.note.connect(self.log)
        worker.signals.finished.connect(self._on_index_done)
        worker.signals.error.connect(self._on_error)
        self.current_index_worker = worker
        self._running_index_quick = newer_than_days is not None

        self._set_indexing_ui(True)
        kind = (
            "completă" if newer_than_days is None
            else f"rapidă (ultimele {newer_than_days} zile)"
        )
        self.log(f"Pornesc indexarea {kind} în: {folder}")
        self.log("Faza 1/2: scanez discul după fișiere candidate…")
        self._set_status(f"Indexare {kind} — scanez discul…")
        self._set_busy(True)          # bară "indeterminată": nu știm încă totalul
        self.thread_pool.start(worker)

    def cancel_indexing(self) -> None:
        if self.current_index_worker is not None:
            self.current_index_worker.cancel()
            self.log("Cerere de oprire — indexarea se oprește la următorul pas…")
            self._set_status("Se oprește indexarea…")

    def _set_indexing_ui(self, running: bool) -> None:
        self.quick_index_button.setEnabled(not running)
        self.full_index_button.setEnabled(not running)
        self.choose_folder_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.cancel_button.setVisible(running)

    def _on_scan_progress(self, found: int, current_dir: str) -> None:
        self._set_status(
            f"Scanez: {current_dir}   —   {_fmt_int(found)} fișiere candidate până acum"
        )

    def _on_index_progress(self, done: int, total: int, path: str) -> None:
        if self.progress_bar.maximum() == 0:    # abia am ieșit din faza de scanare
            self.progress_bar.setRange(0, 100)
            self.log(f"Faza 2/2: scanare încheiată, {_fmt_int(total)} fișiere găsite.")
        percent = int(done / total * 100) if total else 0
        self.progress_bar.setValue(percent)
        name = os.path.basename(path) if path else "triere…"
        self._set_status(f"Indexare {_fmt_int(done)}/{_fmt_int(total)}: {name}")

    def _on_index_done(self, report: IndexReport) -> None:
        self.current_index_worker = None
        self._set_indexing_ui(False)
        self._set_busy(False)
        for err in report.errors:
            self.log(f"Notă: {err}")
        # Format "eticheta: număr" — separatori de mii și fără probleme de
        # plural ("1 indexate" / "20 de indexate").
        parts = [
            f"indexate: {fmt_int(report.indexed)}",
            f"neschimbate: {fmt_int(report.unchanged)}",
            f"necitibile: {fmt_int(report.unreadable)}",
            f"eliminate: {fmt_int(report.removed)}",
            f"noi: {fmt_int(report.added)}",
        ]
        if report.locked:
            parts.append(f"deschise în Word: {fmt_int(report.locked)} (se reiau data viitoare)")
        summary = (
            f"Indexare terminată în {format_duration(report.duration_seconds)} — "
            + " · ".join(parts) + "."
        )
        self._set_status(summary)
        self.log(summary)
        if report.dirs_listed or report.dirs_skipped:
            archived_txt = (
                f", {fmt_int(report.dirs_archived)} subarbori arhivați ocoliți"
                if report.dirs_archived else ""
            )
            self.log(
                f"Foldere: {fmt_int(report.dirs_listed)} listate, "
                f"{fmt_int(report.dirs_skipped)} sărite ca neschimbate{archived_txt}."
            )
        self.refresh_stats()

        # După o indexare rapidă (automată sau manuală), lista se reîmprospătează
        # singură — ca și cum ai fi apăsat pe „fișiere modificate în ultimele N ore".
        # Dacă în câmpul de căutare e deja ceva, re-rulăm căutarea aceea în loc,
        # ca să nu-ți luăm rezultatele de sub ochi.
        if getattr(self, "_running_index_quick", False) and not report.errors:
            if self.search_input.text().strip() and not self._showing_recent:
                self.log("Reîmprospătez rezultatele căutării după indexarea rapidă.")
                self.start_search()
            else:
                self.log(
                    f"Afișez fișierele modificate în ultimele "
                    f"{self.config.modified_files_hours} ore (după indexarea rapidă)."
                )
                self._show_recent_files()
        self._running_index_quick = False

    def _on_error(self, message: str) -> None:
        """Slot comun pentru erorile venite din workeri (căutare / indexare)."""
        logger.error("Eroare din worker:\n%s", message)
        self.log("EROARE — detalii în dialog și în fișierul de log.")
        self.current_index_worker = None
        self._set_indexing_ui(False)
        self.search_button.setEnabled(True)
        self._set_busy(False)
        self._set_status("Eroare — detalii în fereastra de dialog și în log.")
        QMessageBox.warning(self, "Eroare", message[-2000:])
        self.refresh_health()

    # ================================================================== #
    # Panoul lateral: stare + cifre
    # ================================================================== #

    def refresh_stats(self) -> None:
        """Cifrele (scumpe pe baze mari — ex. CNP-uri unice) se calculează pe
        FUNDAL, ca interfața să rămână utilizabilă; semaforul imediat."""
        self.refresh_health()
        if self._stats_running:
            return
        self._stats_running = True
        worker = CallWorker(self.db.get_stats, self.config.modified_files_hours)
        worker.signals.finished.connect(self._on_stats_ready)
        worker.signals.error.connect(self._on_stats_error)
        self.thread_pool.start(worker)

    def _on_stats_error(self, message: str) -> None:
        self._stats_running = False
        logger.error("Statisticile nu au putut fi calculate:\n%s", message)

    def _on_stats_ready(self, stats: dict) -> None:
        self._stats_running = False
        hours = self.config.modified_files_hours
        recent_key = next((k for k in stats if k.startswith("Fișiere modificate")), None)

        self._apply_stats(stats, hours)
        self._set_stale(False)
        self._save_cached_state(stats=stats, hours=hours)
        self.refresh_health()

    def _apply_stats(self, stats: dict, hours: int) -> None:
        recent_key = next((k for k in stats if k.startswith("Fișiere modificate")), None)
        self.docs_value.setText(_fmt_int(stats.get("Total documente", 0)))
        self.cnp_value.setText(_fmt_int(stats.get("CNP-uri unice", 0)))
        self.unreadable_value.setText(_fmt_int(stats.get("Fișiere necitibile", 0)))
        size_mb = stats.get("Dimensiune totală (MB)", 0) or 0
        self.size_value.setText(fmt_size(size_mb * 1024 * 1024))
        recent = int((stats.get(recent_key, 0) if recent_key else 0) or 0)
        self._update_recent_link(recent, hours)

    # ------------------------------------------------------------------ #
    # Ultima stare cunoscută: afișată INSTANT la pornire, până vin cifrele
    # proaspete (altfel panoul stă cu liniuțe câteva secunde).
    # ------------------------------------------------------------------ #

    def _cache_path(self) -> str:
        return os.path.join(self._db_location, "last_state.json")

    def _save_cached_state(self, stats: Optional[dict] = None, hours: Optional[int] = None,
                           health: Optional[IndexHealth] = None) -> None:
        import json
        try:
            data = {}
            if os.path.exists(self._cache_path()):
                with open(self._cache_path(), encoding="utf-8") as f:
                    data = json.load(f)
            if stats is not None:
                data["stats"] = stats
                data["hours"] = hours
            if health is not None:
                data["health"] = {
                    "level": health.level, "headline": health.headline,
                    "reasons": health.reasons, "notes": health.notes,
                    "last_check": health.last_check.isoformat() if health.last_check else None,
                    "last_full": health.last_full.isoformat() if health.last_full else None,
                    "tracked_dirs": health.tracked_dirs,
                    "total_documents": health.total_documents,
                }
            data["saved_at"] = datetime.now().isoformat()
            tmp = self._cache_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self._cache_path())
        except Exception as exc:
            logger.debug("Nu am putut salva ultima stare: %s", exc)

    def _show_cached_state(self) -> None:
        import json
        try:
            with open(self._cache_path(), encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        try:
            if data.get("stats"):
                self._apply_stats(data["stats"], int(data.get("hours") or self.config.modified_files_hours))
            h = data.get("health")
            if h:
                parse = lambda v: datetime.fromisoformat(v) if v else None  # noqa: E731
                self._apply_health(IndexHealth(
                    level=h["level"], headline=h["headline"], reasons=h.get("reasons", []),
                    notes=h.get("notes", []), last_check=parse(h.get("last_check")),
                    last_full=parse(h.get("last_full")), tracked_dirs=h.get("tracked_dirs", 0),
                    total_documents=h.get("total_documents", 0),
                ))
            self._set_stale(True, data.get("saved_at"))
        except Exception as exc:
            logger.debug("Ultima stare salvată nu a putut fi afișată: %s", exc)

    def _set_stale(self, stale: bool, saved_at: Optional[str] = None) -> None:
        """Cifrele din memorie apar estompate, cu explicație la hover."""
        tip = ""
        if stale and saved_at:
            try:
                tip = ("Ultima valoare cunoscută (" +
                       humanize_ago(datetime.fromisoformat(saved_at)) + "); se actualizează…")
            except ValueError:
                tip = "Ultima valoare cunoscută; se actualizează…"
        for widget in (self.docs_value, self.cnp_value, self.unreadable_value,
                       self.size_value, self.tracked_value, self.last_check_value,
                       self.last_full_value, self.health_label):
            widget.setProperty("stale", "true" if stale else "false")
            widget.setToolTip(tip if stale else widget.toolTip() if widget is self.health_label else "")
            widget.style().unpolish(widget); widget.style().polish(widget)

    @staticmethod
    def _hours_window(hours: int) -> str:
        return "în ultima oră" if hours == 1 else f"în ultimele {plural(hours, 'oră', 'ore')}"

    def _update_recent_link(self, recent: int, hours: int) -> None:
        self._recent_total = recent
        window = self._hours_window(hours)
        if recent:
            files = plural(recent, "fișier modificat", "fișiere modificate")
            self.recent_value.setText(f'📄 <a href="recent">{files} {window}</a>')
            self.recent_value.setToolTip("Click pentru a vedea fișierele în listă")
        else:
            self.recent_value.setText(f"Niciun fișier modificat {window}.")
            self.recent_value.setToolTip("")

    def _hours_presets(self) -> List[int]:
        return [int(h) for h in (list(self.config.recent_hours_presets) + [12, 16, 24])[:3]]

    def _sync_hours_toggle(self) -> None:
        """Etichetele și poziția comutatorului, după config."""
        presets = self._hours_presets()
        if self.config.modified_files_hours not in presets:
            self.config.modified_files_hours = presets[0]
        for button, hours in zip(self.hours_buttons, presets):
            button.setText(f"{hours} h")
            button.setToolTip(f"Fișiere modificate {self._hours_window(hours)}")
            button.setChecked(hours == self.config.modified_files_hours)

    def _on_hours_segment(self, index: int) -> None:
        hours = self._hours_presets()[index]
        if hours == self.config.modified_files_hours:
            self._sync_hours_toggle()            # rămâne apăsat
            return
        self.config.modified_files_hours = hours
        self._sync_hours_toggle()
        self.schedule_save()
        # Doar numărătoarea recentelor (rapidă), nu toate statisticile.
        worker = CallWorker(self.db.count_recent, hours)
        worker.signals.finished.connect(lambda count, h=hours: self._on_recent_count(count, h))
        self.thread_pool.start(worker)
        if self._showing_recent:
            self._show_recent_files()           # lista deschisă urmează noul interval

    def _on_recent_count(self, count: int, hours: int) -> None:
        if hours == self.config.modified_files_hours:   # între timp s-a comutat din nou?
            self._update_recent_link(int(count), hours)

    def refresh_health(self) -> None:
        """Semaforul și timpii relativi — calculat pe FUNDAL.

        Conține «există folderul N:\?», care pe un disc de rețea încă
        neconectat poate aștepta secunde bune; pe firul ferestrei, asta ținea
        programul nedesenat la pornire."""
        if self._health_running:
            return
        self._health_running = True
        worker = CallWorker(
            compute_health, self.db, self.config.last_folder,
            self.config.full_index_recommend_days, self.config.pause_warn_days,
            self.config.dynamic_quick_index, self.config.partial_index_days,
            self.config.use_dir_dates, None, self._db_on_network, self._db_location,
        )
        worker.signals.finished.connect(self._on_health_ready)
        worker.signals.error.connect(self._on_health_error)
        self.thread_pool.start(worker)

    def _on_health_error(self, message: str) -> None:
        self._health_running = False
        logger.error("Nu am putut calcula starea indexului:\n%s", message)

    def _on_health_ready(self, health: IndexHealth) -> None:
        self._health_running = False
        self._apply_health(health)
        self._save_cached_state(health=health)

    def _apply_health(self, health: IndexHealth) -> None:
        color = HEALTH_COLORS.get(health.level, "#6b7280")
        self.health_label.setText(
            f'<span style="color:{color}; font-size:16pt;">●</span>'
            f'&nbsp;<b>{health.headline}</b>'
        )
        lines = [f"• {r}" for r in health.reasons] + [f"<i>{n}</i>" for n in health.notes]
        self.health_reasons.setText("<br>".join(lines))
        self.health_reasons.setVisible(bool(lines))

        self.folder_value.setText(_wrappable_path(self.config.last_folder or "neselectat"))
        self.folder_value.setToolTip(self.config.last_folder or "")
        self.last_check_value.setText(humanize_ago(health.last_check))
        self.last_full_value.setText(humanize_ago(health.last_full))
        self.tracked_value.setText(_fmt_int(health.tracked_dirs))
        for widget, moment in ((self.last_check_value, health.last_check),
                               (self.last_full_value, health.last_full)):
            widget.setToolTip(moment.strftime("%d-%m-%Y %H:%M") if moment else "")

        # Istoricul ultimelor rulări, la trecerea cu mouse-ul peste semafor.
        runs = self.db.get_index_runs(limit=6)
        if runs:
            rows = []
            for run in runs:
                started = datetime.fromisoformat(run["started_at"]).strftime("%d-%m %H:%M")
                kind = "completă" if run["kind"] == "full" else "rapidă"
                status = "OK" if run["status"] == "ok" else "oprită"
                rows.append(
                    f"{started}  {kind:<8} {status:<6} "
                    f"{run['indexed']} indexate, {run['dirs_skipped']} foldere sărite"
                )
            self.health_label.setToolTip("Ultimele indexări:\n" + "\n".join(rows))
        else:
            self.health_label.setToolTip("Nicio indexare înregistrată încă.")

        # Butonul de indexare completă e evidențiat când e recomandat.
        recommended = health.level in ("warn", "bad") and health.total_documents > 0
        self.full_index_button.setProperty("recommended", "true" if recommended else "false")
        self.full_index_button.style().unpolish(self.full_index_button)
        self.full_index_button.style().polish(self.full_index_button)

    def _show_recent_files(self, _link: str = "") -> None:
        """Lista fișierelor modificate recent — pe FUNDAL: datele vin din index,
        doar "mai există?" se verifică pe disc, în paralel (înainte: 5 interogări
        pe NAS pentru fiecare fișier, pe firul interfeței, fără limită)."""
        hours = self.config.modified_files_hours
        self._showing_recent = True
        self.results_list.clear()
        self.empty_label.hide()
        self.results_info.setText("Caut…")
        worker = CallWorker(self.db.get_recent_hits, hours, SEARCH_LIMIT)
        worker.signals.finished.connect(lambda hits, h=hours: self._on_recent_ready(hits, h))
        worker.signals.error.connect(self._on_error)
        self.thread_pool.start(worker)

    def _on_recent_ready(self, hits: List[SearchHit], hours: int) -> None:
        self._hits = hits
        total = max(getattr(self, "_recent_total", len(hits)), len(hits))
        self._render_results()
        window = self._hours_window(hours)
        count = (f"primele {fmt_int(len(hits))} din {fmt_int(total)}" if total > len(hits)
                 else fmt_int(len(hits)))
        self.results_info.setText(f"{count} modificate {window}")
        self._set_status(f"Fișiere modificate {window}: {count}.")
        if not hits:
            self.empty_label.setText(f"Niciun document nu a fost modificat {window}.")
            self._place_empty_label()
            self.empty_label.show()

    # ================================================================== #
    # Acțiuni pe rezultate
    # ================================================================== #

    # ------------------------------------------------------------------ #
    # Tooltip la hover: cale, dată, mărime + cine a salvat ultima dată
    # ------------------------------------------------------------------ #

    def eventFilter(self, obj, event) -> bool:
        viewport = getattr(self, "results_list", None)
        if viewport is not None and obj is viewport.viewport():
            if event.type() == QEvent.ToolTip:
                item = self.results_list.itemAt(event.pos())
                if item is None:
                    QToolTip.hideText()
                    self._hover_path = None
                else:
                    self._show_item_tooltip(item, event.globalPos())
                return True
            if event.type() == QEvent.Leave:
                self._hover_path = None
            if event.type() == QEvent.Resize and self.empty_label.isVisible():
                self._place_empty_label()
        return super().eventFilter(obj, event)

    @staticmethod
    def _authors_text(meta: dict) -> str:
        last = meta.get("last_modified_by")
        creator = meta.get("creator")
        lines = [f"Salvat ultima dată de: {last or 'necunoscut (fișierul nu reține numele)'}"]
        if creator and creator != last:
            lines.append(f"Creat de: {creator}")
        return "\n".join(lines)

    def _show_item_tooltip(self, item: QTreeWidgetItem, global_pos) -> None:
        path = self._item_path(item)
        base = item.data(DATA_COL, ROLE_TOOLTIP) or path
        mtime = item.data(DATA_COL, ROLE_MTIME) or 0.0
        exists = item.data(DATA_COL, ROLE_EXISTS)
        self._hover_path = path
        if exists is False:
            text = base                     # fișier lipsă: nu avem ce citi
        else:
            key = (path, mtime)
            meta = self._author_cache.get(key)
            if meta is not None:
                text = f"{base}\n\n{self._authors_text(meta)}"
            else:
                text = f"{base}\n\nSalvat ultima dată de: se citește…"
                if key not in self._author_pending:
                    self._author_pending.add(key)
                    worker = CallWorker(read_doc_authors, path)
                    worker.signals.finished.connect(
                        lambda result, key=key: self._on_authors_ready(key, result)
                    )
                    self.thread_pool.start(worker)
        QToolTip.showText(global_pos, text, self.results_list.viewport())

    def _on_authors_ready(self, key, meta) -> None:
        self._author_pending.discard(key)
        if len(self._author_cache) >= AUTHOR_CACHE_MAX:
            self._author_cache.clear()
        self._author_cache[key] = meta or {}
        # Mouse-ul e încă pe același document? Actualizăm tooltip-ul pe loc.
        if self._hover_path != key[0] or not QToolTip.isVisible():
            return
        pos = QCursor.pos()
        item = self.results_list.itemAt(self.results_list.viewport().mapFromGlobal(pos))
        if item is not None and self._item_path(item) == key[0]:
            self._show_item_tooltip(item, pos)

    @staticmethod
    def _item_path(item: QTreeWidgetItem) -> str:
        """Calea reală a rezultatului (textul afișat poate avea prefix [LIPSĂ...])."""
        data = item.data(DATA_COL, Qt.UserRole)
        return data if data else item.text(COL_NAME)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Navigare rapidă: dublu-clic pe Folder deschide Explorer acolo (cu
        documentul selectat); pe Document sau pe dată deschide documentul."""
        if column == COL_FOLDER:
            self._open_containing_folder(self._item_path(item))
        else:
            self._open_item(item)

    def _open_item(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        path = self._item_path(item)
        if not os.path.exists(path):
            QMessageBox.warning(
                self, "Fișier lipsă",
                f"Fișierul nu există la calea din index:\n{path}\n\n"
                "Dacă arhiva a fost mutată, folosește Setări → «Verifică baza de date» → "
                "«Reconectează la altă cale…».",
            )
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            QMessageBox.warning(self, "Eroare", f"Nu s-a putut deschide fișierul: {exc}")

    def _show_context_menu(self, position) -> None:
        item = self.results_list.itemAt(position)
        if item is None:
            return
        menu = QMenu(self)
        open_file = menu.addAction("Deschide documentul")
        open_folder = menu.addAction("Deschide folderul în Explorer")
        copy_path = menu.addAction("Copiază calea")
        action = menu.exec_(self.results_list.mapToGlobal(position))
        if action == open_file:
            self._open_item(item)
        elif action == open_folder:
            self._open_containing_folder(self._item_path(item))
        elif action == copy_path:
            from PyQt5.QtWidgets import QApplication
            QApplication.clipboard().setText(self._item_path(item))
            self._set_status("Calea a fost copiată.")

    def _open_containing_folder(self, path: str) -> None:
        folder = os.path.dirname(path)
        if not os.path.isdir(folder):
            QMessageBox.warning(
                self, "Folder lipsă",
                f"Folderul nu există pe acest PC:\n{folder}\n\n"
                "Dacă arhiva a fost mutată, folosește Setări → «Verifică baza de date».",
            )
            return
        try:
            if sys.platform == "win32":
                _windows_show_in_folder(path, folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path] if os.path.exists(path) else ["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as exc:
            QMessageBox.warning(self, "Eroare", f"Nu s-a putut deschide folderul: {exc}")

    # ================================================================== #
    # Setări & configurație
    # ================================================================== #

    def choose_folder(self) -> None:
        """Alegerea folderului de indexat — direct din panou (mutată din Setări)."""
        from PyQt5.QtWidgets import QFileDialog

        old_folder = self.config.last_folder
        folder = QFileDialog.getExistingDirectory(
            self, "Alege folderul cu documente de indexat", old_folder or ""
        )
        if not folder:
            return
        if old_folder and os.path.normpath(folder).lower() == os.path.normpath(old_folder).lower():
            return
        self.config.last_folder = folder
        self._save_config_now()
        self.log(f"Folder de indexat: {folder}")
        self._maybe_offer_remap(old_folder, folder)
        self.refresh_stats()

    def show_settings(self) -> None:
        old_folder = self.config.last_folder
        dialog = SettingsDialog(self.config, self, db=self.db)
        accepted = dialog.exec_()
        # Verificarea bazei (din Setări) poate reconecta căile chiar dacă
        # utilizatorul apasă apoi Cancel — statisticile trebuie oricum reîmprospătate.
        if dialog.db_remapped:
            self.log("Index reconectat din «Verifică baza de date».")
            self._set_status("Index reconectat din Verificare bază de date.")
        if accepted:
            # Păstrăm preferințele de așezare schimbate între timp în fereastră.
            new_config = dialog.result_config()
            new_config.sort_method = self.config.sort_method
            new_config.sort_descending = self.config.sort_descending
            new_config.sidebar_visible = self.config.sidebar_visible
            new_config.log_visible = self.config.log_visible
            self.config = new_config
            self._sync_hours_toggle()
            self._apply_font(self.config.font_size)
            self._maybe_offer_remap(old_folder, self.config.last_folder)
            self._save_config_now()
        if accepted or dialog.db_remapped:
            self.refresh_stats()

    def _maybe_offer_remap(self, old_folder: str, new_folder: str) -> None:
        """Folderul s-a schimbat, dar indexul arată spre calea veche?
        (tipic: arhiva a fost mutată pe alt disc sau alt PC)
        Oferim reconectarea indexului la noua cale, fără reindexare.
        """
        if not old_folder or not new_folder:
            return
        if os.path.normpath(old_folder).lower() == os.path.normpath(new_folder).lower():
            return
        count = self.db.count_paths_under(old_folder)
        if count == 0:
            return

        reply = QMessageBox.question(
            self, "Reconectare index",
            f"Indexul conține {count} documente sub calea veche:\n{old_folder}\n\n"
            f"Au fost mutate aceste documente la noua cale?\n{new_folder}\n\n"
            "DA  = reconectez indexul la noua cale (instant, fără reindexare)\n"
            "NU  = păstrez indexul cum e (noua cale se indexează separat)",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        changed = self.db.remap_paths(old_folder, new_folder)
        self._set_status(f"Index reconectat: {changed} căi rescrise spre {new_folder}.")
        QMessageBox.information(
            self, "Index reconectat",
            f"{changed} documente arată acum spre noua cale.\n\n"
            "Recomandare: rulează o Indexare completă — nu va reciti fișierele "
            "neschimbate, doar verifică rapid că totul e la locul lui.",
        )

    def _apply_font(self, size: int) -> None:
        font = QFont("Arial", size)
        self.setFont(font)
        for widget in self.findChildren(QWidget):
            widget.setFont(font)
        # fontul schimbă lățimea textului -> recalibrăm coloana de dată și panoul
        self._fit_date_column()
        self._fit_sidebar_width()

    def _fit_sidebar_width(self) -> None:
        """Lățimea minimă a panoului, măsurată după fontul real: la font 24
        cifrele și butoanele încap (înainte se tăiau la "34" și "ndexare complet")."""
        fm = self.sidebar_content.fontMetrics()
        # După ETICHETE + o cifră tipică; valorile mai lungi ("acum 47 de minute")
        # trec singure pe rândul următor (WrapLongRows), deci nu lățim panoul
        # pe seama lor — la 1024 px lista are nevoie de spațiu.
        widest = max(
            fm.horizontalAdvance("Foldere verificate:") + fm.horizontalAdvance("  888.888"),
            fm.horizontalAdvance("Indexare completă") + 40,
        )
        # Butonul de lucru zilnic: înalt și încăpător (2 rânduri de text + aer).
        self.quick_index_button.setMinimumHeight(int(fm.height() * 3.2))
        scrollbar = self.sidebar.verticalScrollBar().sizeHint().width()
        needed = widest + 2 * 12 + scrollbar + 8
        self.sidebar.setMinimumWidth(needed)
        self.sidebar.setMaximumWidth(max(int(needed * 1.8), 440))
        sizes = self.horizontal_splitter.sizes()
        if len(sizes) == 2 and 0 < sizes[1] < needed:
            total = sum(sizes)
            self.horizontal_splitter.setSizes([max(total - needed, 200), needed])

    def _set_app_icon(self) -> None:
        for base in (os.path.dirname(os.path.abspath(sys.argv[0])),
                     getattr(sys, "_MEIPASS", "")):
            if not base:
                continue
            icon_path = os.path.join(base, "icon.ico")
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
                return

    def schedule_save(self) -> None:
        self._save_timer.start()

    def _save_config_now(self) -> None:
        self.config.window_geometry = bytes(self.saveGeometry()).hex()
        self.config.window_state = bytes(self.saveState()).hex()
        if self.sidebar.isVisible():
            # dimensiunile contează doar cu panoul vizibil (ascuns, lățimea e 0)
            self.config.sidebar_splitter_sizes = self.horizontal_splitter.sizes()
        self.config.save()

    def _restore_geometry(self) -> None:
        try:
            if self.config.window_geometry:
                self.restoreGeometry(QByteArray.fromHex(self.config.window_geometry.encode()))
            if self.config.window_state:
                self.restoreState(QByteArray.fromHex(self.config.window_state.encode()))
            sizes = self.config.sidebar_splitter_sizes
            if sizes and len(sizes) == 2 and sizes[1] > 0:
                self.horizontal_splitter.setSizes(sizes)
        except Exception as exc:
            logger.error("Eroare la restaurarea geometriei: %s", exc)

    # ================================================================== #
    # Evenimente fereastră
    # ================================================================== #

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.schedule_save()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self.schedule_save()

    def closeEvent(self, event) -> None:
        if self.current_index_worker is not None:
            self.current_index_worker.cancel()
        self._save_config_now()
        self.thread_pool.waitForDone(10_000)
        self.db.close()
        event.accept()
