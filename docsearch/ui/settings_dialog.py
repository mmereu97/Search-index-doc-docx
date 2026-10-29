"""Dialogul de setări. Lucrează pe o COPIE a configurației; nimic nu se
aplică până nu apeși OK — fereastra principală primește config-ul nou
și îl aplică ea însăși (dialogul nu mai pipăie atributele părintelui).
"""
from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Optional

from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGroupBox,
    QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from ..config import AppConfig
from ..storage import Database
from .db_check_dialog import DbCheckDialog

FONT_SIZES = ["8", "10", "12", "14", "16", "18", "20", "22", "24"]


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, parent=None, db: Optional[Database] = None):
        super().__init__(parent)
        self.config = dataclasses.replace(config)  # copie de lucru
        self.db = db
        self.db_remapped = False   # citit de fereastra principală după închidere
        self._build_ui()
        self._load_from_config()

    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        self.setWindowTitle("Setări aplicație")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)

        # --- Aspect -------------------------------------------------- #
        appearance = QGroupBox("Aspect")
        app_layout = QHBoxLayout(appearance)
        app_layout.addWidget(QLabel("Mărime font:"))
        self.font_size_combo = QComboBox()
        self.font_size_combo.addItems(FONT_SIZES)
        app_layout.addWidget(self.font_size_combo)
        app_layout.addStretch()
        layout.addWidget(appearance)

        # --- Indexare ------------------------------------------------- #
        # Folderul de indexat se alege din panoul lateral al ferestrei principale.
        indexing = QGroupBox("Indexare")
        idx_layout = QVBoxLayout(indexing)

        self.refresh_on_startup = QCheckBox("Reindexează automat la pornire (indexare rapidă)")
        idx_layout.addWidget(self.refresh_on_startup)

        skip_row = QHBoxLayout()
        skip_row.addWidget(QLabel("…dar sari peste, dacă ultima indexare e mai nouă de (minute):"))
        self.startup_skip = QSpinBox()
        self.startup_skip.setRange(0, 1440)
        self.startup_skip.setToolTip(
            "0 = reindexează la fiecare pornire.\n"
            "60 = dacă ai pornit programul acum 10 minute și indexarea a reușit,\n"
            "următoarea pornire nu mai verifică nimic — pornește instant."
        )
        skip_row.addWidget(self.startup_skip)
        skip_row.addStretch()
        idx_layout.addLayout(skip_row)

        self.dynamic_quick = QCheckBox(
            "Indexare rapidă dinamică (acoperă doar zilele scurse de la ultima indexare)"
        )
        self.dynamic_quick.setToolTip(
            "Programul reține când s-a terminat ultima indexare reușită.\n"
            "La următoarea indexare rapidă scanează doar fișierele modificate\n"
            "de atunci încoace (+ 2 zile marjă de siguranță) — mult mai rapid\n"
            "dacă deschizi programul des.\n"
            "Fereastra fixă de mai jos rămâne folosită ca rezervă când nu\n"
            "există încă o indexare reușită."
        )
        self.dynamic_quick.toggled.connect(self._update_days_label)
        idx_layout.addWidget(self.dynamic_quick)

        quick_row = QHBoxLayout()
        self.days_label = QLabel("Indexarea rapidă acoperă ultimele (zile):")
        quick_row.addWidget(self.days_label)
        self.partial_days = QSpinBox()
        self.partial_days.setRange(1, 3650)
        quick_row.addWidget(self.partial_days)
        quick_row.addStretch()
        idx_layout.addLayout(quick_row)

        # Trei intervale pentru "fișiere modificate recent" — între ele se
        # comută direct din fereastra principală (Lucru zilnic).
        modified_row = QHBoxLayout()
        modified_row.addWidget(QLabel('Intervale pentru «fișiere modificate» (ore):'))
        self.preset_spins = []
        for _ in range(3):
            spin = QSpinBox()
            spin.setRange(1, 720)
            spin.setToolTip("Între 1 și 720 de ore (30 de zile).")
            modified_row.addWidget(spin)
            self.preset_spins.append(spin)
        modified_row.addStretch()
        idx_layout.addLayout(modified_row)

        self.use_dir_dates = QCheckBox(
            "Sari folderele neschimbate, după data folderului (scanare mult mai rapidă)"
        )
        self.use_dir_dates.setToolTip(
            "La indexarea rapidă, un folder cu data de modificare neschimbată\n"
            "față de scanarea precedentă nu mai e listat deloc.\n"
            "Data folderului se schimbă la adăugare/ștergere/redenumire de\n"
            "fișiere (inclusiv la salvarea din Word), dar NU la modificarea\n"
            "în loc a unui fișier — astfel de cazuri rare sunt prinse de\n"
            "Indexarea completă, care verifică totul."
        )
        idx_layout.addWidget(self.use_dir_dates)

        excl_label = QLabel("Foldere EXCLUSE de la scanare (câte unul pe linie):")
        excl_label.setToolTip(
            "Scanarea nu intră deloc în aceste foldere — util când folderul\n"
            "indexat conține și foldere mari fără documente (filme, backup-uri).\n"
            "• un NUME simplu (ex.: Filme) exclude orice folder cu acest nume\n"
            "• o CALE completă (ex.: N:\\Filme) exclude exact acel subarbore"
        )
        idx_layout.addWidget(excl_label)
        self.excluded_edit = QPlainTextEdit()
        self.excluded_edit.setFixedHeight(80)
        self.excluded_edit.setPlaceholderText("Filme\nBackup\nN:\\Altele")
        idx_layout.addWidget(self.excluded_edit)

        layout.addWidget(indexing)

        # --- Arhivă --------------------------------------------------- #
        archive = QGroupBox("Arhivă — verificată doar de Indexarea completă")
        archive.setToolTip(
            "Subarbori în care nu se mai lucrează (ani vechi). Indexarea rapidă\n"
            "nu mai intră deloc în ei — nici măcar ca să le verifice data —\n"
            "iar documentele lor rămân în index și se găsesc la căutare.\n"
            "Indexarea completă îi parcurge pe toți, ca de obicei."
        )
        arch_layout = QVBoxLayout(archive)
        year_row = QHBoxLayout()
        self.archive_years = QCheckBox(
            "Folderele cu an în nume (2015, «Ajutor 2006»…) sunt arhivă; activi ultimii"
        )
        self.archive_years.setToolTip(
            "Un folder care poartă un an în nume — fie doar anul (2015), fie\n"
            "lipit de nume (Ajutor inmormantare 2006, Succesiuni 2015-2016) —\n"
            "e tratat ca arhivă dacă TOȚI anii din nume sunt mai vechi decât\n"
            "ultimii N ani. Folderele fără an în nume se verifică mereu.\n"
            "Indexarea rapidă nu mai intră deloc în arhivă; cea completă, da."
        )
        year_row.addWidget(self.archive_years)
        self.archive_keep = QSpinBox()
        self.archive_keep.setRange(1, 30)
        self.archive_keep.setToolTip(
            "Câți ani rămân „activi”, anul curent inclus.\n"
            "2 => anul curent și cel trecut se scanează rapid; restul sunt arhivă."
        )
        year_row.addWidget(self.archive_keep)
        year_row.addWidget(QLabel("ani"))
        year_row.addStretch()
        arch_layout.addLayout(year_row)
        self.archive_years.toggled.connect(self.archive_keep.setEnabled)
        self.archive_hint = QLabel("")
        self.archive_hint.setStyleSheet("color: #666;")
        self.archive_hint.setWordWrap(True)
        arch_layout.addWidget(self.archive_hint)
        self.archive_years.toggled.connect(lambda _c: self._update_archive_hint())
        self.archive_keep.valueChanged.connect(lambda _v: self._update_archive_hint())

        arch_label = QLabel("Alte foldere arhivate (câte unul pe linie, nume sau cale):")
        arch_layout.addWidget(arch_label)
        self.archived_edit = QPlainTextEdit()
        self.archived_edit.setFixedHeight(60)
        self.archived_edit.setPlaceholderText("Vechi\nN:\\Documente\\Arhiva")
        arch_layout.addWidget(self.archived_edit)
        layout.addWidget(archive)

        # --- Întreținere bază de date --------------------------------- #
        maintenance = QGroupBox("Bază de date")
        maint_v = QVBoxLayout(maintenance)
        maint_layout = QHBoxLayout()
        maint_info = QLabel(
            "Verifică spre ce căi arată indexul și dacă fișierele există pe acest PC;\n"
            "de aici reconectezi indexul la altă cale, fără reindexare."
        )
        maint_layout.addWidget(maint_info, stretch=1)
        self.db_check_button = QPushButton("Verifică baza de date…")
        self.db_check_button.clicked.connect(self._open_db_check)
        self.db_check_button.setEnabled(self.db is not None)
        maint_layout.addWidget(self.db_check_button)
        maint_v.addLayout(maint_layout)

        recommend_row = QHBoxLayout()
        recommend_row.addWidget(QLabel("Recomandă indexare completă după (zile):"))
        self.full_recommend_days = QSpinBox()
        self.full_recommend_days.setRange(1, 365)
        self.full_recommend_days.setToolTip(
            "Când ultima indexare completă e mai veche de atât, semaforul din\n"
            "panoul lateral devine galben și evidențiază butonul de indexare completă."
        )
        recommend_row.addWidget(self.full_recommend_days)
        recommend_row.addStretch()
        maint_v.addLayout(recommend_row)
        layout.addWidget(maintenance)

        # --- OK / Cancel ---------------------------------------------- #
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ #

    def _load_from_config(self) -> None:
        idx = self.font_size_combo.findText(str(self.config.font_size))
        self.font_size_combo.setCurrentIndex(max(idx, 0))
        self.refresh_on_startup.setChecked(self.config.refresh_on_startup)
        self.dynamic_quick.setChecked(self.config.dynamic_quick_index)
        self.partial_days.setValue(self.config.partial_index_days)
        presets = (list(self.config.recent_hours_presets) + [12, 16, 24])[:3]
        for spin, hours in zip(self.preset_spins, presets):
            spin.setValue(int(hours))
        self.startup_skip.setValue(self.config.startup_skip_minutes)
        self.full_recommend_days.setValue(self.config.full_index_recommend_days)
        self.use_dir_dates.setChecked(self.config.use_dir_dates)
        self.excluded_edit.setPlainText("\n".join(self.config.excluded_folders))
        self.archive_years.setChecked(self.config.archive_year_folders)
        self.archive_keep.setValue(self.config.archive_keep_years)
        self.archive_keep.setEnabled(self.config.archive_year_folders)
        self.archived_edit.setPlainText("\n".join(self.config.archived_folders))
        self._update_days_label(self.dynamic_quick.isChecked())
        self._update_archive_hint()

    def _update_archive_hint(self) -> None:
        from ..indexing.scanner import archive_year_cutoff
        if not self.archive_years.isChecked():
            self.archive_hint.setText("Toate folderele se verifică la fiecare indexare rapidă.")
            return
        cutoff = archive_year_cutoff(self.archive_keep.value())
        current = datetime.now().year
        active = ", ".join(str(y) for y in range(cutoff, current + 1))
        self.archive_hint.setText(
            f"Acum: folderele cu {active} în nume se scanează rapid; "
            f"cele cu {cutoff - 1} și mai vechi doar la Indexarea completă."
        )

    def _update_days_label(self, dynamic_on: bool) -> None:
        self.days_label.setText(
            "Fereastră de rezervă, când nu există indexare anterioară (zile):"
            if dynamic_on
            else "Indexarea rapidă acoperă ultimele (zile):"
        )

    def _open_db_check(self) -> None:
        if self.db is None:
            return
        dialog = DbCheckDialog(self.db, self)
        dialog.exec_()
        if dialog.remapped:
            self.db_remapped = True

    def accept(self) -> None:
        self.config.font_size = int(self.font_size_combo.currentText())
        self.config.refresh_on_startup = self.refresh_on_startup.isChecked()
        self.config.dynamic_quick_index = self.dynamic_quick.isChecked()
        self.config.partial_index_days = self.partial_days.value()
        self.config.recent_hours_presets = [spin.value() for spin in self.preset_spins]
        # Intervalul activ dispărut din listă? Trecem pe prima poziție.
        if self.config.modified_files_hours not in self.config.recent_hours_presets:
            self.config.modified_files_hours = self.config.recent_hours_presets[0]
        self.config.startup_skip_minutes = self.startup_skip.value()
        self.config.full_index_recommend_days = self.full_recommend_days.value()
        self.config.use_dir_dates = self.use_dir_dates.isChecked()
        self.config.excluded_folders = [
            line.strip()
            for line in self.excluded_edit.toPlainText().splitlines()
            if line.strip()
        ]
        self.config.archive_year_folders = self.archive_years.isChecked()
        self.config.archive_keep_years = self.archive_keep.value()
        self.config.archived_folders = [
            line.strip()
            for line in self.archived_edit.toPlainText().splitlines()
            if line.strip()
        ]
        super().accept()

    def result_config(self) -> AppConfig:
        return self.config
