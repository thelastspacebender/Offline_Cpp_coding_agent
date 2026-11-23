"""
Incremental Adaptive Semantic File Map Builder.

Generates a compact, persistent, mtime-cached semantic map of the indexed C/C++ project
stored in SQLite (`file_summaries`):
- Normal files (<= 400 lines) -> 1 concise summary line with role tag & key symbols.
- Large files (> 400 lines)   -> adaptively split into 2..5 logical sections (by class or line block).
- Vendor/SDK headers & macro/data tables -> classified instantaneously via heuristics (0 LLM calls).
- Small files are packed 3-4 per LLM call (~2.5k tokens) for fast throughput on local GPUs.
- Supports background execution, live progress/ETA polling, graceful cancellation, and resume.
"""

import os
import re
import math
import time
import json
import threading
from typing import List, Dict, Any, Optional, Tuple, Set

from ..core.agent_logger import log_agent_line, log_agent_file_only
from ..storage.sqlite_db import DatabaseManager
from ..storage.graph_store import GraphStore
from .qwen_service import QwenService
from .reducing_agent import (
    STANDARD_VENDOR_HEADERS,
    DIVIDER_LINE_RE,
    _parse_llm_json,
)


VALID_ROLES = {"core", "driver", "ui", "test", "sdk", "generated", "util"}

EXTRA_VENDOR_HEADERS = {
    "targetver.h", "afxdialogex.h", "winres.h", "afxres.h",
    "windows.h", "winsock2.h", "ws2tcpip.h", "commctrl.h",
    "unistd.h", "errno.h", "fcntl.h", "dlfcn.h",
}

ROLE_ORDER = {
    "core": 0,
    "driver": 1,
    "ui": 2,
    "util": 3,
    "test": 4,
    "generated": 5,
    "sdk": 6,
}

LARGE_FILE_THRESHOLD = 400
MAX_SECTIONS_PER_FILE = 5
BATCH_MAX_ITEMS = 4
BATCH_MAX_CHARS = 8000


def _make_display_path(file_path: str) -> str:
    norm = file_path.replace("\\", "/")
    parts = [p for p in norm.split("/") if p]
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return parts[-1] if parts else norm


ENCODING_ALIASES = {
    "utf-8-bom": "utf-8-sig",
    "latin1_lossy": "latin-1",
    "latin1": "latin-1",
}


def _read_file_lines_safe(file_path: str, preferred_encoding: Optional[str] = None) -> List[str]:
    encodings: List[str] = []
    if preferred_encoding:
        mapped = ENCODING_ALIASES.get(preferred_encoding.lower(), preferred_encoding)
        encodings.append(mapped)
    for enc in ("utf-8-sig", "utf-8", "cp1251", "cp866", "latin-1"):
        if enc not in encodings:
            encodings.append(enc)

    for enc in encodings:
        try:
            with open(file_path, "r", encoding=enc) as f:
                return f.read().splitlines()
        except (UnicodeDecodeError, LookupError):
            continue
        except Exception:
            return []
    try:
        with open(file_path, "r", encoding="latin-1", errors="replace") as f:
            return f.read().splitlines()
    except Exception:
        return []


def _normalize_role(raw_role: Any, file_path: str) -> str:
    r = str(raw_role or "").strip().lower()
    if r in VALID_ROLES:
        return r
    # Map common aliases
    alias_map = {
        "hardware": "driver",
        "hw": "driver",
        "hal": "driver",
        "io": "driver",
        "gui": "ui",
        "dialog": "ui",
        "view": "ui",
        "window": "ui",
        "testing": "test",
        "demo": "test",
        "cli": "test",
        "console": "test",
        "vendor": "sdk",
        "thirdparty": "sdk",
        "third_party": "sdk",
        "header": "core",
        "helper": "util",
        "utility": "util",
        "common": "util",
        "data": "generated",
        "table": "generated",
        "auto": "generated",
    }
    if r in alias_map:
        return alias_map[r]
    # Soft fallback by filename hints
    low_base = os.path.basename(file_path).lower()
    if any(tok in low_base for tok in ("test", "mock", "bench", "sample")):
        return "test"
    if any(tok in low_base for tok in ("dlg", "dialog", "wnd", "view", "frame", "gui", "form")):
        return "ui"
    return "core"


