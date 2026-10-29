"""Cititoare de fișiere, pe tip de extensie.

Două căi de citire:

* .docx / .txt — fișierul e citit O SINGURĂ DATĂ din rețea (bytes); din
  aceiași bytes se calculează și amprenta (hash) și se extrage textul.
  Sunt sigure pentru citire în paralel (fără COM).
* .doc — prin Microsoft Word (COM), doar pe Windows. Pornirea lui Word
  costă ~0,5 s, deci NU mai pornim un Word nou pentru fiecare fișier:
  ``WordSession`` ține o instanță deschisă pe toată durata indexării și o
  repornește preventiv din când în când. O sesiune aparține firului care
  a creat-o (regula COM) — se folosește dintr-un singur fir.
"""
from __future__ import annotations

import hashlib
import io
import logging
import sys
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

# Extensii citite prin Word (COM) — secvențial, în firul indexerului.
COM_EXTENSIONS = {".doc"}


class ReadError(Exception):
    """Fișierul nu a putut fi citit (corupt, gol, format neacceptat etc.)."""


class TransientReadError(ReadError):
    """Eșec temporar (fișier blocat/deschis de altcineva). NU se marchează
    permanent ca necitibil — se va încerca din nou la următoarea indexare."""


def word_lock_file(path: str) -> Path | None:
    """Fișierul-lacăt ``~$`` pe care Word îl creează lângă un document deschis.

    Regula Word: pentru nume lungi, primele 2 caractere sunt înlocuite cu
    ``~$`` ("contract.docx" -> "~$ntract.docx"); pentru nume scurte, ``~$``
    doar se prepune. Verificăm ambele variante; întoarce calea lacătului
    dacă există, altfel None.
    """
    p = Path(path)
    candidates = [p.with_name("~$" + p.name)]
    if len(p.name) > 2:
        candidates.append(p.with_name("~$" + p.name[2:]))
    for lock in candidates:
        if lock.exists():
            return lock
    return None


def is_open_in_word(path: str) -> bool:
    """True dacă documentul pare deschis în Word chiar acum (are lacăt ~$)."""
    return word_lock_file(path) is not None


# ---------------------------------------------------------------------- #
# Citire de bytes + amprentă
# ---------------------------------------------------------------------- #

def _read_bytes(path: str) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read()
    except PermissionError as exc:
        # Blocat de alt proces (de ex. deschis în Word) — reîncercăm altă dată.
        raise TransientReadError(f"Fișier blocat de alt proces: {exc}") from exc
    except OSError as exc:
        raise ReadError(f"Nu pot citi fișierul: {exc}") from exc


def file_hash(path: str) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


# ---------------------------------------------------------------------- #
# .docx — python-docx (multiplatformă). Citim și tabelele, nu doar
# paragrafele (vechiul cod pierdea tot textul din tabele!).
# ---------------------------------------------------------------------- #

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_W_T, _W_TAB, _W_BR, _W_CR, _W_P = _W + "t", _W + "tab", _W + "br", _W + "cr", _W + "p"


def _extract_docx(data: bytes) -> str:
    """Textul unui .docx, citit DIRECT din XML-ul din arhivă.

    De ~10x mai rapid decât python-docx (care recalculează structura
    tabelelor pentru fiecare celulă) și mai complet: prinde și casetele de
    text, antetele și subsolurile. Textul șters din urmărirea modificărilor
    (w:delText) e ignorat, ca în Word.
    """
    import xml.etree.ElementTree as ET
    import zipfile

    out: list = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            if "word/document.xml" not in names:
                raise ReadError("Fișier .docx invalid: lipsește word/document.xml")
            parts = ["word/document.xml"] + sorted(
                n for n in names
                if n.startswith(("word/header", "word/footer")) and n.endswith(".xml")
            )
            for name in parts:
                for _event, el in ET.iterparse(io.BytesIO(archive.read(name)), events=("end",)):
                    tag = el.tag
                    if tag == _W_T:
                        out.append(el.text or "")
                    elif tag == _W_TAB:
                        out.append("\t")
                    elif tag in (_W_BR, _W_CR, _W_P):
                        out.append("\n")
                    el.clear()   # memorie constantă și la documente uriașe
    except ReadError:
        raise
    except Exception as exc:
        raise ReadError(f"Fișier .docx invalid: {exc}") from exc

    content = "".join(out)
    if not content.strip():
        raise ReadError("Document gol")
    return content


def _extract_txt(data: bytes) -> str:
    content = data.decode("utf-8", errors="replace")
    if not content.strip():
        raise ReadError("Document gol")
    return content


_BYTE_EXTRACTORS: Dict[str, Callable[[bytes], str]] = {
    ".docx": _extract_docx,
    ".txt": _extract_txt,
}


