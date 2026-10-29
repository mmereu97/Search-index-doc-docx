"""Modele de date comune (fără dependențe de Qt sau SQLite)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass(frozen=True)
class DocumentRecord:
    """Un document gata de indexat."""
    path: str
    content: str
    mtime: float            # timestamp (os.stat.st_mtime) - sursa unică de adevăr
    size: int
    content_hash: str


@dataclass(frozen=True)
class SearchHit:
    """Un rezultat de căutare."""
    path: str
    mtime: float
    size: int
    exists: bool = True   # fișierul chiar e pe disc la calea asta?


@dataclass
class IndexReport:
    """Rezumatul unei rulări de indexare."""
    scanned: int = 0          # fișiere văzute pe disc (în fereastra de timp)
    indexed: int = 0          # citite + scrise în index
    unchanged: int = 0        # neschimbate, sărite
    unreadable: int = 0       # imposibil de citit
    locked: int = 0           # blocate / deschise în Word, sărite (reîncercate data viitoare)
    locked_paths: List[str] = field(default_factory=list)  # primele nume, pentru jurnal
    removed: int = 0          # dispărute de pe disc, șterse din index
    dirs_listed: int = 0      # foldere listate efectiv la scanare
    dirs_skipped: int = 0     # foldere sărite (data neschimbată față de instantaneu)
    dirs_archived: int = 0    # subarbori arhivați (ani vechi) ocoliți de indexarea rapidă
    read_new: int = 0         # trimise la citire pentru că nu erau în index
    read_changed: int = 0     # trimise la citire pentru că data/mărimea diferă
    read_stats: Dict[str, List[float]] = field(default_factory=dict)  # ext -> [nr, secunde]
    added: int = 0            # noi față de scanarea precedentă
    errors: List[str] = field(default_factory=list)
    started_at: Optional[datetime] = None
    duration_seconds: float = 0.0
