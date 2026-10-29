"""Workeri Qt: punți subțiri între UI și logica din storage/indexing.

Toată munca grea e în Database/Indexer; aici doar rulăm în thread pool
și emitem semnale către UI. Progresul indexării e limitat la ~5 update-uri/s
ca să nu sufocăm event-loop-ul Qt (vechiul cod emitea la fiecare fișier).
"""
from __future__ import annotations

import time
import traceback
from typing import List, Optional

from PyQt5.QtCore import QObject, QRunnable, pyqtSignal, pyqtSlot

from ..indexing import Indexer
from ..storage import Database, IndexReport, SearchHit


SEARCH_LIMIT = 2000   # câte rezultate afișăm cel mult (lista rămâne rapidă)


class SearchSignals(QObject):
    finished = pyqtSignal(list, float, int)   # List[SearchHit], durata (s), total potriviri
    error = pyqtSignal(str)


class SearchWorker(QRunnable):
    def __init__(self, db: Database, query: str):
        super().__init__()
        self.db = db
        self.query = query
        self.signals = SearchSignals()

    @pyqtSlot()
    def run(self) -> None:
        try:
            t0 = time.time()
            hits: List[SearchHit] = self.db.search(self.query, limit=SEARCH_LIMIT)
            # Limita atinsă? Aflăm totalul real, ca interfața să nu pretindă
            # că "2.000" e tot ce există.
            total = (self.db.count_matches(self.query)
                     if len(hits) >= SEARCH_LIMIT else len(hits))
            self.signals.finished.emit(hits, time.time() - t0, max(total, len(hits)))
        except Exception:
            self.signals.error.emit(traceback.format_exc())


class CallSignals(QObject):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)


class CallWorker(QRunnable):
    """Rulează o funcție oarecare pe fundal (ex. statisticile), ca interfața
    să nu înghețe cât timp baza de date lucrează."""

    def __init__(self, fn, *args):
        super().__init__()
        self.fn = fn
        self.args = args
        self.signals = CallSignals()

    @pyqtSlot()
    def run(self) -> None:
        try:
            self.signals.finished.emit(self.fn(*self.args))
        except Exception:
            self.signals.error.emit(traceback.format_exc())


class IndexSignals(QObject):
    scan_progress = pyqtSignal(int, str)   # candidate găsite, folderul scanat acum
    progress = pyqtSignal(int, int, str)   # procesate, total, fișier curent
    note = pyqtSignal(str)                 # rând de diagnostic pentru jurnal
    finished = pyqtSignal(object)          # IndexReport
    error = pyqtSignal(str)


class IndexWorker(QRunnable):
    PROGRESS_INTERVAL = 0.2  # secunde între update-uri de progres

    def __init__(
        self,
        db: Database,
        folder: str,
        extensions: List[str],
        newer_than_days: Optional[int] = None,
        excluded_folders: Optional[List[str]] = None,
        use_dir_dates: bool = True,
        archived_folders: Optional[List[str]] = None,
        archive_year_cutoff: Optional[int] = None,
    ):
        super().__init__()
        self.archived_folders = archived_folders or []
        self.archive_year_cutoff = archive_year_cutoff
        self.indexer = Indexer(db, extensions)
        self.folder = folder
        self.newer_than_days = newer_than_days
        self.excluded_folders = excluded_folders or []
        self.use_dir_dates = use_dir_dates
        self.signals = IndexSignals()
        self._last_emit = 0.0
        self._last_scan_emit = 0.0

    def cancel(self) -> None:
        self.indexer.cancel()

    def _on_progress(self, done: int, total: int, path: str) -> None:
        now = time.time()
        if now - self._last_emit >= self.PROGRESS_INTERVAL or done == total:
            self._last_emit = now
            self.signals.progress.emit(done, total, path)

    def _on_scan(self, found: int, current_dir: str) -> None:
        now = time.time()
        if now - self._last_scan_emit >= self.PROGRESS_INTERVAL:
            self._last_scan_emit = now
            self.signals.scan_progress.emit(found, current_dir)

    @pyqtSlot()
    def run(self) -> None:
        try:
            report: IndexReport = self.indexer.run(
                self.folder,
                newer_than_days=self.newer_than_days,
                on_progress=self._on_progress,
                on_scan=self._on_scan,
                excluded_folders=self.excluded_folders,
                use_dir_dates=self.use_dir_dates,
                on_note=self.signals.note.emit,
                archived_folders=self.archived_folders,
                archive_year_cutoff=self.archive_year_cutoff,
            )
            self.signals.finished.emit(report)
        except Exception:
            self.signals.error.emit(traceback.format_exc())


def format_duration(seconds: float) -> str:
    """'1 oră, 8 minute și 40 de secunde' — cu pluralul românesc corect."""
    from ..ro_format import fmt_duration
    return fmt_duration(seconds)
