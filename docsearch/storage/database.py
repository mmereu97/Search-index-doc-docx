"""Stratul de stocare: SQLite + FTS5, cu UN SINGUR thread de scriere.

Arhitectura care rezolvă problemele vechiului cod:

  * Toate scrierile trec printr-un singur thread (``_writer_loop``) printr-o coadă.
    => zero lock-uri "database is locked", zero connection-pool per thread,
       zero ștergeri manuale de fișiere WAL.
  * Citirile (căutări, statistici) folosesc conexiuni de citire separate,
    deschise la cerere; cu journal_mode=WAL cititorii nu blochează writer-ul.
  * Căutarea de text folosește FTS5 (index full-text adevărat), nu
    ``LIKE '%...%'`` pe tot conținutul.

API-ul public este thread-safe și poate fi apelat din orice thread (workeri Qt).
"""
from __future__ import annotations

import logging
import os
import queue
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from ..search import normalize
from .models import DocumentRecord, SearchHit

logger = logging.getLogger(__name__)

DEFAULT_DB = "document_index.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY,
    path          TEXT UNIQUE NOT NULL,
    mtime         REAL NOT NULL,
    size          INTEGER NOT NULL,
    content_hash  TEXT NOT NULL,
    indexed_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_mtime ON documents(mtime);

-- Index full-text. rowid == documents.id. Conținutul este stocat deja
-- normalizat (fără diacritice), iar interogările se normalizează la fel.
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    content,
    tokenize = 'unicode61'
);

