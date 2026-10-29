"""Scanarea sistemului de fișiere: ce fișiere există, care sunt în fereastra de timp."""
from __future__ import annotations

import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Tuple

# Instantaneu de foldere: {cale: (mtime, [subfoldere - căi complete])}
DirSnapshot = Dict[str, Tuple[float, List[str]]]


@dataclass(frozen=True)
class FileEntry:
    path: str
    mtime: float
    size: int


# Foldere de sistem excluse ÎNTOTDEAUNA, indiferent de setări:
# coșuri de gunoi (NAS Synology / Windows), miniaturi NAS, snapshot-uri.
ALWAYS_EXCLUDED = [
    "#recycle",                   # Recycle Bin pe NAS Synology
    "@eaDir",                     # miniaturi/metadata Synology
    "$RECYCLE.BIN",               # Recycle Bin Windows
    "System Volume Information",  # sistem Windows
    ".snapshot", "#snapshot",     # snapshot-uri NAS
]


# Un an „de sine stătător" în numele folderului: exact 4 cifre, nelipite de
# alte cifre (2015, „Ajutor inmormantare 2006", „Succesiuni 2015-2016";
# NU „20150101" și NU „Nr. 12015").
_YEAR_TOKEN = re.compile(r"(?<!\d)((?:19|20)\d\d)(?!\d)")
YEAR_MIN = 1990


def archive_year_cutoff(keep_years: int, now: Optional[datetime] = None) -> int:
    """Primul an considerat încă „activ". Cu keep_years=2 în 2026 => 2025:
    folderele 2025 și 2026 se scanează rapid, 2024 și mai vechi sunt arhivă."""
    year = (now or datetime.now()).year
    return year - max(int(keep_years), 1) + 1


def folder_years(dir_name: str, now: Optional[datetime] = None) -> List[int]:
    """Anii plauzibili găsiți în numele unui folder (între 1990 și anul viitor)."""
    year_max = (now or datetime.now()).year + 1
    return [
        int(tok) for tok in _YEAR_TOKEN.findall(dir_name)
        if YEAR_MIN <= int(tok) <= year_max
    ]


def is_year_folder_before(dir_name: str, cutoff_year: int,
                          now: Optional[datetime] = None) -> bool:
    """True dacă numele folderului poartă un an (sau mai mulți) și TOȚI sunt
    mai vechi decât ``cutoff_year``.

    Acoperă ambele obiceiuri de arhivare: folderul numit doar cu anul (``2015``)
    și anul lipit de nume (``Ajutor inmormantare 2006``). Un folder fără an în
    nume nu e niciodată arhivă; unul cu un an recent (``Contracte 2024-2026``)
    rămâne activ.
    """
    years = folder_years(dir_name, now)
    return bool(years) and max(years) < cutoff_year


def _build_exclusion_matcher(excluded: Sequence[str], year_cutoff: Optional[int] = None):
    """Potrivire pentru foldere excluse.

    O intrare care conține separator de cale sau ':' e tratată ca PREFIX de
    cale ("N:\\Filme" exclude tot subarborele); altfel e un NUME de folder
    ("Filme" exclude orice folder numit așa, oriunde). Insensibil la majuscule.
    ``year_cutoff``: folderele cu nume de an mai vechi decât el (2015, 2016…)
    sunt și ele excluse — arhiva în care nu se mai lucrează.
    """
    names = set()
    prefixes = []
    for entry in excluded:
        entry = entry.strip()
        if not entry:
            continue
        if any(ch in entry for ch in ("\\", "/", ":")):
            prefixes.append(os.path.normpath(entry).lower().rstrip("\\/"))
        else:
            names.add(entry.lower())

    def is_excluded(dir_path: str, dir_name: str) -> bool:
        if dir_name.lower() in names:
            return True
        if year_cutoff is not None and is_year_folder_before(dir_name, year_cutoff):
            return True
        if prefixes:
            norm = os.path.normpath(dir_path).lower()
            for prefix in prefixes:
                if norm == prefix or norm.startswith(prefix + os.sep) \
                        or norm.startswith(prefix + "\\") or norm.startswith(prefix + "/"):
                    return True
        return False

    return is_excluded if (names or prefixes) else None


