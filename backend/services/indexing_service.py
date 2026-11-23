"""
Indexing Service.
Orchestrates file scanning, multi-encoding reading, AST parsing,
and storage into SQLite FTS5 and the Call Graph.
"""

import time
import os
from typing import Dict, Any

from ..core.file_scanner import scan_source_files
from ..core.encoding_utils import read_file_safe
from ..core.agent_logger import log_agent_line
from ..parser.ast_visitor import parse_code
from ..storage.sqlite_db import DatabaseManager
from ..storage.fts_index import FtsIndex
from ..storage.graph_store import GraphStore


class IndexingService:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.fts = FtsIndex(db_manager)
        self.graph = GraphStore(db_manager)

    def index_directory(self, root_path: str) -> Dict[str, Any]:
        """
        Indexes an entire solution or project directory.
        Filters out .pch, .obj, .pdb, Debug/, Release/.
        """
        start_time = time.time()
        root_path = os.path.abspath(root_path)

        if not os.path.exists(root_path):
            return {
                "success": False,
                "message": f"Каталог не найден: {root_path}",
                "files_count": 0,
                "symbols_count": 0,
                "calls_count": 0,
                "duration_seconds": 0.0
            }

        # Clear previous index
        self.db.clear()

        # Scan valid C/C++ source files
        source_files = scan_source_files(root_path)
        log_agent_line(f"[{time.strftime('%H:%M:%S')}] 📁 [Indexer] Найдено исходников: {len(source_files)} в {root_path}")
        total_symbols = 0
        total_calls = 0

        for file_path in source_files:
            content, enc = read_file_safe(file_path)
            if not content:
                continue

            lines = content.splitlines()
            line_count = len(lines)

            # Parse AST & Call Graph
            symbols, calls, includes = parse_code(file_path, content)
            total_symbols += len(symbols)
            total_calls += len(calls)

            # Store in Graph DB
            self.graph.save_file_data(
                file_path=file_path,
                encoding=enc,
                line_count=line_count,
                symbols=symbols,
                calls=calls
            )

            # Store symbols in FTS5 for BM25 ranking
            for s in symbols:
                self.fts.index_document(
                    file_path=s.file_path,
                    line_start=s.line_start,
                    line_end=s.line_end,
                    symbol_name=s.name,
                    symbol_kind=s.kind,
                    content=f"{s.signature}\n{s.body[:500]}"
                )

            # Also index file chunks for general textual search
            chunk_size = 50
            for i in range(0, line_count, chunk_size):
                chunk_lines = lines[i:i + chunk_size]
                chunk_text = "\n".join(chunk_lines).strip()
                if chunk_text:
                    self.fts.index_document(
                        file_path=file_path,
                        line_start=i + 1,
                        line_end=min(i + chunk_size, line_count),
                        symbol_name=os.path.basename(file_path),
                        symbol_kind="code_chunk",
                        content=chunk_text
                    )

        duration = time.time() - start_time
        log_agent_line(
            f"[{time.strftime('%H:%M:%S')}] ✅ [Indexer] Готово за {duration:.2f}с: "
            f"{len(source_files)} файлов, {total_symbols} символов, {total_calls} связей."
        )

        return {
            "success": True,
            "message": "Индексация успешно завершена",
            "files_count": len(source_files),
            "symbols_count": total_symbols,
            "calls_count": total_calls,
            "duration_seconds": round(duration, 2)
        }
