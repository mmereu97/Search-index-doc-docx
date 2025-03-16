import sys
import os
import docx
import olefile
from PyQt5.QtWidgets import (QApplication, QMainWindow, QPushButton, QVBoxLayout, 
                           QWidget, QFileDialog, QLineEdit, QLabel, QTextEdit, 
                           QProgressBar, QMessageBox, QListWidget, QListWidgetItem,  # Adăugat aici
                           QHBoxLayout, QScrollArea, QDateEdit, QCheckBox, QComboBox, 
                           QSplitter, QTableWidget, QTableWidgetItem, QHeaderView,
                           QSizePolicy, QDialog, QGroupBox, QDialogButtonBox,
                           QSpinBox)
from PyQt5.QtCore import (QThreadPool, QRunnable, pyqtSignal, QObject, Qt, QMutex, 
                         QWaitCondition, QDate, QTimer)
from PyQt5.QtGui import QFont, QIcon, QBrush, QColor  # Am adăugat QBrush și QColor
import json
import psutil
import time
from datetime import datetime, timedelta
import winreg
import logging
from logging.handlers import RotatingFileHandler
import shutil
import tempfile
import subprocess
import re
import win32com.client
import pythoncom
import sqlite3
import hashlib
import pickle
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass
import threading
import unicodedata
import subprocess

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.setup_ui()
        self.load_settings()

    def setup_ui(self):
        """Configurează interfața pentru fereastra de setări"""
        self.setWindowTitle("Setări aplicație")
        self.setMinimumWidth(400)
        layout = QVBoxLayout()

        # Grup pentru setările de aspect
        appearance_group = QGroupBox("Aspect")
        appearance_layout = QVBoxLayout()

        # Font size
        font_layout = QHBoxLayout()
        font_size_label = QLabel("Mărime Font:")
        self.font_size_combo = QComboBox()
        self.font_size_combo.addItems(['8', '10', '12', '14', '16', '18', '20', '22', '24'])
        font_layout.addWidget(font_size_label)
        font_layout.addWidget(self.font_size_combo)
        appearance_layout.addLayout(font_layout)

        appearance_group.setLayout(appearance_layout)
        layout.addWidget(appearance_group)

        # Grup pentru setările de indexare și cache
        index_group = QGroupBox("Indexare și Cache")
        index_layout = QVBoxLayout()

        # Perioada pentru fișiere modificate
        modified_files_layout = QHBoxLayout()
        modified_files_label = QLabel("Perioada de verificare fișiere modificate (ore):")
        self.modified_files_hours = QSpinBox()
        self.modified_files_hours.setRange(1, 720)  # 1 oră până la 30 zile
        self.modified_files_hours.setValue(12)  # Valoare implicită: 12 ore
        self.modified_files_hours.setToolTip("Perioada pentru care se consideră un fișier ca fiind recent modificat")
        modified_files_layout.addWidget(modified_files_label)
        modified_files_layout.addWidget(self.modified_files_hours)
        index_layout.addLayout(modified_files_layout)

        # Adăugăm setarea pentru perioada de indexare parțială
        partial_index_layout = QHBoxLayout()
        partial_index_label = QLabel("Perioada indexare parțială (zile):")
        self.partial_index_days = QSpinBox()
        self.partial_index_days.setRange(1, 3650)  # 1 zi până la 10 ani
        self.partial_index_days.setValue(365)  # Valoare implicită: 1 an
        partial_index_layout.addWidget(partial_index_label)
        partial_index_layout.addWidget(self.partial_index_days)
        index_layout.addLayout(partial_index_layout)

        # Mod indexare
        self.index_mode = QComboBox()
        self.index_mode.addItems(["Adaugă la index", "Înlocuiește index"])
        index_layout.addWidget(QLabel("Mod indexare:"))
        index_layout.addWidget(self.index_mode)

        # Folosește Cache checkbox
        self.use_cache = QCheckBox("Folosește Cache")
        index_layout.addWidget(self.use_cache)

        # Buton pentru curățare index
        self.clear_index_button = QPushButton("Curăță Index")
        self.clear_index_button.clicked.connect(self.clear_index)
        index_layout.addWidget(self.clear_index_button)

        index_group.setLayout(index_layout)
        layout.addWidget(index_group)

        # Butoane Ok/Cancel
        button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.setLayout(layout)

    def load_settings(self):
        """Încarcă setările curente în dialog"""
        current_font_size = str(self.parent.font_size_combo.currentText())
        index = self.font_size_combo.findText(current_font_size)
        if index >= 0:
            self.font_size_combo.setCurrentIndex(index)

        current_index_mode = self.parent.index_mode.currentText()
        index = self.index_mode.findText(current_index_mode)
        if index >= 0:
            self.index_mode.setCurrentIndex(index)

        self.use_cache.setChecked(self.parent.use_cache)
        
        if hasattr(self.parent, 'partial_index_days'):
            self.partial_index_days.setValue(self.parent.partial_index_days)

        if hasattr(self.parent, 'modified_files_hours'):
            self.modified_files_hours.setValue(self.parent.modified_files_hours)

    def clear_index(self):
        """Curăță indexul cu confirmare"""
        reply = QMessageBox.question(
            self, 
            'Confirmare',
            'Sigur vrei să ștergi tot indexul?\nAceastă acțiune nu poate fi anulată.',
            QMessageBox.Yes | QMessageBox.No, 
            QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.parent.db_manager.clear_index()
            self.parent.status_label.setText("Index curățat cu succes")
            self.parent.update_stats(self.parent.db_manager.get_document_stats())

    def accept(self):
        """Salvează setările când se apasă OK"""
        # Actualizează font size
        new_font_size = self.font_size_combo.currentText()
        self.parent.font_size_combo.setCurrentText(new_font_size)
        self.parent.change_font_size(int(new_font_size))

        # Actualizează mod indexare
        new_index_mode = self.index_mode.currentText()
        self.parent.index_mode.setCurrentText(new_index_mode)

        # Actualizează setare cache
        self.parent.use_cache = self.use_cache.isChecked()

        # Salvează perioada de indexare parțială
        self.parent.partial_index_days = self.partial_index_days.value()
        self.parent.db_manager.partial_index_days = self.partial_index_days.value()

        # Salvează perioada pentru fișiere modificate
        self.parent.modified_files_hours = self.modified_files_hours.value()
        self.parent.db_manager.modified_files_hours = self.modified_files_hours.value()
        
        self.parent.save_config()
        super().accept()

# Configurare constantă
SQLITE_DB = "document_index.db"
LOG_FILE = "search_app.log"
ERROR_FILES_DIR = "search_errors"
CACHE_DIR = "search_cache"
MAX_CACHE_AGE = 7  # zile
INDEX_BATCH_SIZE = 1000
FREQUENT_CHANGE_THRESHOLD = 5  # număr de modificări într-o perioadă pentru a considera un document "frecvent modificat"
CHECK_PERIOD_DAYS = 1  # perioada de verificare pentru documente frecvent modificate

DIACRITICE_MAP = {
    'ș': 's', 'ț': 't', 'ă': 'a', 'î': 'i', 'â': 'a',
    'Ș': 'S', 'Ț': 'T', 'Ă': 'A', 'Î': 'I', 'Â': 'A'
}

def setup_logger():
    logger = logging.getLogger('SearchApp')
    logger.setLevel(logging.INFO)
    
    handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger

logger = setup_logger()

class NumericSequenceManager:
    def __init__(self):
        self.min_length = 5
        self.important_lengths = {5, 6, 11, 13}  # Am adăugat 13 pentru CNP-uri
        self.numbers_cache = {}
        
    def extract_numbers(self, content: str) -> Set[str]:
        """Extrage toate secvențele numerice relevante din conținut"""
        # Extrage numerele normale
        numbers = re.findall(r'\b\d{5,}\b', content)
        
        # Extrage CNP-uri (secvențe de 13 cifre care încep cu 1,2,5,6)
        cnp_numbers = []
        for number in numbers:
            if (len(number) == 13 and 
                number[0] in ['1', '2', '5', '6']):
                cnp_numbers.append(number)
        
        return set(numbers + cnp_numbers)

@dataclass
class DocumentInfo:
    """Clasa pentru stocarea informațiilor despre document"""
    def __init__(self, path: str, content: str, last_modified: datetime, file_size: int, file_hash: str, last_indexed: datetime):
        self.path = path
        self.content = content
        self.last_modified = last_modified
        self.file_size = file_size
        self.file_hash = file_hash
        self.last_indexed = last_indexed

class DatabaseManager:
    def __init__(self, db_path: str = SQLITE_DB):
        self.db_path = db_path
        self.current_folder = None
        self.connection_pool = {}
        self.pool_lock = threading.Lock()
        self.partial_index_days = 365  # valoarea implicită
        self.modified_files_hours = 12  # după self.partial_index_days = 365
        self.refresh_on_startup = True  # valoarea implicită
        
        # Curăță fișierele vechi WAL dacă există
        self._cleanup_wal_files()
        
        print("[DEBUG] Inițializare DatabaseManager")
        self.setup_database()
        self.num_manager = NumericSequenceManager()
        self.doc_tracker = DocumentTracker(self)
        self.scan_comparator = ScanComparator(self)

    def needs_reindexing(self, file_path: str, current_stats) -> bool:
        """
        Verifică dacă un document trebuie reindexat.
        
        Args:
            file_path: Calea către document
            current_stats: Statisticile curente ale fișierului
            
        Returns:
            bool: True dacă documentul trebuie reindexat, False altfel
        """
        try:
            conn = self.get_db_connection()
            cursor = conn.execute("""
                SELECT last_modified, file_size, file_hash
                FROM documents
                WHERE path = ?
            """, (file_path,))
            
            existing_doc = cursor.fetchone()
            
            if not existing_doc:
                return True  # Document nou, trebuie indexat
                
            # Extragem informațiile existente
            existing_modified = datetime.fromisoformat(existing_doc[0])
            existing_size = existing_doc[1]
            existing_hash = existing_doc[2]
            
            # Verificăm dacă s-a modificat ceva
            if (current_stats.st_mtime != existing_modified.timestamp() or
                current_stats.st_size != existing_size):
                
                # Opțional: Verificăm și hash-ul conținutului
                # Acest pas este mai lent dar garantează detectarea modificărilor
                if self.calculate_file_hash(file_path) != existing_hash:
                    return True
                    
            return False  # Documentul nu s-a modificat
            
        except Exception as e:
            print(f"[DEBUG] Eroare la verificarea necesității reindexării: {e}")
            return True  # În caz de eroare, reindexăm pentru siguranță
            
    def calculate_file_hash(self, file_path: str) -> str:
        """Calculează hash-ul unui fișier"""
        try:
            hasher = hashlib.md5()
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b''):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception as e:
            print(f"[DEBUG] Eroare la calcularea hash-ului: {e}")
            return ""

    def _cleanup_wal_files(self):
        """Curăță fișierele WAL și SHM dacă există"""
        try:
            wal_file = self.db_path + "-wal"
            shm_file = self.db_path + "-shm"
            
            if os.path.exists(wal_file):
                os.remove(wal_file)
                print("[DEBUG] Fișier WAL șters")
            if os.path.exists(shm_file):
                os.remove(shm_file)
                print("[DEBUG] Fișier SHM șters")
        except Exception as e:
            print(f"[DEBUG] Eroare la ștergerea fișierelor WAL/SHM: {e}")

    def get_db_connection(self):
        """Creează sau refolosește o conexiune thread-safe la baza de date"""
        thread_id = threading.get_ident()
        
        with self.pool_lock:
            if thread_id not in self.connection_pool:
                print(f"[DEBUG] Creare conexiune nouă pentru thread {thread_id}")
                conn = sqlite3.connect(self.db_path, timeout=30.0)
                # Folosim DELETE în loc de WAL pentru mai multă stabilitate
                conn.execute("PRAGMA journal_mode=DELETE")
                conn.execute("PRAGMA busy_timeout=30000")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA cache_size=5000")
                conn.row_factory = sqlite3.Row
                self.connection_pool[thread_id] = conn
            return self.connection_pool[thread_id]

    def release_connection(self):
        """Eliberează conexiunea pentru thread-ul curent"""
        thread_id = threading.get_ident()
        with self.pool_lock:
            if thread_id in self.connection_pool:
                try:
                    self.connection_pool[thread_id].close()
                    del self.connection_pool[thread_id]
                    print(f"[DEBUG] Conexiune eliberată pentru thread {thread_id}")
                except Exception as e:
                    print(f"[DEBUG] Eroare la eliberarea conexiunii: {e}")

    def execute_transaction(self, queries):
        """Execută mai multe query-uri într-o singură tranzacție"""
        conn = self.get_db_connection()
        try:
            with conn:  # Acest context manager va face automat commit/rollback
                for query, params in queries:
                    conn.execute(query, params)
            return True
        except Exception as e:
            print(f"[DEBUG] Eroare în tranzacție: {e}")
            return False

    def close_connections(self):
        """Închide toate conexiunile din pool"""
        with self.pool_lock:
            for thread_id, conn in list(self.connection_pool.items()):
                try:
                    if threading.get_ident() == thread_id:
                        conn.close()
                        del self.connection_pool[thread_id]
                        print(f"[DEBUG] Conexiune închisă pentru thread {thread_id}")
                except Exception as e:
                    print(f"[DEBUG] Eroare la închiderea conexiunii: {e}")
            self.connection_pool.clear()

    def setup_database(self):
        """Inițializează baza de date cu toate tabelele necesare"""
        print("[DEBUG] Începere inițializare bază de date")
        try:
            with sqlite3.connect(self.db_path) as conn:
                print("[DEBUG] Creare tabel documents")
                # Tabel principal pentru documente
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS documents (
                        id INTEGER PRIMARY KEY,
                        path TEXT UNIQUE,
                        content TEXT,
                        normalized_content TEXT,
                        last_modified TIMESTAMP,
                        file_size INTEGER,
                        file_hash TEXT,
                        last_indexed TIMESTAMP,
                        word_count INTEGER
                    )
                """)
                
                print("[DEBUG] Creare tabel numeric_sequences")
                # Tabel pentru secvențe numerice
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS numeric_sequences (
                        id INTEGER PRIMARY KEY,
                        sequence TEXT,
                        document_id INTEGER,
                        FOREIGN KEY(document_id) REFERENCES documents(id)
                    )
                """)
                
                print("[DEBUG] Creare tabel search_cache")
                # Tabel pentru cache-ul căutărilor
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS search_cache (
                        id INTEGER PRIMARY KEY,
                        query_hash TEXT UNIQUE,
                        query_text TEXT,
                        results BLOB,
                        created_at TIMESTAMP,
                        last_used TIMESTAMP,
                        use_count INTEGER DEFAULT 1,
                        is_numeric_search BOOLEAN DEFAULT 0
                    )
                """)
                
                print("[DEBUG] Creare tabel search_stats")
                # Tabel pentru statistici
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS search_stats (
                        id INTEGER PRIMARY KEY,
                        query_text TEXT,
                        execution_time REAL,
                        results_count INTEGER,
                        timestamp TIMESTAMP,
                        is_numeric_search BOOLEAN DEFAULT 0
                    )
                """)

                print("[DEBUG] Creare tabel unreadable_files")
                # Tabel pentru fișiere necitibile
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS unreadable_files (
                        path TEXT PRIMARY KEY,
                        error_message TEXT,
                        first_attempt TIMESTAMP,
                        last_attempt TIMESTAMP,
                        attempt_count INTEGER DEFAULT 1
                    )
                """)
                
                print("[DEBUG] Creare indexuri pentru optimizare")
                # Indexuri pentru optimizare
                conn.execute("CREATE INDEX IF NOT EXISTS idx_path ON documents(path)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_normalized ON documents(normalized_content)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_modified ON documents(last_modified)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_numeric_seq ON numeric_sequences(sequence)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_query ON search_cache(query_hash)")
                
                print("[DEBUG] Bază de date inițializată cu succes")
                
        except sqlite3.Error as e:
            print(f"[DEBUG] EROARE SQLite la inițializare bază de date: {str(e)}")
            raise
        except Exception as e:
            print(f"[DEBUG] EROARE generală la inițializare bază de date: {str(e)}")
            raise

    def normalize_text(self, text: str) -> str:
        """
        Normalizează textul pentru căutare, gestionând diacritice și formatare specială.
        
        Args:
            text (str): Textul care trebuie normalizat
                
        Returns:
            str: Textul normalizat
        """
        if not text:
            return ""
                
        # Normalizare Unicode pentru consistență
        text = unicodedata.normalize('NFKD', text)
            
        # Dicționar pentru înlocuirea diacriticelor românești
        diacritice_romanesti = {
            'ș': 's', 'ț': 't', 'ă': 'a', 'î': 'i', 'â': 'a',
            'Ș': 'S', 'Ț': 'T', 'Ă': 'A', 'Î': 'I', 'Â': 'A'
        }
            
        # Înlocuire diacritice românești
        for diacritic, replacement in diacritice_romanesti.items():
            text = text.replace(diacritic, replacement)
            
        # Eliminare diacritice rămase
        text = ''.join(character for character in text if not unicodedata.combining(character))
            
        # Înlocuire caractere de separare (cratime, punctuație) cu spații
        text = re.sub(r'[-‐‑‒–—―]', ' ', text)
            
        # Înlocuire caractere non-alfanumerice cu spații
        text = re.sub(r'[^a-zA-Z0-9\s]', ' ', text)
            
        # Standardizare spații multiple și eliminare spații de la început și sfârșit
        text = ' '.join(text.split())
            
        return text.lower()
    
    def get_document_info(self, file_path: str) -> Optional[DocumentInfo]:
        """Returnează informații despre un document din baza de date"""
        conn = self.get_db_connection()
        try:
            cursor = conn.execute("""
                SELECT path, content, last_modified, file_size, file_hash, last_indexed
                FROM documents WHERE path = ?
            """, (file_path,))
            row = cursor.fetchone()
            
            if row:
                return DocumentInfo(
                    path=row[0],
                    content=row[1],
                    last_modified=datetime.fromisoformat(row[2]),
                    file_size=row[3],
                    file_hash=row[4],
                    last_indexed=datetime.fromisoformat(row[5])
                )
            return None
        except Exception as e:
            print(f"[DEBUG] Eroare la obținerea info document {file_path}: {str(e)}")
            return None

    def mark_unreadable_file(self, file_path: str, error_message: str):
        """Marchează un fișier ca fiind imposibil de citit"""
        conn = self.get_db_connection()
        now = datetime.now().isoformat()
        try:
            cursor = conn.execute("""
                INSERT INTO unreadable_files (path, error_message, first_attempt, last_attempt, attempt_count)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(path) DO UPDATE SET 
                    last_attempt = ?,
                    attempt_count = attempt_count + 1,
                    error_message = ?
            """, (file_path, error_message, now, now, now, error_message))
            conn.commit()
            print(f"[DEBUG] Fișier marcat ca necitibil: {file_path}")
        except Exception as e:
            print(f"[DEBUG] Eroare la marcarea fișierului necitibil {file_path}: {e}")
            conn.rollback()

    def is_unreadable_file(self, file_path: str) -> bool:
        """Verifică dacă un fișier este marcat ca necitibil"""
        conn = self.get_db_connection()
        cursor = conn.execute("SELECT 1 FROM unreadable_files WHERE path = ?", (file_path,))
        return cursor.fetchone() is not None

    def add_document(self, doc_info: DocumentInfo):
        """Adaugă sau actualizează un document în baza de date"""
        print(f"\n[DEBUG] Început procesare document: {doc_info.path}")
        try:
            conn = self.get_db_connection()
            normalized_content = self.normalize_text(doc_info.content)
            word_count = len(doc_info.content.split())
            numeric_sequences = self.num_manager.extract_numbers(doc_info.content)
            print(f"[DEBUG] Numere găsite în document nou: {numeric_sequences}")

            # Începe tranzacția
            conn.execute("BEGIN IMMEDIATE")
            
            try:
                cursor = conn.cursor()
                print(f"[DEBUG] Inserare document în baza de date: {doc_info.path}")
                cursor.execute("""
                    INSERT OR REPLACE INTO documents
                    (path, content, normalized_content, last_modified, file_size, 
                     file_hash, last_indexed, word_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    doc_info.path,
                    doc_info.content,
                    normalized_content,
                    doc_info.last_modified.isoformat(),
                    doc_info.file_size,
                    doc_info.file_hash,
                    datetime.now().isoformat(),
                    word_count
                ))
                
                doc_id = cursor.lastrowid
                
                print(f"[DEBUG] Actualizare secvențe numerice pentru: {doc_info.path}")
                cursor.execute("DELETE FROM numeric_sequences WHERE document_id = ?", (doc_id,))
                
                if numeric_sequences:
                    cursor.executemany(
                        "INSERT INTO numeric_sequences (sequence, document_id) VALUES (?, ?)",
                        [(seq, doc_id) for seq in numeric_sequences]
                    )
                    # Adăugăm verificarea după inserare:
                    cursor.execute("SELECT COUNT(*) FROM numeric_sequences WHERE document_id = ?", (doc_id,))
                    count = cursor.fetchone()[0]
                    print(f"[DEBUG] Numere indexate pentru documentul {doc_id}: {count}")
                
                # Folosim aceeași conexiune pentru tracking
                now = datetime.now()
                cursor.execute(
                    "SELECT last_modified, change_count, first_seen FROM document_tracking WHERE path = ?",
                    (doc_info.path,)
                )
                existing = cursor.fetchone()
                
                if existing:
                    last_modified, change_count, first_seen = existing
                    last_modified = datetime.fromisoformat(last_modified)
                    first_seen = datetime.fromisoformat(first_seen)
                    
                    if doc_info.last_modified > last_modified:
                        days_since_first = (now - first_seen).days or 1
                        changes_per_day = (change_count + 1) / days_since_first
                        
                        cursor.execute("""
                            UPDATE document_tracking 
                            SET change_count = change_count + 1,
                                last_modified = ?,
                                last_check = ?,
                                is_frequent_change = ?,
                                check_frequency = ?
                            WHERE path = ?
                        """, (
                            doc_info.last_modified.isoformat(),
                            now.isoformat(),
                            changes_per_day >= (FREQUENT_CHANGE_THRESHOLD / CHECK_PERIOD_DAYS),
                            43200 if changes_per_day >= (FREQUENT_CHANGE_THRESHOLD / CHECK_PERIOD_DAYS) else 86400,
                            doc_info.path
                        ))
                else:
                    cursor.execute("""
                        INSERT INTO document_tracking 
                        (path, first_seen, last_modified, last_check, change_count)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        doc_info.path,
                        now.isoformat(),
                        doc_info.last_modified.isoformat(),
                        now.isoformat(),
                        0
                    ))
                
                # Invalidăm cache-ul și comitem tranzacția
                self.invalidate_search_cache()
                conn.commit()
                print(f"[DEBUG] Tranzacție comisă cu succes pentru: {doc_info.path}")
                
            except Exception as e:
                print(f"[DEBUG] Eroare în tranzacție, rollback pentru {doc_info.path}: {e}")
                conn.rollback()
                raise
                
        except Exception as e:
            print(f"[DEBUG] EROARE la procesare document {doc_info.path}: {str(e)}")
            raise
        
    def search_documents(self, query: str, use_cache: bool = True) -> List[str]:
        """
        Caută documente în baza de date folosind un query normalizat.
        
        Args:
            query (str): Textul căutat
            use_cache (bool): Folosește cache pentru rezultate
                
        Returns:
            List[str]: Lista cu path-urile documentelor găsite
        """
        print(f"[DEBUG] Începere căutare pentru query: {query}")
        
        try:
            # Normalizăm query-ul pentru căutare
            normalized_query = self.normalize_text(query)
            
            if not normalized_query:
                return []
            
            # Verifică cache-ul dacă este activat
            if use_cache:
                query_hash = hashlib.md5(query.encode()).hexdigest()
                cached_results = self.get_cached_results(query_hash)
                if cached_results:
                    return cached_results
            
            # Construim și executăm query-ul SQL
            with sqlite3.connect(self.db_path) as connection:  # Folosim self.db_path în loc de self.database_path
                cursor = connection.execute("""
                    SELECT path 
                    FROM documents 
                    WHERE normalized_content LIKE ? 
                    ORDER BY last_modified DESC
                """, [f'%{normalized_query}%'])
                
                # Procesăm rezultatele
                results = []
                for row in cursor:
                    file_path = row[0]
                    if os.path.exists(file_path):
                        results.append(file_path)
                    else:
                        # Curățăm referințele la fișierele care nu mai există
                        connection.execute("DELETE FROM documents WHERE path = ?", (file_path,))
                
                if connection.in_transaction:
                    connection.commit()
                
                # Salvăm în cache dacă este activat
                if use_cache:
                    self.cache_results(query_hash, query, results)  # Folosim query în loc de search_query
                
                return results
                
        except Exception as error:
            print(f"[DEBUG] EROARE la căutare: {str(error)}")
            raise

    def _search_numeric_sequence(self, query: str) -> List[str]:
        """Caută o secvență numerică în baza de date"""
        print(f"\n[DEBUG] Căutare număr: {query}")
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT DISTINCT d.path
                FROM documents d
                JOIN numeric_sequences ns ON ns.document_id = d.id
                WHERE ns.sequence LIKE ?
            """, (f'%{query}%',))
            
            # Filtrăm rezultatele pentru a include doar fișierele care există
            results = []
            for row in cursor:
                file_path = row[0]
                if os.path.exists(file_path):
                    results.append(file_path)
                else:
                    print(f"[DEBUG] Fișier șters detectat, se elimină din rezultate: {file_path}")
                    # Opțional: Putem șterge și din baza de date fișierele care nu mai există
                    conn.execute("DELETE FROM documents WHERE path = ?", (file_path,))
                    conn.execute("DELETE FROM document_tracking WHERE path = ?", (file_path,))
            
            if conn.in_transaction:
                conn.commit()
                
            return results

    def _search_text_content(self, query: str) -> List[str]:
        """Caută text în conținutul documentelor"""
        # Normalizăm query-ul complet ca o singură frază
        normalized_query = self.normalize_text(query)
        
        # Construim query-ul SQL pentru căutare exactă
        sql = "SELECT path FROM documents WHERE normalized_content LIKE ?"
        params = [f'%{normalized_query}%']
                
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(sql, params)
            
            results = []
            for row in cursor:
                file_path = row[0]
                if os.path.exists(file_path):
                    results.append(file_path)
                else:
                    print(f"[DEBUG] Fișier șters detectat, se elimină din rezultate: {file_path}")
                    conn.execute("DELETE FROM documents WHERE path = ?", (file_path,))
                    conn.execute("DELETE FROM document_tracking WHERE path = ?", (file_path,))
            
            if conn.in_transaction:
                conn.commit()
                
            return results

    def get_cached_results(self, query_hash: str) -> Optional[List[str]]:
        """Recuperează rezultate din cache"""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT results, created_at
                FROM search_cache
                WHERE query_hash = ?
            """, (query_hash,)).fetchone()
            
            if row and self._is_cache_valid(row[1]):
                # Aici trebuie să verificăm dacă fișierele din cache încă există
                cached_results = pickle.loads(row[0])
                # Filtrăm rezultatele și păstrăm doar fișierele care există
                valid_results = [path for path in cached_results if os.path.exists(path)]
                
                if len(valid_results) != len(cached_results):
                    # Dacă s-au găsit fișiere șterse, actualizăm cache-ul
                    conn.execute("""
                        UPDATE search_cache
                        SET results = ?,
                            use_count = use_count + 1,
                            last_used = ?
                        WHERE query_hash = ?
                    """, (pickle.dumps(valid_results), datetime.now().isoformat(), query_hash))
                    conn.commit()
                else:
                    conn.execute("""
                        UPDATE search_cache
                        SET use_count = use_count + 1,
                            last_used = ?
                        WHERE query_hash = ?
                    """, (datetime.now().isoformat(), query_hash))
                    conn.commit()
                
                return valid_results
        return None

    def invalidate_search_cache(self):
        """Invalidează cache-ul de căutare"""
        conn = self.get_db_connection()
        try:
            conn.execute("DELETE FROM search_cache")
            conn.commit()
            print("[DEBUG] Cache invalidat cu succes")
        except Exception as e:
            print(f"[DEBUG] Eroare la invalidarea cache: {str(e)}")
            conn.rollback()

    def _is_cache_valid(self, cache_date: str) -> bool:
        """Verifică dacă cache-ul este încă valid"""
        cache_datetime = datetime.fromisoformat(cache_date)
        return (datetime.now() - cache_datetime).days <= MAX_CACHE_AGE

    def cache_results(self, query_hash: str, query_text: str, results: List[str], is_numeric: bool = False):
        """Salvează rezultatele în cache"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO search_cache
                (query_hash, query_text, results, created_at, last_used, is_numeric_search)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                query_hash,
                query_text,
                pickle.dumps(results),
                datetime.now().isoformat(),
                datetime.now().isoformat(),
                is_numeric
            ))

    def save_search_stats(self, query: str, execution_time: float, results_count: int, is_numeric: bool = False):
        """Salvează statistici despre căutare"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO search_stats
                (query_text, execution_time, results_count, timestamp, is_numeric_search)
                VALUES (?, ?, ?, ?, ?)
            """, (
                query,
                execution_time,
                results_count,
                datetime.now().isoformat(),
                is_numeric
            ))

    def clean_old_cache(self):
        """Curăță cache-ul expirat"""
        cutoff_date = (datetime.now() - timedelta(days=MAX_CACHE_AGE)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                DELETE FROM search_cache
                WHERE last_used < ? AND use_count < 10
            """, (cutoff_date,))

    def get_document_stats(self) -> Dict:
        """Returnează statistici despre documente indexate"""
        with sqlite3.connect(self.db_path) as conn:
            # Mai întâi identificăm documentele care nu mai există
            cursor = conn.execute("SELECT id, path FROM documents")
            deleted_doc_ids = []
            for row in cursor:
                doc_id, path = row
                if not os.path.exists(path):
                    deleted_doc_ids.append(doc_id)
                    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
                    conn.execute("DELETE FROM document_tracking WHERE path = ?", (path,))

            # Curățăm secvențele numerice pentru documentele șterse
            if deleted_doc_ids:
                placeholders = ','.join('?' * len(deleted_doc_ids))
                conn.execute(f"DELETE FROM numeric_sequences WHERE document_id IN ({placeholders})", 
                            deleted_doc_ids)
            conn.commit()
            
            # CNP-uri unice - acum va reflecta doar documentele existente
            cursor = conn.execute("""
                SELECT DISTINCT sequence 
                FROM numeric_sequences ns
                JOIN documents d ON ns.document_id = d.id
                WHERE (sequence LIKE '1%' OR sequence LIKE '2%' OR 
                      sequence LIKE '5%' OR sequence LIKE '6%')
                AND length(sequence) = 13
            """)
            unique_cnps = len(cursor.fetchall())
            
            # Fișiere modificate în perioada configurată
            hours_ago = (datetime.now() - timedelta(hours=self.modified_files_hours)).isoformat()
            modified_files_count = conn.execute("""
                SELECT COUNT(*) FROM documents 
                WHERE last_modified > ?
            """, (hours_ago,)).fetchone()[0]
            
            # Statisticile de bază
            basic_stats = {
                'Total documente': conn.execute('SELECT COUNT(*) FROM documents').fetchone()[0],
                'Dimensiune totală (MB)': round(conn.execute('SELECT SUM(file_size) FROM documents').fetchone()[0] / (1024*1024), 2),
                'CNP-uri unice': unique_cnps,
                f'Fișiere modificate (ultimele {self.modified_files_hours} ore)': modified_files_count,
                'Fișiere noi adăugate': 0
            }
            
            # Adăugăm fișierele noi
            try:
                row = conn.execute("""
                    SELECT added_count
                    FROM scan_changes
                    ORDER BY timestamp DESC
                    LIMIT 1
                """).fetchone()
                
                if row:
                    basic_stats['Fișiere noi adăugate'] = row[0]
                    
            except Exception as e:
                print(f"[DEBUG] Eroare la obținerea fișierelor noi: {e}")
            
            return basic_stats

    def get_recently_modified_files(self) -> List[str]:
        """Returnează lista fișierelor modificate în perioada configurată"""
        with sqlite3.connect(self.db_path) as conn:
            hours_ago = (datetime.now() - timedelta(hours=self.modified_files_hours)).isoformat()
            cursor = conn.execute("""
                SELECT path FROM documents 
                WHERE last_modified > ?
                ORDER BY last_modified DESC
            """, (hours_ago,))
            return [row[0] for row in cursor.fetchall()]

    def get_newly_added_files(self) -> List[str]:
        """Returnează lista fișierelor adăugate în ultima sesiune"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT details FROM scan_changes
                ORDER BY timestamp DESC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                details = json.loads(row[0])
                return details.get('added_paths', [])
            return []    

    def format_time_duration(seconds: float) -> str:
        """Formatează durata în ore, minute și secunde"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        
        parts = []
        if hours > 0:
            parts.append(f"{hours} ore")
        if minutes > 0:
            parts.append(f"{minutes} minute")
        if secs > 0 or not parts:  # includem secundele dacă există sau dacă nu avem ore/minute
            parts.append(f"{secs} secunde")
            
        return " și ".join(parts)

    def clear_index(self):
        """Șterge tot indexul"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM documents")
            conn.execute("DELETE FROM numeric_sequences")
            conn.execute("DELETE FROM search_cache")
            conn.execute("VACUUM")

    def __del__(self):
        """Destructor pentru închiderea tuturor conexiunilor"""
        print("[DEBUG] Închidere conexiuni DatabaseManager")
        self.close_connections()
        self._cleanup_wal_files()

