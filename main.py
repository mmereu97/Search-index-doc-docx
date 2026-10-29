"""Punctul de intrare al aplicației: rulează cu  python main.py"""
from __future__ import annotations

import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QPainter, QPixmap
from PyQt5.QtNetwork import QLocalServer, QLocalSocket
from PyQt5.QtWidgets import QApplication, QMessageBox, QSplashScreen

from docsearch import paths
from docsearch.config import AppConfig
from docsearch.storage import Database
from docsearch.ui import MainWindow

# Setat în main() după ce aflăm folderul Data; folosit în mesajele de eroare.
LOG_FILE = "search_app.log"

# Numele „canalului" prin care a doua instanță anunță prima: „ridică-te!".
# Conține calea bazei de date ca două instalări diferite (ex. Project vs dist)
# să nu se încurce între ele.
INSTANCE_KEY_PREFIX = "DocSearch-"


def anchor_working_directory() -> None:
    """Fixează directorul de lucru la rădăcina instalării.

    Toate fișierele aplicației au acum căi absolute (în Data\\), dar ancorarea
    rămâne utilă: un .exe pornit dintr-un shortcut greșit ar avea altfel
    directorul curent prin System32.
    """
    os.chdir(paths.get_base_dir())


def setup_logging(log_file: str) -> None:
    handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    root.addHandler(logging.StreamHandler(sys.stderr))


def install_excepthook() -> None:
    """Plasă de siguranță: PyQt5 transformă excepțiile neprinse din sloturi
    în qFatal() => procesul moare instant cu 0xC0000409, fără traceback.

    Cu un sys.excepthook propriu, excepția e logată și afișată într-un dialog,
    iar aplicația continuă să ruleze.
    """
    def hook(exc_type, exc_value, exc_tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logging.critical("Excepție neprinse:\n%s", text)
        try:
            QMessageBox.critical(
                None, "Eroare neașteptată",
                f"A apărut o eroare neașteptată:\n\n{exc_value}\n\n"
                f"Detalii complete în {LOG_FILE}.",
            )
        except Exception:
            pass  # dacă nici dialogul nu poate fi afișat, rămâne log-ul

    sys.excepthook = hook


# --------------------------------------------------------------------------
# O singură instanță
# --------------------------------------------------------------------------

def instance_key() -> str:
    """Cheie unică pentru instalarea asta (depinde de locul bazei de date)."""
    import hashlib
    digest = hashlib.sha1(paths.db_path().lower().encode("utf-8")).hexdigest()[:12]
    return INSTANCE_KEY_PREFIX + digest


def notify_running_instance(key: str, timeout_ms: int = 500) -> bool:
    """Încearcă să anunțe o instanță deja pornită. True dacă există una."""
    sock = QLocalSocket()
    sock.connectToServer(key)
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(b"raise")
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return True


class SingleInstanceServer:
    """Ascultă cererile instanțelor ulterioare și aduce fereastra în față.

    Folosește QLocalServer (named pipe pe Windows) – dispare singur dacă
    procesul moare, deci nu rămâne niciun „lacăt" uitat după un crash.
    """

    def __init__(self, key: str) -> None:
        self.window = None
        self.server = QLocalServer()
        # Dacă a rămas un pipe orfan după un proces ucis, îl curățăm.
        QLocalServer.removeServer(key)
        if not self.server.listen(key):
            logging.warning("Nu pot asculta pe %s: %s", key, self.server.errorString())
        self.server.newConnection.connect(self._on_connection)

    def _on_connection(self) -> None:
        sock = self.server.nextPendingConnection()
        if sock is not None:
            sock.readAll()
            sock.disconnectFromServer()
        self.raise_window()

    def raise_window(self) -> None:
        w = self.window
        if w is None:
            return
        if w.isMinimized():
            w.showNormal()
        w.show()
        w.raise_()
        w.activateWindow()


# --------------------------------------------------------------------------
# Ecran de pornire
# --------------------------------------------------------------------------

def make_splash() -> QSplashScreen:
    """Un ecran mic, desenat în cod (fără imagini externe), afișat instant,
    ca utilizatorul să vadă că programul a pornit și să nu-l lanseze de 2-3 ori."""
    pix = QPixmap(420, 150)
    pix.fill(QColor("#1f4e79"))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QColor("white"))
    title = QFont()
    title.setPointSize(20)
    title.setBold(True)
    painter.setFont(title)
    painter.drawText(pix.rect().adjusted(24, 24, -24, -70), Qt.AlignLeft | Qt.AlignVCenter,
                     "Căutare documente")
    sub = QFont()
    sub.setPointSize(10)
    painter.setFont(sub)
    painter.setPen(QColor("#cfe2f3"))
    painter.drawText(pix.rect().adjusted(24, 70, -24, -24), Qt.AlignLeft | Qt.AlignTop,
                     "Pornește…")
    painter.end()
    splash = QSplashScreen(pix, Qt.WindowStaysOnTopHint)
    splash.setFont(sub)
    return splash


def splash_message(splash: QSplashScreen, text: str) -> None:
    splash.showMessage(text, Qt.AlignLeft | Qt.AlignBottom, QColor("#cfe2f3"))
    QApplication.processEvents()


def main() -> int:
    global LOG_FILE
    anchor_working_directory()
    LOG_FILE = paths.log_path()   # creează Data\ și migrează fișierele vechi
    setup_logging(LOG_FILE)
    install_excepthook()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # 1. Dacă programul e deja pornit, îl aducem în față și ne oprim.
    key = instance_key()
    if notify_running_instance(key):
        logging.info("Instanță deja pornită – i-am cerut să vină în față.")
        return 0
    guard = SingleInstanceServer(key)

    # 2. Ecranul de pornire apare imediat, înainte de orice lucru greu.
    splash = make_splash()
    splash.show()
    splash_message(splash, "  Deschid baza de date…")

    try:
        config = AppConfig.load(paths.config_path())
        db = Database(paths.db_path())
        splash_message(splash, "  Pregătesc fereastra…")
        window = MainWindow(config, db)
        guard.window = window
        window.show()
        splash.finish(window)
        return app.exec_()
    except Exception as exc:
        splash.close()
        logging.critical("Eroare critică la pornire", exc_info=True)
        QMessageBox.critical(
            None, "Eroare critică",
            f"A apărut o eroare critică: {exc}\n\nVerificați fișierul {LOG_FILE}.",
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