CREATE TABLE IF NOT EXISTS numeric_sequences (
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    sequence    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_numseq_sequence ON numeric_sequences(sequence);
CREATE INDEX IF NOT EXISTS idx_numseq_doc      ON numeric_sequences(document_id);
-- Index parțial, mic: doar secvențele de peste 13 cifre (singurele în care un
-- CNP poate apărea "lipit" de alte cifre). Căutarea de CNP le verifică doar pe ele.
CREATE INDEX IF NOT EXISTS idx_numseq_long ON numeric_sequences(sequence)
    WHERE length(sequence) > 13;

CREATE TABLE IF NOT EXISTS unreadable_files (
    path          TEXT PRIMARY KEY,
    error_message TEXT,
    first_attempt TEXT,
    last_attempt  TEXT,
    attempt_count INTEGER DEFAULT 1
);

-- Starea ultimei scanări complete, pentru raportul adăugate/eliminate.
CREATE TABLE IF NOT EXISTS scan_state (
    id        INTEGER PRIMARY KEY CHECK (id = 1),
    timestamp TEXT,
    paths     TEXT              -- JSON list
);

-- Valori mărunte despre index (ex. momentul ultimei indexări reușite).
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Instantaneul folderelor din ultima scanare reușită (pentru sărirea
-- folderelor neschimbate la indexarea rapidă): data + subfolderele fiecăruia.
CREATE TABLE IF NOT EXISTS dir_state (
    path    TEXT PRIMARY KEY,
    mtime   REAL NOT NULL,
    subdirs TEXT NOT NULL      -- JSON list de căi complete
);

-- Istoricul rulărilor de indexare (pentru diagnostic: când, ce tip, cum s-a terminat).
CREATE TABLE IF NOT EXISTS index_runs (
    id           INTEGER PRIMARY KEY,
    kind         TEXT NOT NULL,      -- 'quick' / 'full'
    started_at   TEXT NOT NULL,
    finished_at  TEXT NOT NULL,
    status       TEXT NOT NULL,      -- 'ok' / 'cancelled'
    indexed      INTEGER DEFAULT 0,
    unchanged    INTEGER DEFAULT 0,
    removed      INTEGER DEFAULT 0,
    dirs_listed  INTEGER DEFAULT 0,
    dirs_skipped INTEGER DEFAULT 0
);
"""

META_LAST_INDEX_COMPLETED = "last_index_completed"     # orice indexare reușită
META_LAST_FULL_INDEX = "last_full_index_completed"     # doar indexări COMPLETE reușite
META_REMAPPED_AT = "remapped_at"                       # ultima reconectare de căi

RUNS_KEEP = 200   # câte rulări păstrăm în istoric

# Versiunea curentă de schemă (stocată în PRAGMA user_version).
# Baza veche creată de search20.py are user_version=0 și alt tabel `documents`.
SCHEMA_VERSION = 2

# Un job de scriere = funcție care primește conexiunea writer-ului.
_WriteJob = Callable[[sqlite3.Connection], Any]
_SENTINEL = object()


class Database:
    """Fațada unică peste SQLite. Deschide writer-ul la construcție; apelează close() la final."""

    def __init__(self, db_path: str = DEFAULT_DB):
        self.db_path = db_path
        self._write_queue: "queue.Queue[Any]" = queue.Queue()
        self._writer_conn: Optional[sqlite3.Connection] = None
        self._writer_thread = threading.Thread(
            target=self._writer_loop, name="db-writer", daemon=True
        )
        self._started = threading.Event()
        self._writer_error: Optional[BaseException] = None
        self._writer_thread.start()
        self._started.wait(timeout=30)
        if self._writer_error:
            raise self._writer_error

    # ------------------------------------------------------------------ #
    # Writer-ul (singurul loc din proces care scrie în baza de date)
    # ------------------------------------------------------------------ #

    def _writer_loop(self) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._ensure_schema(conn)
            self._writer_conn = conn
        except BaseException as exc:  # semnalăm eroarea către __init__
            self._writer_error = exc
            self._started.set()
            return
        self._started.set()
        self._writer_main()

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        """Creează schema; migrează automat o bază veche (din search20.py).

        Detectare: user_version < SCHEMA_VERSION și un tabel ``documents``
        incompatibil (fără coloana ``mtime``). În acest caz tabelele vechi sunt
        șterse și indexul se reconstruiește la următoarea indexare — conținutul
        se poate oricând regenera din documentele de pe disc.
        """
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            has_documents = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'"
            ).fetchone() is not None
            if has_documents:
                columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(documents)")
                }
                if "mtime" not in columns:
                    logger.warning(
                        "Bază de date cu schemă veche detectată — se recreează. "
                        "Indexul va fi reconstruit la următoarea indexare."
                    )
                    old_tables = [
                        row[0]
                        for row in conn.execute(
                            "SELECT name FROM sqlite_master "
                            "WHERE type IN ('table', 'view') "
                            "AND name NOT LIKE 'sqlite_%'"
                        )
                    ]
                    conn.execute("PRAGMA foreign_keys=OFF")
                    for table in old_tables:
                        conn.execute(f'DROP TABLE IF EXISTS "{table}"')
                    conn.commit()
                    conn.execute("PRAGMA foreign_keys=ON")

        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()

    def _writer_main(self) -> None:
        conn = self._writer_conn

        while True:
            item = self._write_queue.get()
            if item is _SENTINEL:
                break
            job, done_event, result_box = item
            try:
                result_box["result"] = job(conn)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                result_box["error"] = exc
                logger.exception("Eroare în job-ul de scriere")
            finally:
                if done_event is not None:
                    done_event.set()

        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
        except Exception:
            pass
        conn.close()

    def _submit(self, job: _WriteJob, wait: bool = True) -> Any:
        """Trimite un job către writer. Cu wait=True întoarce rezultatul sau ridică excepția."""
        done = threading.Event() if wait else None
        box: Dict[str, Any] = {}
        self._write_queue.put((job, done, box))
        if wait:
            done.wait()
            if "error" in box:
                raise box["error"]
            return box.get("result")
        return None

    def close(self) -> None:
        """Golește coada, face checkpoint WAL și închide writer-ul."""
        self._write_queue.put(_SENTINEL)
        self._writer_thread.join(timeout=60)

    # ------------------------------------------------------------------ #
    # Conexiuni de citire (una per apel; ieftin și fără stare partajată)
    # ------------------------------------------------------------------ #

    @contextmanager
    def _read_conn(self) -> Iterator[sqlite3.Connection]:
        """Conexiune de citire, ÎNCHISĂ garantat la ieșire.

        Atenție: ``with sqlite3.connect(...)`` NU închide conexiunea (face doar
        commit/rollback) — de aici veneau conexiunile fantomă din vechiul cod.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA query_only=ON")
            conn.row_factory = sqlite3.Row
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------ #
    # Scrieri
    # ------------------------------------------------------------------ #

    def upsert_documents(self, docs: Sequence[DocumentRecord]) -> None:
        """Adaugă/actualizează un lot de documente într-o singură tranzacție."""
        if not docs:
            return

        prepared = [
            (
                doc,
                normalize.normalize_text(doc.content),
                sorted(normalize.extract_numbers(doc.content)),
            )
            for doc in docs
        ]

        def job(conn: sqlite3.Connection) -> None:
            now = datetime.now().isoformat()
            for doc, normalized, numbers in prepared:
                conn.execute(
                    """
                    INSERT INTO documents (path, mtime, size, content_hash, indexed_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        mtime=excluded.mtime,
                        size=excluded.size,
                        content_hash=excluded.content_hash,
                        indexed_at=excluded.indexed_at
                    """,
                    (doc.path, doc.mtime, doc.size, doc.content_hash, now),
                )
                # lastrowid NU e de încredere pe ramura DO UPDATE -> luăm id-ul explicit.
                doc_id = conn.execute(
                    "SELECT id FROM documents WHERE path = ?", (doc.path,)
                ).fetchone()[0]

                conn.execute("DELETE FROM documents_fts WHERE rowid = ?", (doc_id,))
                conn.execute(
                    "INSERT INTO documents_fts (rowid, content) VALUES (?, ?)",
                    (doc_id, normalized),
                )

                conn.execute(
                    "DELETE FROM numeric_sequences WHERE document_id = ?", (doc_id,)
                )
                if numbers:
                    conn.executemany(
                        "INSERT INTO numeric_sequences (document_id, sequence) VALUES (?, ?)",
                        [(doc_id, seq) for seq in numbers],
                    )
                # Un fișier indexat cu succes nu mai e "necitibil"
                conn.execute("DELETE FROM unreadable_files WHERE path = ?", (doc.path,))

        self._submit(job)

    def remove_documents(self, paths: Iterable[str]) -> int:
        """Șterge documentele (și indexul FTS aferent) pentru căile date."""
        paths = list(paths)
        if not paths:
            return 0

        def job(conn: sqlite3.Connection) -> int:
            removed = 0
            for path in paths:
                row = conn.execute(
                    "SELECT id FROM documents WHERE path = ?", (path,)
                ).fetchone()
                if row:
                    conn.execute("DELETE FROM documents_fts WHERE rowid = ?", (row[0],))
                    conn.execute("DELETE FROM documents WHERE id = ?", (row[0],))
                    removed += 1
                conn.execute("DELETE FROM unreadable_files WHERE path = ?", (path,))
            return removed

        return self._submit(job)

    def load_dir_state(self) -> Dict[str, Tuple[float, List[str]]]:
        """Instantaneul folderelor din ultima scanare: {cale: (mtime, [subfoldere])}."""
        import json

        with self._read_conn() as conn:
            return {
                row[0]: (row[1], json.loads(row[2]))
                for row in conn.execute("SELECT path, mtime, subdirs FROM dir_state")
            }

    def save_dir_state(self, snapshot: Dict[str, Tuple[float, List[str]]]) -> None:
        """Înlocuiește complet instantaneul (doar după o scanare terminată cu succes)."""
        import json

        rows = [
            (path, mtime, json.dumps(subdirs))
            for path, (mtime, subdirs) in snapshot.items()
        ]

        def job(conn: sqlite3.Connection) -> None:
            conn.execute("DELETE FROM dir_state")
            conn.executemany(
                "INSERT INTO dir_state (path, mtime, subdirs) VALUES (?, ?, ?)", rows
            )

        self._submit(job)

    def clear_dir_state(self) -> None:
        def job(conn: sqlite3.Connection) -> None:
            conn.execute("DELETE FROM dir_state")

        self._submit(job)

    def set_meta(self, key: str, value: str) -> None:
        def job(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

        self._submit(job)

    def get_meta(self, key: str) -> Optional[str]:
        with self._read_conn() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None

    def get_meta_datetime(self, key: str) -> Optional[datetime]:
        value = self.get_meta(key)
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def get_last_index_completed(self) -> Optional[datetime]:
        """Momentul ultimei indexări terminate cu succes (semnul pentru
        indexarea rapidă dinamică), sau None dacă nu există încă."""
        return self.get_meta_datetime(META_LAST_INDEX_COMPLETED)

    def get_last_full_index(self) -> Optional[datetime]:
        return self.get_meta_datetime(META_LAST_FULL_INDEX)

    # ------------------------------------------------------------------ #
    # Istoricul rulărilor
    # ------------------------------------------------------------------ #

    def record_index_run(
        self, kind: str, started_at: datetime, status: str, report: Any
    ) -> None:
        finished = datetime.now().isoformat()
        values = (
            kind, started_at.isoformat(), finished, status,
            getattr(report, "indexed", 0), getattr(report, "unchanged", 0),
            getattr(report, "removed", 0), getattr(report, "dirs_listed", 0),
            getattr(report, "dirs_skipped", 0),
        )

        def job(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO index_runs (kind, started_at, finished_at, status, indexed,
                                        unchanged, removed, dirs_listed, dirs_skipped)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            conn.execute(
                "DELETE FROM index_runs WHERE id NOT IN "
                "(SELECT id FROM index_runs ORDER BY id DESC LIMIT ?)",
                (RUNS_KEEP,),
            )

        self._submit(job)

    def get_index_runs(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._read_conn() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM index_runs ORDER BY id DESC LIMIT ?", (limit,)
                )
            ]

    def count_tracked_dirs(self) -> int:
        with self._read_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM dir_state").fetchone()[0]

    def analyze_paths(self, sample_size: int = 150) -> List[Dict[str, Any]]:
        """Diagnoza indexului: grupează documentele după rădăcina căii
        (disc + primul folder) și verifică, pe un eșantion, câte fișiere
        chiar există pe acest PC.

        Rezultat per grup: {prefix, total, sample, found} — found/sample ≈ 0
        înseamnă că acea rădăcină nu există aici și indexul trebuie
        reconectat (remap), NU reindexat.
        """
        import random

        groups: Dict[str, List[str]] = {}
        with self._read_conn() as conn:
            for (path,) in conn.execute("SELECT path FROM documents"):
                drive, rest = os.path.splitdrive(path)
                parts = [p for p in rest.replace("\\", "/").split("/") if p]
                prefix = drive + os.sep + (parts[0] if parts else "")
                groups.setdefault(prefix, []).append(path)

        result: List[Dict[str, Any]] = []
        for prefix, paths_list in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            sample = (
                paths_list
                if len(paths_list) <= sample_size
                else random.sample(paths_list, sample_size)
            )
            found = sum(1 for p in sample if os.path.exists(p))
            result.append({
                "prefix": prefix,
                "total": len(paths_list),
                "sample": len(sample),
                "found": found,
            })
        return result

    def count_paths_under(self, prefix: str) -> int:
        """Câte documente indexate au calea sub prefixul dat (insensibil la majuscule,
        cum sunt căile Windows)."""
        prefix_norm = os.path.normpath(prefix).lower().rstrip("\\/")
        with self._read_conn() as conn:
            return sum(
                1
                for (path,) in conn.execute("SELECT path FROM documents")
                if os.path.normpath(path).lower().startswith(prefix_norm + os.sep)
                or os.path.normpath(path).lower().startswith(prefix_norm + "\\")
                or os.path.normpath(path).lower().startswith(prefix_norm + "/")
            )

    def remap_paths(self, old_prefix: str, new_prefix: str) -> int:
        """Rescrie prefixul căilor din index: mutarea arhivei pe alt disc/PC
        FĂRĂ reindexare. Conținutul FTS și secvențele numerice rămân neatinse.

        Potrivirea prefixului e insensibilă la majuscule (căi Windows) și
        acceptă ambele tipuri de separator. Întoarce numărul de căi rescrise.
        """
        old_norm = os.path.normpath(old_prefix).lower().rstrip("\\/")
        new_clean = os.path.normpath(new_prefix).rstrip("\\/")

        def _maps(path: str) -> Optional[str]:
            norm = os.path.normpath(path)
            if norm.lower() == old_norm:
                return new_clean
            for sep in ("\\", "/"):
                if norm.lower().startswith(old_norm + sep):
                    return new_clean + os.sep + norm[len(old_norm) + 1:]
            return None

        def job(conn: sqlite3.Connection) -> int:
            changed = 0
            # urmă pentru diagnostic: după o reconectare se recomandă o indexare completă
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (META_REMAPPED_AT, datetime.now().isoformat()),
            )
            doc_updates = []
            for doc_id, path in conn.execute("SELECT id, path FROM documents"):
                new_path = _maps(path)
                if new_path is not None:
                    doc_updates.append((new_path, doc_id))
            for new_path, doc_id in doc_updates:
                conn.execute("UPDATE documents SET path = ? WHERE id = ?", (new_path, doc_id))
                changed += 1

            unread_updates = []
            for (path,) in conn.execute("SELECT path FROM unreadable_files"):
                new_path = _maps(path)
                if new_path is not None:
                    unread_updates.append((new_path, path))
            for new_path, old_path in unread_updates:
                # dacă noua cale există deja în tabel, o păstrăm pe aceea
                conn.execute(
                    "UPDATE OR IGNORE unreadable_files SET path = ? WHERE path = ?",
                    (new_path, old_path),
                )
                conn.execute(
                    "DELETE FROM unreadable_files WHERE path = ?", (old_path,)
                )

            # Instantaneul folderelor conține căile vechi -> îl golim; prima
            # scanare după remap îl reconstruiește (o dată, fără scurtătură).
            conn.execute("DELETE FROM dir_state")

            # Starea ultimei scanări se referă la căile vechi -> o rescriem și pe ea.
            import json
            row = conn.execute("SELECT paths FROM scan_state WHERE id = 1").fetchone()
            if row:
                remapped = [(_maps(p) or p) for p in json.loads(row[0])]
                conn.execute(
                    "UPDATE scan_state SET paths = ? WHERE id = 1",
                    (json.dumps(sorted(remapped)),),
                )
            return changed

        return self._submit(job)

    def mark_unreadable(self, path: str, error_message: str) -> None:
        def job(conn: sqlite3.Connection) -> None:
            now = datetime.now().isoformat()
            conn.execute(
                """
                INSERT INTO unreadable_files (path, error_message, first_attempt, last_attempt)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    last_attempt = excluded.last_attempt,
                    attempt_count = attempt_count + 1,
                    error_message = excluded.error_message
                """,
                (path, error_message, now, now),
            )

        self._submit(job, wait=False)

    def save_scan_state(self, paths: Set[str]) -> None:
        import json

        def job(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO scan_state (id, timestamp, paths) VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    timestamp = excluded.timestamp,
                    paths = excluded.paths
                """,
                (datetime.now().isoformat(), json.dumps(sorted(paths))),
            )

        self._submit(job)

    def clear_index(self) -> None:
        def job(conn: sqlite3.Connection) -> None:
            for table in ("numeric_sequences", "documents_fts", "documents",
                          "unreadable_files", "scan_state", "meta", "dir_state",
                          "index_runs"):
                conn.execute(f"DELETE FROM {table}")

        self._submit(job)
        # VACUUM nu poate rula în tranzacție; conexiune dedicată, scurtă.
        with sqlite3.connect(self.db_path) as conn:
            conn.isolation_level = None
            conn.execute("VACUUM")

    # ------------------------------------------------------------------ #
    # Citiri
    # ------------------------------------------------------------------ #

    def get_document_meta(self, path: str) -> Optional[Tuple[float, int, str]]:
        """(mtime, size, content_hash) pentru un document sau None dacă nu e indexat."""
        with self._read_conn() as conn:
            row = conn.execute(
                "SELECT mtime, size, content_hash FROM documents WHERE path = ?",
                (path,),
            ).fetchone()
            return (row[0], row[1], row[2]) if row else None

    def get_all_paths(self) -> Set[str]:
        with self._read_conn() as conn:
            return {row[0] for row in conn.execute("SELECT path FROM documents")}

    def get_all_meta(self) -> Dict[str, Tuple[float, int]]:
        """{path: (mtime, size)} pentru TOATE documentele, într-o singură interogare.

        Folosit de indexer ca să decidă ce e neschimbat fără câte o conexiune
        per fișier (contează mult pe foldere cu zeci de mii de documente).
        """
        with self._read_conn() as conn:
            return {
                row[0]: (row[1], row[2])
                for row in conn.execute("SELECT path, mtime, size FROM documents")
            }

    def get_unreadable_paths(self) -> Set[str]:
        with self._read_conn() as conn:
            return {row[0] for row in conn.execute("SELECT path FROM unreadable_files")}

    def load_scan_state(self) -> Optional[Set[str]]:
        import json

        with self._read_conn() as conn:
            row = conn.execute("SELECT paths FROM scan_state WHERE id = 1").fetchone()
            return set(json.loads(row[0])) if row else None

    def search(self, query: str, limit: int = 2000) -> List[SearchHit]:
        """Caută documente. Interogări numerice -> secvențe numerice; altfel FTS5.

        Întoarce TOATE potrivirile din index. Cele ale căror fișiere nu sunt
        pe disc la calea indexată au exists=False — UI-ul le marchează, dar NU
        le ascunde și NU le șterge. (O versiune anterioară le ștergea tăcut la
        fiecare căutare: pe un PC unde arhiva e montată pe altă cale, asta
        distrugea indexul. Curățarea intrărilor moarte e treaba indexării
        complete, care știe sigur ce a dispărut.)
        """
        query = query.strip()
        if not query:
            return []

        with self._read_conn() as conn:
            if normalize.is_numeric_query(query):
                sql = """
                    SELECT DISTINCT d.path, d.mtime, d.size
                    FROM documents d
                    JOIN numeric_sequences ns ON ns.document_id = d.id
                    WHERE {condition}
                    ORDER BY d.mtime DESC
                    LIMIT ?
                """
                if normalize.is_cnp(query):
                    # CNP complet: (1) potrivire EXACTĂ prin index — instantă;
                    # (2) plus secvențele mai lungi de 13 cifre care îl conțin
                    # (CNP lipit de alte cifre), prin indexul parțial mic.
                    exact = conn.execute(
                        sql.format(condition="ns.sequence = ?"), (query, limit)
                    ).fetchall()
                    glued = conn.execute(
                        sql.format(condition="length(ns.sequence) > 13 "
                                             "AND instr(ns.sequence, ?) > 0"),
                        (query, limit),
                    ).fetchall()
                    merged = {row["path"]: row for row in list(exact) + list(glued)}
                    rows = sorted(merged.values(), key=lambda r: r["mtime"], reverse=True)[:limit]
                else:
                    # Fragment de număr: oriunde în secvență (instr() e mai ieftin ca LIKE '%…%').
                    rows = conn.execute(
                        sql.format(condition="instr(ns.sequence, ?) > 0"), (query, limit)
                    ).fetchall()
            else:
                fts_query = self._build_fts_query(query)
                if not fts_query:
                    return []
                rows = conn.execute(
                    """
                    SELECT d.path, d.mtime, d.size
                    FROM documents_fts f
                    JOIN documents d ON d.id = f.rowid
                    WHERE documents_fts MATCH ?
                    ORDER BY d.mtime DESC
                    LIMIT ?
                    """,
                    (fts_query, limit),
                ).fetchall()

        return self._attach_exists(rows)

    @staticmethod
    def _attach_exists(rows) -> List[SearchHit]:
        """Rânduri (path, mtime, size) -> SearchHit-uri cu 'mai există pe disc?'.

        Verificarea e un drum prin rețea pentru fiecare fișier; în paralel,
        sute de rezultate costă cât câteva, nu cât sute.
        """
        paths = [row["path"] for row in rows]
        if len(paths) > 8:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=16) as pool:
                exists_flags = list(pool.map(os.path.exists, paths))
        else:
            exists_flags = [os.path.exists(p) for p in paths]
        return [
            SearchHit(row["path"], row["mtime"], row["size"], exists=flag)
            for row, flag in zip(rows, exists_flags)
        ]

    def count_matches(self, query: str) -> int:
        """Câte documente se potrivesc în total (fără limită) — se cheamă doar
        când search() a atins limita, ca interfața să spună „primele 2.000 din N"."""
        query = query.strip()
        if not query:
            return 0
        with self._read_conn() as conn:
            if normalize.is_numeric_query(query):
                if normalize.is_cnp(query):
                    sql = ("SELECT COUNT(DISTINCT document_id) FROM numeric_sequences "
                           "WHERE sequence = ? OR "
                           "(length(sequence) > 13 AND instr(sequence, ?) > 0)")
                    return conn.execute(sql, (query, query)).fetchone()[0]
                return conn.execute(
                    "SELECT COUNT(DISTINCT document_id) FROM numeric_sequences "
                    "WHERE instr(sequence, ?) > 0", (query,)
                ).fetchone()[0]
            fts_query = self._build_fts_query(query)
            if not fts_query:
                return 0
            return conn.execute(
                "SELECT COUNT(*) FROM documents_fts WHERE documents_fts MATCH ?",
                (fts_query,),
            ).fetchone()[0]

    def count_recent(self, modified_hours: int = 12) -> int:
        """Câte documente au fost modificate în ultimele N ore (rapid, prin
        indexul pe mtime) — pentru comutatorul de interval din interfață."""
        cutoff = (datetime.now() - timedelta(hours=modified_hours)).timestamp()
        with self._read_conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM documents WHERE mtime > ?", (cutoff,)
            ).fetchone()[0]

    def get_recent_hits(self, modified_hours: int = 12, limit: int = 2000) -> List[SearchHit]:
        """Documentele modificate recent, ca rezultate gata de afișat.

        Data și mărimea vin din index (nu din câte 4-5 interogări pe NAS
        pentru fiecare fișier); doar "mai există?" se verifică, în paralel.
        """
        cutoff = (datetime.now() - timedelta(hours=modified_hours)).timestamp()
        with self._read_conn() as conn:
            rows = conn.execute(
                "SELECT path, mtime, size FROM documents WHERE mtime > ? "
                "ORDER BY mtime DESC LIMIT ?",
                (cutoff, limit),
            ).fetchall()
        return self._attach_exists(rows)

    @staticmethod
    def _build_fts_query(query: str) -> str:
        """Interogarea utilizatorului -> FRAZĂ FTS5: cuvintele trebuie să apară
        exact în ordinea asta, unul lângă altul („VASILESCU VALERIA" NU găsește
        un act cu „Vasilescu" într-un loc și „Valeria" în altul).

        Diacriticele și majusculele sunt ignorate (conținutul e normalizat la
        indexare, interogarea la fel). Ultimul cuvânt rămâne prefix, ca să
        meargă și căutările incrementale ("factur" găsește "factura").
        În FTS5, termenii despărțiți prin spațiu înseamnă ȘI (oriunde în
        document); fraza exactă se cere cu ghilimele în jurul tuturor cuvintelor.
        """
        normalized = normalize.normalize_text(query)
        if not normalized:
            return ""
        words = normalized.split()
        phrase = " ".join(w.replace('"', '""') for w in words)
        return f'"{phrase}" *'

    # ------------------------------------------------------------------ #
    # Statistici
    # ------------------------------------------------------------------ #

    def count_documents(self) -> int:
        with self._read_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

    def get_stats(self, modified_hours: int = 12) -> Dict[str, Any]:
        with self._read_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            size_sum = conn.execute("SELECT COALESCE(SUM(size), 0) FROM documents").fetchone()[0]
            cnps = conn.execute(
                """
                SELECT COUNT(DISTINCT sequence) FROM numeric_sequences
                WHERE length(sequence) = 13
                  AND substr(sequence, 1, 1) IN ('1', '2', '5', '6')
                """
            ).fetchone()[0]
            cutoff = (datetime.now() - timedelta(hours=modified_hours)).timestamp()
            recent = conn.execute(
                "SELECT COUNT(*) FROM documents WHERE mtime > ?", (cutoff,)
            ).fetchone()[0]
            unreadable = conn.execute("SELECT COUNT(*) FROM unreadable_files").fetchone()[0]

        return {
            "Total documente": total,
            "Dimensiune totală (MB)": round(size_sum / (1024 * 1024), 2),
            "CNP-uri unice": cnps,
            f"Fișiere modificate (ultimele {modified_hours} ore)": recent,
            "Fișiere necitibile": unreadable,
        }

    def get_recently_modified(self, modified_hours: int = 12) -> List[str]:
        cutoff = (datetime.now() - timedelta(hours=modified_hours)).timestamp()
        with self._read_conn() as conn:
            return [
                row[0]
                for row in conn.execute(
                    "SELECT path FROM documents WHERE mtime > ? ORDER BY mtime DESC",
                    (cutoff,),
                )
            ]
