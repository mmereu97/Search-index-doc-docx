"""Proprietățile din interiorul documentelor Word: cine l-a creat, cine l-a
salvat ultima dată. Exact ce arată Word la Fișier → Informații.

* .docx — fișierul docProps/core.xml din arhivă (câțiva KB; nu se citește
  documentul întreg, doar cuprinsul arhivei și acea intrare).
* .doc  — fluxul OLE "SummaryInformation", citit cu biblioteca olefile.

Notă: numele e cel setat în Word pe PC-ul care a salvat
(Fișier → Opțiuni → General → Nume utilizator), nu contul Windows.
Orice eroare => dicționar gol; funcția nu ridică excepții (e folosită la hover).
"""
from __future__ import annotations

import logging
import os
import zipfile
from typing import Dict

logger = logging.getLogger(__name__)

_NS_CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
_NS_DC = "{http://purl.org/dc/elements/1.1/}"


def _docx_meta(path: str) -> Dict[str, str]:
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(path) as archive:
        try:
            data = archive.read("docProps/core.xml")
        except KeyError:
            return {}
    root = ET.fromstring(data)
    meta: Dict[str, str] = {}
    last = root.findtext(f"{_NS_CP}lastModifiedBy")
    creator = root.findtext(f"{_NS_DC}creator")
    if last and last.strip():
        meta["last_modified_by"] = last.strip()
    if creator and creator.strip():
        meta["creator"] = creator.strip()
    return meta


def _decode(value) -> str:
    if isinstance(value, bytes):
        for enc in ("utf-8", "cp1250", "latin-1"):
            try:
                return value.decode(enc).strip("\x00 ").strip()
            except UnicodeDecodeError:
                continue
        return ""
    return str(value or "").strip()


def _doc_meta(path: str) -> Dict[str, str]:
    try:
        import olefile
    except ImportError:
        return {}
    if not olefile.isOleFile(path):
        return {}
    with olefile.OleFileIO(path) as ole:
        info = ole.get_metadata()
    meta: Dict[str, str] = {}
    last = _decode(getattr(info, "last_saved_by", None))
    creator = _decode(getattr(info, "author", None))
    if last:
        meta["last_modified_by"] = last
    if creator:
        meta["creator"] = creator
    return meta


def read_doc_authors(path: str) -> Dict[str, str]:
    """{'last_modified_by': ..., 'creator': ...} — doar cheile găsite."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".docx":
            return _docx_meta(path)
        if ext == ".doc":
            return _doc_meta(path)
    except Exception as exc:   # fișier lipsă, blocat, corupt — tooltip-ul merge mai departe
        logger.debug("Nu pot citi proprietățile pentru %s: %s", path, exc)
    return {}