def scan_folder(
    folder: str,
    extensions: Sequence[str],
    newer_than_days: Optional[int] = None,
    on_dir: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    excluded: Optional[Sequence[str]] = None,
) -> Iterator[FileEntry]:
    """Parcurge recursiv folderul și emite fișierele candidate la indexare.

    * ignoră fișierele temporare Office (încep cu ``~$``)
    * cu ``newer_than_days`` setat, emite doar fișierele modificate recent
      (modul "indexare rapidă")
    * ``on_dir`` e apelat pentru fiecare folder vizitat (progres de scanare)
    * ``should_cancel`` e verificat per folder: scanarea unui disc mare poate
      dura mult și trebuie să fie opribilă
    * ``excluded``: foldere sărite COMPLET (scanarea nici nu intră în ele) —
      esențial când rădăcina indexată conține și foldere mari fără documente
    * o SINGURĂ trecere prin disc — vechiul cod parcurgea totul de două ori
      (o dată ca să numere, o dată ca să indexeze)
    """
    exts = tuple(e.lower() for e in extensions)
    cutoff: Optional[float] = None
    if newer_than_days is not None:
        cutoff = (datetime.now() - timedelta(days=newer_than_days)).timestamp()
    is_excluded = _build_exclusion_matcher(ALWAYS_EXCLUDED + list(excluded or []))

    for root, dirs, files in os.walk(folder):
        if should_cancel is not None and should_cancel():
            return
        if is_excluded is not None:
            # tăiem subfolderele excluse ÎNAINTE ca os.walk să intre în ele
            dirs[:] = [
                d for d in dirs
                if not is_excluded(os.path.join(root, d), d)
            ]
        if on_dir is not None:
            on_dir(root)
        for name in files:
            if name.startswith("~$"):
                continue
            if not name.lower().endswith(exts):
                continue
            path = os.path.join(root, name)
            try:
                st = os.stat(path)
            except OSError:
                continue  # fișier dispărut/blocat între listare și stat
            if cutoff is not None and st.st_mtime < cutoff:
                continue
            yield FileEntry(path=path, mtime=st.st_mtime, size=st.st_size)


def collect(
    folder: str,
    extensions: Sequence[str],
    newer_than_days: Optional[int] = None,
) -> List[FileEntry]:
    return list(scan_folder(folder, extensions, newer_than_days))


