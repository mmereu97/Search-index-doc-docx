"""Configurația aplicației: un singur loc, un dataclass, salvare atomică.

Înlocuiește vechiul amestec de atribute împrăștiate prin SearchApp / SettingsDialog /
DatabaseManager. UI-ul citește și scrie DOAR prin acest obiect.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CONFIG_FILE = "config.json"

# Calea activă a configurației, setată la load(). Modul-level (nu pe instanță),
# ca să supraviețuiască lui dataclasses.replace() din dialogul de setări.
_ACTIVE_PATH: str = CONFIG_FILE


@dataclass
class AppConfig:
    # Indexare
    last_folder: str = ""
    refresh_on_startup: bool = True
    partial_index_days: int = 365        # "indexare rapidă": doar fișiere mai noi de N zile
    dynamic_quick_index: bool = True     # fereastra rapidă = zile de la ultima indexare + marjă
    modified_files_hours: int = 12       # fereastra ACTIVĂ pt. "fișiere modificate recent"
    recent_hours_presets: List[int] = field(default_factory=lambda: [12, 16, 24])  # comutator
    extensions: List[str] = field(default_factory=lambda: [".doc", ".docx"])
    excluded_folders: List[str] = field(default_factory=list)  # sărite la scanare
    # Foldere ARHIVATE: sărite de indexarea rapidă, verificate doar de cea completă.
    archived_folders: List[str] = field(default_factory=list)   # nume sau căi, ca excluded_folders
    archive_year_folders: bool = True   # folderele cu nume de an (2015, 2016…) sunt arhivă
    archive_keep_years: int = 2         # …cu excepția ultimilor N ani (anul curent inclus)
    use_dir_dates: bool = True   # sari folderele cu data neschimbată (indexarea rapidă)
    startup_skip_minutes: int = 60   # sari reindexarea la pornire dacă ultima e mai nouă de atât
    full_index_recommend_days: int = 30   # după câte zile semaforul recomandă indexare completă
    pause_warn_days: int = 7              # pauză fără verificare care merită semnalată

    # Interfață
    font_size: int = 14
    sort_method: str = "Sortare după data modificării"   # coloana după care se sortează
    sort_descending: Optional[bool] = None   # None = ordinea firească a coloanei
    sidebar_visible: bool = True             # panoul lateral (stare + cifre + indexare)
    sidebar_splitter_sizes: List[int] = field(default_factory=list)  # [rezultate, panou]
    log_visible: bool = False                # jurnalul de jos, pliat implicit
    window_geometry: str = ""            # hex din QByteArray
    window_state: str = ""
    main_splitter_sizes: List[int] = field(default_factory=list)
    horizontal_splitter_sizes: List[int] = field(default_factory=list)
    stats_table_columns: List[int] = field(default_factory=list)

    # Meta
    config_version: str = "2.0"

    # ------------------------------------------------------------------ #

    @classmethod
    def load(cls, path: Optional[str] = None) -> "AppConfig":
        """Încarcă din JSON; câmpurile necunoscute sunt ignorate, cele lipsă primesc default.

        Calea dată devine calea activă: toate save()-urile ulterioare scriu acolo.
        """
        global _ACTIVE_PATH
        if path is not None:
            _ACTIVE_PATH = path
        path = _ACTIVE_PATH
        cfg = cls()
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data: Dict[str, Any] = json.load(f)
                for key, value in data.items():
                    if hasattr(cfg, key):
                        setattr(cfg, key, value)
        except Exception as exc:  # config corupt -> pornim cu default, nu crăpăm
            logger.error("Eroare la încărcarea configurației: %s", exc)
        return cfg

    def save(self, path: Optional[str] = None) -> None:
        """Salvare atomică: scriem într-un fișier temporar și facem os.replace()."""
        if path is None:
            path = _ACTIVE_PATH
        try:
            directory = os.path.dirname(os.path.abspath(path)) or "."
            fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(asdict(self), f, indent=2, ensure_ascii=False)
                os.replace(tmp_path, path)  # atomic pe aceeași partiție
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
        except Exception as exc:
            logger.error("Eroare la salvarea configurației: %s", exc)

    # Comoditate
    @property
    def folder(self) -> Optional[Path]:
        if self.last_folder and os.path.isdir(self.last_folder):
            return Path(self.last_folder)
        return None