def _parse_batch_items_json(raw: str) -> List[Dict[str, Any]]:
    """Fault-tolerant parser for MapBuilder batch JSON responses."""
    if not raw or not raw.strip():
        return []

    parsed = _parse_llm_json(raw)
    items = parsed.get("items")
    if isinstance(items, list) and items:
        return [it for it in items if isinstance(it, dict)]

    # Single-object fallback: {"role": "...", "summary": "...", ...}
    if isinstance(parsed, dict) and parsed.get("summary"):
        return [parsed]

    # Try direct JSON array
    cleaned = re.sub(r'```(?:json)?\s*|\s*```', '', raw).strip()
    arr_start = cleaned.find('[')
    arr_end = cleaned.rfind(']')
    if arr_start != -1 and arr_end > arr_start:
        cand = re.sub(r',\s*([\]}])', r'\1', cleaned[arr_start:arr_end + 1])
        try:
            arr = json.loads(cand)
            if isinstance(arr, list):
                return [it for it in arr if isinstance(it, dict)]
        except Exception:
            pass

    # Regex extraction fallback for partially truncated outputs
    recovered: List[Dict[str, Any]] = []
    for m in re.finditer(r'\{[^{}]*"summary"\s*:\s*"([^"]+)"[^{}]*\}', cleaned, re.DOTALL):
        blk = m.group(0)
        entry: Dict[str, Any] = {"summary": m.group(1)}
        id_m = re.search(r'"id"\s*:\s*"?([A-Za-z0-9_.\-/]+)"?', blk)
        if id_m:
            entry["id"] = id_m.group(1)
        role_m = re.search(r'"role"\s*:\s*"([^"]+)"', blk)
        if role_m:
            entry["role"] = role_m.group(1)
        sym_m = re.search(r'"key_symbols"\s*:\s*\[([^\]]*)\]', blk)
        if sym_m:
            entry["key_symbols"] = re.findall(r'"([^"]+)"', sym_m.group(1))
        recovered.append(entry)
    return recovered


