"""Date de test "cel mai rău caz" pentru interfață (metoda break-ui).

DOAR PENTRU DEZVOLTARE — nu intră în .exe.

Construiește o arhivă notarială de test cu documente .docx reale, o trece
prin indexarea și căutarea REALE (aceeași graniță pe care intră datele
adevărate) și deschide fereastra programului cu o bară de comutare:

    Demo · Cel mai rău caz · Gol · Unu · 2.000+

Utilizare:
    python dev/break_ui_fixture.py                 # fereastră interactivă
    python dev/break_ui_fixture.py --shots OUT     # capturi PNG în OUT/
    python dev/break_ui_fixture.py --root CALE     # unde se creează arhiva

Arhiva rămâne pe disc: e testul de regresie pentru data viitoare când
cineva umblă la interfață.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import zipfile
from datetime import datetime, timedelta
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))   # Project/ în path

# Interogările care selectează fiecare stare din aceeași arhivă
STATES = [
    ("Demo", "procura"),
    ("Cel mai rău caz", "cazlimita"),
    ("Gol", "xyzinexistent"),
    ("Unu", "unicatlitate"),
    ("2.000+", "contract"),
]


# ---------------------------------------------------------------------- #
# .docx minimal, scris direct (rapid: 2.600 de fișiere în câteva secunde)
# ---------------------------------------------------------------------- #

_CT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-'
    'package.core-properties+xml"/></Types>'
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" Target="word/document.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/'
    'relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>'
)


def write_docx(path: str, paragraphs, author: str = "", last_by: str = "",
               pad_bytes: int = 0) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{escape(p)}</w:t></w:r></w:p>'
        for p in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
        'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        + (f"<dc:creator>{escape(author)}</dc:creator>" if author else "")
        + (f"<cp:lastModifiedBy>{escape(last_by)}</cp:lastModifiedBy>" if last_by else "")
        + "</cp:coreProperties>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", document)
        z.writestr("docProps/core.xml", core)
        if pad_bytes:   # un document cu scanări/imagini: mare pe disc
            z.writestr("word/media/scan.bin", os.urandom(pad_bytes),
                       compress_type=zipfile.ZIP_STORED)


# ---------------------------------------------------------------------- #
# Arhiva de test
# ---------------------------------------------------------------------- #

def build_archive(root: str) -> list:
    """Creează arhiva. Întoarce lista fișierelor de șters DUPĂ indexare."""
    if os.path.exists(root):
        shutil.rmtree(root)
    j = lambda *p: os.path.join(root, *p)  # noqa: E731
    delete_after_index = []

    # --- Demo: 20 de procuri obișnuite (și regula românească "20 de ...")
    for i in range(1, 21):
        write_docx(j("PROCURI", "Vanzare", f"Procura vanzare {i:02d}.docx"),
                   [f"Procura speciala de vanzare nr {i}", "procura"],
                   author="Notar Ionescu", last_by="Maria")

    # --- Cel mai rău caz (toate conțin cuvântul "cazlimita")
    worst = [
        # cale foarte lungă, cu diacritice, cum apare în succesiuni
        (j("SUCCESIUNI", "SUCCESIUNI NOUL COD-01.10.2011",
           "ȘTEFĂNESCU-POPOVICI ALEXANDRA-MĂDĂLINA - ap. 4, boxă, loc de parcare, teren extravilan",
           "Încheiere finală de dezbatere a succesiunii defunctului ȘTEFĂNESCU-POPOVICI "
           "CONSTANTIN-DUMITRU, cu certificat de moștenitor.docx"),
         "Notar Ionescu", "Secretara Maria"),
        # nume identice în foldere de ani diferiți: diferă DOAR la mijloc
        (j("SALARIATI", "Cereri", "2021 - 2030", "2023", "Cerere concediu odihnă ION POPESCU.docx"),
         "Ion", "Ion"),
        (j("SALARIATI", "Cereri", "2021 - 2030", "2024", "Cerere concediu odihnă ION POPESCU.docx"),
         "Ion", "Ion"),
        (j("SALARIATI", "Cereri", "2021 - 2030", "2025", "Cerere concediu odihnă ION POPESCU.docx"),
         "Ion", "Ion"),
        # foarte scurt, în rădăcină
        (j("1.docx"), "", ""),
        # & paranteze, "- copie" (copiat din Explorer)
        (j("ACT DE ALIPIRE", "Act de alipire & dezlipire [FINAL] (2) - copie.docx"),
         "Notar Ionescu", "User"),
        # emoji în nume (Windows îl permite)
        (j("CONVENTII", "Contract ✔ semnat - Popescu.docx"), "Ana", "Ana"),
        # autor cu nume foarte lung, fără "creat de"
        (j("DECLARATII", "Declaratie 208 - Ionescu.docx"), "",
         "Biroul Individual Notarial Ionescu-Constantinescu Maria-Magdalena"),
        # fără autor deloc
        (j("DIVERSE", "Notite telefon.docx"), "", ""),
    ]
    for path, author, last_by in worst:
        write_docx(path, ["Document de test", "cazlimita", "CNP 1800618270870"],
                   author=author, last_by=last_by)

    # document mare (scanări în el): 12 MB
    big = j("ACT DE DEZMEMBRARE", "Dezmembrare teren cu constructii - cu planuri scanate.docx")
    write_docx(big, ["Dezmembrare", "cazlimita"], author="Notar Ionescu",
               last_by="Secretara Maria", pad_bytes=12 * 1024 * 1024)
    # dată în viitor (ceas greșit pe PC-ul care a salvat)
    future = j("PROCURI", "Procura din viitor - ceas gresit.docx")
    write_docx(future, ["cazlimita"], last_by="Maria")
    t = (datetime.now() + timedelta(days=400)).timestamp()
    os.utime(future, (t, t))
    # dată zero (copiat de pe un CD vechi / arhivă stricată)
    epoch = j("ARHIVA", "Contract vechi recuperat de pe CD.docx")
    write_docx(epoch, ["cazlimita"])
    os.utime(epoch, (86400 * 2, 86400 * 2))
    # dispare după indexare (șters/mutat de un coleg)
    gone = j("PROCURI", "Procura stearsa intre timp.docx")
    write_docx(gone, ["cazlimita"], last_by="Maria")
    delete_after_index.append(gone)

    # --- Unu: un singur document
    write_docx(j("DIVERSE", "Document unic.docx"), ["unicatlitate"], last_by="Maria")

    # --- 2.000+: 2.600 de contracte (peste limita de 2.000 de rezultate)
    for i in range(2600):
        write_docx(j("CONTRACTE", f"{2000 + i // 200}", f"Contract {i:04d}.docx"),
                   ["Contract de vanzare cumparare", "contract"], last_by="Maria")
    return delete_after_index


def build_index(root: str, data_dir: str):
    from docsearch.indexing import Indexer
    from docsearch.storage import Database

    to_delete = build_archive(root)
    os.makedirs(data_dir, exist_ok=True)
    db_file = os.path.join(data_dir, "breakui.db")
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(db_file + suffix):
            os.remove(db_file + suffix)
    db = Database(db_file)
    Indexer(db, [".docx"]).run(root)
    for path in to_delete:
        os.remove(path)
    return db


def age_index(db, check_minutes_ago: int, full_days_ago: int) -> None:
    """Starea indexului ca după o pauză: semaforul trebuie să o arate."""
    from docsearch.storage.database import META_LAST_FULL_INDEX, META_LAST_INDEX_COMPLETED
    now = datetime.now()
    db.set_meta(META_LAST_INDEX_COMPLETED, (now - timedelta(minutes=check_minutes_ago)).isoformat())
    db.set_meta(META_LAST_FULL_INDEX, (now - timedelta(days=full_days_ago)).isoformat())


# ---------------------------------------------------------------------- #
# Fereastra cu bara de comutare / capturi
# ---------------------------------------------------------------------- #

def make_window(db, root: str, data_dir: str, font: int = 14, modified_hours: int = 12):
    from docsearch.config import AppConfig
    from docsearch.ui import MainWindow

    cfg = AppConfig.load(os.path.join(data_dir, "breakui_config.json"))
    cfg.last_folder = root
    cfg.extensions = [".docx"]
    cfg.refresh_on_startup = False
    cfg.font_size = font
    cfg.modified_files_hours = modified_hours
    cfg.sort_method = "Sortare după data modificării"
    cfg.sidebar_visible = True
    cfg.sidebar_splitter_sizes = []
    cfg.window_geometry = cfg.window_state = ""
    return MainWindow(cfg, db)


def add_dev_toggle(window) -> None:
    """Bara de comutare, doar în dezvoltare: jos, simplă, evident 'unealtă'."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QButtonGroup, QLabel, QPushButton, QToolBar

    bar = QToolBar("Date de test")
    bar.setMovable(False)
    bar.addWidget(QLabel("  DEV · date de test:  "))
    group = QButtonGroup(bar)
    group.setExclusive(True)
    for label, query in STATES:
        button = QPushButton(label)
        button.setCheckable(True)

        def run(_checked=False, q=query):
            window.search_input.setText(q)
            window.start_search()

        button.clicked.connect(run)
        group.addButton(button)
        bar.addWidget(button)
    window.addToolBar(Qt.BottomToolBarArea, bar)


