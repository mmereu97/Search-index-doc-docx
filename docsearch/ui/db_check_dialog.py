"""Verificarea bazei de date: unde arată indexul și dacă fișierele chiar
există pe acest PC. Plus reconectare manuală (remap) a unei rădăcini —
compromisul care evită reindexarea completă după mutarea arhivei.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ..storage import Database


class DbCheckDialog(QDialog):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self._rows: List[Dict[str, Any]] = []
        self.remapped = False   # fereastra principală reîmprospătează statisticile dacă e True

        self.setWindowTitle("Verificare bază de date")
        self.setMinimumSize(720, 360)
        layout = QVBoxLayout(self)

        self.info_label = QLabel(
            "Fiecare rând este o rădăcină de cale din index. Dacă «Găsite pe disc» "
            "e 0% iar documentele chiar există pe acest PC la ALTĂ cale, selectează "
            "rândul și apasă «Reconectează la altă cale…» — instant, fără reindexare."
        )
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Rădăcină cale", "Documente", "Găsite pe disc (eșantion)", "Diagnostic"]
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        self.refresh_btn = QPushButton("Reîmprospătează")
        self.refresh_btn.clicked.connect(self.refresh)
        buttons.addWidget(self.refresh_btn)
        buttons.addStretch()
        self.remap_btn = QPushButton("Reconectează la altă cale…")
        self.remap_btn.clicked.connect(self._remap_selected)
        buttons.addWidget(self.remap_btn)
        close_btn = QPushButton("Închide")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        self.refresh()

    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        self._rows = self.db.analyze_paths()
        self.table.setRowCount(0)
        for info in self._rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            pct = (info["found"] / info["sample"] * 100) if info["sample"] else 0.0

            if pct >= 95:
                diag = "OK"
            elif pct > 5:
                diag = "Parțial — unele fișiere lipsesc"
            else:
                diag = "Calea NU există aici → reconectează"

            cells = [
                info["prefix"],
                f'{info["total"]}',
                f'{info["found"]}/{info["sample"]}  ({pct:.0f}%)',
                diag,
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col > 0:
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, col, item)

        total_docs = sum(info["total"] for info in self._rows)
        self.setWindowTitle(f"Verificare bază de date — {total_docs} documente în index")

    def _remap_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            QMessageBox.information(self, "Info", "Selectați întâi un rând din tabel.")
            return
        info = self._rows[row]
        old_prefix: str = info["prefix"]

        new_folder: Optional[str] = QFileDialog.getExistingDirectory(
            self, f"Unde se află ACUM documentele din {old_prefix} ?"
        )
        if not new_folder:
            return

        reply = QMessageBox.question(
            self, "Confirmare reconectare",
            f"Toate cele {info['total']} căi care încep cu:\n    {old_prefix}\n"
            f"vor fi rescrise să înceapă cu:\n    {new_folder}\n\n"
            "Conținutul indexat nu se modifică. Continuați?",
        )
        if reply != QMessageBox.Yes:
            return

        changed = self.db.remap_paths(old_prefix, new_folder)
        self.remapped = True
        QMessageBox.information(
            self, "Gata",
            f"{changed} căi reconectate.\n\n"
            "Verifică acum coloana «Găsite pe disc» — ar trebui să fie ~100%.",
        )
        self.refresh()