def scan_folder_parallel(
    folder: str,
    extensions: Sequence[str],
    newer_than_days: Optional[int] = None,
    on_scan: Optional[Callable[[int, str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    excluded: Optional[Sequence[str]] = None,
    dir_snapshot: Optional[DirSnapshot] = None,
    snapshot_out: Optional[DirSnapshot] = None,
    stats_out: Optional[Dict[str, int]] = None,
    max_workers: int = 32,
    archived: Optional[Sequence[str]] = None,
    archive_year_cutoff: Optional[int] = None,
) -> List[FileEntry]:
    """Scanare cu mai multe foldere în paralel + sărirea folderelor neschimbate.

    Două accelerări, cumulate:

    1. PARALELISM: pe un NAS, scanarea clasică merge un folder pe rând și
       fiecare listare e un drum dus-întors prin rețea. Aici se listează
       ``max_workers`` foldere simultan — tipic de 5-10x mai repede.

    2. SĂRIREA FOLDERELOR NESCHIMBATE (ideea utilizatorului): cu
       ``dir_snapshot`` (starea din scanarea precedentă), un folder a cărui
       dată de modificare e NEschimbată nu mai e listat deloc — un singur
       ``stat`` în loc de o listare completă; subfolderele lui se iau din
       instantaneu și se verifică la rândul lor.
       Compromis asumat: data unui folder se schimbă la adăugare / ștergere /
       redenumire de fișiere în el (inclusiv la salvarea din Word, care
       scrie fișier temporar + redenumire), dar NU la modificarea în loc a
       unui fișier — astfel de editări sunt prinse abia de Indexarea
       completă, care ignoră instantaneul și reconstruiește tot.

    3. FOLDERE ARHIVATE (``archived`` – nume/căi – și ``archive_year_cutoff`` –
       foldere cu nume de an mai vechi de atât, ex. 2015, 2016): subarbori în
       care nu se mai lucrează. Indexarea rapidă nu intră deloc în ei (nici
       măcar un ``stat``); doar Indexarea completă (care nu trimite acești
       parametri) îi parcurge. Documentele lor rămân în index.

    ``snapshot_out`` primește starea curentă (de salvat DOAR după o rulare
    completă, neanulată). ``stats_out`` primește
    {'listed': X, 'skipped': Y, 'archived': Z}.
    ``on_scan(count, dir)`` poate veni din mai multe fire (semnalele Qt sunt OK).
    """
    exts = tuple(e.lower() for e in extensions)
    cutoff: Optional[float] = None
    if newer_than_days is not None:
        cutoff = (datetime.now() - timedelta(days=newer_than_days)).timestamp()
    is_excluded = _build_exclusion_matcher(ALWAYS_EXCLUDED + list(excluded or []))
    is_archived = None
    if archived or archive_year_cutoff is not None:
        is_archived = _build_exclusion_matcher(list(archived or []), archive_year_cutoff)

    entries: List[FileEntry] = []
    cond = threading.Condition()
    outstanding = 0          # foldere trimise la lucru, neterminate încă
    listed = skipped = archived_n = 0
    executor = ThreadPoolExecutor(max_workers=max_workers)

    def spawn(path: str) -> None:
        nonlocal outstanding
        with cond:
            outstanding += 1
        executor.submit(walk_dir, path)

    def walk_dir(path: str) -> None:
        nonlocal outstanding, listed, skipped, archived_n
        local: List[FileEntry] = []
        subdirs: List[str] = []      # de parcurs mai departe (filtrate)
        all_subdirs: List[str] = []  # de reținut în instantaneu (nefiltrate)
        dir_mtime: Optional[float] = None
        was_skipped = False
        archived_here = 0

        def keep(sub_path: str, sub_name: str) -> bool:
            nonlocal archived_here
            if is_excluded is not None and is_excluded(sub_path, sub_name):
                return False
            if is_archived is not None and is_archived(sub_path, sub_name):
                archived_here += 1
                return False
            return True

        try:
            if should_cancel is None or not should_cancel():
                dir_mtime = os.stat(path).st_mtime
                snap = dir_snapshot.get(path) if dir_snapshot else None

                if snap is not None and snap[0] == dir_mtime:
                    # Folder neschimbat: nu-l listăm. Subfolderele — din instantaneu.
                    was_skipped = True
                    all_subdirs = snap[1]
                    subdirs = [
                        sub for sub in all_subdirs
                        if keep(sub, os.path.basename(sub))
                    ]
                else:
                    with os.scandir(path) as it:
                        for de in it:
                            try:
                                if de.is_dir(follow_symlinks=False):
                                    all_subdirs.append(de.path)
                                    if keep(de.path, de.name):
                                        subdirs.append(de.path)
                                elif de.is_file(follow_symlinks=False):
                                    name = de.name
                                    if name.startswith("~$"):
                                        continue
                                    if not name.lower().endswith(exts):
                                        continue
                                    st = de.stat()
                                    if cutoff is not None and st.st_mtime < cutoff:
                                        continue
                                    local.append(FileEntry(de.path, st.st_mtime, st.st_size))
                            except OSError:
                                continue  # intrare dispărută între listare și stat
        except OSError:
            dir_mtime = None  # folder inaccesibil/dispărut — nu intră în instantaneu
        with cond:
            entries.extend(local)
            count = len(entries)
            archived_n += archived_here
            if dir_mtime is not None:
                if was_skipped:
                    skipped += 1
                else:
                    listed += 1
                if snapshot_out is not None:
                    snapshot_out[path] = (dir_mtime, all_subdirs)
        if on_scan is not None:
            on_scan(count, path)
        for sub in subdirs:
            spawn(sub)
        with cond:
            outstanding -= 1
            if outstanding == 0:
                cond.notify_all()

    spawn(folder)
    with cond:
        while outstanding:
            cond.wait(timeout=0.25)
    executor.shutdown(wait=True)
    if stats_out is not None:
        stats_out["listed"] = listed
        stats_out["skipped"] = skipped
        stats_out["archived"] = archived_n
    return entries
