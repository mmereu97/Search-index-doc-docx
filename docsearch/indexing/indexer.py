"""Orchestratorul indexării. Fără Qt, fără SQL — doar logică.

Primește un Database și un folder, raportează progresul prin callback-uri
simple, deci poate fi rulat identic din UI, din teste sau din linia de comandă.
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from ..storage import Database, DocumentRecord, IndexReport
from ..storage.database import META_LAST_FULL_INDEX, META_LAST_INDEX_COMPLETED
from . import readers, scanner

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]  # (procesate, total, fișier curent)
ScanCallback = Callable[[int, str], None]           # (candidate găsite, folderul curent)
NoteCallback = Callable[[str], None]                # un rând de diagnostic pentru jurnal

BATCH_SIZE = 25  # documente per tranzacție de scriere

# Diferență de dată tolerată între index și disc. Discuri/NAS-uri diferite
# rotunjesc data fișierului diferit (NTFS: 100 ns, SMB/FAT: 1-2 s); fără
# toleranță, un index mutat de pe alt disc ar recitit fișiere identice.
MTIME_TOLERANCE = 2.0

READ_WORKERS = 4          # fire de citire în paralel pentru .docx/.txt
READ_CHUNK = 64           # câte fișiere trimitem odată la fire (anulare + loturi)
REPORT_INTERVAL = 300.0   # secunde între rapoartele de diagnostic din jurnal

# Marjă de siguranță pentru fereastra dinamică: acoperă diferențe de ceas
# între PC-uri și fișiere salvate chiar în timpul ultimei indexări.
DYNAMIC_MARGIN_DAYS = 2


def dynamic_window_days(db: Database, fallback_days: int) -> int:
    """Fereastra pentru indexarea rapidă dinamică.

    = zilele scurse de la ultima indexare terminată cu succes + marjă.
    Dacă nu există semn (index nou / rulare anulată înainte de primul succes),
    întoarce fallback-ul (fereastra fixă din setări).
    """
    last = db.get_last_index_completed()
    if last is None:
        return fallback_days
    elapsed_days = (datetime.now() - last).total_seconds() / 86400.0
    return max(1, int(elapsed_days) + 1 + DYNAMIC_MARGIN_DAYS)


def _fmt_n(value: float) -> str:
    return f"{int(value):,}".replace(",", ".")


def _removed_breakdown(stale: Set[str], root: str) -> str:
    """'Eliminate din index: 13.019 — pe foldere: Vechi 12.800, #recycle 219'."""
    root_norm = os.path.normpath(root).lower().rstrip("\\/")
    counts: Dict[str, int] = {}
    for path in stale:
        norm = os.path.normpath(path)
        if norm.lower().startswith(root_norm):
            rest = norm[len(root_norm):].lstrip("\\/")
            top = rest.replace("\\", "/").split("/")[0] if ("/" in rest or "\\" in rest) else "(rădăcină)"
        else:
            top = "(în afara folderului indexat)"
        counts[top] = counts.get(top, 0) + 1
    top_items = sorted(counts.items(), key=lambda kv: -kv[1])[:8]
    detail = ", ".join(f"{name} {_fmt_n(n)}" for name, n in top_items)
    more = f" și alte {len(counts) - 8} foldere" if len(counts) > 8 else ""
    return (
        f"Eliminate din index (nu mai sunt găsite la scanare — șterse, mutate "
        f"sau în foldere excluse): {_fmt_n(len(stale))} — pe foldere: {detail}{more}."
    )


Phases = Dict[str, float]   # "verificare", "disc", "extragere", "word" -> secunde; "octeti"


def _read_without_com(
    entry: scanner.FileEntry,
) -> Tuple[scanner.FileEntry, object, float, Phases]:
    """Rulează într-un fir de citire: (intrare, (text, hash) sau excepție, secunde, faze)."""
    t0 = time.perf_counter()
    phases: Phases = {}
    try:
        # Un .docx deschis în Word se citește TOTUȘI: Word salvează atomic
        # (fișier temporar + redenumire), deci ce e pe disc e mereu o versiune
        # salvată, completă. Fișierele la care se lucrează azi sunt exact cele
        # căutate cel mai des. Dacă e totuși blocat (PermissionError), devine
        # eroare tranzitorie și se reia data viitoare — fără a fi marcat necitibil.
        text, digest, io_s, parse_s, size = readers.read_bytes_based_timed(entry.path)
        phases["disc"], phases["extragere"], phases["octeti"] = io_s, parse_s, size
        result: object = (text, digest)
    except Exception as exc:  # inclusiv ReadError — e tratată în firul principal
        result = exc
    return entry, result, time.perf_counter() - t0, phases


class _ReadDiagnostics:
    """Contoare pentru rapoartele periodice din jurnal.

    Pe lângă viteză, despică timpul pe faze — verificarea lacătului Word,
    citirea de pe disc/NAS (rețea + antivirus), extragerea textului
    (procesor), Word — și ține minte cele mai lente fișiere. Așa raportul
    spune DE CE e lent, nu doar CĂ e lent.
    """

    TOP_SLOW = 3

    def __init__(self, to_read: int, on_note: Optional[NoteCallback]):
        self.to_read = to_read
        self.on_note = on_note
        self.done = 0
        self.by_ext: Dict[str, List[float]] = {}   # ext -> [număr, secunde]
        self.phases: Dict[str, float] = {}         # fază -> secunde totale
        self.slowest: List[Tuple[float, str, int]] = []   # (secunde, cale, octeți)
        self.started = time.time()
        self.last_report = self.started
        self.next_report = self.started + 60.0   # primul raport repede, apoi din 5 în 5 min
        self.done_at_last_report = 0

    def add(self, path: str, seconds: float, phases: Optional[Phases] = None) -> None:
        ext = os.path.splitext(path)[1].lower() or "?"
        slot = self.by_ext.setdefault(ext, [0, 0.0])
        slot[0] += 1
        slot[1] += seconds
        self.done += 1
        size = 0
        for name, value in (phases or {}).items():
            if name == "octeti":
                size = int(value)
            else:
                self.phases[name] = self.phases.get(name, 0.0) + value
        self.slowest.append((seconds, path, size))
        self.slowest.sort(reverse=True)
        del self.slowest[self.TOP_SLOW:]

    def averages(self) -> str:
        parts = [
            f"{ext} {secs / count:.2f} s/fișier ({_fmt_n(count)})"
            for ext, (count, secs) in sorted(self.by_ext.items()) if count
        ]
        text = ", ".join(parts) if parts else "—"
        if self.done and self.phases:
            phase_txt = ", ".join(
                f"{name} {secs / self.done:.2f} s"
                for name, secs in sorted(self.phases.items(), key=lambda kv: -kv[1])
            )
            text += f". Pe faze (medie/fișier): {phase_txt}"
        if self.slowest:
            slow_txt = "; ".join(
                f"{secs:.1f} s {os.path.basename(path)}"
                + (f" ({size / 1048576:.1f} MB)" if size else "")
                for secs, path, size in self.slowest
            )
            text += f". Cele mai lente: {slow_txt}"
        return text

    def maybe_report(self, extra: str = "", force: bool = False) -> None:
        now = time.time()
        if self.on_note is None or (not force and now < self.next_report):
            return
        window = max(now - self.last_report, 1e-6)
        done_window = self.done - self.done_at_last_report
        rate = done_window / window
        remaining = self.to_read - self.done
        eta = f"~{remaining / rate / 60:.0f} min" if rate > 0 and remaining > 0 else "—"
        self.on_note(
            f"Raport: {_fmt_n(done_window)} fișiere citite în ultimele "
            f"{window / 60:.0f} min ({rate:.1f}/s). Citite {_fmt_n(self.done)} din "
            f"{_fmt_n(self.to_read)}, rămase {_fmt_n(remaining)} ({eta}). "
            f"Timp mediu: {self.averages()}.{(' ' + extra) if extra else ''}"
        )
        self.last_report = now
        self.next_report = now + REPORT_INTERVAL
        self.done_at_last_report = self.done


class Indexer:
    def __init__(self, db: Database, extensions: Sequence[str] = (".doc", ".docx")):
        self.db = db
        self.extensions = list(extensions)
        self._cancelled = False

    def cancel(self) -> None:
        """Poate fi apelat din alt thread; indexarea se oprește curat la următorul fișier."""
        self._cancelled = True

    # ------------------------------------------------------------------ #

    def run(
        self,
        folder: str,
        newer_than_days: Optional[int] = None,
        on_progress: Optional[ProgressCallback] = None,
        on_scan: Optional[ScanCallback] = None,
        retry_unreadable: bool = False,
        excluded_folders: Optional[List[str]] = None,
        use_dir_dates: bool = True,
        on_note: Optional[NoteCallback] = None,
        archived_folders: Optional[List[str]] = None,
        archive_year_cutoff: Optional[int] = None,
    ) -> IndexReport:
        """Indexează folderul. ``newer_than_days=None`` => indexare completă.

        ``archived_folders`` / ``archive_year_cutoff``: subarbori arhivați (ani
        vechi), ocoliți DOAR de indexarea rapidă; completă îi parcurge pe toți.

        Pași:
          1. scanează discul (o singură trecere)
          2. compară cu indexul: ce e nou/modificat, ce a dispărut
          3. citește și scrie în loturi DOAR ce s-a schimbat
        """
        self._cancelled = False
        report = IndexReport(started_at=datetime.now())
        t0 = time.time()

        # 1. Scanare disc — paralelă (16 foldere deodată), cu progres, anulare
        # și sărirea folderelor neschimbate (după data folderului, din
        # instantaneul scanării precedente). Scurtătura se aplică DOAR la
        # indexarea rapidă: Indexarea completă relistează tot, ca plasă de
        # siguranță pentru editările care nu ating data folderului.
        dir_snapshot = None
        if use_dir_dates and newer_than_days is not None:
            dir_snapshot = self.db.load_dir_state() or None
        snapshot_out: scanner.DirSnapshot = {}
        scan_stats: dict = {}

        quick = newer_than_days is not None
        entries = scanner.scan_folder_parallel(
            folder, self.extensions, newer_than_days,
            on_scan=on_scan,
            should_cancel=lambda: self._cancelled,
            excluded=excluded_folders,
            dir_snapshot=dir_snapshot,
            snapshot_out=snapshot_out,
            stats_out=scan_stats,
            archived=archived_folders if quick else None,
            archive_year_cutoff=archive_year_cutoff if quick else None,
        )

        kind = "full" if newer_than_days is None else "quick"

        if self._cancelled:
            report.errors.append("Indexare anulată în timpul scanării discului")
            report.duration_seconds = time.time() - t0
            self.db.record_index_run(kind, report.started_at, "cancelled", report)
            return report

        # Instantaneul se salvează doar după o scanare dusă la capăt.
        if snapshot_out:
            self.db.save_dir_state(snapshot_out)

        report.scanned = len(entries)
        report.dirs_listed = scan_stats.get("listed", 0)
        report.dirs_skipped = scan_stats.get("skipped", 0)
        report.dirs_archived = scan_stats.get("archived", 0)
        current_paths: Set[str] = {e.path for e in entries}

        # 2. Diff cu starea precedentă și cu indexul
        previous = self.db.load_scan_state()
        if previous is not None:
            report.added = len(current_paths - previous)

        if newer_than_days is None:
            # Indexare completă: tot ce e în index dar nu mai e pe disc -> afară.
            indexed_paths = self.db.get_all_paths()
            stale = indexed_paths - current_paths
            if stale:
                report.removed = self.db.remove_documents(stale)
                if on_note is not None:
                    on_note(_removed_breakdown(stale, folder))
            self.db.save_scan_state(current_paths)

        unreadable_known: Set[str] = (
            set() if retry_unreadable else self.db.get_unreadable_paths()
        )
        # Metadatele tuturor documentelor indexate, O SINGURĂ interogare
        # (nu una per fișier) — esențial pe foldere mari.
        known_meta = self.db.get_all_meta()

        # 3a. Triere — fără nicio citire din rețea: ce e neschimbat se sare.
        total = len(entries)
        to_read: List[scanner.FileEntry] = []
        for entry in entries:
            if entry.path in unreadable_known:
                report.unreadable += 1
                continue
            meta = known_meta.get(entry.path)
            if (meta is not None and meta[1] == entry.size
                    and abs(meta[0] - entry.mtime) <= MTIME_TOLERANCE):
                report.unchanged += 1
                continue
            if meta is None:
                report.read_new += 1
            else:
                report.read_changed += 1
            to_read.append(entry)

        done = total - len(to_read)
        if on_progress and total:
            on_progress(done, total, "")
        if on_note is not None:
            com_count = sum(
                1 for e in to_read
                if os.path.splitext(e.path)[1].lower() in readers.COM_EXTENSIONS
            )
            on_note(
                f"Triere: {_fmt_n(report.unchanged)} neschimbate (sărite fără citire), "
                f"{_fmt_n(len(to_read))} de citit — {_fmt_n(report.read_new)} noi, "
                f"{_fmt_n(report.read_changed)} modificate; dintre ele "
                f"{_fmt_n(com_count)} .doc (prin Word) și "
                f"{_fmt_n(len(to_read) - com_count)} .docx/alte tipuri."
            )

        # 3b. Citire: întâi .docx/.txt în paralel, apoi .doc printr-un singur Word.
        diag = _ReadDiagnostics(len(to_read), on_note)
        batch: List[DocumentRecord] = []

        def flush(force: bool = False) -> None:
            nonlocal batch
            if batch and (force or len(batch) >= BATCH_SIZE):
                self.db.upsert_documents(batch)
                batch = []

        def handle(entry: scanner.FileEntry, result: object, seconds: float,
                   phases: Optional[Phases] = None) -> None:
            nonlocal done
            done += 1
            diag.add(entry.path, seconds, phases)
            if on_progress:
                on_progress(done, total, entry.path)
            if isinstance(result, readers.TransientReadError):
                # Blocat temporar: NU îl marcăm necitibil, îl prindem la
                # următoarea indexare. Reținem numele pentru jurnal.
                report.locked += 1
                if len(report.locked_paths) < 20:
                    report.locked_paths.append(entry.path)
                return
            if isinstance(result, readers.ReadError):
                self.db.mark_unreadable(entry.path, str(result))
                report.unreadable += 1
                return
            if isinstance(result, Exception):
                logger.error("Eroare neașteptată la %s: %s", entry.path, result)
                self.db.mark_unreadable(entry.path, str(result))
                report.unreadable += 1
                report.errors.append(f"{entry.path}: {result}")
                return
            content, content_hash = result  # type: ignore[misc]
            batch.append(DocumentRecord(
                path=entry.path, content=content, mtime=entry.mtime,
                size=entry.size, content_hash=content_hash,
            ))
            report.indexed += 1
            flush()

        plain = [e for e in to_read
                 if os.path.splitext(e.path)[1].lower() not in readers.COM_EXTENSIONS]
        via_word = [e for e in to_read
                    if os.path.splitext(e.path)[1].lower() in readers.COM_EXTENSIONS]

        with ThreadPoolExecutor(max_workers=READ_WORKERS) as pool:
            for start in range(0, len(plain), READ_CHUNK):
                if self._cancelled:
                    break
                for entry, result, seconds, phases in pool.map(
                    _read_without_com, plain[start:start + READ_CHUNK]
                ):
                    handle(entry, result, seconds, phases)
                diag.maybe_report()

        session: Optional[readers.WordSession] = None
        try:
            for entry in via_word:
                if self._cancelled:
                    break
                t_read = time.perf_counter()
                word_phases: Phases = {}
                try:
                    # Doar .doc-urile (citite prin Word) sunt sărite când sunt
                    # deschise: Word ar putea afișa un dialog.
                    if readers.is_open_in_word(entry.path):
                        result: object = readers.TransientReadError("deschis în Word")
                    else:
                        if session is None:
                            session = readers.WordSession()
                        t_word = time.perf_counter()
                        content = session.read(entry.path)
                        word_phases["word"] = time.perf_counter() - t_word
                        result = (content, readers.file_hash(entry.path))
                except Exception as exc:
                    result = exc
                handle(entry, result, time.perf_counter() - t_read, word_phases)
                diag.maybe_report(
                    extra=f"Word pornit de {session.starts} ori." if session else ""
                )
        finally:
            if session is not None:
                session.close()

        flush(force=True)
        if report.locked_paths and on_note is not None:
            shown = ", ".join(os.path.basename(p) for p in report.locked_paths)
            more = f" și încă {report.locked - len(report.locked_paths)}" if report.locked > len(report.locked_paths) else ""
            on_note(f"Sărite (blocate / deschise în Word, se reiau data viitoare): {shown}{more}.")
        if self._cancelled:
            report.errors.append("Indexare anulată de utilizator")
        if to_read:
            diag.maybe_report(force=True)
        report.read_stats = {ext: list(v) for ext, v in diag.by_ext.items()}

        # Semnul pentru indexarea rapidă dinamică: DOAR dacă rularea s-a
        # terminat complet. O rulare anulată nu acoperă toată fereastra,
        # deci nu are voie să "avanseze" semnul.
        if not self._cancelled:
            self.db.set_meta(META_LAST_INDEX_COMPLETED, report.started_at.isoformat())
            if kind == "full":
                self.db.set_meta(META_LAST_FULL_INDEX, report.started_at.isoformat())
        self.db.record_index_run(
            kind, report.started_at, "cancelled" if self._cancelled else "ok", report
        )

        report.duration_seconds = time.time() - t0
        logger.info(
            "Indexare terminată: %d indexate, %d neschimbate, %d necitibile, "
            "%d eliminate, %.1fs",
            report.indexed, report.unchanged, report.unreadable,
            report.removed, report.duration_seconds,
        )
        return report