class DocumentTracker:
    def __init__(self, db_manager: 'DatabaseManager'):
        self.db_manager = db_manager
        self.setup_tracking_table()
        
    def setup_tracking_table(self):
        """Inițializează tabelul pentru tracking-ul documentelor"""
        conn = self.db_manager.get_db_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS document_tracking (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE,
                change_count INTEGER DEFAULT 0,
                first_seen TIMESTAMP,
                last_modified TIMESTAMP,
                last_check TIMESTAMP,
                is_frequent_change BOOLEAN DEFAULT 0,
                check_frequency INTEGER DEFAULT 86400
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_track_path ON document_tracking(path)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_frequent ON document_tracking(is_frequent_change)")
        conn.commit()
    
    def update_document_status(self, file_path: str, current_modified_time: datetime):
        """Actualizează statusul unui document folosind conexiunea din pool"""
        conn = self.db_manager.get_db_connection()
        now = datetime.now()
        
        # Verifică dacă documentul există în tracking
        existing = conn.execute(
            "SELECT last_modified, change_count, first_seen FROM document_tracking WHERE path = ?",
            (file_path,)
        ).fetchone()
        
        try:
            if existing:
                last_modified, change_count, first_seen = existing
                last_modified = datetime.fromisoformat(last_modified)
                first_seen = datetime.fromisoformat(first_seen)
                
                if current_modified_time > last_modified:
                    days_since_first = (now - first_seen).days or 1
                    changes_per_day = (change_count + 1) / days_since_first
                    
                    conn.execute("""
                        UPDATE document_tracking 
                        SET change_count = change_count + 1,
                            last_modified = ?,
                            last_check = ?,
                            is_frequent_change = ?,
                            check_frequency = ?
                        WHERE path = ?
                    """, (
                        current_modified_time.isoformat(),
                        now.isoformat(),
                        changes_per_day >= (FREQUENT_CHANGE_THRESHOLD / CHECK_PERIOD_DAYS),
                        43200 if changes_per_day >= (FREQUENT_CHANGE_THRESHOLD / CHECK_PERIOD_DAYS) else 86400,
                        file_path
                    ))
            else:
                conn.execute("""
                    INSERT INTO document_tracking 
                    (path, first_seen, last_modified, last_check, change_count)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    file_path,
                    now.isoformat(),
                    current_modified_time.isoformat(),
                    now.isoformat(),
                    0
                ))
            conn.commit()
            
        except Exception as e:
            print(f"[DEBUG] Eroare la actualizare tracking pentru {file_path}: {e}")
            conn.rollback()
            raise
            
    def is_check_needed(self, file_path: str) -> bool:
        """Verifică dacă un document trebuie reverificat"""
        conn = self.db_manager.get_db_connection()
        row = conn.execute("""
            SELECT last_check, check_frequency, is_frequent_change
            FROM document_tracking 
            WHERE path = ?
        """, (file_path,)).fetchone()
        
        if not row:
            return True
            
        last_check = datetime.fromisoformat(row[0])
        check_frequency = row[1]  # în secunde
        is_frequent = row[2]
        
        time_since_check = (datetime.now() - last_check).total_seconds()
        return time_since_check >= check_frequency or is_frequent

    def get_tracking_stats(self) -> Dict:
        """Returnează statistici despre documentele urmărite"""
        conn = self.db_manager.get_db_connection()
        total = conn.execute("SELECT COUNT(*) FROM document_tracking").fetchone()[0]
        frequent = conn.execute("SELECT COUNT(*) FROM document_tracking WHERE is_frequent_change = 1").fetchone()[0]
        total_changes = conn.execute("SELECT SUM(change_count) FROM document_tracking").fetchone()[0] or 0
        
        return {
            "Total documente urmărite": total,
            "Documente modificate frecvent": frequent,
            "Total modificări detectate": total_changes
        }

class ScanComparator:
    """Compară rezultatele între scanări succesive"""
    def __init__(self, db_manager):
        self.db_manager = db_manager
        self.previous_scan = self._load_last_state()
        self.current_scan = None
        self._setup_tables()
    
    def _setup_tables(self):
        """Inițializează tabelele necesare"""
        conn = self.db_manager.get_db_connection()
        try:
            # Tabel pentru ultima stare
            conn.execute("""
                CREATE TABLE IF NOT EXISTS last_scan_state (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    paths TEXT
                )
            """)
            
            # Tabel pentru istoricul schimbărilor
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scan_changes (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    removed_count INTEGER,
                    added_count INTEGER,
                    details TEXT
                )
            """)
            conn.commit()
        except Exception as e:
            print(f"[DEBUG] Eroare la inițializarea tabelelor: {e}")
            conn.rollback()

    def _load_last_state(self):
        """Încarcă ultima stare salvată"""
        print("[DEBUG] Începere încărcare stare anterioară")
        conn = self.db_manager.get_db_connection()
        try:
            row = conn.execute("""
                SELECT paths 
                FROM last_scan_state 
                ORDER BY timestamp DESC LIMIT 1
            """).fetchone()
            
            if row:
                paths = set(json.loads(row[0]))
                print(f"[DEBUG] Stare anterioară încărcată: {len(paths)} fișiere")
                return {
                    'paths': paths
                }
            print("[DEBUG] Nu există stare anterioară salvată")
            return None
        except Exception as e:
            print(f"[DEBUG] Eroare la încărcarea stării anterioare: {e}")
            return None

    def _save_state(self, paths):
        """Salvează o stare în baza de date"""
        conn = self.db_manager.get_db_connection()
        try:
            conn.execute("DELETE FROM last_scan_state")
            conn.execute("""
                INSERT INTO last_scan_state (timestamp, paths)
                VALUES (?, ?)
            """, (
                datetime.now().isoformat(),
                json.dumps(list(paths))
            ))
            conn.commit()
            print(f"[DEBUG] Stare salvată: {len(paths)} fișiere")
        except Exception as e:
            print(f"[DEBUG] Eroare la salvarea stării: {e}")
            conn.rollback()

    def start_new_scan(self):
        """Începe o nouă scanare"""
        print("[DEBUG] Începere scanare nouă")
        if not hasattr(self.db_manager, 'current_folder') or not self.db_manager.current_folder:
            print("[DEBUG] Nu există folder curent setat")
            return

        if not self.previous_scan:
            current_paths = set()
            for root, _, files in os.walk(self.db_manager.current_folder):
                for file in files:
                    if file.endswith(('.doc', '.docx')):
                        current_paths.add(os.path.join(root, file))
            print(f"[DEBUG] Prima scanare - găsite {len(current_paths)} fișiere")
            self.previous_scan = {'paths': current_paths}
            self._save_state(current_paths)
        else:
            print(f"[DEBUG] Scanare cu stare anterioară de {len(self.previous_scan['paths'])} fișiere")

    def finish_scan(self):
        """Finalizează scanarea și calculează diferențele"""
        print("[DEBUG] Finalizare scanare")
        if not hasattr(self.db_manager, 'current_folder') or not self.db_manager.current_folder:
            print("[DEBUG] Nu există folder curent setat")
            return None

        # Scanăm direct folderele pentru fișierele actuale
        current_paths = set()
        for root, _, files in os.walk(self.db_manager.current_folder):
            for file in files:
                if file.endswith(('.doc', '.docx')):
                    current_paths.add(os.path.join(root, file))

        print(f"[DEBUG] Scanare finalizată - găsite {len(current_paths)} fișiere")

        if self.previous_scan:
            # Calculăm diferențele
            removed_files = self.previous_scan['paths'] - current_paths
            added_files = current_paths - self.previous_scan['paths']

            print(f"[DEBUG] Analiză diferențe:")
            print(f"[DEBUG] Stare anterioară: {len(self.previous_scan['paths'])} fișiere")
            print(f"[DEBUG] Stare curentă: {len(current_paths)} fișiere")
            print(f"[DEBUG] Fișiere eliminate: {len(removed_files)}")
            print(f"[DEBUG] Fișiere adăugate: {len(added_files)}")

            # Salvăm schimbările în baza de date
            conn = self.db_manager.get_db_connection()
            try:
                # Salvăm detaliile schimbărilor
                conn.execute("""
                    INSERT INTO scan_changes 
                    (timestamp, removed_count, added_count, details)
                    VALUES (?, ?, ?, ?)
                """, (
                    datetime.now().isoformat(),
                    len(removed_files),
                    len(added_files),
                    json.dumps({
                        'removed_paths': list(removed_files),
                        'added_paths': list(added_files)
                    })
                ))
                conn.commit()
                print("[DEBUG] Schimbări salvate în baza de date")

                changes = {
                    'removed_count': len(removed_files),
                    'added_count': len(added_files),
                    'removed_paths': removed_files,
                    'added_paths': added_files
                }

                # Salvăm noua stare pentru viitoare comparații
                self._save_state(current_paths)

                return changes

            except Exception as e:
                print(f"[DEBUG] Eroare la salvarea schimbărilor: {e}")
                conn.rollback()
                return None
        else:
            print("[DEBUG] Nu există stare anterioară pentru comparație")
            # La prima scanare doar salvăm starea
            self._save_state(current_paths)
            return None

    def get_last_changes(self):
        """Returnează ultimele schimbări înregistrate"""
        conn = self.db_manager.get_db_connection()
        try:
            row = conn.execute("""
                SELECT removed_count, added_count, details 
                FROM scan_changes 
                ORDER BY timestamp DESC LIMIT 1
            """).fetchone()
            
            if row:
                return {
                    'removed_count': row[0],
                    'added_count': row[1],
                    'details': json.loads(row[2])
                }
        except Exception as e:
            print(f"[DEBUG] Eroare la obținerea ultimelor schimbări: {e}")
        return None
            
class DocumentReader:
    """Clasa pentru citirea diferitelor tipuri de documente"""
    @staticmethod
    def read_docx(file_path: str) -> Tuple[str, Optional[str]]:
        """Citește conținutul unui fișier .docx"""
        print(f"\n[DEBUG] Încercare citire DOCX: {file_path}")
        try:
            print(f"[DEBUG] Deschidere document DOCX: {file_path}")
            document = docx.Document(file_path)
            content = "\n".join([paragraph.text for paragraph in document.paragraphs])
            
            if not content.strip():
                error_msg = "Document gol"
                return "", error_msg
                
            print(f"[DEBUG] DOCX citit cu succes: {file_path}")
            return content, None
            
        except Exception as e:
            print(f"[DEBUG] EROARE la citire DOCX {file_path}: {str(e)}")
            return "", str(e)

    @staticmethod
    def read_doc(file_path: str) -> Tuple[str, Optional[str]]:
        """Citește conținutul unui fișier .doc folosind multiple metode"""
        print(f"\n[DEBUG] Încercare citire DOC: {file_path}")
        try:
            # Prima încercare - OLE
            print(f"[DEBUG] Încercare metodă OLE pentru: {file_path}")
            if olefile.isOleFile(file_path):
                print(f"[DEBUG] Fișier OLE valid detectat: {file_path}")
                ole = olefile.OleFileIO(file_path)
                if ole.exists('WordDocument'):
                    try:
                        print(f"[DEBUG] Citire stream WordDocument pentru: {file_path}")
                        stream = ole.openstream('WordDocument')
                        content = stream.read().decode('utf-8', errors='ignore')
                        ole.close()
                        if content.strip():
                            print(f"[DEBUG] Conținut extras cu succes prin OLE: {file_path}")
                            return content, None
                    except Exception as e:
                        print(f"[DEBUG] EROARE la citire OLE stream pentru {file_path}: {str(e)}")

            # A doua încercare - Word automation
            print(f"[DEBUG] Încercare Word automation pentru: {file_path}")
            pythoncom.CoInitialize()
            try:
                print(f"[DEBUG] Inițializare Word pentru: {file_path}")
                word = win32com.client.Dispatch("Word.Application")
                word.Visible = False
                
                print(f"[DEBUG] Deschidere document Word: {file_path}")
                doc = word.Documents.Open(file_path)
                content = doc.Content.Text
                doc.Close()
                word.Quit()
                print(f"[DEBUG] Document Word procesat cu succes: {file_path}")
                return content, None
                
            except Exception as e:
                print(f"[DEBUG] EROARE la procesare Word pentru {file_path}: {str(e)}")
                return "", str(e)
            finally:
                print(f"[DEBUG] Închidere COM pentru: {file_path}")
                pythoncom.CoUninitialize()
                    
        except Exception as e:
            print(f"[DEBUG] EROARE generală la citire DOC {file_path}: {str(e)}")
            return "", str(e)

class SearchWorkerSignals(QObject):
    """Semnale pentru comunicarea cu interfața"""
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(tuple)  # Modificat din list în tuple
    error = pyqtSignal(str)
    stats = pyqtSignal(dict)

class SearchWorker(QRunnable):
    """Worker pentru căutare în thread separat"""
    def __init__(self, db_manager: DatabaseManager, query: str):
        super().__init__()
        self.db_manager = db_manager
        self.query = query
        self.signals = SearchWorkerSignals()

    def run(self):
        try:
            start_time = time.time()  # Începem măsurarea timpului
            results = self.db_manager.search_documents(self.query)
            end_time = time.time()  # Terminăm măsurarea timpului
            search_time = end_time - start_time  # Calculăm durata
            
            stats = self.db_manager.get_document_stats()
            self.signals.stats.emit(stats)
            self.signals.finished.emit((results, search_time))  # Transmitem tuplul
        except Exception as e:
            self.signals.error.emit(str(e))

class IndexWorkerSignals(QObject):
    """Semnale pentru procesul de indexare"""
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(tuple)  # Modificat pentru a include timpul
    error = pyqtSignal(str)
    stats = pyqtSignal(dict)

class IndexWorker(QRunnable):
    """Worker pentru indexarea documentelor"""
    def __init__(self, db_manager: DatabaseManager, folder_path: str, replace_index: bool = False):
        super().__init__()
        self.db_manager = db_manager
        self.folder_path = folder_path
        self.replace_index = replace_index
        self.signals = IndexWorkerSignals()

    def run(self):
        """Worker pentru indexarea documentelor"""
        try:
            start_time = time.time()
            
            # Începem o nouă scanare pentru urmărirea schimbărilor
            self.db_manager.scan_comparator.start_new_scan()
            
            if self.replace_index:
                self.db_manager.clear_index()

            total_files = 0
            indexed_files = 0
            skipped_files = 0
            unreadable_files = 0
            unchanged_files = 0  # Contor nou pentru fișiere neschimbate
            
            # Calculăm data limită pentru indexarea parțială la pornire
            cutoff_date = None
            if hasattr(self.db_manager, 'partial_index_days') and self.db_manager.refresh_on_startup:
                cutoff_date = datetime.now() - timedelta(days=self.db_manager.partial_index_days)

            # Numără fișierele
            for root, _, files in os.walk(self.folder_path):
                for file in files:
                    # Ignorăm fișierele temporare care încep cu ~$
                    if file.startswith('~$'):
                        continue
                    if file.endswith(('.doc', '.docx')):
                        if cutoff_date:
                            file_path = os.path.join(root, file)
                            stats = os.stat(file_path)
                            if datetime.fromtimestamp(stats.st_mtime) < cutoff_date:
                                continue
                        total_files += 1

            # Indexează fișierele
            for root, _, files in os.walk(self.folder_path):
                for file in files:
                    # Ignorăm fișierele temporare care încep cu ~$
                    if file.startswith('~$'):
                        continue
                    if file.endswith(('.doc', '.docx')):
                        file_path = os.path.join(root, file)
                        
                        try:
                            # Verifică data modificării pentru indexare parțială
                            current_stats = os.stat(file_path)
                            if cutoff_date:
                                if datetime.fromtimestamp(current_stats.st_mtime) < cutoff_date:
                                    skipped_files += 1
                                    continue

                            # Verificăm dacă documentul necesită reindexare
                            if not self.db_manager.needs_reindexing(file_path, current_stats):
                                unchanged_files += 1
                                indexed_files += 1
                                continue

                            # Verifică dacă fișierul este deja marcat ca necitibil
                            if self.db_manager.is_unreadable_file(file_path):
                                unreadable_files += 1
                                indexed_files += 1
                                continue

                            # Verifică dacă fișierul necesită reindexare
                            if not self.replace_index and not self.db_manager.doc_tracker.is_check_needed(file_path):
                                skipped_files += 1
                                indexed_files += 1
                                continue

                            last_modified = datetime.fromtimestamp(current_stats.st_mtime)
                            
                            if file.endswith('.docx'):
                                content, error = DocumentReader.read_docx(file_path)
                            else:
                                content, error = DocumentReader.read_doc(file_path)

                            if error:
                                self.db_manager.mark_unreadable_file(file_path, error)
                                unreadable_files += 1
                                indexed_files += 1
                                continue

                            if not content.strip():
                                self.db_manager.mark_unreadable_file(file_path, "Document gol")
                                unreadable_files += 1
                                indexed_files += 1
                                continue

                            doc_info = DocumentInfo(
                                path=file_path,
                                content=content,
                                last_modified=last_modified,
                                file_size=current_stats.st_size,
                                file_hash=self.db_manager.calculate_file_hash(file_path),
                                last_indexed=datetime.now()
                            )

                            self.db_manager.add_document(doc_info)
                            indexed_files += 1
                            
                            elapsed_time = int(time.time() - start_time)
                            status_text = (f"Procesare: {file_path}\n"
                                         f"Fișiere procesate: {indexed_files}/{total_files}\n"
                                         f"Fișiere sărite (neschimbate): {skipped_files}\n"
                                         f"Fișiere necitibile: {unreadable_files}\n"
                                         f"Fișiere nemodificate: {unchanged_files}\n"
                                         f"Timp scurs: {elapsed_time} secunde")
                            
                            progress = int((indexed_files / total_files) * 100) if total_files > 0 else 0
                            self.signals.progress.emit(progress, status_text)
                            
                        except Exception as e:
                            self.signals.error.emit(f"Error indexing {file}: {str(e)}")

            changes = self.db_manager.scan_comparator.finish_scan()
            total_time = time.time() - start_time

            stats = self.db_manager.get_document_stats()
            stats["Fișiere verificate în această scanare"] = indexed_files - skipped_files - unreadable_files - unchanged_files
            stats["Fișiere sărite (neschimbate)"] = skipped_files
            stats["Fișiere necitibile"] = unreadable_files
            stats["Fișiere nemodificate"] = unchanged_files
            if changes:
                stats["Fișiere eliminate"] = changes['removed_count']
                stats["Fișiere noi adăugate"] = changes['added_count']
            self.signals.stats.emit(stats)            
            self.signals.finished.emit((indexed_files, total_time))
            
        except Exception as e:
            self.signals.error.emit(f"Indexing error: {str(e)}")

class SearchApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db_manager = DatabaseManager()
        self.thread_pool = QThreadPool()
        self.use_cache = True
        self.index_mode = "Adaugă la index"
        self.partial_index_days = 365
        self.modified_files_hours = 12  # Valoare implicită
        self.setup_ui()
        self.load_config()
        
        # Setăm valorile și în DatabaseManager
        self.db_manager.partial_index_days = self.partial_index_days
        self.db_manager.modified_files_hours = self.modified_files_hours

# În clasa SettingsDialog, păstrăm o singură implementare a metodei accept()
        
    def setup_ui(self):
        """Configurează interfața utilizator"""
        # Setări de bază pentru fereastra principală
        self.setWindowTitle("Document Search System")
        self.setGeometry(100, 100, 1400, 900)
        
        # Widget central și layout principal
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        
        # Buton setări
        settings_layout = QHBoxLayout()
        self.settings_button = QPushButton("Setări")
        self.settings_button.clicked.connect(self.show_settings)
        settings_layout.addWidget(self.settings_button)
        settings_layout.addStretch()
        layout.addLayout(settings_layout)
        
        # Căutare și control
        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Introduceți textul pentru căutare...")
        self.search_input.returnPressed.connect(self.start_search)
        search_layout.addWidget(self.search_input)
        
        self.search_button = QPushButton("Caută")
        self.search_button.clicked.connect(self.start_search)
        search_layout.addWidget(self.search_button)
        layout.addLayout(search_layout)

        # Butoane pentru indexare
        index_layout = QHBoxLayout()
        self.index_button = QPushButton("Director de indexat")
        self.index_button.clicked.connect(self.browse_folder)
        index_layout.addWidget(self.index_button)

        # Buton pentru reindexare fișiere modificate
        self.reindex_modified_button = QPushButton("Reindexare Fișiere Modificate")
        self.reindex_modified_button.setToolTip(f"Reindexează fișierele modificate în ultimele {self.modified_files_hours} ore")
        self.reindex_modified_button.clicked.connect(self.reindex_modified_files)
        index_layout.addWidget(self.reindex_modified_button)

        # Buton pentru indexare rapidă
        self.quick_index_button = QPushButton("Indexare Rapidă")
        self.quick_index_button.setToolTip(f"Indexează doar documentele modificate în ultimele {self.partial_index_days} zile")
        self.quick_index_button.clicked.connect(self.start_quick_indexing)
        index_layout.addWidget(self.quick_index_button)

        # Buton pentru indexare completă
        self.full_index_button = QPushButton("Indexare de la Începuturi")
        self.full_index_button.setToolTip("Reindexează toate documentele din istoric")
        self.full_index_button.clicked.connect(self.start_full_indexing)
        index_layout.addWidget(self.full_index_button)

        # Buton pentru continuare indexare
        self.continue_index_button = QPushButton("Continuare Indexare de la Începuturi")
        self.continue_index_button.setToolTip("Continuă o indexare completă întreruptă")
        self.continue_index_button.clicked.connect(self.continue_full_indexing)
        index_layout.addWidget(self.continue_index_button)

        layout.addLayout(index_layout)
        
        # Păstrăm aceste controale dar le ascundem (necesare pentru funcționalitate)
        self.font_size_combo = QComboBox()
        self.font_size_combo.addItems(['8', '10', '12', '14', '16', '18', '20', '22', '24'])
        self.font_size_combo.setCurrentText('14')
        self.font_size_combo.hide()
        
        self.index_mode = QComboBox()
        self.index_mode.addItems(["Adaugă la index", "Înlocuiește index"])
        self.index_mode.setCurrentText("Adaugă la index")
        self.index_mode.hide()

        # Layout pentru folder
        folder_layout = QHBoxLayout()
        
        # Label pentru afișarea folderului curent
        self.current_folder_label = QLabel("Folder: Nu este selectat")
        self.current_folder_label.setStyleSheet("color: #666;")
        folder_layout.addWidget(self.current_folder_label)
        
        # Ascundem butonul de refresh (îl păstrăm pentru logică)
        self.refresh_button = QPushButton("Reindexează Folder")
        self.refresh_button.setIcon(QIcon.fromTheme("view-refresh"))
        self.refresh_button.clicked.connect(self.refresh_current_folder)
        self.refresh_button.setEnabled(False)
        self.refresh_button.hide()  # Ascundem butonul
        
        # Checkbox pentru refresh la pornire
        self.refresh_on_startup = QCheckBox("Reindexează la pornire")
        self.refresh_on_startup.setChecked(True)
        folder_layout.addWidget(self.refresh_on_startup)
        
        layout.addLayout(folder_layout)
        
        # Splitter principal vertical
        self.main_splitter = QSplitter(Qt.Vertical)
        
        # Zona de sus (rezultate și statistici)
        top_area = QWidget()
        top_layout = QVBoxLayout(top_area)
        top_layout.setContentsMargins(0, 0, 0, 0)
        
        # Splitter orizontal pentru rezultate și statistici
        self.horizontal_splitter = QSplitter(Qt.Horizontal)
        
        # Configurare panou rezultate (stânga)
        results_widget = QWidget()
        results_layout = QVBoxLayout(results_widget)
        results_layout.setContentsMargins(0, 0, 0, 0)
        
        results_label = QLabel("Rezultate găsite:")
        results_layout.addWidget(results_label)
        
        # Lista de rezultate cu configurări îmbunătățite
        results_widget = self.setup_results_area()
        self.horizontal_splitter.addWidget(results_widget)
        
        # Configurare panou statistici (dreapta)
        stats_widget = QWidget()
        stats_layout = QVBoxLayout(stats_widget)
        stats_layout.setContentsMargins(0, 0, 0, 0)
        
        self.stats_label = QLabel("Statistici")
        stats_layout.addWidget(self.stats_label)
        
        # Tabel statistici cu configurări îmbunătățite
        self.stats_table = QTableWidget()
        self.stats_table.setColumnCount(2)
        self.stats_table.setHorizontalHeaderLabels(["Metrică", "Valoare"])
        
        # Configurare header pentru stats_table
        header = self.stats_table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        
        # Setări inițiale pentru coloane
        self.stats_table.setColumnWidth(0, 300)
        self.stats_table.setColumnWidth(1, 150)
        
        # Conectare semnal pentru salvare automată la redimensionare coloane
        header.sectionResized.connect(lambda: self.save_config())
        
        # Alte setări pentru tabel
        self.stats_table.setAlternatingRowColors(True)
        self.stats_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.stats_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.stats_table.setSelectionMode(QTableWidget.SingleSelection)
        
        stats_layout.addWidget(self.stats_table)
        
        self.horizontal_splitter.addWidget(stats_widget)
        top_layout.addWidget(self.horizontal_splitter)
        
        # Setăm proporțiile inițiale pentru splitter-ul orizontal
        self.horizontal_splitter.setSizes([700, 700])
        
        self.main_splitter.addWidget(top_area)
        
        # Zona de jos (bara de progres și status)
        bottom_area = QWidget()
        bottom_layout = QVBoxLayout(bottom_area)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        
        # Progress bar îmbunătățit
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setAlignment(Qt.AlignCenter)
        bottom_layout.addWidget(self.progress_bar)
        
        # Status label îmbunătățit
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumHeight(50)
        bottom_layout.addWidget(self.status_label)
        
        self.main_splitter.addWidget(bottom_area)
        
        # Setăm proporțiile inițiale pentru splitter-ul principal
        self.main_splitter.setSizes([800, 100])
        
        layout.addWidget(self.main_splitter)
        
        # Permite redimensionarea ferestrei și salvează starea
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint | Qt.WindowMinimizeButtonHint)
        
        # Aplicăm fontul inițial la toate widget-urile
        self.apply_font_to_all_widgets(QFont('Arial', 14))
        
        # Setăm policy-ul de dimensiune pentru widget-uri importante
        self.stats_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.results_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        # Configurare pentru salvarea stării la închidere
        self.setAttribute(Qt.WA_DeleteOnClose)

    def setup_results_area(self):
        """Configurează zona de rezultate cu opțiuni de sortare și deschidere folder"""
        results_widget = QWidget()
        results_layout = QVBoxLayout(results_widget)
        results_layout.setContentsMargins(0, 0, 0, 0)

        # Header cu label și dropdown pentru sortare
        header_layout = QHBoxLayout()
        results_label = QLabel("Rezultate găsite:")
        header_layout.addWidget(results_label)
        
        # Dropdown pentru sortare
        self.sort_combo = QComboBox()
        self.sort_combo.addItems([
            "Sortare după nume",
            "Sortare după folder",
            "Sortare după data modificării"
        ])
        self.sort_combo.currentTextChanged.connect(self.sort_results)
        header_layout.addWidget(QLabel("Sortare:"))
        header_layout.addWidget(self.sort_combo)
        header_layout.addStretch()
        results_layout.addLayout(header_layout)

        # Lista de rezultate îmbunătățită
        self.results_list = QListWidget()
        self.results_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.results_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.results_list.setSelectionMode(QListWidget.SingleSelection)
        self.results_list.itemDoubleClicked.connect(self.open_file)
        self.results_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.results_list.customContextMenuRequested.connect(self.show_context_menu)
        results_layout.addWidget(self.results_list)

        return results_widget

    def show_context_menu(self, position):
        """Afișează meniul contextual pentru lista de rezultate"""
        menu = QMenu()
        current_item = self.results_list.itemAt(position)
        
        if current_item is not None:
            open_file_action = menu.addAction("Deschide fișier")
            open_folder_action = menu.addAction("Deschide folder")
            
            action = menu.exec_(self.results_list.mapToGlobal(position))
            
            if action == open_file_action:
                self.open_file(current_item)
            elif action == open_folder_action:
                self.open_containing_folder(current_item.text())

    def open_containing_folder(self, file_path):
        """Deschide folderul care conține fișierul"""
        try:
            folder_path = os.path.dirname(file_path)
            if os.path.exists(folder_path):
                # Folosim explorer pentru a selecta fișierul în folder
                subprocess.Popen(f'explorer /select,"{file_path}"')
            else:
                QMessageBox.warning(self, "Eroare", 
                                  "Folderul nu mai există sau nu poate fi accesat!")
        except Exception as e:
            QMessageBox.warning(self, "Eroare", 
                              f"Nu s-a putut deschide folderul: {str(e)}")

    def sort_results(self):
        """Sortează rezultatele în funcție de criteriul selectat"""
        print("[DEBUG] Începere sortare rezultate")
        
        if self.results_list.count() == 0:
            print("[DEBUG] Lista de rezultate este goală")
            return

        try:
            items = []
            print(f"[DEBUG] Număr total de items: {self.results_list.count()}")
            
            # Colectăm items cu verificare
            for i in range(self.results_list.count()):
                item = self.results_list.item(i)
                if item is None:
                    print(f"[DEBUG] Item null găsit la poziția {i}")
                    continue
                file_path = item.text()
                print(f"[DEBUG] Procesare item {i}: {file_path}")
                items.append(file_path)

            sort_method = self.sort_combo.currentText()
            print(f"[DEBUG] Metoda de sortare selectată: {sort_method}")
            
            # Sortare cu verificări
            try:
                if sort_method == "Sortare după nume":
                    print("[DEBUG] Sortare după nume...")
                    items.sort(key=lambda x: os.path.basename(x).lower())
                elif sort_method == "Sortare după folder":
                    print("[DEBUG] Sortare după folder...")
                    items.sort(key=lambda x: os.path.dirname(x).lower() if os.path.dirname(x) else "")
                elif sort_method == "Sortare după data modificării":
                    print("[DEBUG] Sortare după data modificării...")
                    items.sort(key=lambda x: os.path.getmtime(x) if os.path.exists(x) else 0, reverse=True)
            except Exception as sort_error:
                print(f"[DEBUG] Eroare la sortare: {str(sort_error)}")
                raise

            print("[DEBUG] Curățare listă rezultate")
            self.results_list.clear()

            # Readăugare items cu verificare
            print("[DEBUG] Readăugare items sortate")
            for item in items:
                try:
                    list_item = QListWidgetItem()
                    list_item.setText(item)
                    
                    if os.path.exists(item):
                        try:
                            mtime = datetime.fromtimestamp(os.path.getmtime(item))
                            size = os.path.getsize(item) / 1024
                            tooltip = f"Folder: {os.path.dirname(item)}\n"
                            tooltip += f"Ultima modificare: {mtime.strftime('%d-%m-%Y %H:%M:%S')}\n"
                            tooltip += f"Dimensiune: {size:.1f} KB"
                            list_item.setToolTip(tooltip)
                        except Exception as info_error:
                            print(f"[DEBUG] Eroare la generare tooltip pentru {item}: {str(info_error)}")
                    
                    self.results_list.addItem(list_item)
                    
                except Exception as item_error:
                    print(f"[DEBUG] Eroare la adăugarea item-ului {item}: {str(item_error)}")

            print("[DEBUG] Sortare finalizată cu succes")
            
        except Exception as e:
            print(f"[DEBUG] Eroare generală în sort_results: {str(e)}")
            # Afișăm un mesaj de eroare către utilizator
            QMessageBox.warning(self, "Eroare la sortare", 
                              f"A apărut o eroare la sortarea rezultatelor: {str(e)}")

    def browse_folder(self):
        """Funcție pentru selectarea directorului"""
        folder = QFileDialog.getExistingDirectory(self, "Selectați directorul pentru indexare")
        if folder:
            self.current_folder = folder
            self.current_folder_label.setText(f"Folder: {folder}")
            self.refresh_button.setEnabled(True)
            self.reindex_modified_button.setEnabled(True)            
            self.save_config()

    def show_settings(self):
        """Afișează dialogul de setări"""
        try:
            print("[DEBUG] Începere creare dialog setări")
            dialog = SettingsDialog(self)
            print("[DEBUG] Dialog creat, încercare exec")
            result = dialog.exec_()
            if result:
                # Transmitem starea checkbox-ului către DatabaseManager
                self.db_manager.refresh_on_startup = self.refresh_on_startup.isChecked()
            print("[DEBUG] Dialog închis cu succes")
        except Exception as e:
            print(f"[DEBUG] Eroare la deschiderea dialogului de setări: {str(e)}")
            logger.error(f"Eroare la deschiderea dialogului de setări: {str(e)}")
            
    def apply_font_to_all_widgets(self, font: QFont):
        """Aplică fontul la toate widget-urile"""
        self.setFont(font)
        for widget in self.findChildren(QWidget):
            widget.setFont(font)
        
    def change_font_size(self, size):
        """Schimbă mărimea fontului pentru toată aplicația"""
        font = QFont('Arial', int(size))
        self.apply_font_to_all_widgets(font)
        self.save_config()
        
    def clear_index(self):
        """Curăță indexul existent"""
        reply = QMessageBox.question(self, 'Confirmare',
                                   'Sigur vrei să ștergi tot indexul?\nAceastă acțiune nu poate fi anulată.',
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.db_manager.clear_index()
            self.status_label.setText("Index curățat cu succes")
            self.update_stats(self.db_manager.get_document_stats())
            
    def load_config(self):
        """Încarcă configurația salvată"""
        try:
            if os.path.exists('config.json'):
                with open('config.json', 'r', encoding='utf-8') as f:
                    config = json.load(f)
                        
                    # Încarcă setările existente
                    if 'last_folder' in config:
                        folder = config['last_folder']
                        if folder and os.path.exists(folder):
                            self.current_folder = folder
                            self.current_folder_label.setText(f"Folder: {folder}")
                            self.refresh_button.setEnabled(True)
                        
                    if 'refresh_on_startup' in config:
                        refresh_on_startup = config['refresh_on_startup']
                        self.refresh_on_startup.setChecked(refresh_on_startup)
                        if refresh_on_startup and hasattr(self, 'current_folder') and os.path.exists(self.current_folder):
                            QTimer.singleShot(1000, self.refresh_current_folder)
                        
                    # Restaurează geometria ferestrei
                    if 'window_geometry' in config:
                        try:
                            self.restoreGeometry(bytes.fromhex(config['window_geometry']))
                        except Exception as e:
                            logger.error(f"Eroare la restaurare geometrie: {e}")
                        
                    # Restaurează dimensiunile splitter-ului
                    if 'horizontal_splitter_sizes' in config:
                        try:
                            sizes = config['horizontal_splitter_sizes']
                            self.horizontal_splitter.setSizes(sizes)
                        except Exception as e:
                            logger.error(f"Eroare la restaurare splitter orizontal: {e}")

                    if 'main_splitter_sizes' in config:
                        try:
                            sizes = config['main_splitter_sizes']
                            self.main_splitter.setSizes(sizes)
                        except Exception as e:
                            logger.error(f"Eroare la restaurare splitter principal: {e}")

                    # Restaurează lățimile coloanelor pentru stats_table
                    if 'stats_table_columns' in config:
                        try:
                            column_widths = config['stats_table_columns']
                            for i, width in enumerate(column_widths):
                                if i < self.stats_table.columnCount():
                                    self.stats_table.setColumnWidth(i, width)
                        except Exception as e:
                            logger.error(f"Eroare la restaurare lățimi coloane: {e}")
                        
                    # Încarcă setările pentru font și interfață
                    if 'font_size' in config:
                        size = str(config['font_size'])
                        if size in [str(x) for x in range(8, 25, 2)]:
                            self.font_size_combo.setCurrentText(size)
                            self.change_font_size(int(size))
                        
                    if 'index_mode' in config:
                        mode = config['index_mode']
                        if mode in ["Adaugă la index", "Înlocuiește index"]:
                            self.index_mode.setCurrentText(mode)
                        
                    if 'use_cache' in config:
                        self.use_cache = config['use_cache']

                    # Încarcă perioada de indexare parțială
                    if 'partial_index_days' in config:
                        self.partial_index_days = config['partial_index_days']
                        self.db_manager.partial_index_days = self.partial_index_days

                    # Încarcă perioada pentru fișiere modificate
                    if 'modified_files_hours' in config:
                        self.modified_files_hours = config['modified_files_hours']
                        self.db_manager.modified_files_hours = self.modified_files_hours
                        
        except Exception as e:
            logger.error(f"Eroare la încărcarea configurației: {e}")
            # Setări implicite în caz de eroare
            self.setGeometry(100, 100, 1400, 900)
        
    def save_config(self):
        """Salvează configurația curentă a interfeței în fișierul config.json"""
        try:
            current_folder = getattr(self, 'current_folder', '')
            
            # Construim dicționarul de configurare
            config = {
                # Geometria și starea ferestrei principale
                'window_geometry': bytes(self.saveGeometry()).hex(),
                'window_state': bytes(self.saveState()).hex(),
                'window_maximized': self.isMaximized(),
                'window_pos_x': self.pos().x(),
                'window_pos_y': self.pos().y(),
                'window_width': self.width(),
                'window_height': self.height(),
                
                # Setări font și interfață
                'font_size': int(self.font_size_combo.currentText()),
                'font_family': self.font().family(),
                
                # Setări pentru modul de indexare
                'index_mode': self.index_mode.currentText(),
                'use_cache': self.use_cache,
                'partial_index_days': self.partial_index_days,
                'modified_files_hours': self.modified_files_hours,
                
                # Setări folder și refresh
                'last_folder': current_folder,
                'refresh_on_startup': self.refresh_on_startup.isChecked(),
                
                # Stats table și splitter sizes
                'main_splitter_sizes': self.main_splitter.sizes(),
                'horizontal_splitter_sizes': self.horizontal_splitter.sizes(),
                'stats_table_columns': [self.stats_table.columnWidth(i) for i in range(self.stats_table.columnCount())],
                
                # Timestamp pentru validare
                'last_saved': datetime.now().isoformat(),
                'config_version': '1.0'
            }
            
            # Salvare atomică folosind fișier temporar
            temp_file = 'config.json.tmp'
            
            # Salvăm mai întâi în fișier temporar
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            
            # Înlocuim fișierul vechi cu cel nou
            if os.path.exists('config.json'):
                os.remove('config.json')
            os.rename(temp_file, 'config.json')
                
        except Exception as e:
            logger.error(f"Eroare la salvarea configurației: {e}")
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass

    def start_search(self):
        """Inițiază procesul de căutare"""
        query = self.search_input.text().strip()
        if not query:
            QMessageBox.warning(self, "Avertisment", "Introduceți textul pentru căutare!")
            return
                
        self.search_button.setEnabled(False)
        self.results_list.clear()
        self.progress_bar.setRange(0, 0)
        self.status_label.setText("Căutare în progres...")
            
        worker = SearchWorker(self.db_manager, query)
        worker.signals.finished.connect(self.handle_search_results)
        worker.signals.error.connect(self.handle_error)
        worker.signals.stats.connect(self.update_stats)
            
        self.thread_pool.start(worker)
            
    def handle_search_results(self, data: Tuple[List[str], float]):
        """Procesează rezultatele căutării cu suport pentru sortare"""
        results, search_time = data
        self.results_list.clear()
        
        # Sortăm rezultatele conform criteriului curent
        sort_method = self.sort_combo.currentText()
        
        if sort_method == "Sortare după nume":
            results.sort(key=lambda x: os.path.basename(x).lower())
        elif sort_method == "Sortare după folder":
            results.sort(key=lambda x: os.path.dirname(x).lower())
        elif sort_method == "Sortare după data modificării":
            results.sort(key=lambda x: os.path.getmtime(x) if os.path.exists(x) else 0, reverse=True)
        
        # Adăugăm rezultatele sortate în listă
        for result in results:
            list_item = QListWidgetItem()
            list_item.setText(result)
            
            # Adăugăm tooltip cu informații suplimentare
            if os.path.exists(result):
                mtime = datetime.fromtimestamp(os.path.getmtime(result))
                size = os.path.getsize(result) / 1024  # mărime în KB
                tooltip = f"Folder: {os.path.dirname(result)}\n"
                tooltip += f"Ultima modificare: {mtime.strftime('%d-%m-%Y %H:%M:%S')}\n"
                tooltip += f"Dimensiune: {size:.1f} KB"
                list_item.setToolTip(tooltip)
                
            self.results_list.addItem(list_item)
        
        self.search_button.setEnabled(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        
        # Formatăm timpul în funcție de durată
        if search_time < 1:
            time_str = f"{search_time*1000:.0f} milisecunde"
        else:
            time_str = f"{search_time:.2f} secunde"
        
        self.status_label.setText(f"Găsite {len(results)} rezultate în {time_str}")
        
    def start_indexing(self):
        """Inițiază procesul de indexare"""
        if hasattr(self, 'current_folder') and os.path.exists(self.current_folder):
            folder = self.current_folder
            reply = QMessageBox.question(self, 'Confirmare',
                                       f'Doriți să reindexați folderul curent?\n{folder}',
                                       QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.No:
                new_folder = QFileDialog.getExistingDirectory(self, "Selectați directorul pentru indexare")
                if new_folder:
                    folder = new_folder
                else:
                    return
        else:
            folder = QFileDialog.getExistingDirectory(self, "Selectați directorul pentru indexare")
            if not folder:
                return
        
        # Actualizăm folderul curent și interfața
        self.current_folder = folder
        self.current_folder_label.setText(f"Folder: {folder}")
        self.refresh_button.setEnabled(True)
        
        # Salvăm configurația pentru a reține noul folder
        self.save_config()
        
        # Începem procesul de indexare    
        self._start_indexing_process(folder)        

    def update_indexing_progress(self, progress: int, current_file: str):
        """Actualizează progresul indexării"""
        self.progress_bar.setValue(progress)
        
        # Extragem timpul din mesaj dacă există
        if "Timp scurs:" in current_file:
            parts = current_file.split("Timp scurs:")
            file_info = parts[0]
            try:
                seconds = int(parts[1].strip().split()[0])
                # Formatare timp
                if seconds < 60:
                    time_str = f"{seconds} secunde"
                elif seconds < 3600:
                    minutes = seconds // 60
                    remaining_seconds = seconds % 60
                    time_str = f"{minutes} minute și {remaining_seconds} secunde"
                else:
                    hours = seconds // 3600
                    remaining_time = seconds % 3600
                    minutes = remaining_time // 60
                    remaining_seconds = remaining_time % 60
                    
                    time_parts = []
                    if hours > 0:
                        time_parts.append(f"{hours} ore")
                    if minutes > 0:
                        time_parts.append(f"{minutes} minute")
                    if remaining_seconds > 0:
                        time_parts.append(f"{remaining_seconds} secunde")
                    time_str = " și ".join(time_parts)
                
                current_file = f"{file_info}Timp scurs: {time_str}"
            except:
                pass
                
        self.status_label.setText(f"Indexare: {current_file}")
        
    def indexing_finished(self, data: Tuple[int, float]):
        """Se apelează când procesul de indexare s-a terminat"""
        indexed_files, total_time = data
        self.quick_index_button.setEnabled(True)
        self.full_index_button.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.reindex_modified_button.setEnabled(True)
        
        # Formatăm timpul în funcție de durată
        hours = int(total_time // 3600)
        minutes = int((total_time % 3600) // 60)
        seconds = int(total_time % 60)
        
        parts = []
        if hours > 0:
            parts.append(f"{hours} ore")
        if minutes > 0:
            parts.append(f"{minutes} minute")
        if seconds > 0 or not parts:  # includem secundele dacă există sau dacă nu avem ore/minute
            parts.append(f"{seconds} secunde")
            
        time_str = " și ".join(parts)
        
        index_type = "rapidă" if self.db_manager.refresh_on_startup else "completă"
        self.status_label.setText(f"Indexare {index_type} completă: {indexed_files} fișiere procesate în {time_str}")
        self.progress_bar.setValue(100)
        self.save_config()
        
    def handle_error(self, error_message: str):
        """Gestionează erorile"""
        QMessageBox.warning(self, "Eroare", error_message)
        logger.error(error_message)
        
        self.search_button.setEnabled(True)
        self.index_button.setEnabled(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.status_label.setText("Eroare: " + error_message)
        
    def update_stats(self, stats: Dict):
        """Actualizează statisticile afișate"""
        self.stats_table.setRowCount(0)
        
        # Definim ordinea dorită pentru statistici
        stat_order = [
            'Total documente',
            'Dimensiune totală (MB)',
            'CNP-uri unice',
            f'Fișiere modificate (ultimele {self.modified_files_hours} ore)',  # Modificat aici
            'Fișiere noi adăugate'
        ]
        
        # Adăugăm statisticile în ordinea specificată
        for key in stat_order:
            if key in stats:
                row = self.stats_table.rowCount()
                self.stats_table.insertRow(row)
                
                # Creăm item-ul pentru prima coloană (numele statisticii)
                name_item = QTableWidgetItem(str(key))
                
                # Dacă este o statistică pe care vrem să fie clickabilă
                if key.startswith('Fișiere modificate') or key == 'Fișiere noi adăugate':  # Modificat aici
                    name_item.setForeground(QBrush(QColor('blue')))
                    name_item.setFlags(name_item.flags() | Qt.ItemIsSelectable)
                    name_item.setToolTip('Click pentru a vedea fișierele')
                
                self.stats_table.setItem(row, 0, name_item)
                self.stats_table.setItem(row, 1, QTableWidgetItem(str(stats[key])))
        
        # Conectăm semnalul de click dacă nu este deja conectat
        try:
            self.stats_table.itemClicked.disconnect()
        except:
            pass
        self.stats_table.itemClicked.connect(self.handle_stat_click)
        
    def handle_stat_click(self, item):
        """Gestionează click-ul pe statistici"""
        if item.column() == 0:  # Doar pentru prima coloană
            stat_name = item.text()
            
            if stat_name.startswith('Fișiere modificate'):  # Modificat aici
                files = self.db_manager.get_recently_modified_files()
                self.display_file_list(files, stat_name)
                
            elif stat_name == 'Fișiere noi adăugate':
                files = self.db_manager.get_newly_added_files()
                self.display_file_list(files, "Fișiere noi adăugate în această sesiune")

    def display_file_list(self, files: List[str], title: str):
        """Afișează lista de fișiere în zona de rezultate"""
        self.results_list.clear()
        self.results_list.addItems(files)
        self.status_label.setText(f"{title}: {len(files)} fișiere")
            
    def open_file(self, item):
        """Deschide fișierul selectat"""
        try:
            os.startfile(item.text())
        except Exception as e:
            QMessageBox.warning(self, "Eroare", f"Nu s-a putut deschide fișierul: {str(e)}")
            
    def perform_maintenance(self):
        """Efectuează operații de întreținere"""
        try:
            self.db_manager.clean_old_cache()
            self.save_config()
        except Exception as e:
            logger.error(f"Maintenance error: {e}")
            
    def cleanup_and_save(self):
        """Salvează configurația și închide conexiunile în mod sigur"""
        try:
            # Salvăm dimensiunile splitter-urilor
            splitter_sizes = self.main_splitter.sizes()
            horizontal_splitter_sizes = self.horizontal_splitter.sizes()
            
            config = {
                'window_geometry': self.saveGeometry().toHex().decode(),
                'window_state': self.saveState().toHex().decode(),
                'font_size': int(self.font_size_combo.currentText()),
                'index_mode': self.index_mode.currentText(),
                'main_splitter_sizes': splitter_sizes,
                'horizontal_splitter_sizes': horizontal_splitter_sizes
            }
            with open('config.json', 'w', encoding='utf-8') as f:
                json.dump(config, f)
                
            # Oprim timerul de mentenanță
            self.maintenance_timer.stop()
            
            # Așteptăm să se termine toate operațiile din thread pool
            self.thread_pool.waitForDone()
            
            # Închide conexiunile la baza de date
            self.db_manager.close_connections()
            
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    def closeEvent(self, event):
        """Gestionează închiderea aplicației"""
        self.save_config()  # Salvează configurația înainte de închidere
        self.cleanup_and_save()
        event.accept()

    def resizeEvent(self, event):
        """Gestionează evenimentul de redimensionare a ferestrei"""
        super().resizeEvent(event)
        self.save_config()  # Salvează configurația la redimensionare

    def moveEvent(self, event):
        """Gestionează evenimentul de mutare a ferestrei"""
        super().moveEvent(event)
        self.save_config()  # Salvează configurația la mutare

    def refresh_current_folder(self):
        """Reindexează folderul curent"""
        if hasattr(self, 'current_folder') and os.path.exists(self.current_folder):
            self._start_indexing_process(self.current_folder)
        else:
            QMessageBox.warning(self, "Eroare", 
                              "Nu există un folder valid selectat pentru reindexare!")
            self.refresh_button.setEnabled(False)

    def reindex_modified_files(self):
        """Reindexează fișierele modificate în perioada configurată"""
        if not hasattr(self, 'current_folder') or not os.path.exists(self.current_folder):
            QMessageBox.warning(self, "Eroare", "Nu există un folder valid selectat!")
            return

        reply = QMessageBox.question(
            self, 
            'Confirmare',
            f'Doriți să reindexați fișierele modificate în ultimele {self.modified_files_hours} ore din folderul:\n{self.current_folder}?',
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.No:
            return

        # Dezactivăm temporar butoanele în timpul procesării
        self.quick_index_button.setEnabled(False)
        self.full_index_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        
        # Setăm starea pentru verificarea fișierelor modificate
        self.db_manager.current_folder = self.current_folder
        
        # Creăm un worker pentru reindexarea fișierelor modificate
        worker = IndexWorker(self.db_manager, self.current_folder, False)
        worker.signals.progress.connect(self.update_indexing_progress)
        worker.signals.finished.connect(self.indexing_finished)
        worker.signals.error.connect(self.handle_error)
        worker.signals.stats.connect(self.update_stats)
        
        self.status_label.setText(f"Reindexare fișiere modificate în ultimele {self.modified_files_hours} ore...")
        self.thread_pool.start(worker)

    def start_quick_indexing(self):
        """Inițiază procesul de indexare rapidă"""
        if hasattr(self, 'current_folder') and os.path.exists(self.current_folder):
            folder = self.current_folder
            reply = QMessageBox.question(
                self, 
                'Confirmare',
                f'Doriți să indexați rapid documentele din ultimele {self.partial_index_days} zile din folderul:\n{folder}?',
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.No:
                new_folder = QFileDialog.getExistingDirectory(self, "Selectați directorul pentru indexare")
                if new_folder:
                    folder = new_folder
                else:
                    return
        else:
            folder = QFileDialog.getExistingDirectory(self, "Selectați directorul pentru indexare")
            if not folder:
                return
        
        # Actualizăm folderul curent și interfața
        self.current_folder = folder
        self.current_folder_label.setText(f"Folder: {folder}")
        self.refresh_button.setEnabled(True)
        self.save_config()
        
        # Setăm flag-ul pentru indexare rapidă și pornim procesul
        self.db_manager.refresh_on_startup = True
        self._start_indexing_process(folder)

    def start_full_indexing(self):
        """Inițiază procesul de indexare completă"""
        if hasattr(self, 'current_folder') and os.path.exists(self.current_folder):
            folder = self.current_folder
            reply = QMessageBox.question(
                self, 
                'Confirmare',
                f'ATENȚIE! Se va reindexata COMPLET folderul:\n{folder}\n\n' + 
                'Acest proces poate dura mult timp. Doriți să continuați?',
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.No:
                new_folder = QFileDialog.getExistingDirectory(self, "Selectați directorul pentru indexare")
                if new_folder:
                    folder = new_folder
                else:
                    return
        else:
            folder = QFileDialog.getExistingDirectory(self, "Selectați directorul pentru indexare")
            if not folder:
                return
        
        # Actualizăm folderul curent și interfața
        self.current_folder = folder
        self.current_folder_label.setText(f"Folder: {folder}")
        self.refresh_button.setEnabled(True)
        self.save_config()
        
        # Dezactivăm flag-ul pentru indexare rapidă și pornim procesul
        self.db_manager.refresh_on_startup = False
        self._start_indexing_process(folder)

    def continue_full_indexing(self):
        """Continuă indexarea completă de la ultimul punct de întrerupere"""
        if not hasattr(self, 'current_folder') or not os.path.exists(self.current_folder):
            QMessageBox.warning(self, "Eroare", 
                              "Nu există un folder valid selectat pentru continuarea indexării!")
            return

        reply = QMessageBox.question(
            self, 
            'Confirmare',
            f'Doriți să continuați indexarea completă pentru folderul:\n{self.current_folder}\n\n' + 
            'Se va continua de la ultimul punct de întrerupere.',
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.No:
            return

        # Actualizăm folderul curent și interfața
        self.current_folder = self.current_folder
        self.current_folder_label.setText(f"Folder: {self.current_folder}")
        self.refresh_button.setEnabled(True)
        self.save_config()
        
        # Dezactivăm flag-ul pentru indexare rapidă și pornim procesul
        self.db_manager.refresh_on_startup = False
        
        # Dezactivăm temporar butoanele în timpul procesării
        self.quick_index_button.setEnabled(False)
        self.full_index_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.reindex_modified_button.setEnabled(False)
        self.continue_index_button.setEnabled(False)
        
        self.progress_bar.setValue(0)
        
        # Creăm worker-ul pentru continuarea indexării
        worker = IndexWorker(self.db_manager, self.current_folder, False)
        worker.signals.progress.connect(self.update_indexing_progress)
        worker.signals.finished.connect(self.indexing_finished)
        worker.signals.error.connect(self.handle_error)
        worker.signals.stats.connect(self.update_stats)
        
        self.status_label.setText("Continuare indexare completă în progres...")
        self.thread_pool.start(worker)

    def _start_indexing_process(self, folder: str):
        """Pornește procesul de indexare pentru un folder"""
        self.db_manager.current_folder = folder
        self.quick_index_button.setEnabled(False)  # Dezactivăm ambele butoane
        self.full_index_button.setEnabled(False)   # în timpul procesării
        self.refresh_button.setEnabled(False)
        self.progress_bar.setValue(0)
        
        index_type = "rapidă" if self.db_manager.refresh_on_startup else "completă"
        self.status_label.setText(f"Indexare {index_type} în progres...")
        
        replace_index = self.index_mode.currentText() == "Înlocuiește index"
        worker = IndexWorker(self.db_manager, folder, replace_index)
        worker.signals.progress.connect(self.update_indexing_progress)
        worker.signals.finished.connect(self.indexing_finished)
        worker.signals.error.connect(self.handle_error)
        worker.signals.stats.connect(self.update_stats)
        
        self.thread_pool.start(worker)

    def indexing_finished(self, data: Tuple[int, float]):
        """Se apelează când procesul de indexare s-a terminat"""
        indexed_files, total_time = data
        self.quick_index_button.setEnabled(True)
        self.full_index_button.setEnabled(True)
        self.refresh_button.setEnabled(True)
        
        # Formatăm timpul în funcție de durată
        hours = int(total_time // 3600)
        minutes = int((total_time % 3600) // 60)
        seconds = int(total_time % 60)
        
        parts = []
        if hours > 0:
            parts.append(f"{hours} ore")
        if minutes > 0:
            parts.append(f"{minutes} minute")
        if seconds > 0 or not parts:  # includem secundele dacă există sau dacă nu avem ore/minute
            parts.append(f"{seconds} secunde")
            
        time_str = " și ".join(parts)
        
        index_type = "rapidă" if self.db_manager.refresh_on_startup else "completă"
        self.status_label.setText(f"Indexare {index_type} completă: {indexed_files} fișiere procesate în {time_str}")
        self.progress_bar.setValue(100)
        self.save_config()

def main():
    """Funcția principală"""
    try:
        app = QApplication(sys.argv)
        app.setStyle('Fusion')
        
        window = SearchApp()
        window.show()
        
        sys.exit(app.exec_())
        
    except Exception as e:
        logger.critical(f"Critical error: {str(e)}")
        QMessageBox.critical(None, "Eroare Critică", 
                           f"A apărut o eroare critică: {str(e)}\n\n"
                           f"Verificați fișierul log pentru detalii.")
        sys.exit(1)

if __name__ == '__main__':
    main()