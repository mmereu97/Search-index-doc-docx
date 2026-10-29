"""Unde stau fișierele aplicației.

Structura:
    <rădăcină>\
    ├── Project\          codul (ce urcă pe GitHub)
    │   ├── main.py
    │   └── docsearch\...
    └── Data\             baza de date, config, log (NU urcă pe GitHub)

Rulat ca .exe (PyInstaller), folderul Data se creează lângă executabil:
    dist\DocSearch\
    ├── DocSearch.exe
    └── Data\

La prima rulare după reorganizare, fișierele vechi (document_index.db,
config.json, search_app.log) găsite lângă program sunt MUTATE automat în
Data\, ca indexul existent să nu se piardă.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR_NAME = "Data"

# Fișierele pe care le mutăm din vechea locație (lângă program) în Data\
_MIGRATABLE = [
    "document_index.db",
    "document_index.db-wal",
    "document_index.db-shm",
    "config.json",
    "search_app.log",
]


def get_base_dir() -> Path:
    """Rădăcina instalării: folderul exe-ului (frozen) sau părintele lui Project (dev)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # paths.py e în Project/docsearch/ -> parent = docsearch, ^2 = Project, ^3 = rădăcina
    return Path(__file__).resolve().parent.parent.parent


def get_data_dir() -> Path:
    """Folderul Data (creat dacă lipsește), cu migrarea fișierelor vechi."""
    base = get_base_dir()
    data = base / DATA_DIR_NAME
    data.mkdir(parents=True, exist_ok=True)

    # Migrare: fișiere rămase din vechea structură, lângă program.
    # Căutăm atât în rădăcină cât și în Project/ (vechiul loc al lui main.py).
    project_dir = Path(__file__).resolve().parent.parent if not getattr(sys, "frozen", False) else base
    for source_dir in {base, project_dir}:
        for name in _MIGRATABLE:
            old = source_dir / name
            new = data / name
            if old.exists() and not new.exists() and old != new:
                try:
                    shutil.move(str(old), str(new))
                    logger.info("Migrat %s -> %s", old, new)
                except OSError as exc:
                    logger.warning("Nu am putut muta %s în Data: %s", old, exc)
    return data


def is_network_path(path: str) -> bool:
    """True dacă calea e pe un disc de rețea (literă mapată la NAS sau \\\\server\\share).

    Contează pentru baza de date: SQLite în modul WAL NU funcționează corect
    pe discuri de rețea, iar fiecare citire/scriere trece prin rețea.
    """
    if not path:
        return False
    p = str(path)
    if p.startswith("\\\\") or p.startswith("//"):
        return True  # cale UNC
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        drive = os.path.splitdrive(os.path.abspath(p))[0]
        if not drive:
            return False
        DRIVE_REMOTE = 4
        return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == DRIVE_REMOTE
    except Exception:
        return False


def db_path() -> str:
    return str(get_data_dir() / "document_index.db")


def config_path() -> str:
    return str(get_data_dir() / "config.json")


def log_path() -> str:
    return str(get_data_dir() / "search_app.log")


def wake_network_drive(folder: str, timeout: float = 20.0) -> str:
    """Trezește un disc de rețea mapat (N:\\) rămas „deconectat" după pornirea
    Windows-ului: Windows nu-l reconectează singur până nu-l atinge cineva —
    exact ce face utilizatorul când dă clic pe el în Explorer.

    1. atingere simplă (listarea rădăcinii) — de obicei e de ajuns;
    2. dacă nu răspunde, reconectare explicită a literei la calea UNC asociată
       (WNetAddConnection2, cu credențialele salvate în Windows).
    Întoarce un mesaj scurt pentru jurnal. Nu ridică excepții.
    """
    if not folder:
        return "niciun folder"
    if sys.platform != "win32":
        return "disc local"
    drive = os.path.splitdrive(os.path.abspath(folder))[0]
    if not drive or not is_network_path(folder):
        return "disc local"

    import ctypes
    import threading
    from ctypes import wintypes

    root = drive + "\\"
    result = {"ok": False}

    def touch() -> None:
        try:
            os.listdir(root)
            result["ok"] = True
        except OSError:
            pass

    worker = threading.Thread(target=touch, daemon=True)
    worker.start()
    worker.join(timeout)
    if result["ok"]:
        return f"{drive} răspunde"

    # Reconectare explicită: aflăm calea UNC a literei și o remapăm.
    try:
        mpr = ctypes.windll.mpr
        length = wintypes.DWORD(1024)
        remote = ctypes.create_unicode_buffer(length.value)
        if mpr.WNetGetConnectionW(drive, remote, ctypes.byref(length)) != 0 or not remote.value:
            return f"{drive} nu răspunde și nu-i cunosc calea de rețea"

        class NETRESOURCE(ctypes.Structure):
            _fields_ = [
                ("dwScope", wintypes.DWORD), ("dwType", wintypes.DWORD),
                ("dwDisplayType", wintypes.DWORD), ("dwUsage", wintypes.DWORD),
                ("lpLocalName", wintypes.LPWSTR), ("lpRemoteName", wintypes.LPWSTR),
                ("lpComment", wintypes.LPWSTR), ("lpProvider", wintypes.LPWSTR),
            ]

        RESOURCETYPE_DISK = 1
        res = NETRESOURCE(0, RESOURCETYPE_DISK, 0, 0, drive, remote.value, None, None)
        code = mpr.WNetAddConnection2W(ctypes.byref(res), None, None, 0)
        if code in (0, 85):            # 0 = OK, 85 = ERROR_ALREADY_ASSIGNED
            try:
                os.listdir(root)
                return f"{drive} reconectat la {remote.value}"
            except OSError as exc:
                return f"{drive} remapat la {remote.value}, dar încă nu răspunde: {exc}"
        return f"{drive} ({remote.value}) nu a putut fi reconectat (cod Windows {code})"
    except Exception as exc:
        return f"{drive} nu răspunde: {exc}"
