"""Diagnosticul indexului: semafor + motive, în limbaj clar.

Fără Qt — primește baza de date și câteva praguri, întoarce un IndexHealth
pe care UI-ul doar îl afișează. Așa logica poate fi testată direct.

Niveluri:
    ok    (verde)   — totul la zi
    warn  (galben)  — funcționează, dar se recomandă o indexare completă
    bad   (roșu)    — indexul nu poate fi folosit corect până nu se acționează
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from ..ro_format import plural
from ..storage import Database
from ..storage.database import META_REMAPPED_AT


@dataclass
class IndexHealth:
    level: str                         # "ok" / "warn" / "bad"
    headline: str
    reasons: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)   # informații, nu probleme
    last_check: Optional[datetime] = None            # ultima indexare reușită (orice tip)
    last_full: Optional[datetime] = None             # ultima indexare COMPLETĂ reușită
    tracked_dirs: int = 0
    total_documents: int = 0


def humanize_ago(moment: Optional[datetime], now: Optional[datetime] = None) -> str:
    """'acum 47 de minute', 'acum 3 zile', 'niciodată'."""
    if moment is None:
        return "niciodată"
    now = now or datetime.now()
    seconds = max(0.0, (now - moment).total_seconds())
    minutes = seconds / 60
    if minutes < 1:
        return "chiar acum"
    if minutes < 60:
        return "acum " + plural(int(minutes), "minut", "minute")
    hours = minutes / 60
    if hours < 24:
        return "acum " + plural(int(hours), "oră", "ore")
    return "acum " + plural(int(hours / 24), "zi", "zile")


def compute_health(
    db: Database,
    folder: str,
    full_recommend_days: int = 30,
    pause_warn_days: int = 7,
    dynamic_quick: bool = True,
    quick_window_days: int = 365,
    use_dir_dates: bool = True,
    now: Optional[datetime] = None,
    db_on_network: bool = False,
    db_location: str = "",
) -> IndexHealth:
    now = now or datetime.now()
    last_check = db.get_last_index_completed()
    last_full = db.get_last_full_index()
    remapped_at = db.get_meta_datetime(META_REMAPPED_AT)
    total = db.count_documents()
    tracked = db.count_tracked_dirs()
    runs = db.get_index_runs(limit=1)

    health = IndexHealth(
        level="ok", headline="La zi",
        last_check=last_check, last_full=last_full,
        tracked_dirs=tracked, total_documents=total,
    )
    bad: List[str] = []
    warn: List[str] = []

    # --- Probleme blocante ------------------------------------------------ #
    if not folder:
        bad.append("Nu e ales niciun folder de indexat (Setări → Alege folder).")
    elif not os.path.isdir(folder):
        bad.append(f"Folderul indexat nu e accesibil acum: {folder}")
    if total == 0:
        bad.append("Indexul e gol — rulează o indexare completă.")
    if db_on_network:
        bad.append(
            f"Baza de date e pe un disc de rețea ({db_location}). Căutarea și "
            f"indexarea sunt mult mai lente, iar baza se poate deteriora. Mută "
            f"folderul programului (cu tot cu «Data») pe un disc local, de ex. C: sau D:."
        )

    # --- Recomandări -------------------------------------------------------- #
    if total > 0:
        if last_full is None:
            warn.append(
                "Nu există încă o indexare completă înregistrată. "
                "Una completă confirmă că indexul acoperă tot."
            )
        else:
            full_days = (now - last_full).days
            if full_days >= full_recommend_days:
                warn.append(
                    f"Ultima indexare completă e de acum {plural(full_days, 'zi', 'zile')}. Modificările "
                    f"care nu schimbă data folderului se pot strânge între timp."
                )

        if remapped_at is not None and (last_full is None or remapped_at > last_full):
            warn.append(
                "Indexul a fost reconectat la altă cale după ultima indexare completă."
            )

        if last_check is not None:
            pause_days = (now - last_check).days
            if not dynamic_quick and pause_days > quick_window_days:
                warn.append(
                    f"Perioadă neacoperită: au trecut {plural(pause_days, 'zi', 'zile')} de la "
                    f"ultima verificare, dar indexarea rapidă acoperă doar "
                    f"{plural(quick_window_days, 'zi', 'zile')}."
                )
            elif pause_days >= pause_warn_days:
                warn.append(
                    f"Pauză lungă: {plural(pause_days, 'zi', 'zile')} fără nicio verificare a folderelor."
                )

        if runs and runs[0]["status"] != "ok":
            kind = "completă" if runs[0]["kind"] == "full" else "rapidă"
            warn.append(f"Ultima indexare ({kind}) a fost oprită înainte de final.")

    # --- Note (informative) ------------------------------------------------- #
    if total > 0 and use_dir_dates and tracked == 0:
        health.notes.append(
            "Prima indexare rapidă va lista toate folderele (o singură dată), "
            "ca să-și facă harta datelor."
        )

    if bad:
        health.level, health.headline, health.reasons = "bad", "Necesită atenție", bad + warn
    elif warn:
        health.level, health.headline, health.reasons = "warn", "Recomand indexare completă", warn
    return health