class MapBuilder:
    """Builds and caches the Adaptive Semantic File Map in SQLite (`file_summaries`)."""

    def __init__(
        self,
        db_manager: DatabaseManager,
        qwen_service: QwenService,
        graph_store: GraphStore
    ):
        self.db = db_manager
        self.qwen = qwen_service
        self.graph = graph_store

        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._state: Dict[str, Any] = {
            "state": "idle",  # idle | running | completed | cancelled | failed
            "percent": 0.0,
            "processed": 0,
            "total_to_process": 0,
            "skipped_cached": 0,
            "heuristic_tagged": 0,
            "llm_processed": 0,
            "current_file": "",
            "eta_seconds": 0.0,
            "duration_seconds": 0.0,
            "error": "",
        }

    # ------------------------------------------------------------------
    # Public Status & Lifecycle API
    # ------------------------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        """Returns combined SQLite map coverage stats + background build progress."""
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM indexed_files;")
            total_files = int(cur.fetchone()[0] or 0)

            cur.execute("""
                SELECT COUNT(DISTINCT fs.file_path),
                       COUNT(fs.id),
                       COALESCE(SUM(LENGTH(fs.summary) + LENGTH(fs.key_symbols) + 48), 0)
                FROM file_summaries fs
                INNER JOIN indexed_files inf ON fs.file_path = inf.file_path;
            """)
            row = cur.fetchone()
            mapped_files = int(row[0] or 0)
            summary_entries = int(row[1] or 0)
            approx_chars = int(row[2] or 0)

        coverage_pct = round((mapped_files / total_files) * 100.0, 1) if total_files > 0 else 0.0
        approx_tokens = approx_chars // 4

        with self._lock:
            st = dict(self._state)

        st.update({
            "total_files": total_files,
            "mapped_files": mapped_files,
            "summary_entries": summary_entries,
            "coverage_percent": coverage_pct,
            "approx_tokens": approx_tokens,
            "is_ready": total_files > 0 and coverage_pct >= 50.0,
        })
        return st

    def is_ready(self, min_coverage: float = 0.5) -> bool:
        """True if indexed files exist and at least min_coverage (0..1) have semantic summaries."""
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM indexed_files;")
            total_files = int(cur.fetchone()[0] or 0)
            if total_files == 0:
                return False
            cur.execute("""
                SELECT COUNT(DISTINCT fs.file_path)
                FROM file_summaries fs
                INNER JOIN indexed_files inf ON fs.file_path = inf.file_path;
            """)
            mapped_files = int(cur.fetchone()[0] or 0)
        return (mapped_files / float(total_files)) >= min_coverage

    def start_build(self, force_rebuild: bool = False) -> Dict[str, Any]:
        """Launches incremental semantic map generation in a background daemon thread."""
        with self._lock:
            if self._state["state"] == "running" and self._thread and self._thread.is_alive():
                return {
                    "started": False,
                    "message": "Сборка карты проекта уже выполняется.",
                    "status": self.get_status()
                }
            self._cancel_event.clear()
            self._state.update({
                "state": "running",
                "percent": 0.0,
                "processed": 0,
                "total_to_process": 0,
                "skipped_cached": 0,
                "heuristic_tagged": 0,
                "llm_processed": 0,
                "current_file": "Подготовка списка файлов...",
                "eta_seconds": 0.0,
                "duration_seconds": 0.0,
                "error": "",
            })

        self._thread = threading.Thread(
            target=self._run_build_thread,
            args=(force_rebuild,),
            daemon=True,
            name="SemanticMapBuilder"
        )
        self._thread.start()
        return {
            "started": True,
            "message": "Запущена сборка семантической карты проекта.",
            "status": self.get_status()
        }

    def cancel_build(self) -> Dict[str, Any]:
        """Requests graceful cancellation; already processed files remain saved in SQLite."""
        self._cancel_event.set()
        with self._lock:
            was_running = self._state["state"] == "running"
            if was_running:
                self._state["current_file"] = "Остановка после текущего батча..."
        return {
            "cancelled": was_running,
            "message": "Запрошена остановка сборки карты. Прогресс сохранён." if was_running else "Сборка не запущена.",
            "status": self.get_status()
        }

    def _run_build_thread(self, force_rebuild: bool):
        try:
            self.build_sync(force_rebuild=force_rebuild)
        except Exception as e:
            log_agent_line(f"[{time.strftime('%H:%M:%S')}] ❌ [MapBuilder] Критическая ошибка сборки карты: {e}")
            with self._lock:
                self._state["state"] = "failed"
                self._state["error"] = str(e)
                self._state["current_file"] = ""

    # ------------------------------------------------------------------
    # Core Synchronous Build Pipeline (also directly testable in unit tests)
    # ------------------------------------------------------------------
    def build_sync(self, force_rebuild: bool = False) -> Dict[str, Any]:
        """Runs full incremental map build synchronously and returns final status."""
        t0 = time.time()
        self._cancel_event.clear()

        with self._lock:
            self._state.update({
                "state": "running",
                "percent": 0.0,
                "processed": 0,
                "skipped_cached": 0,
                "heuristic_tagged": 0,
                "llm_processed": 0,
                "current_file": "Загрузка индекса...",
                "eta_seconds": 0.0,
                "duration_seconds": 0.0,
                "error": "",
            })

        # 1. Load indexed files & prune orphaned summaries
        all_files = self._load_indexed_files()
        valid_paths = {f["file_path"] for f in all_files}
        self._prune_stale_summaries(valid_paths)

        total_files = len(all_files)
        with self._lock:
            self._state["total_to_process"] = total_files

        if total_files == 0:
            with self._lock:
                self._state.update({
                    "state": "completed",
                    "percent": 100.0,
                    "current_file": "",
                    "duration_seconds": round(time.time() - t0, 2),
                })
            return self.get_status()

        log_agent_line(
            f"[{time.strftime('%H:%M:%S')}] 🗺️ [MapBuilder] Старт сборки карты: "
            f"{total_files} файлов (force_rebuild={force_rebuild})"
        )

        # 2. Load cached mtimes & macro-heavy / symbol stats in bulk (fast SQLite)
        cached_mtimes = self._load_cached_mtimes()
        file_sym_stats = self._load_file_symbol_stats()

        # Partition files: cached (skip) -> heuristic (0s) -> llm_units (batched)
        processed = 0
        skipped_cached = 0
        heuristic_tagged = 0
        llm_units: List[Dict[str, Any]] = []  # each unit = 1 file or 1 section of a large file
        files_needing_llm: Set[str] = set()

        for rec in all_files:
            if self._cancel_event.is_set():
                break

            fp = rec["file_path"]
            enc = rec["encoding"]
            line_count = int(rec["line_count"] or 0)
            disk_mtime = self._get_file_mtime(fp)

            # Check mtime cache
            if not force_rebuild and fp in cached_mtimes:
                if abs(cached_mtimes[fp] - disk_mtime) < 1e-3:
                    skipped_cached += 1
                    processed += 1
                    continue

            # Check instant heuristic classification (0 LLM calls)
            heur = self._try_heuristic_classify(fp, line_count, file_sym_stats.get(fp))
            if heur is not None:
                role, summary, key_syms = heur
                self._save_file_summaries(
                    fp,
                    [{
                        "section_id": "file",
                        "line_start": 1,
                        "line_end": max(1, line_count),
                        "role_tag": role,
                        "summary": summary,
                        "key_symbols": ", ".join(key_syms[:6]),
                    }],
                    disk_mtime=disk_mtime,
                    model_name="heuristic"
                )
                heuristic_tagged += 1
                processed += 1
                continue

            # Otherwise prepare 1 unit (<= 400 lines) or 2-5 adaptive sections (> 400 lines)
            units = self._build_llm_units_for_file(fp, enc, line_count, disk_mtime)
            llm_units.extend(units)
            files_needing_llm.add(fp)

        with self._lock:
            self._state["processed"] = processed
            self._state["skipped_cached"] = skipped_cached
            self._state["heuristic_tagged"] = heuristic_tagged
            self._state["percent"] = round((processed / max(1, total_files)) * 100.0, 1)

        log_agent_line(
            f"[{time.strftime('%H:%M:%S')}] 🗺️ [MapBuilder] Предфильтр готов: "
            f"из кэша={skipped_cached}, эвристика(SDK/Macro/Data)={heuristic_tagged}, "
            f"в очередь LLM={len(files_needing_llm)} файл. ({len(llm_units)} секц.)"
        )

        # 3. Pack LLM units into compact ~2.5k-token batches (up to 4 items or 8000 chars)
        batches = self._pack_units(llm_units)
        llm_processed_files: Set[str] = set()
        pending_file_rows: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
        expected_sections_per_file: Dict[str, int] = {}
        for u in llm_units:
            expected_sections_per_file[u["file_path"]] = expected_sections_per_file.get(u["file_path"], 0) + 1

        batch_durations: List[float] = []
        total_batches = len(batches)

        for b_idx, batch in enumerate(batches, 1):
            if self._cancel_event.is_set():
                log_agent_line(f"[{time.strftime('%H:%M:%S')}] ⏹ [MapBuilder] Сборка остановлена пользователем на батче {b_idx}/{total_batches}.")
                break

            preview_names = ", ".join(list(dict.fromkeys(os.path.basename(u["file_path"]) for u in batch))[:3])
            with self._lock:
                self._state["current_file"] = f"[{b_idx}/{total_batches}] {preview_names}"

            bt0 = time.time()
            results = self._summarize_batch_with_llm(batch)
            bdt = time.time() - bt0
            batch_durations.append(max(0.1, bdt))

            # Group results per file and flush atomically once all sections of a file are ready
            for u, res_row in zip(batch, results):
                fp = u["file_path"]
                if fp not in pending_file_rows:
                    pending_file_rows[fp] = (u["disk_mtime"], [])
                pending_file_rows[fp][1].append(res_row)

                if len(pending_file_rows[fp][1]) >= expected_sections_per_file.get(fp, 1):
                    mtime_val, rows = pending_file_rows.pop(fp)
                    self._save_file_summaries(fp, rows, disk_mtime=mtime_val, model_name="local-llm")
                    if fp not in llm_processed_files:
                        llm_processed_files.add(fp)
                        processed += 1

            avg_bt = sum(batch_durations[-8:]) / len(batch_durations[-8:])
            rem_batches = total_batches - b_idx
            eta_sec = round(avg_bt * rem_batches, 1)

            with self._lock:
                self._state["processed"] = processed
                self._state["llm_processed"] = len(llm_processed_files)
                self._state["percent"] = round((processed / max(1, total_files)) * 100.0, 1)
                self._state["eta_seconds"] = eta_sec
                self._state["duration_seconds"] = round(time.time() - t0, 1)

            log_agent_line(
                f"[{time.strftime('%H:%M:%S')}]    ✓ [MapBuilder | {b_idx}/{total_batches} | {bdt:.1f}с] "
                f"{preview_names} (готово {processed}/{total_files}, ETA ~{eta_sec:.0f}с)"
            )

        # Flush any partially collected multi-section files if build wasn't cancelled mid-file
        if not self._cancel_event.is_set():
            for fp, (mtime_val, rows) in list(pending_file_rows.items()):
                if rows:
                    self._save_file_summaries(fp, rows, disk_mtime=mtime_val, model_name="local-llm")
                    if fp not in llm_processed_files:
                        llm_processed_files.add(fp)
                        processed += 1

        final_state = "cancelled" if self._cancel_event.is_set() else "completed"
        total_dt = round(time.time() - t0, 1)

        with self._lock:
            self._state.update({
                "state": final_state,
                "processed": processed,
                "llm_processed": len(llm_processed_files),
                "percent": 100.0 if final_state == "completed" else round((processed / max(1, total_files)) * 100.0, 1),
                "eta_seconds": 0.0,
                "current_file": "",
                "duration_seconds": total_dt,
            })

        log_agent_line(
            f"[{time.strftime('%H:%M:%S')}] 🗺️ [MapBuilder] Завершено ({final_state}) за {total_dt:.1f}с | "
            f"кэш={skipped_cached}, эвристика={heuristic_tagged}, LLM={len(llm_processed_files)} файл."
        )
        return self.get_status()

    # ------------------------------------------------------------------
    # Formatted Map Export for SemanticFunnelAgent Stage 1
    # ------------------------------------------------------------------
    def get_formatted_map_lines(self) -> List[Dict[str, Any]]:
        """
        Returns all semantic map entries joined with currently indexed files,
        sorted by role priority (core/driver/ui first, test/generated/sdk last).
        """
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT fs.file_path, fs.section_id, fs.line_start, fs.line_end,
                       fs.role_tag, fs.summary, fs.key_symbols, inf.encoding, inf.line_count
                FROM file_summaries fs
                INNER JOIN indexed_files inf ON fs.file_path = inf.file_path
                ORDER BY fs.file_path ASC, fs.line_start ASC;
            """)
            rows = cur.fetchall()

        entries: List[Dict[str, Any]] = []
        for r in rows:
            fp = r["file_path"]
            disp = _make_display_path(fp)
            role = (r["role_tag"] or "core").lower()
            ls = int(r["line_start"] or 1)
            le = int(r["line_end"] or ls)
            syms = (r["key_symbols"] or "").strip()
            summary = (r["summary"] or "").strip()

            sym_part = f" | Syms: {syms}" if syms else ""
            formatted = f"{disp} [{role.upper()}] (L{ls}-{le}){sym_part} | {summary}"
            entries.append({
                "file_path": fp,
                "display_path": disp,
                "section_id": r["section_id"],
                "line_start": ls,
                "line_end": le,
                "role_tag": role,
                "summary": summary,
                "key_symbols": syms,
                "encoding": r["encoding"] or "utf-8",
                "line_count": int(r["line_count"] or 0),
                "formatted_line": formatted,
            })

        entries.sort(key=lambda e: (ROLE_ORDER.get(e["role_tag"], 3), e["display_path"].lower(), e["line_start"]))
        return entries

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------
    def _load_indexed_files(self) -> List[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT file_path, encoding, line_count FROM indexed_files ORDER BY line_count ASC, file_path ASC;")
            return [
                {
                    "file_path": r["file_path"],
                    "encoding": r["encoding"] or "utf-8",
                    "line_count": int(r["line_count"] or 0),
                }
                for r in cur.fetchall()
            ]

    def _prune_stale_summaries(self, valid_paths: Set[str]):
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT file_path FROM file_summaries;")
            existing = [r[0] for r in cur.fetchall()]
            stale = [fp for fp in existing if fp not in valid_paths]
            for fp in stale:
                cur.execute("DELETE FROM file_summaries WHERE file_path = ?;", (fp,))
            if stale:
                conn.commit()

    def _load_cached_mtimes(self) -> Dict[str, float]:
        result: Dict[str, float] = {}
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT file_path, MAX(file_mtime) FROM file_summaries GROUP BY file_path;")
            for r in cur.fetchall():
                try:
                    result[r[0]] = float(r[1] or 0.0)
                except Exception:
                    result[r[0]] = 0.0
        return result

    def _load_file_symbol_stats(self) -> Dict[str, Dict[str, Any]]:
        stats: Dict[str, Dict[str, Any]] = {}
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT file_path,
                       COUNT(*) as total_cnt,
                       SUM(CASE WHEN kind = 'macro' THEN 1 ELSE 0 END) as macro_cnt,
                       SUM(CASE WHEN kind IN ('function', 'method', 'class', 'struct') THEN 1 ELSE 0 END) as code_cnt
                FROM symbols
                GROUP BY file_path;
            """)
            for r in cur.fetchall():
                stats[r["file_path"]] = {
                    "total_cnt": int(r["total_cnt"] or 0),
                    "macro_cnt": int(r["macro_cnt"] or 0),
                    "code_cnt": int(r["code_cnt"] or 0),
                }
        return stats

    @staticmethod
    def _get_file_mtime(file_path: str) -> float:
        try:
            return round(os.path.getmtime(file_path), 3)
        except Exception:
            return 0.0

    def _get_top_symbols_for_file(self, file_path: str, limit: int = 6) -> List[str]:
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT name FROM symbols
                WHERE file_path = ?
                ORDER BY (CASE WHEN kind IN ('class', 'struct') THEN 0 WHEN kind IN ('function', 'method') THEN 1 ELSE 2 END),
                         line_start ASC
                LIMIT ?;
            """, (file_path, limit))
            return [r[0] for r in cur.fetchall() if r[0]]

    def _try_heuristic_classify(
        self,
        file_path: str,
        line_count: int,
        sym_stat: Optional[Dict[str, Any]]
    ) -> Optional[Tuple[str, str, List[str]]]:
        """
        Classifies obvious SDK/vendor headers, constant macro tables, giant data tables,
        and tiny stub files in 0ms without spending GPU inference time.
        Returns (role, summary, key_symbols) or None if LLM summarization is needed.
        """
        base_low = os.path.basename(file_path).lower()
        ext = os.path.splitext(base_low)[1]
        sym_stat = sym_stat or {"total_cnt": 0, "macro_cnt": 0, "code_cnt": 0}
        total_cnt = sym_stat["total_cnt"]
        macro_cnt = sym_stat["macro_cnt"]
        code_cnt = sym_stat["code_cnt"]

        # 1. Known standard/vendor/resource/precompiled headers
        if base_low in STANDARD_VENDOR_HEADERS or base_low in EXTRA_VENDOR_HEADERS:
            syms = self._get_top_symbols_for_file(file_path, 5)
            desc = "Standard vendor/SDK or precompiled system header"
            if base_low == "resource.h":
                desc = "UI resource ID constants and macro definitions"
            return ("sdk", desc, syms)

        # 2. Macro-only constant/header tables (>=20 symbols and >=80% macros, OR >=20 #define lines with 0 code symbols)
        if total_cnt >= 20 and (macro_cnt / float(total_cnt)) >= 0.80:
            syms = self._get_top_symbols_for_file(file_path, 5)
            role = "sdk" if ext in (".h", ".hpp", ".hxx", ".hh") else "generated"
            return (role, f"Macro and constant definitions table ({macro_cnt} macros)", syms)

        if code_cnt == 0 and line_count >= 20:
            raw_lines = _read_file_lines_safe(file_path)
            define_names = []
            for ln in raw_lines:
                m = re.match(r'^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)', ln)
                if m:
                    define_names.append(m.group(1))
            if len(define_names) >= 20 and (len(define_names) / float(max(1, len([l for l in raw_lines if l.strip()])))) >= 0.70:
                role = "sdk" if ext in (".h", ".hpp", ".hxx", ".hh") else "generated"
                return (role, f"Macro and constant definitions table ({len(define_names)} macros)", define_names[:5])

        # 3. Giant data / lookup table files (>600 lines with <=2 code symbols)
        if line_count > 600 and code_cnt <= 2:
            syms = self._get_top_symbols_for_file(file_path, 5)
            return ("generated", f"Large static data/lookup table or generated file ({line_count} lines)", syms)

        # 4. Tiny stub files (<= 5 lines and 0 code symbols)
        if line_count <= 5 and code_cnt == 0:
            return ("util", f"Minimal stub or include wrapper ({line_count} lines)", [])

        return None

    def _build_llm_units_for_file(
        self,
        file_path: str,
        encoding: str,
        line_count: int,
        disk_mtime: float
    ) -> List[Dict[str, Any]]:
        """
        Creates 1 unit for files <= 400 lines, or 2..5 adaptive sections for larger files.
        """
        lines = _read_file_lines_safe(file_path, encoding)
        actual_lines = len(lines) if lines else max(1, line_count)
        disp = _make_display_path(file_path)

        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT name, kind, line_start, line_end, signature, parent_scope
                FROM symbols
                WHERE file_path = ?
                ORDER BY line_start ASC;
            """, (file_path,))
            file_syms = [dict(r) for r in cur.fetchall()]

            cur.execute("""
                SELECT DISTINCT callee_name, line_number
                FROM call_relations
                WHERE file_path = ?
                ORDER BY line_number ASC;
            """, (file_path,))
            file_calls = [(r["callee_name"], int(r["line_number"] or 0)) for r in cur.fetchall()]

        if actual_lines <= LARGE_FILE_THRESHOLD:
            digest = self._format_unit_digest(
                disp, 1, actual_lines, lines, file_syms, file_calls, max_chars=2200
            )
            default_syms = [s["name"] for s in file_syms if s["kind"] in ("class", "struct", "function", "method")][:6]
            return [{
                "unit_id": f"{disp}#file",
                "file_path": file_path,
                "display_path": disp,
                "section_id": "file",
                "line_start": 1,
                "line_end": actual_lines,
                "disk_mtime": disk_mtime,
                "digest": digest,
                "fallback_syms": default_syms,
            }]

        # Adaptive splitting for files > 400 lines
        sections = self._plan_adaptive_sections(actual_lines, file_syms)
        units: List[Dict[str, Any]] = []
        for idx, (sec_id, ls, le, label) in enumerate(sections, 1):
            sec_syms = [s for s in file_syms if ls <= int(s["line_start"] or 1) <= le]
            sec_calls = [(c, ln) for (c, ln) in file_calls if ls <= ln <= le]
            digest = self._format_unit_digest(
                f"{disp} ({label})", ls, le, lines, sec_syms, sec_calls, max_chars=2400
            )
            default_syms = [s["name"] for s in sec_syms if s["kind"] in ("class", "struct", "function", "method")][:6]
            units.append({
                "unit_id": f"{disp}#{sec_id}",
                "file_path": file_path,
                "display_path": disp,
                "section_id": sec_id,
                "line_start": ls,
                "line_end": le,
                "disk_mtime": disk_mtime,
                "digest": digest,
                "fallback_syms": default_syms,
            })
        return units

    @staticmethod
    def _plan_adaptive_sections(
        actual_lines: int,
        file_syms: List[Dict[str, Any]]
    ) -> List[Tuple[str, int, int, str]]:
        """
        Plans 2..5 logical sections for a large file:
        - Prefers class/parent_scope grouping when >=2 distinct classes exist.
        - Otherwise splits evenly into 2..5 line blocks (~350-450 lines each).
        """
        # Try grouping by class/parent_scope
        scope_spans: Dict[str, List[int]] = {}
        for s in file_syms:
            scope = (s.get("parent_scope") or "").strip()
            if not scope and s.get("kind") in ("class", "struct"):
                scope = (s.get("name") or "").strip()
            if scope:
                ls = int(s.get("line_start") or 1)
                le = int(s.get("line_end") or ls)
                if scope not in scope_spans:
                    scope_spans[scope] = [ls, le]
                else:
                    scope_spans[scope][0] = min(scope_spans[scope][0], ls)
                    scope_spans[scope][1] = max(scope_spans[scope][1], le)

        if 2 <= len(scope_spans) <= MAX_SECTIONS_PER_FILE:
            ordered = sorted(scope_spans.items(), key=lambda kv: kv[1][0])
            sections: List[Tuple[str, int, int, str]] = []
            for idx, (scope_name, span) in enumerate(ordered, 1):
                safe_id = re.sub(r'[^A-Za-z0-9_]+', '_', scope_name)[:28] or f"sec{idx}"
                sections.append((f"cls_{safe_id}", max(1, span[0]), min(actual_lines, max(span[0], span[1])), f"class {scope_name}"))
            return sections

        # Even line-range splitting (2 to MAX_SECTIONS_PER_FILE sections)
        num_secs = min(MAX_SECTIONS_PER_FILE, max(2, int(math.ceil(actual_lines / 400.0))))
        step = int(math.ceil(actual_lines / float(num_secs)))
        sections = []
        for idx in range(num_secs):
            ls = idx * step + 1
            le = min(actual_lines, (idx + 1) * step)
            if ls > actual_lines:
                break
            sections.append((f"part{idx + 1}", ls, le, f"part {idx + 1}/{num_secs}"))
        return sections

    @staticmethod
    def _format_unit_digest(
        header_label: str,
        line_start: int,
        line_end: int,
        all_lines: List[str],
        syms: List[Dict[str, Any]],
        calls: List[Tuple[str, int]],
        max_chars: int = 2200
    ) -> str:
        """Builds a compact AST + source header digest for LLM summarization."""
        parts: List[str] = [f"FILE: {header_label} (L{line_start}-{line_end})"]

        if syms:
            sig_lines: List[str] = []
            for s in syms[:30]:
                sig = (s.get("signature") or s.get("name") or "").strip()
                sig = re.sub(r'\s+', ' ', sig)[:110]
                sig_lines.append(f"  L{s.get('line_start', 1)} [{s.get('kind', 'sym')}] {sig}")
            parts.append("SYMBOLS:\n" + "\n".join(sig_lines))

        if calls:
            uniq_calls = list(dict.fromkeys(c for c, _ in calls if c))[:25]
            if uniq_calls:
                parts.append("CALLS: " + ", ".join(uniq_calls))

        # Add compact code preview from the slice (preserving comments and key declarations)
        if all_lines:
            slice_lines = all_lines[max(0, line_start - 1):min(len(all_lines), line_end)]
            compact_preview: List[str] = []
            for offset, raw_ln in enumerate(slice_lines):
                st = raw_ln.strip()
                if not st or st == "#pragma once" or DIVIDER_LINE_RE.match(st):
                    continue
                compact_preview.append(f"{line_start + offset}| {raw_ln[:120]}")
                if len(compact_preview) >= 45:
                    break
            if compact_preview:
                parts.append("CODE EXCERPT:\n" + "\n".join(compact_preview))

        text = "\n".join(parts)
        if len(text) > max_chars:
            text = text[:max_chars - 20] + "\n...[truncated]"
        return text

    @staticmethod
    def _pack_units(units: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """Packs units into compact batches (<= BATCH_MAX_ITEMS items and <= BATCH_MAX_CHARS chars)."""
        batches: List[List[Dict[str, Any]]] = []
        current: List[Dict[str, Any]] = []
        cur_chars = 0

        for u in units:
            u_len = len(u["digest"]) + 80
            if current and (len(current) >= BATCH_MAX_ITEMS or cur_chars + u_len > BATCH_MAX_CHARS):
                batches.append(current)
                current = []
                cur_chars = 0
            current.append(u)
            cur_chars += u_len

        if current:
            batches.append(current)
        return batches

    def _summarize_batch_with_llm(self, batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Calls local LLM statelessly to summarize 1..4 file/section digests in one shot."""
        blocks: List[str] = []
        for idx, u in enumerate(batch):
            blocks.append(f"--- ITEM id=\"{idx}\" ---\n{u['digest']}")
        body = "\n\n".join(blocks)

        sys_p = (
            "You are a C/C++ codebase indexer building a compact semantic map.\n"
            "For EACH numbered item below, classify its architectural role and write ONE dense English sentence "
            "(max 20 words) describing its concrete responsibilities, algorithms, hardware/IO, or UI behavior.\n"
            "Valid roles:\n"
            "- core: primary business/application logic, processing loops, state machines, algorithms\n"
            "- driver: hardware, FPGA, serial/USB/socket/ioctl/low-level device IO\n"
            "- ui: GUI dialogs, windows, views, MFC/Win32 UI controls\n"
            "- test: console test menus, unit tests, debug harnesses, demos\n"
            "- sdk: third-party/vendor/OS headers or resource constants\n"
            "- generated: auto-generated tables, huge constant arrays\n"
            "- util: generic helpers, logging, math, string/file utilities\n"
            "Respond ONLY with valid JSON:\n"
            "{\"items\": [{\"id\": \"0\", \"role\": \"core\", \"summary\": \"...\", \"key_symbols\": [\"ClassOrFunc1\", \"Func2\"]}]}"
        )
        usr_p = f"ITEMS TO INDEX ({len(batch)}):\n\n{body}\n\nJSON:"

        raw = self.qwen.generate_raw(sys_p, usr_p, temperature=0.0, max_tokens=550, timeout=75.0)
        log_agent_file_only(
            "MAP_BUILDER BATCH RAW RESPONSE",
            f"Items: {', '.join(u['unit_id'] for u in batch)}\n\n{raw or '<empty>'}"
        )

        parsed_items = _parse_batch_items_json(raw)
        by_id: Dict[str, Dict[str, Any]] = {}
        for pos, it in enumerate(parsed_items):
            raw_id = str(it.get("id", pos)).strip()
            by_id[raw_id] = it

        rows: List[Dict[str, Any]] = []
        for idx, u in enumerate(batch):
            matched = by_id.get(str(idx))
            if matched is None and idx < len(parsed_items):
                matched = parsed_items[idx]

            if matched and str(matched.get("summary", "")).strip():
                role = _normalize_role(matched.get("role"), u["file_path"])
                summary = re.sub(r'\s+', ' ', str(matched.get("summary", "")).strip())[:240]
                ksyms_raw = matched.get("key_symbols", [])
                if isinstance(ksyms_raw, str):
                    ksyms = [s.strip() for s in ksyms_raw.split(",") if s.strip()]
                elif isinstance(ksyms_raw, list):
                    ksyms = [str(s).strip() for s in ksyms_raw if str(s).strip()]
                else:
                    ksyms = []
                if not ksyms:
                    ksyms = u["fallback_syms"]
            else:
                # Deterministic AST fallback if LLM response was empty/malformed
                role = _normalize_role("", u["file_path"])
                ksyms = u["fallback_syms"]
                if ksyms:
                    summary = f"Defines {', '.join(ksyms[:5])}"
                else:
                    summary = f"C/C++ source section (L{u['line_start']}-{u['line_end']})"

            rows.append({
                "section_id": u["section_id"],
                "line_start": u["line_start"],
                "line_end": u["line_end"],
                "role_tag": role,
                "summary": summary,
                "key_symbols": ", ".join(ksyms[:6]),
            })
        return rows

    def _save_file_summaries(
        self,
        file_path: str,
        rows: List[Dict[str, Any]],
        disk_mtime: float,
        model_name: str
    ):
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM file_summaries WHERE file_path = ?;", (file_path,))
            for r in rows:
                cur.execute("""
                    INSERT OR REPLACE INTO file_summaries
                        (file_path, section_id, line_start, line_end, role_tag, summary, key_symbols, file_mtime, model_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    file_path,
                    r["section_id"],
                    int(r["line_start"]),
                    int(r["line_end"]),
                    r["role_tag"],
                    r["summary"],
                    r["key_symbols"],
                    float(disk_mtime),
                    model_name,
                ))
            conn.commit()
