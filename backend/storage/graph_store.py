"""
Symbol Table and Call-Graph Storage.
Provides instant lookup for definitions, callers, callees, fuzzy matching,
typo resolution, and project architecture summaries.
"""

import os
import re
import difflib
from typing import List, Optional, Dict, Any, Tuple, Set
from ..core.models import SymbolDefinition, CallRelation
from .sqlite_db import DatabaseManager


class GraphStore:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self._all_symbols_cache: Optional[List[Tuple[str, str]]] = None  # (name, name_lower)
        self._all_files_cache: Optional[List[Tuple[str, str, str]]] = None  # (file_path, base_name, base_name_lower)
        self._project_map_cache: Optional[str] = None

    def _ensure_caches(self):
        """Loads unique symbol names and file paths into memory for fast fuzzy matching."""
        if self._all_symbols_cache is not None and self._all_files_cache is not None:
            return

        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT name FROM symbols;")
            self._all_symbols_cache = [(r[0], r[0].lower()) for r in cur.fetchall()]

            cur.execute("SELECT DISTINCT file_path FROM indexed_files;")
            files = []
            for r in cur.fetchall():
                fp = r[0]
                base = os.path.basename(fp)
                files.append((fp, base, base.lower()))
            self._all_files_cache = files

    def invalidate_cache(self):
        """Clears in-memory caches when project is re-indexed."""
        self._all_symbols_cache = None
        self._all_files_cache = None
        self._project_map_cache = None

    def save_file_data(
        self,
        file_path: str,
        encoding: str,
        line_count: int,
        symbols: List[SymbolDefinition],
        calls: List[CallRelation]
    ):
        with self.db.get_connection() as conn:
            cur = conn.cursor()

            # Record file
            cur.execute("""
                INSERT OR REPLACE INTO indexed_files (file_path, encoding, line_count, mtime)
                VALUES (?, ?, ?, datetime('now'));
            """, (file_path, encoding, line_count))

            # Record symbols
            for s in symbols:
                cur.execute("""
                    INSERT INTO symbols (name, kind, file_path, line_start, line_end, signature, parent_scope, body)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """, (s.name, s.kind, s.file_path, s.line_start, s.line_end, s.signature, s.parent_scope, s.body))

            # Record call relations
            for c in calls:
                cur.execute("""
                    INSERT INTO call_relations (caller_symbol, callee_name, file_path, line_number, call_snippet)
                    VALUES (?, ?, ?, ?, ?);
                """, (c.caller_symbol, c.callee_name, c.file_path, c.line_number, c.call_snippet))

            conn.commit()
        self.invalidate_cache()

    def get_callers(self, callee_name: str, limit: int = 50) -> List[CallRelation]:
        """Finds all places where callee_name is called across the codebase."""
        short_name = callee_name.split("::")[-1]
        results: List[CallRelation] = []

        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT caller_symbol, callee_name, file_path, line_number, call_snippet
                FROM call_relations
                WHERE callee_name = ? OR callee_name = ?
                LIMIT ?;
            """, (callee_name, short_name, limit))

            for row in cur.fetchall():
                results.append(CallRelation(
                    caller_symbol=row["caller_symbol"],
                    callee_name=row["callee_name"],
                    file_path=row["file_path"],
                    line_number=row["line_number"],
                    call_snippet=row["call_snippet"]
                ))
        return results

    def get_callees(self, caller_symbol: str, limit: int = 50) -> List[CallRelation]:
        """Finds all functions called by caller_symbol."""
        results: List[CallRelation] = []
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT caller_symbol, callee_name, file_path, line_number, call_snippet
                FROM call_relations
                WHERE caller_symbol = ? OR caller_symbol LIKE ?
                LIMIT ?;
            """, (caller_symbol, f"%::{caller_symbol}", limit))

            for row in cur.fetchall():
                results.append(CallRelation(
                    caller_symbol=row["caller_symbol"],
                    callee_name=row["callee_name"],
                    file_path=row["file_path"],
                    line_number=row["line_number"],
                    call_snippet=row["call_snippet"]
                ))
        return results

    def get_definition(self, symbol_name: str) -> Optional[SymbolDefinition]:
        """Finds the definition of a symbol (function, struct, class, macro)."""
        short_name = symbol_name.split("::")[-1]
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT name, kind, file_path, line_start, line_end, signature, parent_scope, body
                FROM symbols
                WHERE name = ? OR name LIKE ?
                ORDER BY (CASE WHEN kind IN ('function', 'method') THEN 1 ELSE 2 END)
                LIMIT 1;
            """, (symbol_name, f"%::{short_name}"))

            row = cur.fetchone()
            if row:
                return SymbolDefinition(
                    name=row["name"],
                    kind=row["kind"],
                    file_path=row["file_path"],
                    line_start=row["line_start"],
                    line_end=row["line_end"],
                    signature=row["signature"],
                    parent_scope=row["parent_scope"],
                    body=row["body"]
                )
        return None

    def search_symbols(self, query: str, limit: int = 15) -> List[SymbolDefinition]:
        """Substring / pattern search for symbols."""
        results: List[SymbolDefinition] = []
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT name, kind, file_path, line_start, line_end, signature, parent_scope, body
                FROM symbols
                WHERE name LIKE ?
                LIMIT ?;
            """, (f"%{query}%", limit))

            for row in cur.fetchall():
                results.append(SymbolDefinition(
                    name=row["name"],
                    kind=row["kind"],
                    file_path=row["file_path"],
                    line_start=row["line_start"],
                    line_end=row["line_end"],
                    signature=row["signature"],
                    parent_scope=row["parent_scope"],
                    body=row["body"]
                ))
        return results

    def get_class_methods(self, class_name: str, limit: int = 15) -> List[SymbolDefinition]:
        """
        Retrieves member methods of a class sorted by business importance.
        Prioritizes functional methods (Connect, Read, Write, Send, Recv, Exchange, Process, Init, Fill, Register)
        and longer body implementations over empty constructors/destructors (~).
        """
        clean_cls = re.sub(r'^[&*~;\s]+', '', class_name).strip()
        results: List[SymbolDefinition] = []
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT name, kind, file_path, line_start, line_end, signature, parent_scope, body
                FROM symbols
                WHERE (parent_scope = ? OR name LIKE ? OR name LIKE ?)
                ORDER BY
                    (CASE WHEN name LIKE '%~%' THEN 100 ELSE 1 END) ASC,
                    (CASE 
                        WHEN name LIKE '%Connect%' OR name LIKE '%Read%' OR name LIKE '%Write%'
                          OR name LIKE '%Send%' OR name LIKE '%Recv%' OR name LIKE '%Exchange%'
                          OR name LIKE '%Process%' OR name LIKE '%Init%' OR name LIKE '%Register%'
                          OR name LIKE '%Fill%' OR name LIKE '%Set%' OR name LIKE '%Get%'
                          OR name LIKE '%Packet%' OR name LIKE '%Open%' OR name LIKE '%Close%'
                        THEN 0 ELSE 1 END) ASC,
                    LENGTH(body) DESC,
                    line_start ASC
                LIMIT ?;
            """, (clean_cls, f"{clean_cls}::%", f"%::{clean_cls}::%", limit))

            for row in cur.fetchall():
                results.append(SymbolDefinition(
                    name=row["name"],
                    kind=row["kind"],
                    file_path=row["file_path"],
                    line_start=row["line_start"],
                    line_end=row["line_end"],
                    signature=row["signature"] or "",
                    parent_scope=row["parent_scope"] or "",
                    body=row["body"] or ""
                ))
        return results

    def fuzzy_search(self, terms: List[str], limit: int = 15) -> Tuple[List[SymbolDefinition], Dict[str, str]]:
        """
        Fuzzy matching across symbol names and file paths with typo correction tracking.
        Only runs difflib (typo correction) when exact and substring match return 0 hits.
        Returns: (matched_definitions, typo_corrections_map)
        """
        self._ensure_caches()
        results: List[SymbolDefinition] = []
        seen_keys: Set[Tuple[str, int]] = set()
        typo_corrections: Dict[str, str] = {}

        if not terms:
            return results, typo_corrections

        symbol_names_lower = [s[1] for s in (self._all_symbols_cache or [])]
        file_bases_lower = [f[2] for f in (self._all_files_cache or [])]

        for term in terms:
            # Clean garbage prefixes (& ptr -> ptr, C::GetClient -> GetClient)
            clean_term = re.sub(r'^[&*~;\s]+', '', term).strip()
            clean_term = re.sub(r'^(?:const|virtual|static)\s+', '', clean_term).strip()
            if len(clean_term) < 3:
                continue

            term_clean_lower = clean_term.lower()

            # 1. Exact or Substring Match first (SQL search)
            term_matches = []
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT name, kind, file_path, line_start, line_end, signature, parent_scope, body
                    FROM symbols
                    WHERE name = ? OR name LIKE ? OR file_path LIKE ?
                    ORDER BY (CASE WHEN kind IN ('class', 'struct') THEN 0 ELSE 1 END),
                             (CASE WHEN name LIKE '%~%' THEN 100 ELSE 1 END),
                             LENGTH(body) DESC
                    LIMIT 8;
                """, (clean_term, f"%{clean_term}%", f"%{clean_term}%"))
                term_matches = cur.fetchall()

            # If term already exists as exact or substring match in symbols or files:
            # DO NOT alter it! DO NOT apply difflib to corrupt it!
            if term_matches:
                for row in term_matches:
                    key = (row["file_path"], row["line_start"])
                    if key not in seen_keys:
                        seen_keys.add(key)
                        results.append(SymbolDefinition(
                            name=row["name"],
                            kind=row["kind"],
                            file_path=row["file_path"],
                            line_start=row["line_start"],
                            line_end=row["line_end"],
                            signature=row["signature"] or "",
                            parent_scope=row["parent_scope"] or "",
                            body=row["body"] or ""
                        ))
                continue

            # 2. ONLY if exact/substring search yielded ZERO results:
            # Check for typos via difflib (e.g. 'soket' -> 'Socket')
            # First in file names:
            matched_files = []
            for fp, base, base_lower in (self._all_files_cache or []):
                if term_clean_lower in base_lower:
                    matched_files.append((fp, base))
                elif difflib.SequenceMatcher(None, term_clean_lower, base_lower.replace(".cpp", "").replace(".h", "")).quick_ratio() > 0.78:
                    matched_files.append((fp, base))
                    typo_corrections[term] = base

            if matched_files:
                with self.db.get_connection() as conn:
                    cur = conn.cursor()
                    for fp, base in matched_files[:3]:
                        cur.execute("""
                            SELECT name, kind, file_path, line_start, line_end, signature, parent_scope, body
                            FROM symbols
                            WHERE file_path = ?
                            ORDER BY (CASE WHEN kind IN ('class', 'struct') THEN 0 ELSE 1 END),
                                     (CASE WHEN name LIKE '%~%' THEN 100 ELSE 1 END),
                                     line_start ASC
                            LIMIT 6;
                        """, (fp,))
                        for row in cur.fetchall():
                            key = (row["file_path"], row["line_start"])
                            if key not in seen_keys:
                                seen_keys.add(key)
                                results.append(SymbolDefinition(
                                    name=row["name"],
                                    kind=row["kind"],
                                    file_path=row["file_path"],
                                    line_start=row["line_start"],
                                    line_end=row["line_end"],
                                    signature=row["signature"] or "",
                                    parent_scope=row["parent_scope"] or "",
                                    body=row["body"] or ""
                                ))
                continue

            # In symbol names via difflib (strict cutoff=0.78)
            close_matches = difflib.get_close_matches(term_clean_lower, symbol_names_lower, n=4, cutoff=0.78)
            for cm in close_matches:
                for orig, lower in (self._all_symbols_cache or []):
                    if lower == cm:
                        if orig.lower() != term_clean_lower:
                            # Clean prefix from correction target
                            clean_orig = re.sub(r'^[&*~;\s]+', '', orig).strip()
                            typo_corrections[term] = clean_orig
                        d = self.get_definition(orig)
                        if d:
                            key = (d.file_path, d.line_start)
                            if key not in seen_keys:
                                seen_keys.add(key)
                                results.append(d)
                        break

        return results[:limit], typo_corrections

    def get_compact_project_map(self) -> str:
        """
        Generates and caches a compact architectural passport (~1200 chars).
        Fully dynamic: extracts modules, top files, and top classes from the indexed database.
        """
        if self._project_map_cache is not None:
            return self._project_map_cache

        stats = self.get_stats()
        with self.db.get_connection() as conn:
            cur = conn.cursor()

            # Modules and primary files
            cur.execute("SELECT file_path FROM indexed_files;")
            all_files = [r[0] for r in cur.fetchall()]

            modules = set()
            for fp in all_files:
                norm_fp = fp.replace('\\', '/')
                parts = norm_fp.split('/')
                if len(parts) >= 2:
                    mod = parts[-2]
                    modules.add(mod)

            sorted_modules = sorted(list(modules))[:15]
            # Key files ranked purely dynamically by indexed symbol count
            cur.execute("""
                SELECT file_path, COUNT(*) as sym_cnt
                FROM symbols
                GROUP BY file_path
                ORDER BY sym_cnt DESC
                LIMIT 20;
            """)
            top_files = [os.path.basename(r[0]) for r in cur.fetchall()]
            if not top_files:
                top_files = [os.path.basename(f) for f in all_files[:20]]

            # Top classes and structs across architecture ordered dynamically by size and presence
            cur.execute("""
                SELECT name, kind, file_path
                FROM symbols
                WHERE kind IN ('class', 'struct')
                ORDER BY length(body) DESC
                LIMIT 50;
            """)
            key_classes = []
            seen_classes = set()
            for r in cur.fetchall():
                clean_cls = re.sub(r'^[&*~;\s]+', '', r[0]).strip()
                if clean_cls in seen_classes or len(clean_cls) < 3:
                    continue
                seen_classes.add(clean_cls)
                fp_short = os.path.basename(r[2])
                key_classes.append(f"{clean_cls} ({r[1]} in {fp_short})")

        lines = [
            f"• Статистика проекта: {stats['files_count']} файлов, {stats['symbols_count']} символов, {stats['calls_count']} связей вызовов.",
            f"• Основные модули/каталоги: {', '.join(sorted_modules)}.",
            f"• Ключевые файлы исходного кода: {', '.join(top_files)}.",
            f"• Основные классы и компоненты архитектуры:\n  " + ", ".join(key_classes[:30])
        ]
        self._project_map_cache = "\n".join(lines)
        return self._project_map_cache

    def get_entry_points(self) -> List[SymbolDefinition]:
        """Finds main entry points (InitInstance, WinMain, main, OnInitDialog, constructors)."""
        results: List[SymbolDefinition] = []
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT name, kind, file_path, line_start, line_end, signature, parent_scope, body
                FROM symbols
                WHERE name LIKE '%InitInstance%' OR name LIKE '%OnInitDialog%'
                   OR name LIKE '%WinMain%' OR name = 'main'
                ORDER BY (CASE WHEN name LIKE '%InitInstance%' THEN 0 ELSE 1 END)
                LIMIT 6;
            """)
            for row in cur.fetchall():
                results.append(SymbolDefinition(
                    name=row["name"],
                    kind=row["kind"],
                    file_path=row["file_path"],
                    line_start=row["line_start"],
                    line_end=row["line_end"],
                    signature=row["signature"] or "",
                    parent_scope=row["parent_scope"] or "",
                    body=row["body"] or ""
                ))
        return results

    def get_stats(self) -> Dict[str, Any]:
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM indexed_files;")
            files_count = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM symbols;")
            symbols_count = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM call_relations;")
            calls_count = cur.fetchone()[0]

            return {
                "files_count": files_count,
                "symbols_count": symbols_count,
                "calls_count": calls_count
            }

    def get_project_overview(self) -> Dict[str, Any]:
        """Returns top modules, key classes, and files for high-level architecture inquiries."""
        with self.db.get_connection() as conn:
            cur = conn.cursor()

            cur.execute("""
                SELECT name, kind, file_path, line_start, signature, body
                FROM symbols
                WHERE kind IN ('class', 'struct')
                ORDER BY (CASE WHEN name LIKE 'C%App' OR name LIKE 'C%Dlg' OR name LIKE '%Client%' OR name LIKE '%Manager%' THEN 0 ELSE 1 END),
                         length(body) DESC
                LIMIT 25;
            """)
            classes = [
                SymbolDefinition(
                    name=row["name"],
                    kind=row["kind"],
                    file_path=row["file_path"],
                    line_start=row["line_start"],
                    line_end=row["line_start"],
                    signature=row["signature"] or "",
                    body=row["body"] or ""
                )
                for row in cur.fetchall()
            ]

            cur.execute("SELECT DISTINCT file_path FROM indexed_files LIMIT 300;")
            modules = set()
            for r in cur.fetchall():
                fp = r["file_path"].replace('\\', '/')
                parts = fp.split('/')
                if len(parts) >= 3:
                    modules.add(parts[-2])

            return {
                "classes": classes,
                "modules": sorted(list(modules))[:15]
            }
