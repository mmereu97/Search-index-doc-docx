"""Normalizare de text și extragere de secvențe numerice.

Un singur loc pentru logica de diacritice (vechiul cod o avea duplicată în
DatabaseManager.normalize_text + DIACRITICE_MAP + NumericSequenceManager).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Set

_ROMANIAN_MAP = str.maketrans({
    "ș": "s", "ş": "s", "ț": "t", "ţ": "t",
    "ă": "a", "î": "i", "â": "a",
    "Ș": "s", "Ş": "s", "Ț": "t", "Ţ": "t",
    "Ă": "a", "Î": "i", "Â": "a",
})

_SEPARATORS = re.compile(r"[-‐‑‒–—―_/\\.]")
_NON_ALNUM = re.compile(r"[^a-z0-9\s]")

# Secvențe numerice de interes: minim 5 cifre, întregi (nu părți din numere mai lungi)
_NUMBER_RE = re.compile(r"(?<!\d)\d{5,}(?!\d)")

CNP_LENGTH = 13
CNP_FIRST_DIGITS = frozenset("1256")


def normalize_text(text: str) -> str:
    """Aduce textul la o formă canonică pentru indexare și căutare.

    Aceeași funcție se aplică și conținutului documentelor și interogării,
    deci 'Școală' găsește 'scoala' și invers.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = text.translate(_ROMANIAN_MAP)
    # elimină orice alt semn diacritic combinat rămas după NFKD
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = _SEPARATORS.sub(" ", text)
    text = _NON_ALNUM.sub(" ", text)
    return " ".join(text.split())


def extract_numbers(content: str) -> Set[str]:
    """Extrage toate secvențele numerice de minim 5 cifre din text."""
    return set(_NUMBER_RE.findall(content))


def is_cnp(sequence: str) -> bool:
    return len(sequence) == CNP_LENGTH and sequence[0] in CNP_FIRST_DIGITS


def is_numeric_query(query: str) -> bool:
    """O interogare formată doar din cifre (min. 4) caută în secvențele numerice."""
    q = query.strip()
    return q.isdigit() and len(q) >= 4