def read_bytes_based_timed(path: str) -> Tuple[str, str, float, float, int]:
    """(text, hash, secunde_citire_disc, secunde_extragere, octeți) pentru .docx/.txt.

    Timpii separați arată unde se pierde timpul: citirea de pe disc/NAS
    (rețea, antivirus) sau extragerea textului (procesor).
    O singură citire din rețea; sigur de apelat din mai multe fire în paralel.
    """
    import time

    ext = Path(path).suffix.lower()
    extractor = _BYTE_EXTRACTORS.get(ext)
    if extractor is None:
        raise ReadError(f"Extensie neacceptată: {ext}")
    t0 = time.perf_counter()
    data = _read_bytes(path)
    t1 = time.perf_counter()
    text = extractor(data)
    digest = hashlib.sha256(data).hexdigest()
    t2 = time.perf_counter()
    return text, digest, t1 - t0, t2 - t1, len(data)


def read_bytes_based(path: str) -> Tuple[str, str]:
    """(text, hash) pentru .docx/.txt — o singură citire din rețea."""
    text, digest, _io, _parse, _size = read_bytes_based_timed(path)
    return text, digest


# ---------------------------------------------------------------------- #
# .doc — Word prin COM, cu instanță reutilizată
# ---------------------------------------------------------------------- #

class WordSession:
    """O instanță de Word ținută deschisă pentru multe fișiere .doc.

    * DispatchEx, NU Dispatch: Dispatch s-ar atașa la Word-ul DESCHIS al
      utilizatorului, iar Quit() i l-ar închide cu tot cu documentele lui.
    * Repornire preventivă la fiecare ``restart_every`` documente (Word
      acumulează memorie) și automat după orice eroare COM.
    * Macro-urile sunt dezactivate pentru instanța noastră (AutomationSecurity),
      iar documentele protegate cu parolă eșuează imediat în loc să aștepte
      la nesfârșit un dialog de parolă invizibil.
    * Trebuie folosită și închisă din ACELAȘI fir care a creat-o.
    """

    def __init__(self, restart_every: int = 200):
        self.restart_every = restart_every
        self._word = None
        self._docs_since_start = 0
        self._com_initialized = False
        self.starts = 0   # câte porniri de Word (pentru diagnostic)

    def _start(self) -> None:
        if not IS_WINDOWS:
            raise ReadError("Fișierele .doc pot fi citite doar pe Windows (necesită MS Word)")
        import pythoncom
        import win32com.client

        if not self._com_initialized:
            pythoncom.CoInitialize()
            self._com_initialized = True
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        try:
            word.AutomationSecurity = 3   # msoAutomationSecurityForceDisable: fără macro-uri
        except Exception:
            pass
        self._word = word
        self._docs_since_start = 0
        self.starts += 1

    def _stop(self) -> None:
        if self._word is not None:
            try:
                self._word.Quit(SaveChanges=False)
            except Exception:
                pass
            self._word = None

    def read(self, path: str) -> str:
        """Textul unui .doc. O eroare COM repornește Word și reîncearcă o dată."""
        for attempt in (1, 2):
            if self._word is None or self._docs_since_start >= self.restart_every:
                self._stop()
                self._start()
            doc = None
            try:
                doc = self._word.Documents.Open(
                    str(Path(path).resolve()),
                    ConfirmConversions=False,   # fără dialoguri de conversie
                    ReadOnly=True,
                    AddToRecentFiles=False,     # nu poluăm "Recent" al utilizatorului
                    PasswordDocument="#nopassword#",       # protejat => eroare, nu blocaj
                    WritePasswordDocument="#nopassword#",
                    Visible=False,
                    NoEncodingDialog=True,
                )
                content = doc.Content.Text
                self._docs_since_start += 1
                if not content.strip():
                    raise ReadError("Document gol")
                return content
            except ReadError:
                raise
            except Exception as exc:
                message = str(exc)
                # Erorile legate de document (parolă, corupt) nu se rezolvă cu repornire.
                if attempt == 2 or "password" in message.lower() or "parol" in message.lower():
                    raise ReadError(f"Eroare Word COM: {exc}") from exc
                logger.warning("Word a dat eroare, îl repornesc: %s", exc)
                self._stop()
            finally:
                if doc is not None:
                    try:
                        doc.Close(SaveChanges=False)
                    except Exception:
                        pass
        raise ReadError("Eroare Word COM")   # nu se ajunge aici

    def close(self) -> None:
        self._stop()
        if self._com_initialized:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass
            self._com_initialized = False


def read_doc(path: str) -> str:
    """Citire izolată a unui singur .doc (pentru apeluri ocazionale).
    Indexerul folosește direct o WordSession reutilizată."""
    session = WordSession()
    try:
        return session.read(path)
    finally:
        session.close()


def read_file(path: str) -> str:
    """Alege cititorul potrivit după extensie și întoarce textul."""
    ext = Path(path).suffix.lower()
    if ext in COM_EXTENSIONS:
        return read_doc(path)
    if ext in _BYTE_EXTRACTORS:
        return read_bytes_based(path)[0]
    raise ReadError(f"Extensie neacceptată: {ext}")


# Compatibilitate cu codul/testele care enumerau cititoarele.
READERS: Dict[str, Callable[[str], str]] = {
    ".docx": read_file,
    ".doc": read_file,
    ".txt": read_file,
}
