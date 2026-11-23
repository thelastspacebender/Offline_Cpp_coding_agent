"""
SQLite Database Connection and Schema Management.
Uses built-in SQLite with FTS5 for zero-dependency BM25 full-text search.
"""

import os
import sqlite3
from typing import Optional

DB_FILE_NAME = "rag_index.db"


class DatabaseManager:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.db_path = os.path.join(base_dir, DB_FILE_NAME)
        else:
            self.db_path = db_path
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self.get_connection() as conn:
            cur = conn.cursor()

            # Table for indexed files metadata
            cur.execute("""
                CREATE TABLE IF NOT EXISTS indexed_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT UNIQUE,
                    encoding TEXT,
                    line_count INTEGER,
                    mtime REAL
                );
            """)

            # Table for symbols (functions, structs, classes, macros)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS symbols (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    kind TEXT,
                    file_path TEXT,
                    line_start INTEGER,
                    line_end INTEGER,
                    signature TEXT,
                    parent_scope TEXT,
                    body TEXT
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_path);")

            # Table for Call Graph relations (caller -> callee)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS call_relations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    caller_symbol TEXT,
                    callee_name TEXT,
                    file_path TEXT,
                    line_number INTEGER,
                    call_snippet TEXT
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_calls_callee ON call_relations(callee_name);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_calls_caller ON call_relations(caller_symbol);")

            # FTS5 Virtual Table for BM25 ranking across code snippets and symbols
            cur.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS rag_fts USING fts5(
                    file_path UNINDEXED,
                    line_start UNINDEXED,
                    line_end UNINDEXED,
                    symbol_name,
                    symbol_kind,
                    content,
                    tokenize='porter unicode61'
                );
            """)

            # Table for Adaptive Semantic File Map (cached by file_mtime)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS file_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    section_id TEXT NOT NULL DEFAULT 'file',
                    line_start INTEGER DEFAULT 1,
                    line_end INTEGER DEFAULT 1,
                    role_tag TEXT NOT NULL DEFAULT 'core',
                    summary TEXT NOT NULL,
                    key_symbols TEXT DEFAULT '',
                    file_mtime REAL DEFAULT 0,
                    model_name TEXT DEFAULT '',
                    UNIQUE(file_path, section_id)
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_file_summaries_path ON file_summaries(file_path);")
            conn.commit()

    def clear(self, keep_summaries: bool = True):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM indexed_files;")
            cur.execute("DELETE FROM symbols;")
            cur.execute("DELETE FROM call_relations;")
            cur.execute("DELETE FROM rag_fts;")
            if not keep_summaries:
                cur.execute("DELETE FROM file_summaries;")
            conn.commit()