def wait(ms: int) -> None:
    from PyQt5.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec_()


def shoot(db, root, data_dir, out_dir, name, query, size, font=14, modified_hours=12,
          tooltip_row=None):
    from PyQt5.QtCore import QEvent
    from PyQt5.QtGui import QCursor, QHelpEvent
    from PyQt5.QtWidgets import QApplication

    w = make_window(db, root, data_dir, font=font, modified_hours=modified_hours)
    w.resize(*size)
    w.show()
    wait(300)
    w.search_input.setText(query)
    w.start_search()
    wait(1200)
    w.grab().save(os.path.join(out_dir, f"{name}.png"))
    if tooltip_row is not None and w.results_list.topLevelItemCount() > tooltip_row:
        item = w.results_list.topLevelItem(tooltip_row)
        vp = w.results_list.viewport()
        pos = w.results_list.visualItemRect(item).center()
        gpos = vp.mapToGlobal(pos)
        QCursor.setPos(gpos)
        QApplication.sendEvent(vp, QHelpEvent(QEvent.ToolTip, pos, gpos))
        wait(900)
        QApplication.sendEvent(vp, QHelpEvent(QEvent.ToolTip, pos, gpos))
        wait(200)
        for tip in QApplication.topLevelWidgets():
            if tip.metaObject().className() == "QTipLabel" and tip.isVisible():
                tip.grab().save(os.path.join(out_dir, f"{name}_tooltip.png"))
    w.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    default_root = os.path.join(os.path.expanduser("~"), "DocSearchTest", "M")
    parser.add_argument("--root", default=default_root)
    parser.add_argument("--shots", default="")
    args = parser.parse_args()
    root = os.path.abspath(args.root)
    data_dir = os.path.join(os.path.dirname(root), "Data")

    from PyQt5.QtWidgets import QApplication
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    print(f"Construiesc arhiva de test în {root} …")
    db = build_index(root, data_dir)
    print("Gata: arhivă indexată.")

    if not args.shots:
        age_index(db, check_minutes_ago=47, full_days_ago=34)
        w = make_window(db, root, data_dir)
        add_dev_toggle(w)
        w.resize(1366, 768)
        w.show()
        return app.exec_()

    out = os.path.abspath(args.shots)
    os.makedirs(out, exist_ok=True)
    age_index(db, check_minutes_ago=47, full_days_ago=34)
    shoot(db, root, data_dir, out, "01_worst_1366", "cazlimita", (1366, 768),
          modified_hours=1, tooltip_row=None)
    shoot(db, root, data_dir, out, "02_worst_1024", "cazlimita", (1024, 700), modified_hours=1)
    shoot(db, root, data_dir, out, "03_worst_font24", "cazlimita", (1366, 768), font=24)
    shoot(db, root, data_dir, out, "04_empty", "xyzinexistent", (1366, 768))
    shoot(db, root, data_dir, out, "05_one", "unicatlitate", (1366, 768))
    shoot(db, root, data_dir, out, "06_huge", "contract", (1366, 768))
    shoot(db, root, data_dir, out, "07_demo", "procura", (1366, 768))
    # tooltip pe documentul mare (rândul îl găsim după nume)
    w = make_window(db, root, data_dir)
    w.search_input.setText("cazlimita"); w.start_search(); wait(1200)
    rows = [w._item_path(w.results_list.topLevelItem(i)) for i in range(w.results_list.topLevelItemCount())]
    w.close()
    for idx, path in enumerate(rows):
        if "planuri scanate" in path:
            shoot(db, root, data_dir, out, "08_big_file", "cazlimita", (1366, 768), tooltip_row=idx)
        if "Declaratie 208" in path:
            shoot(db, root, data_dir, out, "09_long_author", "cazlimita", (1366, 768), tooltip_row=idx)
    db.close()
    print(f"Capturi în {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
