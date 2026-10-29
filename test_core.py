"""Smoke test pentru nucleu (storage + indexing + search), fără UI.

Rulează cu:  python test_core.py
Folosește fișiere .txt (cititorul e același mecanism ca pentru .docx/.doc).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from docsearch.indexing import Indexer
from docsearch.search import normalize
from docsearch.storage import Database

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def main() -> int:
    workdir = tempfile.mkdtemp(prefix="docsearch_test_")
    docs = Path(workdir) / "docs"
    docs.mkdir()
    db_path = os.path.join(workdir, "test_index.db")

    try:
        # ------------------------------------------------------------ #
        print("\n1) Normalizare și extragere numere")
        check("diacritice", normalize.normalize_text("Școală Țâfnă-Împărat") == "scoala tafna imparat",
              repr(normalize.normalize_text("Școală Țâfnă-Împărat")))
        check("numere >=5 cifre", normalize.extract_numbers("tel 0740123456 și nr. 123") == {"0740123456"})
        check("CNP valid", normalize.is_cnp("1960101223344"))
        check("CNP invalid", not normalize.is_cnp("3960101223344"))
        check("query numeric", normalize.is_numeric_query("12345") and not normalize.is_numeric_query("abc123"))

        # ------------------------------------------------------------ #
        print("\n2) Indexare inițială")
        (docs / "contract.txt").write_text(
            "Contract de vânzare-cumpărare încheiat între Societatea Școlară\n"
            "CNP cumpărător: 1850512123456\nValoare: 45000 lei", encoding="utf-8")
        (docs / "factura.txt").write_text(
            "Factură fiscală seria PN nr. 99887\nClient: Ion Popescu, Piatra Neamț",
            encoding="utf-8")
        (docs / "gol.txt").write_text("   \n  ", encoding="utf-8")
        (docs / "~$temp.txt").write_text("temporar office", encoding="utf-8")
        sub = docs / "arhiva"
        sub.mkdir()
        (sub / "proces.txt").write_text("Proces verbal ședință consiliu", encoding="utf-8")

        db = Database(db_path)
        indexer = Indexer(db, extensions=[".txt"])
        report = indexer.run(str(docs))
        check("4 fișiere scanate (fără ~$)", report.scanned == 4, f"scanned={report.scanned}")
        check("3 indexate", report.indexed == 3, f"indexed={report.indexed}")
        check("1 necitibil (gol)", report.unreadable == 1, f"unreadable={report.unreadable}")

        # ------------------------------------------------------------ #
        print("\n3) Căutare FTS (cu și fără diacritice)")
        hits = db.search("vânzare-cumpărare")
        check("găsește cu diacritice", len(hits) == 1 and hits[0].path.endswith("contract.txt"))
        hits = db.search("vanzare cumparare")
        check("găsește fără diacritice", len(hits) == 1)
        hits = db.search("Piatra Neamt")
        check("frază cu 2 cuvinte", len(hits) == 1 and hits[0].path.endswith("factura.txt"))
        hits = db.search("factur")   # prefix pe ultimul cuvânt
        check("căutare prefix", len(hits) == 1)
        hits = db.search("inexistent")
        check("fără rezultate false", len(hits) == 0)

        # Frază EXACTĂ: două cuvinte înseamnă „unul lângă altul, în ordinea asta".
        from docsearch.storage.models import DocumentRecord as _DR
        _now = time.time()
        db.upsert_documents([
            _DR(str(docs / "n1.txt"), "Declaratie DASCĂLU VALERIA domiciliata in Roman", _now, 1, "n1"),
            _DR(str(docs / "n2.txt"), "Vasilescu Ion si sotia Popescu Valeria", _now, 1, "n2"),
            _DR(str(docs / "n3.txt"), "VALERIA VASILESCU contract", _now, 1, "n3"),
            _DR(str(docs / "n4.txt"), "parti: VASILESCU, VALERIA-MARIA", _now, 1, "n4"),
        ])
        names = lambda q: sorted(os.path.basename(h.path) for h in db.search(q))
        check("frază exactă: nu amestecă cuvintele din locuri diferite",
              names("VASILESCU VALERIA") == ["n1.txt", "n4.txt"], str(names("VASILESCU VALERIA")))
        check("frază exactă: ordinea contează", names("valeria vasilescu") == ["n3.txt"])
        check("frază exactă: diacriticele ignorate", names("dascălu valeria") == ["n1.txt", "n4.txt"])
        check("frază exactă + prefix pe ultimul cuvânt", names("vasilescu val") == ["n1.txt", "n4.txt"])
        check("un singur cuvânt: oriunde", len(names("vasilescu")) == 4)
        check("count_matches la fel ca search", db.count_matches("VASILESCU VALERIA") == 2)
        db.remove_documents({str(docs / f"n{i}.txt") for i in range(1, 5)})

        print("\n4) Căutare numerică")
        hits = db.search("1850512123456")
        check("CNP complet", len(hits) == 1)
        hits = db.search("99887")
        check("număr factură", len(hits) == 1 and hits[0].path.endswith("factura.txt"))
        hits = db.search("0512123")
        check("fragment din mijlocul numărului", len(hits) == 1)

        # ------------------------------------------------------------ #
        print("\n5) Reindexare incrementală")
        report2 = indexer.run(str(docs))
        check("nimic reindexat a doua oară", report2.indexed == 0 and report2.unchanged == 3,
              f"indexed={report2.indexed} unchanged={report2.unchanged}")

        time.sleep(0.05)
        (docs / "factura.txt").write_text(
            "Factură fiscală seria PN nr. 55555\nClient: Maria Ionescu", encoding="utf-8")
        os.utime(docs / "factura.txt")
        report3 = indexer.run(str(docs))
        check("doar fișierul modificat reindexat", report3.indexed == 1 and report3.unchanged == 2,
              f"indexed={report3.indexed} unchanged={report3.unchanged}")
        check("conținut vechi scos din index", len(db.search("99887")) == 0)
        check("conținut nou în index", len(db.search("55555")) == 1)

        # ------------------------------------------------------------ #
        print("\n6) Fișiere șterse")
        os.remove(docs / "arhiva" / "proces.txt")
        report4 = indexer.run(str(docs))
        check("ștergerea detectată la indexare completă", report4.removed == 1,
              f"removed={report4.removed}")
        check("nu mai apare în căutare", len(db.search("proces verbal")) == 0)

        # ------------------------------------------------------------ #
        print("\n7) Statistici")
        stats = db.get_stats(modified_hours=12)
        check("total documente = 2", stats["Total documente"] == 2, str(stats))
        check("1 CNP unic", stats["CNP-uri unice"] == 1, str(stats))
        check("1 fișier necitibil", stats["Fișiere necitibile"] == 1, str(stats))

        # ------------------------------------------------------------ #
        print("\n8) Concurență: căutări în timpul indexării")
        import threading
        for i in range(60):
            (docs / f"bulk_{i:03d}.txt").write_text(
                f"Document generat numărul {100000 + i} conținut de test", encoding="utf-8")
        errors: list[str] = []

        def search_loop() -> None:
            for _ in range(30):
                try:
                    db.search("document generat")
                    db.get_stats()
                except Exception as exc:
                    errors.append(str(exc))
                time.sleep(0.01)

        t = threading.Thread(target=search_loop)
        t.start()
        report5 = indexer.run(str(docs))
        t.join()
        check("60 fișiere noi indexate sub căutări concurente",
              report5.indexed == 60, f"indexed={report5.indexed}")
        check("zero erori 'database is locked'", not errors, "; ".join(errors[:3]))
        check("toate căutabile", len(db.search("document generat")) == 60)

        db.close()
        check("închidere curată (fără WAL rămas)", not os.path.exists(db_path + "-wal"))

        # ------------------------------------------------------------ #
        print("\n9) Migrare de la baza veche (search20.py)")
        import sqlite3
        old_db_path = os.path.join(workdir, "old_index.db")
        with sqlite3.connect(old_db_path) as old:
            # Schema v1, exact ca în search20.py
            old.execute("""
                CREATE TABLE documents (
                    id INTEGER PRIMARY KEY, path TEXT UNIQUE, content TEXT,
                    normalized_content TEXT, last_modified TIMESTAMP,
                    file_size INTEGER, file_hash TEXT, last_indexed TIMESTAMP,
                    word_count INTEGER)""")
            old.execute("CREATE TABLE search_cache (id INTEGER PRIMARY KEY, query_hash TEXT)")
            old.execute("CREATE TABLE document_tracking (id INTEGER PRIMARY KEY, path TEXT)")
            old.execute("CREATE INDEX idx_modified ON documents(last_modified)")
            old.execute("INSERT INTO documents (path, content) VALUES ('x.doc', 'vechi')")
        old.close()

        db2 = None
        try:
            db2 = Database(old_db_path)       # exact apelul care crăpa
            check("baza veche se deschide fără eroare", True)
            check("schema nouă funcțională după migrare",
                  db2.get_stats()["Total documente"] == 0)
            with sqlite3.connect(old_db_path) as chk:
                version = chk.execute("PRAGMA user_version").fetchone()[0]
                leftover = chk.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE name='search_cache'"
                ).fetchone()[0]
            chk.close()
            check("user_version setat", version == 2, f"version={version}")
            check("tabelele vechi curățate", leftover == 0)

            # Reindexăm peste baza migrată — totul trebuie să meargă normal.
            idx2 = Indexer(db2, extensions=[".txt"])
            rep = idx2.run(str(docs))
            check("indexare funcțională după migrare", rep.indexed > 0, str(rep))
        except Exception as exc:
            check("baza veche se deschide fără eroare", False, repr(exc))
        finally:
            if db2 is not None:
                db2.close()

        # ------------------------------------------------------------ #
        print("\n10) Remapare căi (mutarea arhivei pe alt PC)")
        # Simulăm: documente indexate sub docs/, apoi "mutate" în alt folder.
        new_docs = Path(workdir) / "mutat" / "arhiva_noua"
        shutil.copytree(docs, new_docs)

        db3 = Database(os.path.join(workdir, "remap_test.db"))
        idx3 = Indexer(db3, extensions=[".txt"])
        rep_before = idx3.run(str(docs))
        check("index inițial populat", rep_before.indexed > 0)
        hits_before = db3.search("document generat")
        check("căutarea merge înainte de remap", len(hits_before) == 60)

        count = db3.count_paths_under(str(docs))
        check("count_paths_under găsește documentele", count == rep_before.indexed,
              f"count={count} vs indexed={rep_before.indexed}")

        changed = db3.remap_paths(str(docs), str(new_docs))
        check("toate căile rescrise", changed == count, f"changed={changed}")
        check("nimic sub calea veche", db3.count_paths_under(str(docs)) == 0)

        hits_after = db3.search("document generat")
        check("căutarea merge după remap (fișierele există la noua cale)",
              len(hits_after) == 60, f"găsite={len(hits_after)}")
        check("rezultatele arată spre noua cale",
              all(str(new_docs) in h.path for h in hits_after))

        # Reindexare după remap: nimic de recitit (mtime+size păstrate de copy).
        rep_after = idx3.run(str(new_docs))
        check("reindexare după remap: 0 citite, totul neschimbat",
              rep_after.indexed == 0 and rep_after.unchanged == len(hits_after) + 2,
              f"indexed={rep_after.indexed} unchanged={rep_after.unchanged}")
        db3.close()

        # ------------------------------------------------------------ #
        print("\n11) Fișiere deschise în Word: .docx/.txt se indexează (ultima versiune salvată)")
        from docsearch.indexing import readers as rd

        lockdir = Path(workdir) / "lockdocs"
        lockdir.mkdir()
        (lockdir / "deschis.txt").write_text("lucrez chiar acum in el", encoding="utf-8")
        (lockdir / "inchis.txt").write_text("document linistit", encoding="utf-8")
        # Lacătul Word: primele 2 caractere înlocuite cu ~$
        (lockdir / "~$schis.txt").write_text("", encoding="utf-8")

        check("detectare lacăt (nume lung)", rd.is_open_in_word(str(lockdir / "deschis.txt")))
        check("fără lacăt => liber", not rd.is_open_in_word(str(lockdir / "inchis.txt")))

        db4 = Database(os.path.join(workdir, "lock_test.db"))
        idx4 = Indexer(db4, extensions=[".txt"])
        notes4: list[str] = []
        rep_lock = idx4.run(str(lockdir), on_note=notes4.append)
        check("documentul deschis în Word e TOTUȘI indexat (versiunea salvată)",
              rep_lock.indexed == 2 and rep_lock.locked == 0,
              f"indexed={rep_lock.indexed} locked={rep_lock.locked}")
        check("conținutul lui e căutabil imediat", len(db4.search("lucrez chiar acum")) == 1)
        check("lacătul ~$ nu e indexat ca document", db4.count_documents() == 2)

        # Un fișier chiar blocat (PermissionError) e amânat, nu marcat necitibil,
        # și apare cu numele în jurnal.
        real_read = rd.read_bytes_based_timed
        def blocked(path):
            if path.endswith("deschis.txt"):
                raise rd.TransientReadError("blocat de alt proces")
            return real_read(path)
        rd.read_bytes_based_timed = blocked
        (lockdir / "deschis.txt").write_text("lucrez chiar acum in el v2", encoding="utf-8")
        try:
            rep_blk = idx4.run(str(lockdir), on_note=notes4.append)
        finally:
            rd.read_bytes_based_timed = real_read
        check("fișier blocat => amânat (locked=1), nu necitibil",
              rep_blk.locked == 1 and db4.get_stats()["Fișiere necitibile"] == 0,
              f"locked={rep_blk.locked}")
        check("jurnalul spune CARE fișier a fost sărit",
              any("Sărite" in n and "deschis.txt" in n for n in notes4), str(notes4[-2:]))
        rep_after = idx4.run(str(lockdir))
        check("data viitoare e prins", rep_after.indexed == 1 and len(db4.search("v2")) == 1)
        db4.close()

        # ------------------------------------------------------------ #
        print("\n12) Indexare rapidă dinamică (semn + fereastră calculată)")
        from datetime import datetime, timedelta
        from docsearch.indexing.indexer import dynamic_window_days, DYNAMIC_MARGIN_DAYS
        from docsearch.storage.database import META_LAST_INDEX_COMPLETED

        dyn_dir = Path(workdir) / "dyndocs"
        dyn_dir.mkdir()
        (dyn_dir / "a.txt").write_text("primul document dinamic", encoding="utf-8")

        db5 = Database(os.path.join(workdir, "dyn_test.db"))
        check("fără semn => fallback", dynamic_window_days(db5, 365) == 365)

        idx5 = Indexer(db5, extensions=[".txt"])
        rep = idx5.run(str(dyn_dir))
        check("semnul apare după o indexare reușită",
              db5.get_last_index_completed() is not None)

        # Simulăm: ultima indexare acum 10 zile.
        ten_days_ago = (datetime.now() - timedelta(days=10)).isoformat()
        db5.set_meta(META_LAST_INDEX_COMPLETED, ten_days_ago)
        window = dynamic_window_days(db5, 365)
        check("fereastră = zile scurse + marjă",
              window == 10 + 1 + DYNAMIC_MARGIN_DAYS, f"window={window}")

        # Simulăm: indexare chiar adineauri -> fereastră minimă, dar >= 1.
        db5.set_meta(META_LAST_INDEX_COMPLETED, datetime.now().isoformat())
        window = dynamic_window_days(db5, 365)
        check("indexare recentă => fereastră mică", 1 <= window <= 1 + 1 + DYNAMIC_MARGIN_DAYS,
              f"window={window}")

        # O rulare ANULATĂ nu are voie să avanseze semnul.
        db5.set_meta(META_LAST_INDEX_COMPLETED, ten_days_ago)
        (dyn_dir / "b.txt").write_text("al doilea document", encoding="utf-8")
        # anulăm din callback-ul de progres, ca în utilizarea reală (buton Oprește)
        idx5.run(str(dyn_dir), on_progress=lambda *_: idx5.cancel())
        still_old = db5.get_meta(META_LAST_INDEX_COMPLETED)
        check("rularea anulată nu avansează semnul", still_old == ten_days_ago,
              f"semn={still_old}")

        # Semn corupt -> fallback, fără crash.
        db5.set_meta(META_LAST_INDEX_COMPLETED, "nu-e-data")
        check("semn corupt => fallback", dynamic_window_days(db5, 365) == 365)
        db5.close()

        # ------------------------------------------------------------ #
        print("\n13) Căutarea NU mai șterge/ascunde căile lipsă + analiza bazei")
        chk_dir = Path(workdir) / "chkdocs"
        chk_dir.mkdir()
        (chk_dir / "unu.txt").write_text("cuvant special verificare", encoding="utf-8")
        (chk_dir / "doi.txt").write_text("alt cuvant special verificare", encoding="utf-8")

        db6 = Database(os.path.join(workdir, "chk_test.db"))
        idx6 = Indexer(db6, extensions=[".txt"])
        idx6.run(str(chk_dir))

        # Simulăm "arhiva mutată": fișierul dispare de la calea indexată.
        os.remove(chk_dir / "doi.txt")
        hits = db6.search("special verificare")
        check("ambele potriviri sunt întoarse", len(hits) == 2, f"len={len(hits)}")
        missing_hits = [h for h in hits if not h.exists]
        check("cel lipsă e marcat exists=False, nu ascuns", len(missing_hits) == 1)

        # Căutarea NU trebuie să fi șters nimic din index.
        time.sleep(0.2)   # lăsăm orice scriere asincronă ipotetică să curgă
        check("indexul rămâne intact după căutare", db6.count_documents() == 2,
              f"count={db6.count_documents()}")

        analysis = db6.analyze_paths()
        check("analiza vede grupul de căi", len(analysis) == 1 and analysis[0]["total"] == 2,
              str(analysis))
        check("analiza raportează lipsurile", analysis[0]["found"] < analysis[0]["sample"],
              str(analysis))
        db6.close()

        # ------------------------------------------------------------ #
        print("\n14) Scanare cu progres și anulare")
        from docsearch.indexing import scanner as sc

        scan_dir = Path(workdir) / "scandocs"
        for i in range(5):
            sub = scan_dir / f"sub{i}"
            sub.mkdir(parents=True)
            (sub / f"f{i}.txt").write_text(f"continut {i}", encoding="utf-8")

        visited: list[str] = []
        found = list(sc.scan_folder(str(scan_dir), [".txt"], on_dir=visited.append))
        check("on_dir apelat pentru fiecare folder", len(visited) == 6,  # rădăcina + 5
              f"visited={len(visited)}")
        check("toate fișierele găsite", len(found) == 5)

        # Anulare în timpul scanării: oprire imediată, fără să termine discul.
        stop_after = {"n": 0}

        def cancel_soon() -> bool:
            stop_after["n"] += 1
            return stop_after["n"] > 2

        partial = list(sc.scan_folder(str(scan_dir), [".txt"], should_cancel=cancel_soon))
        check("anularea oprește scanarea devreme", len(partial) < 5,
              f"partial={len(partial)}")

        # Anulare prin Indexer în faza de scanare: raport cu eroare, fără semn avansat.
        db7 = Database(os.path.join(workdir, "scan_test.db"))
        idx7 = Indexer(db7, extensions=[".txt"])
        idx7._cancelled = False
        scan_calls = {"n": 0}

        def cancel_on_scan(found_count: int, current_dir: str) -> None:
            scan_calls["n"] += 1
            idx7.cancel()

        rep_scan = idx7.run(str(scan_dir), on_scan=cancel_on_scan)
        check("anulare în scanare => raport cu notă, nimic indexat",
              rep_scan.indexed == 0 and any("scan" in e for e in rep_scan.errors),
              f"errors={rep_scan.errors}")
        check("semnul NU avansează la anulare în scanare",
              db7.get_last_index_completed() is None)
        db7.close()

        # ------------------------------------------------------------ #
        print("\n15) Foldere excluse de la scanare")
        excl_dir = Path(workdir) / "excldocs"
        (excl_dir / "ACTE").mkdir(parents=True)
        (excl_dir / "Filme").mkdir()
        (excl_dir / "Backup" / "adanc").mkdir(parents=True)
        (excl_dir / "ACTE" / "act.txt").write_text("act util", encoding="utf-8")
        (excl_dir / "Filme" / "nu.txt").write_text("nu ma indexa", encoding="utf-8")
        (excl_dir / "Backup" / "adanc" / "vechi.txt").write_text("backup vechi", encoding="utf-8")

        # excludere după NUME
        found = list(sc.scan_folder(str(excl_dir), [".txt"], excluded=["Filme", "Backup"]))
        check("excluderea după nume sare folderele", len(found) == 1
              and found[0].path.endswith("act.txt"), f"found={[f.path for f in found]}")

        # excludere după CALE completă
        found = list(sc.scan_folder(str(excl_dir), [".txt"],
                                    excluded=[str(excl_dir / "Filme")]))
        check("excluderea după cale sare doar acel subarbore", len(found) == 2,
              f"found={len(found)}")

        # fără excluderi -> tot
        found = list(sc.scan_folder(str(excl_dir), [".txt"]))
        check("fără excluderi se vede tot", len(found) == 3)

        # folderele de sistem (#recycle, @eaDir...) sunt excluse MEREU
        (excl_dir / "#recycle" / "sters").mkdir(parents=True)
        (excl_dir / "#recycle" / "sters" / "gunoi.txt").write_text("sters", encoding="utf-8")
        (excl_dir / "@eaDir").mkdir()
        (excl_dir / "@eaDir" / "thumb.txt").write_text("thumb", encoding="utf-8")
        found = list(sc.scan_folder(str(excl_dir), [".txt"]))
        check("#recycle și @eaDir excluse automat", len(found) == 3,
              f"found={[f.path for f in found]}")

        # ------------------------------------------------------------ #
        print("\n16) Scanare paralelă + sărirea folderelor neschimbate")
        par_dir = Path(workdir) / "pardocs"
        for i in range(4):
            sub = par_dir / f"dosar{i}"
            sub.mkdir(parents=True)
            (sub / f"act{i}.txt").write_text(f"act numarul {i}", encoding="utf-8")

        # paralel == serial (aceleași fișiere găsite)
        serial = {e.path for e in sc.scan_folder(str(par_dir), [".txt"])}
        snap1: dict = {}
        stats1: dict = {}
        parallel = {e.path for e in sc.scan_folder_parallel(
            str(par_dir), [".txt"], snapshot_out=snap1, stats_out=stats1)}
        check("paralel găsește exact ce găsește și serialul", parallel == serial)
        check("prima scanare listează tot, nu sare nimic",
              stats1["skipped"] == 0 and stats1["listed"] == 5, str(stats1))
        check("instantaneul reține toate folderele", len(snap1) == 5)

        # A doua scanare cu instantaneu: nimic schimbat => totul sărit (doar rădăcina
        # poate fi listată dacă sistemul i-a atins data; important: subfolderele sar).
        snap2: dict = {}
        stats2: dict = {}
        found2 = sc.scan_folder_parallel(
            str(par_dir), [".txt"], dir_snapshot=snap1, snapshot_out=snap2,
            stats_out=stats2)
        check("folderele neschimbate sunt sărite", stats2["skipped"] >= 4, str(stats2))
        check("niciun fișier raportat din foldere sărite", len(found2) == 0)
        check("instantaneul se păstrează complet", len(snap2) == 5)

        # Fișier NOU într-un dosar -> data folderului se schimbă -> e prins.
        time.sleep(0.05)
        (par_dir / "dosar2" / "act_nou.txt").write_text("act proaspat", encoding="utf-8")
        snap3: dict = {}
        stats3: dict = {}
        found3 = sc.scan_folder_parallel(
            str(par_dir), [".txt"], dir_snapshot=snap2, snapshot_out=snap3,
            stats_out=stats3)
        check("fișierul nou e prins (folderul lui s-a schimbat)",
              any(e.path.endswith("act_nou.txt") for e in found3),
              f"found={[e.path for e in found3]}")
        check("celelalte dosare rămân sărite", stats3["skipped"] >= 3, str(stats3))

        # Compromisul asumat, documentat: modificare ÎN LOC fără schimbarea
        # datei folderului -> nevăzută de scanarea cu instantaneu...
        target = par_dir / "dosar1" / "act1.txt"
        dir1 = par_dir / "dosar1"
        saved_times = (os.stat(dir1).st_atime, os.stat(dir1).st_mtime)
        target.write_text("act numarul 1 MODIFICAT", encoding="utf-8")
        os.utime(dir1, saved_times)   # simulăm: data folderului nu s-a schimbat
        found4 = sc.scan_folder_parallel(
            str(par_dir), [".txt"], dir_snapshot=snap3)
        check("compromis: editarea fără schimbarea datei folderului e sărită",
              not any(e.path.endswith("act1.txt") for e in found4))
        # ...dar scanarea FĂRĂ instantaneu (= indexarea completă) o vede.
        found5 = sc.scan_folder_parallel(str(par_dir), [".txt"])
        check("indexarea completă o prinde (plasa de siguranță)",
              any(e.path.endswith("act1.txt") for e in found5))

        # Instantaneul supraviețuiește prin baza de date.
        db8 = Database(os.path.join(workdir, "snap_test.db"))
        db8.save_dir_state(snap3)
        loaded = db8.load_dir_state()
        check("instantaneul se salvează și se reîncarcă identic",
              loaded.keys() == snap3.keys()
              and all(abs(loaded[k][0] - snap3[k][0]) < 1e-6
                      and sorted(loaded[k][1]) == sorted(snap3[k][1]) for k in snap3))
        db8.close()

        # Foldere ARHIVATE (ani vechi): ocolite de indexarea rapidă, nu de cea completă.
        from datetime import datetime as _dt
        this_year = _dt.now().year
        check("archive_year_cutoff(2) = anul trecut",
              sc.archive_year_cutoff(2) == this_year - 1)
        check("archive_year_cutoff(1) = anul curent",
              sc.archive_year_cutoff(1) == this_year)
        check("2015 e folder de an mai vechi", sc.is_year_folder_before("2015", 2025))
        check("2025 nu e sub prag", not sc.is_year_folder_before("2025", 2025))
        check("'Ajutor inmormantare 2006' e arhivă",
              sc.is_year_folder_before("Ajutor inmormantare 2006", 2025))
        check("'Succesiuni 2015-2016' e arhivă", sc.is_year_folder_before("Succesiuni 2015-2016", 2025))
        check("'Contracte 2024-2026' rămâne activ",
              not sc.is_year_folder_before("Contracte 2024-2026", 2025))
        check("fără an în nume => nu e arhivă", not sc.is_year_folder_before("Declaratii", 2025))
        check("'20150101' nu e an", not sc.is_year_folder_before("20150101", 2025))
        check("'Nr. 12015' nu e an", not sc.is_year_folder_before("Nr. 12015", 2025))
        check("'Bloc 1234' nu e an plauzibil", not sc.is_year_folder_before("Bloc 1234", 2025))
        arch_root = Path(workdir) / "arch_scan"
        for year in ("2015", "2016", str(this_year - 1), str(this_year)):
            d = arch_root / year / "client"
            d.mkdir(parents=True)
            (d / f"act_{year}.txt").write_text("x", encoding="utf-8")
        d = arch_root / "Ajutor inmormantare 2006"
        d.mkdir()
        (d / "act_ai2006.txt").write_text("x", encoding="utf-8")
        (arch_root / "Vechi").mkdir()
        (arch_root / "Vechi" / "act_v.txt").write_text("x", encoding="utf-8")
        (arch_root / "Curent").mkdir()
        (arch_root / "Curent" / "act_c.txt").write_text("x", encoding="utf-8")
        st_a: dict = {}
        quick = {os.path.basename(e.path) for e in sc.scan_folder_parallel(
            str(arch_root), [".txt"], archived=["Vechi"],
            archive_year_cutoff=sc.archive_year_cutoff(2), stats_out=st_a)}
        check("rapidă: anii vechi și 'Vechi' ocoliți",
              quick == {f"act_{this_year - 1}.txt", f"act_{this_year}.txt", "act_c.txt"}, str(quick))
        check("rapidă: numără subarborii arhivați", st_a.get("archived") == 4, str(st_a))
        full = {os.path.basename(e.path) for e in sc.scan_folder_parallel(str(arch_root), [".txt"])}
        check("completă: vede tot", len(full) == 7, str(full))
        # Și prin snapshot (folder părinte neschimbat): subfolderele arhivate tot nu intră.
        snap_a: dict = {}
        sc.scan_folder_parallel(str(arch_root), [".txt"], snapshot_out=snap_a)
        time.sleep(0.05)
        (arch_root / "2015" / "client" / "nou_2015.txt").write_text("x", encoding="utf-8")
        (arch_root / "Curent" / "nou_c.txt").write_text("x", encoding="utf-8")
        quick2 = {os.path.basename(e.path) for e in sc.scan_folder_parallel(
            str(arch_root), [".txt"], dir_snapshot=snap_a, archived=["Vechi"],
            archive_year_cutoff=sc.archive_year_cutoff(2))}
        check("rapidă cu instantaneu: vede folderul activ schimbat, nu arhiva",
              quick2 == {"act_c.txt", "nou_c.txt"}, str(quick2))
        # Indexer: parametrii de arhivă sunt ignorați la indexarea completă.
        db9 = Database(os.path.join(workdir, "arch_idx.db"))
        from docsearch.indexing.indexer import Indexer as _Ix
        rep_full = _Ix(db9, [".txt"]).run(str(arch_root), None, archived_folders=["Vechi"],
                                          archive_year_cutoff=sc.archive_year_cutoff(2))
        check("indexer completă: arhiva se indexează", rep_full.scanned == 9 and rep_full.dirs_archived == 0,
              f"{rep_full.scanned} {rep_full.dirs_archived}")
        rep_quick = _Ix(db9, [".txt"]).run(str(arch_root), 3650, archived_folders=["Vechi"],
                                           archive_year_cutoff=sc.archive_year_cutoff(2),
                                           use_dir_dates=False)
        check("indexer rapidă: arhiva ocolită, nimic eliminat",
              rep_quick.scanned == 4 and rep_quick.dirs_archived == 4 and rep_quick.removed == 0,
              f"{rep_quick.scanned} {rep_quick.dirs_archived} {rep_quick.removed}")
        check("documentele din arhivă rămân în index", db9.count_documents() == 9, str(db9.count_documents()))
        db9.close()

        # ------------------------------------------------------------ #
        print("\n17) Istoric rulări + diagnostic (semafor)")
        from docsearch.indexing.health import compute_health, humanize_ago
        from docsearch.storage.database import META_LAST_FULL_INDEX, META_REMAPPED_AT

        h_dir = Path(workdir) / "healthdocs"
        h_dir.mkdir()
        (h_dir / "a.txt").write_text("document de sanatate", encoding="utf-8")
        db9 = Database(os.path.join(workdir, "health_test.db"))

        h = compute_health(db9, str(h_dir))
        check("index gol => roșu", h.level == "bad", f"{h.level} {h.reasons}")

        idx9 = Indexer(db9, extensions=[".txt"])
        idx9.run(str(h_dir))                       # completă (newer_than_days=None)
        h = compute_health(db9, str(h_dir))
        check("după indexare completă => verde", h.level == "ok", f"{h.level} {h.reasons}")
        check("ultima completă înregistrată", h.last_full is not None)

        runs = db9.get_index_runs()
        check("rularea apare în istoric", len(runs) == 1
              and runs[0]["kind"] == "full" and runs[0]["status"] == "ok", str(runs))

        idx9.run(str(h_dir), newer_than_days=3)    # rapidă
        runs = db9.get_index_runs()
        check("rapida apare în istoric, cea mai nouă prima",
              runs[0]["kind"] == "quick", str(runs[:2]))
        check("rapida NU mută data ultimei complete",
              db9.get_last_full_index() == h.last_full)

        # completa veche de 40 de zile => galben
        old = (datetime.now() - timedelta(days=40)).isoformat()
        db9.set_meta(META_LAST_FULL_INDEX, old)
        h = compute_health(db9, str(h_dir), full_recommend_days=30)
        check("completă veche => galben cu motiv",
              h.level == "warn" and any("40 de zile" in r for r in h.reasons), str(h.reasons))

        # pauză lungă fără verificare => galben
        db9.set_meta(META_LAST_FULL_INDEX, datetime.now().isoformat())
        db9.set_meta(META_LAST_INDEX_COMPLETED, (datetime.now() - timedelta(days=12)).isoformat())
        h = compute_health(db9, str(h_dir), pause_warn_days=7)
        check("pauză lungă => galben", h.level == "warn"
              and any("Pauză" in r for r in h.reasons), str(h.reasons))

        # perioadă neacoperită (mod fix, fereastră mai mică decât pauza)
        h = compute_health(db9, str(h_dir), dynamic_quick=False, quick_window_days=5)
        check("perioadă neacoperită semnalată",
              any("neacoperită" in r for r in h.reasons), str(h.reasons))

        # reconectare după ultima completă => galben
        db9.set_meta(META_LAST_INDEX_COMPLETED, datetime.now().isoformat())
        db9.set_meta(META_REMAPPED_AT, (datetime.now() + timedelta(seconds=1)).isoformat())
        h = compute_health(db9, str(h_dir))
        check("reconectare după completă => galben",
              any("reconectat" in r for r in h.reasons), str(h.reasons))

        # folder inaccesibil => roșu
        h = compute_health(db9, str(h_dir / "nu_exista"))
        check("folder inaccesibil => roșu", h.level == "bad", str(h.reasons))

        check("humanize: minute (cu «de» de la 20)",
              humanize_ago(datetime.now() - timedelta(minutes=47)) == "acum 47 de minute")
        check("humanize: zile", humanize_ago(datetime.now() - timedelta(days=3)) == "acum 3 zile")
        check("humanize: niciodată", humanize_ago(None) == "niciodată")
        db9.close()

        # ------------------------------------------------------------ #
        print("\n18) Viteză: toleranță la dată, Word reutilizat, diagnostic")
        import docsearch.indexing.indexer as indexer_mod
        from docsearch.indexing import readers as rdr

        sp_dir = Path(workdir) / "speeddocs"
        sp_dir.mkdir()
        for i in range(30):
            (sp_dir / f"nou_{i}.txt").write_text(f"document rapid {i}", encoding="utf-8")
        for i in range(12):
            (sp_dir / f"vechi_{i}.doc").write_bytes(b"binar doc")

        # Word "fals": numără pornirile și citirile (pe Linux nu avem COM).
        class FakeWord:
            instances = 0

            def __init__(self, restart_every: int = 200):
                FakeWord.instances += 1
                self.starts = 1
                self.reads = 0

            def read(self, path: str) -> str:
                self.reads += 1
                return f"continut doc {os.path.basename(path)}"

            def close(self) -> None:
                pass

        real_session = rdr.WordSession
        rdr.WordSession = FakeWord
        notes: list[str] = []
        try:
            db10 = Database(os.path.join(workdir, "speed_test.db"))
            idx10 = Indexer(db10, extensions=[".txt", ".doc"])
            rep10 = idx10.run(str(sp_dir), on_note=notes.append)
        finally:
            rdr.WordSession = real_session

        check("toate cele 42 citite (30 .txt în paralel + 12 .doc)",
              rep10.indexed == 42, f"indexed={rep10.indexed} unreadable={rep10.unreadable}")
        check("UN SINGUR Word pentru toate .doc-urile", FakeWord.instances == 1,
              f"instances={FakeWord.instances}")
        check("conținutul .doc e căutabil", len(db10.search("continut doc")) == 12)
        check("conținutul citit în paralel e căutabil", len(db10.search("document rapid")) == 30)
        check("motive de citire: 42 noi, 0 modificate",
              rep10.read_new == 42 and rep10.read_changed == 0,
              f"new={rep10.read_new} changed={rep10.read_changed}")
        check("jurnalul primește triere + raport final",
              any(n.startswith("Triere:") for n in notes)
              and any(n.startswith("Raport:") for n in notes), str(notes))
        check("raportul arată timpul mediu pe tip",
              any(".doc" in n and "s/fișier" in n for n in notes), str(notes[-1:]))

        # Toleranța la dată: diferență de 1 s (alt disc/NAS) => NU se recitește.
        target = sp_dir / "nou_0.txt"
        st = os.stat(target)
        os.utime(target, (st.st_atime, st.st_mtime + 1.0))
        rep11 = idx10.run(str(sp_dir))
        check("diferență de 1 s la dată => neschimbat, fără recitire",
              rep11.indexed == 0 and rep11.read_changed == 0,
              f"indexed={rep11.indexed} changed={rep11.read_changed}")

        # Dar o modificare reală (mărime diferită) e prinsă.
        target.write_text("document rapid 0 MODIFICAT serios", encoding="utf-8")
        rdr.WordSession = FakeWord
        try:
            rep12 = idx10.run(str(sp_dir))
        finally:
            rdr.WordSession = real_session
        check("modificarea reală e recitită", rep12.indexed == 1 and rep12.read_changed == 1,
              f"indexed={rep12.indexed} changed={rep12.read_changed}")
        check("conținutul nou e în index", len(db10.search("modificat serios")) == 1)

        # Fără Word (Linux), .doc-urile devin necitibile curat, fără crash.
        sp2 = Path(workdir) / "speeddocs2"
        sp2.mkdir()
        (sp2 / "x.doc").write_bytes(b"binar")
        rep13 = Indexer(db10, extensions=[".doc"]).run(str(sp2))
        check("fără Word: .doc marcat necitibil, fără crash",
              rep13.unreadable == 1 and rep13.indexed == 0, str(rep13))
        db10.close()

        # ------------------------------------------------------------ #
        print("\n19) Extractor .docx direct + diagnostic pe faze + eliminate pe foldere")
        try:
            import docx as _pydocx
            dx_dir = Path(workdir) / "docxdocs"
            (dx_dir / "Vechi").mkdir(parents=True)
            (dx_dir / "Noi").mkdir()
            d = _pydocx.Document()
            d.add_paragraph("Contract de vânzare-cumpărare CNP 1800618270870")
            t = d.add_table(rows=2, cols=2)
            t.cell(1, 1).text = "suprafața din tabel 500 mp"
            d.sections[0].header.paragraphs[0].text = "Antet notarial SPN Exemplu"
            d.save(str(dx_dir / "Noi" / "act.docx"))
            d.save(str(dx_dir / "Vechi" / "act_vechi.docx"))

            text, _h, io_s, parse_s, size = rdr.read_bytes_based_timed(str(dx_dir / "Noi" / "act.docx"))
            check("extractorul prinde paragraful și diacriticele", "vânzare-cumpărare" in text)
            check("extractorul prinde textul din tabel", "suprafața din tabel" in text)
            check("extractorul prinde ANTETUL (python-docx îl rata)", "Antet notarial" in text)
            check("timpi pe faze măsurați", io_s >= 0 and parse_s >= 0 and size > 0)

            db11 = Database(os.path.join(workdir, "docx_test.db"))
            notes11: list[str] = []
            idx11 = Indexer(db11, extensions=[".docx"])
            idx11.run(str(dx_dir), on_note=notes11.append)
            check("raportul conține fazele (disc / extragere)",
                  any("Pe faze" in n and "disc" in n and "extragere" in n for n in notes11),
                  str(notes11))
            check("raportul listează cele mai lente fișiere",
                  any("Cele mai lente" in n for n in notes11))

            # Excludem "Vechi" -> la indexarea completă docs de acolo ies din index,
            # iar jurnalul spune din ce folder.
            notes12: list[str] = []
            idx11.run(str(dx_dir), excluded_folders=["Vechi"], on_note=notes12.append)
            check("eliminarea e defalcată pe foldere în jurnal",
                  any(n.startswith("Eliminate din index") and "Vechi 1" in n for n in notes12),
                  str([n for n in notes12 if n.startswith("Eliminate")]))
            db11.close()
        except ImportError:
            print("  (python-docx lipsește — sar testele .docx)")

        # ------------------------------------------------------------ #
        print("\n20) Căutare în timpul indexării + CNP prin index + disc de rețea")
        import threading as _th
        from docsearch import paths as _paths

        par_root = Path(workdir) / "paralel"
        (par_root / "vechi").mkdir(parents=True)
        (par_root / "vechi" / "act.txt").write_text(
            "act vechi cu CNP 1850512123456 si ceva text", encoding="utf-8")
        (par_root / "vechi" / "lipit.txt").write_text(
            "numar lipit 918505121234560 de alte cifre", encoding="utf-8")
        db12 = Database(os.path.join(workdir, "parallel_test.db"))
        idx12 = Indexer(db12, extensions=[".txt"])
        idx12.run(str(par_root))   # indexul "vechi", deja existent

        # CNP exact prin index + fallback pentru CNP lipit de alte cifre
        check("CNP exact găsit prin index", any(
            h.path.endswith("act.txt") for h in db12.search("1850512123456")))
        check("CNP lipit de alte cifre găsit prin căutarea de rezervă", any(
            h.path.endswith("lipit.txt") for h in db12.search("1850512123456")))

        # Acum o indexare LENTĂ (500 fișiere noi, citire artificial lentă) pe fundal
        for i in range(500):
            (par_root / f"nou_{i}.txt").write_text(f"document nou {i}", encoding="utf-8")
        real_reader = rdr.read_bytes_based_timed

        def slow_reader(path):
            time.sleep(0.01)
            return real_reader(path)

        rdr.read_bytes_based_timed = slow_reader
        latencies: list[float] = []
        found_during: list[int] = []
        try:
            t = _th.Thread(target=idx12.run, args=(str(par_root),))
            t.start()
            time.sleep(0.2)
            while t.is_alive():
                t0 = time.perf_counter()
                hits = db12.search("1850512123456")
                latencies.append(time.perf_counter() - t0)
                found_during.append(len(hits))
                time.sleep(0.05)
            t.join()
        finally:
            rdr.read_bytes_based_timed = real_reader

        check("au rulat căutări în timpul indexării", len(latencies) >= 3, f"n={len(latencies)}")
        check("căutările găsesc documentele vechi în timpul indexării",
              found_during and all(n >= 2 for n in found_during), str(found_during[:5]))
        check("căutările rămân rapide în timpul indexării (< 0,5 s)",
              latencies and max(latencies) < 0.5, f"max={max(latencies or [0]):.3f}s")
        check("după indexare, documentele noi sunt și ele căutabile",
              len(db12.search("document nou")) == 500)
        db12.close()

        check("cale UNC recunoscută ca rețea", _paths.is_network_path(r"\\NAS\documente\Data"))
        check("cale locală nu e rețea", not _paths.is_network_path(str(workdir)))
        db13 = Database(os.path.join(workdir, "net_test.db"))
        Indexer(db13, extensions=[".txt"]).run(str(par_root / "vechi"))
        h = compute_health(db13, str(par_root / "vechi"), db_on_network=True, db_location="N:\\Data")
        check("bază pe rețea => semafor roșu cu explicație",
              h.level == "bad" and any("disc de rețea" in r for r in h.reasons), str(h.reasons))
        db13.close()

        # ------------------------------------------------------------ #
        print("\n21) Cine a salvat ultima dată (proprietățile documentului)")
        from docsearch.indexing.doc_meta import read_doc_authors
        meta_dir = Path(workdir) / "metadocs"
        meta_dir.mkdir()
        try:
            import docx as _pydocx2
            d = _pydocx2.Document()
            d.add_paragraph("act")
            d.core_properties.author = "Notar Ionescu"
            d.core_properties.last_modified_by = "Secretara Maria"
            d.save(str(meta_dir / "act.docx"))
            m = read_doc_authors(str(meta_dir / "act.docx"))
            check("docx: «salvat ultima dată de» citit",
                  m.get("last_modified_by") == "Secretara Maria", str(m))
            check("docx: «creat de» citit", m.get("creator") == "Notar Ionescu", str(m))
        except ImportError:
            print("  (python-docx lipsește — sar testul .docx)")
        (meta_dir / "stricat.docx").write_bytes(b"nu e zip")
        (meta_dir / "vechi.doc").write_bytes(b"nu e OLE")
        check("docx stricat => fără eroare, dicționar gol",
              read_doc_authors(str(meta_dir / "stricat.docx")) == {})
        check(".doc ne-OLE => fără eroare, dicționar gol",
              read_doc_authors(str(meta_dir / "vechi.doc")) == {})
        check("fișier inexistent => fără eroare",
              read_doc_authors(str(meta_dir / "nu_exista.docx")) == {})

        # ------------------------------------------------------------ #
        print("\n22) break-ui: plural românesc, numere, total la limită, recente")
        from docsearch.ro_format import fmt_duration, fmt_size, plural as ro_plural
        from docsearch.ui.workers import format_duration as ui_duration
        check("1 rezultat (singular)", ro_plural(1, "rezultat", "rezultate") == "1 rezultat")
        check("15 rezultate (fără «de»)", ro_plural(15, "rezultat", "rezultate") == "15 rezultate")
        check("20 de rezultate", ro_plural(20, "rezultat", "rezultate") == "20 de rezultate")
        check("101 rezultate (fără «de»)", ro_plural(101, "rezultat", "rezultate") == "101 rezultate")
        check("2.000 de rezultate", ro_plural(2000, "rezultat", "rezultate") == "2.000 de rezultate")
        check("durata din captura reală, corectată",
              fmt_duration(4120) == "1 oră, 8 minute și 40 de secunde", fmt_duration(4120))
        check("interfața folosește aceeași durată", ui_duration(4120) == fmt_duration(4120))
        check("1 milisecundă", fmt_duration(0.001) == "1 milisecundă")
        check("mărime cu virgulă zecimală", fmt_size(1331) == "1,3 KB" and
              fmt_size(12884505) == "12,3 MB", f"{fmt_size(1331)} / {fmt_size(12884505)}")

        cap_dir = Path(workdir) / "capdocs"
        cap_dir.mkdir()
        for i in range(30):
            (cap_dir / f"c{i}.txt").write_text(f"contract numarul {i} CNP 1850512123456", encoding="utf-8")
        db14 = Database(os.path.join(workdir, "cap_test.db"))
        Indexer(db14, extensions=[".txt"]).run(str(cap_dir))
        check("limita taie lista", len(db14.search("contract", limit=10)) == 10)
        check("count_matches dă totalul real (text)", db14.count_matches("contract") == 30,
              str(db14.count_matches("contract")))
        check("count_matches dă totalul real (CNP)", db14.count_matches("1850512123456") == 30)
        check("count_matches pe fragment de număr", db14.count_matches("0512123") == 30)
        check("count_recent numără documentele din interval",
              db14.count_recent(12) == 30 and db14.count_recent(0) == 0,
              f"{db14.count_recent(12)} / {db14.count_recent(0)}")
        recent = db14.get_recent_hits(modified_hours=12, limit=5)
        check("recente: limitate și cu date din index",
              len(recent) == 5 and all(h.exists and h.size > 0 and h.mtime > 0 for h in recent))
        db14.close()

        # ------------------------------------------------------------ #
        print("\n23) Verificare statică UI: fiecare self.X folosit chiar există")
        # Prinde bug-uri de tip "slot conectat dar nedefinit" fără a rula Qt.
        import ast

        ui_dir = Path(__file__).parent / "docsearch" / "ui"
        for py_file in sorted(ui_dir.glob("*.py")):
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
                defined: set[str] = set()
                used: set[str] = set()
                for node in ast.walk(cls):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        defined.add(node.name)
                    elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                            and node.value.id == "self":
                        if isinstance(node.ctx, ast.Store):
                            defined.add(node.attr)
                        else:
                            used.add(node.attr)
                # Atribute moștenite din Qt (încep cu literă mică + sunt camelCase
                # tipice Qt) nu pot fi verificate static; verificăm doar numele
                # cu underscore (convenția noastră pentru membri proprii).
                ours = {name for name in used if name.startswith("_") and not name.startswith("__")}
                missing = ours - defined
                check(f"{py_file.name}::{cls.name} fără referințe lipsă",
                      not missing, f"lipsesc: {sorted(missing)}")

    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\n{'=' * 50}\nRezultat: {PASS} trecute, {FAIL} eșuate")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
