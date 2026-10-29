"""Formatare în limba română: plural, numere, mărimi de fișier.

Un singur loc, ca textele din toată aplicația să sune la fel și corect.

Pluralul românesc (regulile CLDR pentru "ro") are trei forme:
    1                       -> "1 rezultat"            (singular)
    0, 2–19, 101–119, …     -> "15 rezultate"          (fără "de")
    20–100, 120–200, …      -> "20 de rezultate"       (cu "de")
"""
from __future__ import annotations


def fmt_int(value) -> str:
    """1234567 -> '1.234.567' (punct pentru mii, ca în română)."""
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(value)


def fmt_decimal(value: float, digits: int = 1) -> str:
    """1.25 -> '1,3' — virgulă zecimală și punct pentru mii, ca în română."""
    text = f"{value:,.{digits}f}"                     # 12,345.6 (englezesc)
    return text.replace(",", "\u0001").replace(".", ",").replace("\u0001", ".")


def plural_form(n: int) -> str:
    """'one' / 'few' / 'other' după regulile românești."""
    n = abs(int(n))
    if n == 1:
        return "one"
    if n == 0 or 1 <= n % 100 <= 19:
        return "few"
    return "other"


def plural(n: int, one: str, many: str, number: bool = True) -> str:
    """plural(1, 'rezultat', 'rezultate') -> '1 rezultat'
       plural(15, ...) -> '15 rezultate';  plural(2000, ...) -> '2.000 de rezultate'.
    Cu number=False întoarce doar cuvântul (cu "de" acolo unde trebuie)."""
    form = plural_form(n)
    word = one if form == "one" else (many if form == "few" else f"de {many}")
    return f"{fmt_int(n)} {word}" if number else word


def fmt_size(num_bytes: float) -> str:
    """Mărime de fișier, cu unitatea potrivită: '850 B', '1,3 KB', '12,0 MB', '6,7 GB'."""
    size = float(num_bytes or 0)
    if size < 1024:
        return f"{int(size)} B"
    for unit in ("KB", "MB", "GB"):
        size /= 1024
        if size < 1024 or unit == "GB":
            return f"{fmt_decimal(size, 1)} {unit}"
    return f"{fmt_decimal(size, 1)} GB"


def join_ro(parts) -> str:
    """['a', 'b', 'c'] -> 'a, b și c'."""
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " și " + parts[-1]


def fmt_duration(seconds: float) -> str:
    """0,004 -> '4 milisecunde'; 4120 -> '1 oră, 8 minute și 40 de secunde'."""
    if seconds < 1:
        ms = int(round(seconds * 1000))
        return "sub o milisecundă" if ms == 0 else plural(ms, "milisecundă", "milisecunde")
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(plural(hours, "oră", "ore"))
    if minutes:
        parts.append(plural(minutes, "minut", "minute"))
    if secs or not parts:
        parts.append(plural(secs, "secundă", "secunde"))
    return join_ro(parts)
