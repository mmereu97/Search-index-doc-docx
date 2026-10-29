"""DocSearch - sistem de căutare în documente Word, rescris pe arhitectură modulară.

Straturi:
    storage/   - baza de date SQLite (FTS5), cu un singur thread de scriere
    indexing/  - citirea fișierelor .doc/.docx și orchestrarea indexării
    search/    - normalizare text și construirea interogărilor
    ui/        - interfața PyQt5 (doar afișare + comenzi, zero logică de business)
"""

__version__ = "2.0.0"
