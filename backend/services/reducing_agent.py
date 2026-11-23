"""
Stateless Multi-Pass Map-Reduce + Rescue Agent for Exhaustive C/C++ Codebase Analysis.

Architecture:
- Pass 0: Fast keyword extraction solely for Pass 1 queue prioritization (no files filtered out).
- Pass 1: Stateless Map over all headers (.h/.hpp/.hxx/.hh/.inl) + compact outlines of standalone .c/.cpp.
- Pass 2: Stem/Symbol/#include mapping from Pass 1 hits to .cpp/.c implementations + Stateless Map
          collecting both relevant blocks and external missing_symbols.
- Pass 3 (Rescue): Combines missing_symbols + Call-Graph callers/callees to recover and scan files
          that were skipped in Pass 1 (with optional 2nd hop in Deep mode).
- Pass 4 (Recursive Reduce): Merges overlapping line ranges (+-5 lines context) and iteratively
          compresses evidence if total size exceeds the active LLM context window.
- Pass 5 (Final Synthesis): Generates grounded answer with verified [File:Line] citations.
"""

import os
import re
import json
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple, Set

from ..core.models import CodeCitation, SearchResult, SymbolDefinition
from ..core.agent_logger import log_agent_boundary, log_agent_section, log_agent_line, log_agent_file_only
from ..storage.sqlite_db import DatabaseManager
from ..storage.graph_store import GraphStore
from ..storage.fts_index import FtsIndex
from .qwen_service import QwenService
from .prompt_builder import build_system_prompt, smart_truncate_code


HEADER_EXTS = {".h", ".hpp", ".hxx", ".hh", ".inl"}
SOURCE_EXTS = {".cpp", ".c", ".cc", ".cxx"}
INCLUDE_RE = re.compile(r'#\s*include\s*[<"]([^">]+)[">]')
CITATION_RE = re.compile(r'\[([A-Za-z0-9_.\-\\/]+\.(?:cpp|c|cc|cxx|h|hpp|hxx|hh|inl|cs|py)):(\d+)\]')


@dataclass
class CodeChunk:
    file_path: str
    display_path: str
    line_start: int
    line_end: int
    text: str
    is_outline: bool = False


@dataclass
class EvidenceItem:
    file_path: str
    display_path: str
    line_start: int
    line_end: int
    symbols: List[str] = field(default_factory=list)
    reason: str = ""
    source_pass: str = "pass1"
    score: int = 1


@dataclass
class MergedBlock:
    file_path: str
    display_path: str
    line_start: int
    line_end: int
    symbols: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    source_passes: Set[str] = field(default_factory=set)
    score: int = 1
    code: str = ""


class BudgetTracker:
    """Tracks elapsed time, per-phase deadlines (with unused budget rollover), and final synthesis reserve."""

    PHASE_FRACTIONS = {
        "pass1": 0.45,
        "pass2": 0.72,
        "pass3": 0.85,
        "pass4": 0.92,
    }

    def __init__(self, total_budget_sec: float = 300.0, deep: bool = False):
        self.start_time = time.time()
        self.total_budget_sec = max(30.0, float(total_budget_sec))
        self.deep = deep
        self.final_reserve_sec = min(self.total_budget_sec * 0.25, 60.0 if deep else 45.0)
        self.batch_durations: List[float] = []

    def elapsed(self) -> float:
        return time.time() - self.start_time

    def remaining_total(self) -> float:
        return max(0.0, self.total_budget_sec - self.elapsed())

    def remaining_for_work(self) -> float:
        return max(0.0, self.total_budget_sec - self.final_reserve_sec - self.elapsed())

    def avg_batch_sec(self) -> float:
        if not self.batch_durations:
            return 4.0
        recent = self.batch_durations[-8:]
        return sum(recent) / len(recent)

    def record_batch(self, duration_sec: float):
        self.batch_durations.append(max(0.1, float(duration_sec)))

    def phase_deadline(self, phase: str) -> float:
        frac = self.PHASE_FRACTIONS.get(phase, 0.90)
        work_cap = max(5.0, self.total_budget_sec - self.final_reserve_sec)
        return min(work_cap, self.total_budget_sec * frac)

    def can_start_batch(self, phase: str, phase_batches_done: int = 0) -> bool:
        now_el = self.elapsed()
        work_cap = self.total_budget_sec - self.final_reserve_sec
        if now_el >= work_cap:
            return False

        deadline = self.phase_deadline(phase)
        if now_el >= deadline:
            return False

        # After at least 1 batch in a phase, avoid starting a batch likely to overrun the phase deadline
        if phase_batches_done > 0 and (now_el + self.avg_batch_sec() * 0.6) > deadline:
            return False

        return True

    def batch_timeout(self, max_cap: float = 45.0) -> float:
        rem = self.remaining_for_work()
        return max(5.0, min(max_cap, rem + 2.0))


def _parse_line_range(item: Dict[str, Any], default_start: int = 1, default_end: int = 1) -> Tuple[int, int]:
    """Extracts (line_start, line_end) from flexible LLM JSON representations."""
    if "line_start" in item or "line_end" in item:
        try:
            ls = int(item.get("line_start", default_start))
            le = int(item.get("line_end", ls))
            return min(ls, le), max(ls, le)
        except Exception:
            pass

    lines_val = item.get("lines")
    if isinstance(lines_val, (list, tuple)) and len(lines_val) >= 1:
        try:
            nums = [int(x) for x in lines_val if str(x).strip().isdigit()]
            if len(nums) >= 2:
                return min(nums[0], nums[1]), max(nums[0], nums[1])
            elif len(nums) == 1:
                return nums[0], nums[0]
        except Exception:
            pass
    elif isinstance(lines_val, str):
        nums = [int(x) for x in re.findall(r'\d+', lines_val)]
        if len(nums) >= 2:
            return min(nums[0], nums[1]), max(nums[0], nums[1])
        elif len(nums) == 1:
            return nums[0], nums[0]
    elif isinstance(lines_val, int):
        return lines_val, lines_val

    return default_start, default_end


def _parse_llm_json(raw: str) -> Dict[str, Any]:
    """
    Fault-tolerant JSON parser for local LLM outputs.
    Handles markdown code fences, trailing commas, and truncated JSON streams.
    """
    if not raw or not raw.strip():
        return {}

    cleaned = re.sub(r'```(?:json)?\s*|\s*```', '', raw).strip()
    start = cleaned.find('{')
    end = cleaned.rfind('}')
    candidate = cleaned[start:end + 1] if (start != -1 and end > start) else cleaned

    # Remove trailing commas before ] or }
    candidate_fixed = re.sub(r',\s*([\]}])', r'\1', candidate)
    try:
        parsed = json.loads(candidate_fixed)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    # Fallback: extract individual file/lines/symbols objects if JSON was truncated mid-stream
    result: Dict[str, Any] = {}
    extracted_items: List[Dict[str, Any]] = []
    obj_pattern = re.compile(r'\{[^{}]*"file"\s*:\s*"([^"]+)"[^{}]*\}', re.DOTALL)
    for m in obj_pattern.finditer(cleaned):
        block = m.group(0)
        entry: Dict[str, Any] = {"file": m.group(1)}
        lines_m = re.search(r'"lines"\s*:\s*(\[[^\]]*\]|"[^"]*"|\d+)', block)
        if lines_m:
            raw_l = lines_m.group(1).strip('"')
            nums = [int(x) for x in re.findall(r'\d+', raw_l)]
            if nums:
                entry["lines"] = [nums[0], nums[-1]]
        sym_m = re.search(r'"symbols"\s*:\s*\[([^\]]*)\]', block)
        if sym_m:
            entry["symbols"] = re.findall(r'"([^"]+)"', sym_m.group(1))
        reason_m = re.search(r'"reason"\s*:\s*"([^"]*)"', block)
        if reason_m:
            entry["reason"] = reason_m.group(1)
        extracted_items.append(entry)

    if '"keep"' in cleaned:
        result["keep"] = extracted_items
    else:
        result["relevant"] = extracted_items

    miss_m = re.search(r'"missing_symbols"\s*:\s*\[([^\]]*)', cleaned)
    if miss_m:
        result["missing_symbols"] = re.findall(r'"([^"]+)"', miss_m.group(1))

    sum_m = re.search(r'"summary"\s*:\s*"([^"]*)', cleaned)
    if sum_m:
        result["summary"] = sum_m.group(1)

    return result


class ReducingAgent:
    """
    Executes multi-pass stateless scanning (Headers -> Mapped .cpp -> Rescue -> Recursive Reduce -> Synthesis)
    over an entire indexed codebase within a configurable wall-clock time budget.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        qwen_service: QwenService,
        graph_store: GraphStore,
        fts_index: FtsIndex
    ):
        self.db = db_manager
        self.qwen = qwen_service
        self.graph = graph_store
        self.fts = fts_index
        self._file_lines_cache: Dict[str, List[str]] = {}

    def _load_indexed_files(self) -> List[Dict[str, Any]]:
        """Loads all indexed file records from SQLite."""
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT file_path, encoding, line_count FROM indexed_files ORDER BY file_path ASC;")
            return [
                {
                    "file_path": r["file_path"],
                    "encoding": r["encoding"] or "utf-8",
                    "line_count": r["line_count"] or 0
                }
                for r in cur.fetchall()
            ]

    @staticmethod
    def _make_display_path(file_path: str) -> str:
        norm = file_path.replace("\\", "/")
        parts = [p for p in norm.split("/") if p]
        if len(parts) >= 2:
            return f"{parts[-2]}/{parts[-1]}"
        return parts[-1] if parts else norm

    def _read_file_lines(self, file_path: str, preferred_encoding: Optional[str] = None) -> List[str]:
        if file_path in self._file_lines_cache:
            return self._file_lines_cache[file_path]

        encodings = []
        if preferred_encoding:
            encodings.append(preferred_encoding)
        for enc in ("utf-8", "cp1251", "cp866", "latin-1"):
            if enc not in encodings:
                encodings.append(enc)

        lines: List[str] = []
        for enc in encodings:
            try:
                with open(file_path, "r", encoding=enc) as f:
                    lines = f.read().splitlines()
                break
            except UnicodeDecodeError:
                continue
            except Exception:
                return []
        else:
            try:
                with open(file_path, "r", encoding="latin-1", errors="replace") as f:
                    lines = f.read().splitlines()
            except Exception:
                lines = []

        self._file_lines_cache[file_path] = lines
        return lines

    def _format_numbered_slice(self, file_path: str, line_start: int, line_end: int, enc: Optional[str] = None) -> str:
        lines = self._read_file_lines(file_path, enc)
        if not lines:
            return ""
        total = len(lines)
        ls = max(1, min(line_start, total))
        le = max(ls, min(line_end, total))
        formatted = [f"{idx}| {lines[idx - 1]}" for idx in range(ls, le + 1)]
        return "\n".join(formatted)

    def _chunk_file(
        self,
        file_path: str,
        display_path: str,
        encoding: str,
        max_lines: int = 320,
        overlap: int = 15,
        max_chunks: Optional[int] = None
    ) -> List[CodeChunk]:
        lines = self._read_file_lines(file_path, encoding)
        if not lines:
            return []

        total = len(lines)
        chunks: List[CodeChunk] = []
        start = 1
        step = max(50, max_lines - overlap)

        while start <= total:
            end = min(total, start + max_lines - 1)
            text = "\n".join(f"{idx}| {lines[idx - 1]}" for idx in range(start, end + 1))
            if text.strip():
                chunks.append(CodeChunk(
                    file_path=file_path,
                    display_path=display_path,
                    line_start=start,
                    line_end=end,
                    text=text,
                    is_outline=False
                ))
            if end >= total:
                break
            if max_chunks is not None and len(chunks) >= max_chunks:
                break
            start += step

        return chunks

    def _build_standalone_outline(self, file_path: str, display_path: str, encoding: str) -> Optional[CodeChunk]:
        """Builds a compact signature outline from symbols table for standalone .c/.cpp without headers."""
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT name, kind, line_start, signature
                FROM symbols
                WHERE file_path = ?
                ORDER BY line_start ASC
                LIMIT 120;
            """, (file_path,))
            rows = cur.fetchall()

        if rows:
            outline_lines = []
            for r in rows:
                sig = (r["signature"] or r["name"] or "").strip()
                sig = " ".join(sig.split())[:160]
                outline_lines.append(f"{r['line_start']}| [{r['kind']}] {sig}")
            first_ln = rows[0]["line_start"]
            last_ln = rows[-1]["line_start"]
            return CodeChunk(
                file_path=file_path,
                display_path=display_path,
                line_start=first_ln,
                line_end=last_ln,
                text="// [OUTLINE: standalone source signatures]\n" + "\n".join(outline_lines),
                is_outline=True
            )

        # Fallback: first 45 lines if parser found no symbols in this file
        lines = self._read_file_lines(file_path, encoding)
        if not lines:
            return None
        cap = min(45, len(lines))
        text = "\n".join(f"{i}| {lines[i - 1]}" for i in range(1, cap + 1))
        return CodeChunk(
            file_path=file_path,
            display_path=display_path,
            line_start=1,
            line_end=cap,
            text="// [OUTLINE: file head]\n" + text,
            is_outline=True
        )

    @staticmethod
    def _pack_chunks_into_batches(chunks: List[CodeChunk], batch_char_budget: int) -> List[List[CodeChunk]]:
        """Greedily packs CodeChunks into LLM prompt batches within batch_char_budget."""
        batches: List[List[CodeChunk]] = []
        current_batch: List[CodeChunk] = []
        current_chars = 0

        for ch in chunks:
            header = f"=== FILE: {ch.display_path} (lines {ch.line_start}-{ch.line_end}) ===\n"
            ch_text = ch.text
            entry_len = len(header) + len(ch_text) + 4

            # Hard cap single oversized chunk so one chunk can't blow up n_ctx
            if entry_len > batch_char_budget:
                max_body = max(1000, batch_char_budget - len(header) - 80)
                ch_text = smart_truncate_code(ch_text, max_chars=max_body)
                ch = CodeChunk(
                    file_path=ch.file_path,
                    display_path=ch.display_path,
                    line_start=ch.line_start,
                    line_end=ch.line_end,
                    text=ch_text,
                    is_outline=ch.is_outline
                )
                entry_len = len(header) + len(ch_text) + 4

            if current_batch and (current_chars + entry_len > batch_char_budget):
                batches.append(current_batch)
                current_batch = []
                current_chars = 0

            current_batch.append(ch)
            current_chars += entry_len

        if current_batch:
            batches.append(current_batch)

        return batches

    def _resolve_file_ref(
        self,
        ref: str,
        display_to_full: Dict[str, str],
        base_to_fulls: Dict[str, List[str]],
        batch_chunks: Optional[List[CodeChunk]] = None
    ) -> Optional[str]:
        """Resolves an LLM-reported file string ('Dir/Name.h' or 'Name.h') to an exact indexed file_path."""
        if not ref:
            return None
        norm = ref.strip().replace("\\", "/").lstrip("./")
        norm_lower = norm.lower()

        # First prefer matches present in the current batch to prevent cross-folder ambiguity
        if batch_chunks:
            for ch in batch_chunks:
                if ch.display_path.lower() == norm_lower or os.path.basename(ch.file_path).lower() == os.path.basename(norm_lower):
                    return ch.file_path

        if norm_lower in display_to_full:
            return display_to_full[norm_lower]

        base_lower = os.path.basename(norm_lower)
        candidates = base_to_fulls.get(base_lower, [])
        if candidates:
            for c in candidates:
                if norm_lower in c.replace("\\", "/").lower():
                    return c
            return candidates[0]

        for full in display_to_full.values():
            if norm_lower in full.replace("\\", "/").lower():
                return full
        return None

    def _pass0_extract_keywords(self, question: str, timeout: float = 15.0) -> List[str]:
        """Fast (~2-4s) keyword expansion used ONLY to order Pass 1 queue priority."""
        sys_p = (
            "Extract 5 to 12 concise technical search keywords (C/C++ identifier fragments, domain nouns, "
            "and their English equivalents if question is non-English) from the user question. "
            "Respond ONLY with JSON: {\"keywords\": [\"kw1\", \"kw2\"]}"
        )
        raw = self.qwen.generate_raw(sys_p, f"Question: {question}", temperature=0.0, max_tokens=120, timeout=timeout)
        parsed = _parse_llm_json(raw)
        kws = [str(k).strip() for k in parsed.get("keywords", []) if len(str(k).strip()) > 1]

        raw_words = re.findall(r'[A-Za-z0-9_]+|[А-Яа-яЁё]+', question)
        for w in raw_words:
            if len(w) > 2 and w not in kws:
                kws.append(w)
        return list(dict.fromkeys(kws))[:18]

    def _scan_batch_map(
        self,
        batch: List[CodeChunk],
        question: str,
        pass_name: str,
        ask_missing: bool,
        timeout: float,
        display_to_full: Dict[str, str],
        base_to_fulls: Dict[str, List[str]]
    ) -> Tuple[List[EvidenceItem], List[str]]:
        """Runs a single stateless LLM Map call on one batch of CodeChunks."""
        batch_text_parts = []
        for ch in batch:
            batch_text_parts.append(
                f"=== FILE: {ch.display_path} (lines {ch.line_start}-{ch.line_end}) ===\n{ch.text}"
            )
        batch_body = "\n\n".join(batch_text_parts)

        if ask_missing:
            schema_desc = (
                "{\"relevant\": [{\"file\": \"Dir/File.cpp\", \"lines\": [start, end], \"symbols\": [\"Sym\"], \"reason\": \"why\"}], "
                "\"missing_symbols\": [\"ExternalFuncOrClassCalledHere\"]}"
            )
            extra_rule = (
                "Also list in \"missing_symbols\" any external functions, classes, structs, or macros "
                "used in the relevant code whose definitions are NOT shown in this batch."
            )
        else:
            schema_desc = (
                "{\"relevant\": [{\"file\": \"Dir/File.h\", \"lines\": [start, end], \"symbols\": [\"Sym\"], \"reason\": \"why\"}]}"
            )
            extra_rule = ""

        sys_p = (
            "You are a stateless C/C++ code auditor. Inspect ONLY the code chunks below.\n"
            "Ignore commented-out code (//, /* */, #if 0) unless it is the only declaration.\n"
            "Task: Identify any files/line ranges relevant to answering the user's question (declarations, "
            "implementations, data structures, protocols, constants, helpers, dispatchers).\n"
            f"{extra_rule}\n"
            "Do NOT invent file names or line numbers outside the headers provided.\n"
            f"Respond ONLY with valid JSON matching: {schema_desc}\n"
            "If nothing is relevant, respond with: {\"relevant\": []}"
        )
        usr_p = f"USER QUESTION:\n{question}\n\nCODE BATCH:\n{batch_body}\n\nJSON:"

        raw = self.qwen.generate_raw(sys_p, usr_p, temperature=0.0, max_tokens=700, timeout=timeout)
        log_agent_file_only(
            f"{pass_name.upper()} RAW LLM RESPONSE",
            f"Files in batch: {', '.join(dict.fromkeys(c.display_path for c in batch))}\n\n{raw or '<empty>'}"
        )
        parsed = _parse_llm_json(raw)

        evidences: List[EvidenceItem] = []
        for item in parsed.get("relevant", []):
            if not isinstance(item, dict):
                continue
            f_ref = str(item.get("file", "")).strip()
            resolved = self._resolve_file_ref(f_ref, display_to_full, base_to_fulls, batch_chunks=batch)
            if not resolved:
                continue

            # Pick matching chunk bounds as fallback bounds
            chunk_for_file = next((c for c in batch if c.file_path == resolved), batch[0])
            ls, le = _parse_line_range(item, chunk_for_file.line_start, chunk_for_file.line_end)

            syms = item.get("symbols", [])
            if isinstance(syms, str):
                syms = [s.strip() for s in syms.split(",") if s.strip()]
            elif not isinstance(syms, list):
                syms = []
            syms = [str(s).strip() for s in syms if str(s).strip()]

            reason = str(item.get("reason", "")).strip()
            score = 2 if syms else 1
            evidences.append(EvidenceItem(
                file_path=resolved,
                display_path=self._make_display_path(resolved),
                line_start=ls,
                line_end=le,
                symbols=syms,
                reason=reason,
                source_pass=pass_name,
                score=score
            ))

        missing: List[str] = []
        for ms in parsed.get("missing_symbols", []):
            s = str(ms).strip()
            # Filter noisy single-char names or keywords
            if len(s) >= 3 and s not in missing:
                missing.append(s)

        return evidences, missing

    def _map_headers_to_sources(
        self,
        pass1_hits: List[EvidenceItem],
        all_files: List[Dict[str, Any]],
        current_file: Optional[str],
        deep: bool
    ) -> List[Dict[str, Any]]:
        """
        Maps Pass 1 hit headers and symbols to candidate .cpp/.c source files using 3 signals:
        1. Matching filename stem (Foo.h -> Foo.cpp / Foo.c)
        2. SQLite symbols table (where hit classes/functions are defined)
        3. #include graph across indexed source files (guarded against global headers >12 includers)
        """
        source_records = [
            f for f in all_files
            if os.path.splitext(f["file_path"])[1].lower() in SOURCE_EXTS
        ]
        if not source_records:
            return []

        source_by_path: Dict[str, Dict[str, Any]] = {f["file_path"]: f for f in source_records}
        source_by_stem: Dict[str, List[str]] = {}
        for f in source_records:
            stem = os.path.splitext(os.path.basename(f["file_path"]))[0].lower()
            source_by_stem.setdefault(stem, []).append(f["file_path"])

        candidate_scores: Dict[str, int] = {}

        def add_score(fp: str, pts: int):
            if fp in source_by_path:
                candidate_scores[fp] = candidate_scores.get(fp, 0) + pts

        hit_files = {ev.file_path for ev in pass1_hits}
        if current_file and os.path.splitext(current_file)[1].lower() in SOURCE_EXTS:
            add_score(current_file, 10)
        elif current_file:
            cur_stem = os.path.splitext(os.path.basename(current_file))[0].lower()
            for sp in source_by_stem.get(cur_stem, []):
                add_score(sp, 6)

        # Channel 1: Stem match
        hit_header_basenames: Set[str] = set()
        for fp in hit_files:
            base = os.path.basename(fp)
            stem, ext = os.path.splitext(base)
            if ext.lower() in SOURCE_EXTS:
                add_score(fp, 8)
            else:
                hit_header_basenames.add(base.lower())
                for sp in source_by_stem.get(stem.lower(), []):
                    add_score(sp, 8)

        # Channel 2: SQLite symbol definition locations for Pass 1 symbols
        hit_symbols: List[str] = []
        for ev in pass1_hits:
            for s in ev.symbols:
                clean_s = s.split("::")[-1].strip()
                if len(clean_s) >= 3 and clean_s not in hit_symbols:
                    hit_symbols.append(clean_s)

        if hit_symbols:
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                for sym in hit_symbols[:40]:
                    cur.execute("""
                        SELECT DISTINCT file_path
                        FROM symbols
                        WHERE name = ? OR parent_scope = ? OR name LIKE ?
                        LIMIT 10;
                    """, (sym, sym, f"{sym}::%"))
                    for row in cur.fetchall():
                        add_score(row[0], 5)

        # Channel 3: #include reverse map with fan-out guard (ignore common headers included >12 times)
        if hit_header_basenames:
            include_to_sources: Dict[str, List[str]] = {}
            for rec in source_records:
                fp = rec["file_path"]
                lines = self._read_file_lines(fp, rec["encoding"])
                # Inspect top 80 lines where #include directives live
                for line in lines[:80]:
                    m = INCLUDE_RE.search(line)
                    if m:
                        inc_base = os.path.basename(m.group(1).replace("\\", "/")).lower()
                        if inc_base in hit_header_basenames:
                            include_to_sources.setdefault(inc_base, []).append(fp)

            for inc_base, includers in include_to_sources.items():
                if len(includers) <= 12:
                    for fp in includers:
                        add_score(fp, 3)

        ranked_paths = sorted(candidate_scores.keys(), key=lambda p: (-candidate_scores[p], p))
        limit = 60 if deep else 25
        return [source_by_path[p] for p in ranked_paths[:limit]]

    def _find_rescue_targets(
        self,
        missing_symbols: Set[str],
        found_symbols: Set[str],
        scanned_pass2_files: Set[str],
        all_files: List[Dict[str, Any]],
        already_rescued: Set[str],
        deep: bool
    ) -> List[Dict[str, Any]]:
        """
        Combines LLM-reported missing_symbols with static Call-Graph callers/callees
        to uncover skipped .h and .cpp/.c files from Pass 1.
        """
        file_by_path = {f["file_path"]: f for f in all_files}
        rescue_scores: Dict[str, int] = {}

        # Expand via static Call-Graph for key discovered symbols
        graph_symbols: Set[str] = set(missing_symbols)
        for sym in list(found_symbols)[:20]:
            for rel in self.graph.get_callees(sym, limit=6):
                if len(rel.callee_name) >= 3:
                    graph_symbols.add(rel.callee_name)
            for rel in self.graph.get_callers(sym, limit=6):
                caller_cls = rel.caller_symbol.split("::")[0]
                if len(rel.caller_symbol) >= 3:
                    graph_symbols.add(rel.caller_symbol)
                if len(caller_cls) >= 3:
                    graph_symbols.add(caller_cls)
                if rel.file_path not in scanned_pass2_files and rel.file_path not in already_rescued:
                    rescue_scores[rel.file_path] = rescue_scores.get(rel.file_path, 0) + 1

        # Resolve symbols to definition files
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            for sym in list(graph_symbols)[:60]:
                short_sym = sym.split("::")[-1].strip()
                if len(short_sym) < 3:
                    continue
                pts = 4 if sym in missing_symbols else 2
                cur.execute("""
                    SELECT DISTINCT file_path
                    FROM symbols
                    WHERE name = ? OR name = ? OR parent_scope = ? OR name LIKE ?
                    LIMIT 8;
                """, (sym, short_sym, short_sym, f"{short_sym}::%"))
                for row in cur.fetchall():
                    fp = row[0]
                    if fp not in scanned_pass2_files and fp not in already_rescued and fp in file_by_path:
                        rescue_scores[fp] = rescue_scores.get(fp, 0) + pts

        # Pair rescued headers with their .cpp/.c if not yet scanned
        stem_to_sources: Dict[str, List[str]] = {}
        for fp in file_by_path:
            if os.path.splitext(fp)[1].lower() in SOURCE_EXTS:
                st = os.path.splitext(os.path.basename(fp))[0].lower()
                stem_to_sources.setdefault(st, []).append(fp)

        for fp in list(rescue_scores.keys()):
            ext = os.path.splitext(fp)[1].lower()
            if ext in HEADER_EXTS:
                st = os.path.splitext(os.path.basename(fp))[0].lower()
                for src_fp in stem_to_sources.get(st, []):
                    if src_fp not in scanned_pass2_files and src_fp not in already_rescued:
                        rescue_scores[src_fp] = rescue_scores.get(src_fp, 0) + 3

        ranked = sorted(rescue_scores.keys(), key=lambda p: (-rescue_scores[p], p))
        limit = 30 if deep else 12
        return [file_by_path[p] for p in ranked[:limit]]

    def _merge_evidences(
        self,
        evidences: List[EvidenceItem],
        file_encodings: Dict[str, str],
        context_pad: int = 5
    ) -> List[MergedBlock]:
        """Merges overlapping or adjacent (+-context_pad, gap <= 10) EvidenceItems into full numbered blocks."""
        by_file: Dict[str, List[EvidenceItem]] = {}
        for ev in evidences:
            by_file.setdefault(ev.file_path, []).append(ev)

        merged_blocks: List[MergedBlock] = []
        for fp, items in by_file.items():
            enc = file_encodings.get(fp, "utf-8")
            lines = self._read_file_lines(fp, enc)
            total_lines = len(lines)
            if total_lines == 0:
                continue

            expanded: List[Tuple[int, int, EvidenceItem]] = []
            for it in items:
                ls = max(1, min(it.line_start, total_lines) - context_pad)
                le = min(total_lines, max(it.line_start, it.line_end) + context_pad)
                expanded.append((ls, le, it))

            expanded.sort(key=lambda x: (x[0], x[1]))

            cur_ls, cur_le, first_it = expanded[0]
            cur_syms: List[str] = list(first_it.symbols)
            cur_reasons: List[str] = [first_it.reason] if first_it.reason else []
            cur_passes: Set[str] = {first_it.source_pass}
            cur_score = first_it.score

            for ls, le, it in expanded[1:]:
                if ls <= cur_le + 10:
                    cur_le = max(cur_le, le)
                    for s in it.symbols:
                        if s not in cur_syms:
                            cur_syms.append(s)
                    if it.reason and it.reason not in cur_reasons:
                        cur_reasons.append(it.reason)
                    cur_passes.add(it.source_pass)
                    cur_score += it.score
                else:
                    code = self._format_numbered_slice(fp, cur_ls, cur_le, enc)
                    merged_blocks.append(MergedBlock(
                        file_path=fp,
                        display_path=self._make_display_path(fp),
                        line_start=cur_ls,
                        line_end=cur_le,
                        symbols=cur_syms,
                        reasons=cur_reasons,
                        source_passes=cur_passes,
                        score=cur_score,
                        code=code
                    ))
                    cur_ls, cur_le = ls, le
                    cur_syms = list(it.symbols)
                    cur_reasons = [it.reason] if it.reason else []
                    cur_passes = {it.source_pass}
                    cur_score = it.score

            code = self._format_numbered_slice(fp, cur_ls, cur_le, enc)
            merged_blocks.append(MergedBlock(
                file_path=fp,
                display_path=self._make_display_path(fp),
                line_start=cur_ls,
                line_end=cur_le,
                symbols=cur_syms,
                reasons=cur_reasons,
                source_passes=cur_passes,
                score=cur_score,
                code=code
            ))

        # Sort strongest evidence first (prefer pass2/pass3 implementations and multi-hit blocks)
        def rank_block(b: MergedBlock):
            has_impl = 1 if ("pass2_cpp" in b.source_passes or "pass3_rescue" in b.source_passes) else 0
            return (has_impl, b.score, len(b.symbols))

        merged_blocks.sort(key=rank_block, reverse=True)
        return merged_blocks

    def _reduce_blocks(
        self,
        blocks: List[MergedBlock],
        question: str,
        batch_char_budget: int,
        final_char_budget: int,
        budget: BudgetTracker,
        file_encodings: Dict[str, str],
        display_to_full: Dict[str, str],
        base_to_fulls: Dict[str, List[str]]
    ) -> Tuple[List[MergedBlock], List[str], int]:
        """Iteratively compresses MergedBlocks until total characters fit within final_char_budget."""
        summaries: List[str] = []
        rounds_done = 0
        current_blocks = list(blocks)

        while rounds_done < 3:
            total_chars = sum(len(b.code) + 120 for b in current_blocks) + sum(len(s) for s in summaries)
            if total_chars <= final_char_budget:
                break
            if not budget.can_start_batch("pass4", phase_batches_done=rounds_done):
                break

            rounds_done += 1
            now_r = time.strftime("%H:%M:%S")
            log_agent_line(f"[{now_r}] 🗜️ [Pass 4: Reduce раунд {rounds_done}] Объём доказательств {total_chars} симв. > лимит {final_char_budget}. Сжатие...")

            chunks = [
                CodeChunk(
                    file_path=b.file_path,
                    display_path=b.display_path,
                    line_start=b.line_start,
                    line_end=b.line_end,
                    text=b.code
                )
                for b in current_blocks
            ]
            batches = self._pack_chunks_into_batches(chunks, batch_char_budget)
            next_evidences: List[EvidenceItem] = []

            for b_idx, batch in enumerate(batches, 1):
                if b_idx > 1 and not budget.can_start_batch("pass4", phase_batches_done=b_idx - 1):
                    # Preserve remaining unreduced blocks deterministically rather than dropping them
                    for rem_ch in batch:
                        next_evidences.append(EvidenceItem(
                            file_path=rem_ch.file_path,
                            display_path=rem_ch.display_path,
                            line_start=rem_ch.line_start,
                            line_end=rem_ch.line_end,
                            source_pass="pass4_keep",
                            score=1
                        ))
                    continue

                t_b0 = time.time()
                batch_text = "\n\n".join(
                    f"=== FILE: {c.display_path} (lines {c.line_start}-{c.line_end}) ===\n{c.text}"
                    for c in batch
                )
                sys_p = (
                    "You are compressing C/C++ evidence blocks for final synthesis.\n"
                    "Keep ONLY exact sub-ranges directly needed to answer the question, AND write a compact factual summary "
                    "citing exact [FileName:Line] references for key facts.\n"
                    "Respond ONLY with valid JSON: "
                    "{\"keep\": [{\"file\": \"Dir/File.cpp\", \"lines\": [start, end], \"symbols\": [\"Sym\"]}], "
                    "\"summary\": \"concise facts with [File.cpp:123]\"}"
                )
                usr_p = f"QUESTION:\n{question}\n\nEVIDENCE BATCH:\n{batch_text}\n\nJSON:"
                raw = self.qwen.generate_raw(
                    sys_p, usr_p, temperature=0.0, max_tokens=750, timeout=budget.batch_timeout(45.0)
                )
                budget.record_batch(time.time() - t_b0)
                parsed = _parse_llm_json(raw)

                summary_str = str(parsed.get("summary", "")).strip()
                if summary_str:
                    summaries.append(summary_str)

                kept_in_batch = 0
                for item in parsed.get("keep", []) or parsed.get("relevant", []):
                    if not isinstance(item, dict):
                        continue
                    resolved = self._resolve_file_ref(str(item.get("file", "")), display_to_full, base_to_fulls, batch_chunks=batch)
                    if not resolved:
                        continue
                    ch_match = next((c for c in batch if c.file_path == resolved), batch[0])
                    ls, le = _parse_line_range(item, ch_match.line_start, ch_match.line_end)
                    syms = item.get("symbols", [])
                    if not isinstance(syms, list):
                        syms = []
                    next_evidences.append(EvidenceItem(
                        file_path=resolved,
                        display_path=self._make_display_path(resolved),
                        line_start=ls,
                        line_end=le,
                        symbols=[str(s) for s in syms],
                        source_pass="pass4_keep",
                        score=2
                    ))
                    kept_in_batch += 1

                # Safeguard: if Qwen failed to return valid keep ranges for a batch, retain first block trimmed
                if kept_in_batch == 0 and batch:
                    top_ch = batch[0]
                    next_evidences.append(EvidenceItem(
                        file_path=top_ch.file_path,
                        display_path=top_ch.display_path,
                        line_start=top_ch.line_start,
                        line_end=min(top_ch.line_end, top_ch.line_start + 90),
                        source_pass="pass4_keep",
                        score=1
                    ))

            if next_evidences:
                prev_chars = total_chars
                current_blocks = self._merge_evidences(next_evidences, file_encodings, context_pad=0)
                new_chars = sum(len(b.code) + 120 for b in current_blocks)
                if new_chars >= int(prev_chars * 0.95):
                    break
            else:
                break

        # Final deterministic budget fit so Pass 5 prompt never overflows n_ctx
        summary_budget = min(len("\n".join(summaries)), int(final_char_budget * 0.25)) if summaries else 0
        code_budget = max(4000, final_char_budget - summary_budget)

        fitted_blocks: List[MergedBlock] = []
        used_chars = 0
        for blk in current_blocks:
            blk_cost = len(blk.code) + 120
            if used_chars + blk_cost <= code_budget:
                fitted_blocks.append(blk)
                used_chars += blk_cost
            else:
                rem_chars = code_budget - used_chars - 120
                if rem_chars >= 600:
                    trimmed_code = smart_truncate_code(blk.code, max_chars=rem_chars)
                    fitted_blocks.append(MergedBlock(
                        file_path=blk.file_path,
                        display_path=blk.display_path,
                        line_start=blk.line_start,
                        line_end=blk.line_end,
                        symbols=blk.symbols,
                        reasons=blk.reasons,
                        source_passes=blk.source_passes,
                        score=blk.score,
                        code=trimmed_code
                    ))
                break

        return fitted_blocks, summaries, rounds_done

    def run(
        self,
        question: str,
        current_file: Optional[str] = None,
        current_line: Optional[int] = None,
        selected_code: Optional[str] = None,
        language: str = "ru",
        budget_sec: Optional[int] = None,
        deep: bool = False
    ) -> SearchResult:
        effective_budget = float(budget_sec if budget_sec is not None else (600 if deep else 300))
        budget = BudgetTracker(total_budget_sec=effective_budget, deep=deep)
        mode_tag = "DEEP" if deep else "STANDARD"
        self._file_lines_cache.clear()

        log_agent_boundary(f"REDUCING AGENT ({mode_tag}, бюджет {int(effective_budget)}с): '{question}'")

        all_files = self._load_indexed_files()
        if not all_files:
            return SearchResult(
                answer="⚠️ Индекс проекта пуст. Сначала выполните индексацию решения/папки проекта.",
                citations=[],
                success=False,
                error="Empty index"
            )

        n_ctx = self.qwen.get_context_size()
        batch_char_budget = max(8000, int(n_ctx * 0.60 * 3.0))
        final_char_budget = max(10000, int((n_ctx - 2800) * 2.8))

        log_agent_line(
            f"[{time.strftime('%H:%M:%S')}] 🧠 [Reducing {mode_tag}] n_ctx={n_ctx} | "
            f"batch={batch_char_budget} симв. | final_window={final_char_budget} симв. | "
            f"файлов в индексе: {len(all_files)}"
        )

        file_encodings: Dict[str, str] = {f["file_path"]: f["encoding"] for f in all_files}
        display_to_full: Dict[str, str] = {}
        base_to_fulls: Dict[str, List[str]] = {}
        for f in all_files:
            fp = f["file_path"]
            disp = self._make_display_path(fp)
            display_to_full[disp.lower()] = fp
            base_to_fulls.setdefault(os.path.basename(fp).lower(), []).append(fp)

        # ---------------- PASS 0: Priority Ordering Only ----------------
        log_agent_line(f"[{time.strftime('%H:%M:%S')}] ⏳ [Pass 0] Извлечение ключевых терминов вопроса через LLM...")
        keywords = self._pass0_extract_keywords(question, timeout=budget.batch_timeout(15.0))
        bm25_boost_files = {m["file_path"] for m in self.fts.search_bm25(question, limit=15, custom_tokens=keywords or None)}
        log_agent_line(f"[{time.strftime('%H:%M:%S')}] 🔑 [Pass 0] Ключевые слова для порядка очереди: {keywords[:10]}")

        # ---------------- PASS 1: All Headers + Standalone Outlines ----------------
        header_records = [f for f in all_files if os.path.splitext(f["file_path"])[1].lower() in HEADER_EXTS]
        source_records = [f for f in all_files if os.path.splitext(f["file_path"])[1].lower() in SOURCE_EXTS]

        header_stems = {os.path.splitext(os.path.basename(h["file_path"]))[0].lower() for h in header_records}
        standalone_sources = [
            s for s in source_records
            if os.path.splitext(os.path.basename(s["file_path"]))[0].lower() not in header_stems
        ]

        # Non-C/C++ project fallback
        if not header_records:
            pass1_targets = list(all_files)
            use_outlines_for_sources = False
        else:
            pass1_targets = list(header_records)
            use_outlines_for_sources = True

        # Queue prioritization (ALL targets remain in queue; only order changes)
        cur_stem = os.path.splitext(os.path.basename(current_file))[0].lower() if current_file else ""
        kw_lowers = [k.lower() for k in keywords if len(k) >= 2]

        def target_priority(rec: Dict[str, Any]) -> Tuple[int, str]:
            fp = rec["file_path"]
            base = os.path.basename(fp).lower()
            stem = os.path.splitext(base)[0]
            if current_file and (fp == current_file or (cur_stem and stem == cur_stem)):
                return (0, fp)
            score = 0
            if fp in bm25_boost_files:
                score += 5
            if any(kw in base for kw in kw_lowers):
                score += 4
            if score > 0:
                return (1, f"{999 - score:03d}_{fp}")
            return (2, fp)

        pass1_targets.sort(key=target_priority)
        standalone_sources.sort(key=target_priority)

        pass1_chunks: List[CodeChunk] = []
        max_chunks_p1 = None if deep else 6
        for rec in pass1_targets:
            fp = rec["file_path"]
            pass1_chunks.extend(self._chunk_file(
                fp, self._make_display_path(fp), rec["encoding"],
                max_lines=320, overlap=15, max_chunks=max_chunks_p1
            ))

        if use_outlines_for_sources:
            for rec in standalone_sources:
                out_ch = self._build_standalone_outline(
                    rec["file_path"], self._make_display_path(rec["file_path"]), rec["encoding"]
                )
                if out_ch:
                    pass1_chunks.append(out_ch)

        pass1_batches = self._pack_chunks_into_batches(pass1_chunks, batch_char_budget)
        pass1_evidences: List[EvidenceItem] = []
        pass1_batches_done = 0

        log_agent_line(
            f"[{time.strftime('%H:%M:%S')}] 📂 [Pass 1: Заголовки + Standalone] Файлов: {len(pass1_targets) + (len(standalone_sources) if use_outlines_for_sources else 0)}, батчей: {len(pass1_batches)}"
        )

        for idx, batch in enumerate(pass1_batches, 1):
            if not budget.can_start_batch("pass1", phase_batches_done=pass1_batches_done):
                log_agent_line(f"[{time.strftime('%H:%M:%S')}] ⏱️ [Pass 1] Достигнут лимит фазы ({budget.elapsed():.1f}с). Переход к Pass 2.")
                break
            preview = ", ".join(list(dict.fromkeys(os.path.basename(c.file_path) for c in batch))[:4])
            log_agent_line(f"[{time.strftime('%H:%M:%S')}]    ▶ [Pass 1 | {idx}/{len(pass1_batches)}] Анализ батча ({preview})...")
            t0 = time.time()
            evs, _ = self._scan_batch_map(
                batch, question, "pass1_headers", False,
                budget.batch_timeout(45.0), display_to_full, base_to_fulls
            )
            dt = time.time() - t0
            budget.record_batch(dt)
            pass1_batches_done += 1
            pass1_evidences.extend(evs)
            log_agent_line(
                f"[{time.strftime('%H:%M:%S')}]    ✓ [Pass 1 | {idx}/{len(pass1_batches)} | {dt:.1f}с] найдено блоков: {len(evs)}"
            )

        # ---------------- PASS 2: Map Headers -> .cpp/.c ----------------
        pass2_targets = self._map_headers_to_sources(pass1_evidences, all_files, current_file, deep=deep)
        pass2_chunks: List[CodeChunk] = []
        max_chunks_p2 = None if deep else 6
        scanned_pass2_files: Set[str] = set()

        for rec in pass2_targets:
            fp = rec["file_path"]
            scanned_pass2_files.add(fp)
            pass2_chunks.extend(self._chunk_file(
                fp, self._make_display_path(fp), rec["encoding"],
                max_lines=320, overlap=15, max_chunks=max_chunks_p2
            ))

        pass2_batches = self._pack_chunks_into_batches(pass2_chunks, batch_char_budget)
        pass2_evidences: List[EvidenceItem] = []
        missing_symbols: Set[str] = set()
        pass2_batches_done = 0

        log_agent_line(
            f"[{time.strftime('%H:%M:%S')}] 📄 [Pass 2: Реализации .cpp/.c] Отобрано файлов: {len(pass2_targets)}, батчей: {len(pass2_batches)}"
        )

        for idx, batch in enumerate(pass2_batches, 1):
            if not budget.can_start_batch("pass2", phase_batches_done=pass2_batches_done):
                log_agent_line(f"[{time.strftime('%H:%M:%S')}] ⏱️ [Pass 2] Достигнут лимит фазы ({budget.elapsed():.1f}с). Переход к Pass 3 Rescue.")
                break
            preview = ", ".join(list(dict.fromkeys(os.path.basename(c.file_path) for c in batch))[:4])
            log_agent_line(f"[{time.strftime('%H:%M:%S')}]    ▶ [Pass 2 | {idx}/{len(pass2_batches)}] Анализ батча ({preview})...")
            t0 = time.time()
            evs, miss = self._scan_batch_map(
                batch, question, "pass2_cpp", True,
                budget.batch_timeout(45.0), display_to_full, base_to_fulls
            )
            dt = time.time() - t0
            budget.record_batch(dt)
            pass2_batches_done += 1
            pass2_evidences.extend(evs)
            missing_symbols.update(miss)
            log_agent_line(
                f"[{time.strftime('%H:%M:%S')}]    ✓ [Pass 2 | {idx}/{len(pass2_batches)} | {dt:.1f}с] найдено: {len(evs)}, внешних символов: {len(miss)}"
            )

        # ---------------- PASS 3: Rescue Skipped Files ----------------
        found_symbols: Set[str] = set()
        for ev in pass1_evidences + pass2_evidences:
            found_symbols.update(ev.symbols)

        already_rescued: Set[str] = set()
        rescue_targets = self._find_rescue_targets(
            missing_symbols, found_symbols, scanned_pass2_files, all_files, already_rescued, deep=deep
        )

        pass3_evidences: List[EvidenceItem] = []
        pass3_files_scanned = 0
        pass3_batches_done = 0

        if rescue_targets and budget.can_start_batch("pass3", phase_batches_done=0):
            rescue_chunks: List[CodeChunk] = []
            for rec in rescue_targets:
                fp = rec["file_path"]
                already_rescued.add(fp)
                pass3_files_scanned += 1
                rescue_chunks.extend(self._chunk_file(
                    fp, self._make_display_path(fp), rec["encoding"],
                    max_lines=320, overlap=15, max_chunks=max_chunks_p2
                ))

            rescue_batches = self._pack_chunks_into_batches(rescue_chunks, batch_char_budget)
            log_agent_line(
                f"[{time.strftime('%H:%M:%S')}] 🛟 [Pass 3: Rescue отсеянных] Файлов: {len(rescue_targets)}, батчей: {len(rescue_batches)}"
            )

            pass3_new_missing: Set[str] = set()
            for idx, batch in enumerate(rescue_batches, 1):
                if not budget.can_start_batch("pass3", phase_batches_done=pass3_batches_done):
                    break
                preview = ", ".join(list(dict.fromkeys(os.path.basename(c.file_path) for c in batch))[:4])
                log_agent_line(f"[{time.strftime('%H:%M:%S')}]    ▶ [Pass 3 | {idx}/{len(rescue_batches)}] Досканирование ({preview})...")
                t0 = time.time()
                evs, miss = self._scan_batch_map(
                    batch, question, "pass3_rescue", deep,
                    budget.batch_timeout(45.0), display_to_full, base_to_fulls
                )
                dt = time.time() - t0
                budget.record_batch(dt)
                pass3_batches_done += 1
                pass3_evidences.extend(evs)
                pass3_new_missing.update(miss)
                log_agent_line(
                    f"[{time.strftime('%H:%M:%S')}]    ✓ [Pass 3 | {idx}/{len(rescue_batches)} | {dt:.1f}с] спасено блоков: {len(evs)}"
                )

            # Deep Mode Hop 2
            if deep and pass3_new_missing and budget.can_start_batch("pass3", phase_batches_done=pass3_batches_done):
                hop2_targets = self._find_rescue_targets(
                    pass3_new_missing, found_symbols, scanned_pass2_files, all_files, already_rescued, deep=True
                )[:10]
                if hop2_targets:
                    hop2_chunks: List[CodeChunk] = []
                    for rec in hop2_targets:
                        fp = rec["file_path"]
                        already_rescued.add(fp)
                        pass3_files_scanned += 1
                        hop2_chunks.extend(self._chunk_file(
                            fp, self._make_display_path(fp), rec["encoding"], max_lines=320, overlap=15
                        ))
                    hop2_batches = self._pack_chunks_into_batches(hop2_chunks, batch_char_budget)
                    log_agent_line(
                        f"[{time.strftime('%H:%M:%S')}] 🛟 [Pass 3b: Deep Rescue Hop 2] Файлов: {len(hop2_targets)}, батчей: {len(hop2_batches)}"
                    )
                    for idx, batch in enumerate(hop2_batches, 1):
                        if not budget.can_start_batch("pass3", phase_batches_done=pass3_batches_done):
                            break
                        t0 = time.time()
                        evs, _ = self._scan_batch_map(
                            batch, question, "pass3_rescue", False,
                            budget.batch_timeout(45.0), display_to_full, base_to_fulls
                        )
                        dt = time.time() - t0
                        budget.record_batch(dt)
                        pass3_batches_done += 1
                        pass3_evidences.extend(evs)
                        log_agent_line(
                            f"[{time.strftime('%H:%M:%S')}]    ✓ [Pass 3b | {idx}/{len(hop2_batches)} | {dt:.1f}с] спасено блоков: {len(evs)}"
                        )

        # Combine all evidence; fallback to BM25 snippets only if zero chunks matched
        all_evidences = pass1_evidences + pass2_evidences + pass3_evidences
        if not all_evidences:
            log_agent_line(f"[{time.strftime('%H:%M:%S')}] ⚠️ [Reducing] Карты дали 0 совпадений, резервный подбор через BM25.")
            for sn in self.fts.search_bm25(question, limit=10, custom_tokens=keywords or None):
                fp = sn["file_path"]
                all_evidences.append(EvidenceItem(
                    file_path=fp,
                    display_path=self._make_display_path(fp),
                    line_start=int(sn["line_start"]),
                    line_end=int(sn["line_end"]),
                    symbols=[sn["symbol_name"]] if sn.get("symbol_name") else [],
                    reason="BM25 fallback match",
                    source_pass="bm25_fallback",
                    score=1
                ))

        # ---------------- PASS 4: Merge & Recursive Reduce ----------------
        merged_blocks = self._merge_evidences(all_evidences, file_encodings)
        final_blocks, reduce_summaries, reduce_rounds = self._reduce_blocks(
            merged_blocks, question, batch_char_budget, final_char_budget,
            budget, file_encodings, display_to_full, base_to_fulls
        )

        # ---------------- PASS 5: Final Synthesis ----------------
        system_prompt = build_system_prompt(language=language)
        ctx_parts: List[str] = []

        if current_file:
            ctx_parts.append(
                f"--- АКТИВНЫЙ ФАЙЛ В РЕДАКТОРЕ VS2005 ---\n"
                f"ССЫЛКА ДЛЯ ЦИТИРОВАНИЯ: [{os.path.basename(current_file)}:{current_line or 1}]"
            )
        if selected_code:
            ctx_parts.append(f"--- ВЫДЕЛЕННЫЙ ПОЛЬЗОВАТЕЛЕМ КОД ---\n{selected_code}")

        if reduce_summaries:
            ctx_parts.append(
                "--- ВЫЖИМКА ПО РЕЗУЛЬТАТАМ МНОГОПРОХОДНОГО СЖАТИЯ ---\n" +
                "\n".join(f"• {s}" for s in reduce_summaries)
            )

        ctx_parts.append("--- ПРОВЕРЕННЫЙ КОД ПРОЕКТА (СОБРАН PASS 1 -> PASS 2 -> PASS 3 RESCUE) ---")
        final_line_count = 0
        for blk in final_blocks:
            short_name = os.path.basename(blk.file_path)
            ext = os.path.splitext(short_name)[1][1:] or "cpp"
            final_line_count += blk.code.count("\n") + 1
            passes_str = ", ".join(sorted(blk.source_passes))
            ctx_parts.append(
                f"ССЫЛКА ДЛЯ ЦИТИРОВАНИЯ: [{short_name}:{blk.line_start}] "
                f"(строки {blk.line_start}-{blk.line_end}, путь: {blk.display_path}, найдено в: {passes_str})\n"
                f"```{ext}\n{blk.code}\n```"
            )

        ctx_parts.append(
            f"--- ВОПРОС ПОЛЬЗОВАТЕЛЯ И ЗАДАЧА ---\n"
            f"Вопрос: {question}\n\n"
            f"Инструкция: Дай точный, технически подробный ответ исключительно по приведённым выше фрагментам кода. "
            f"Каждый класс, функцию и файл сопровождай точной ссылкой [FileName.ext:LineNumber].\n"
            f"Ответ:"
        )
        final_user_prompt = "\n\n".join(ctx_parts)

        log_agent_section(
            "PASS 5: ФИНАЛЬНЫЙ ПРОМПТ SYNTHESIS (REDUCING AGENT)",
            f"[SYSTEM PROMPT]\n{system_prompt}\n\n[USER PROMPT]\n{final_user_prompt}"
        )

        ai_answer = self.qwen.generate_response(
            system_prompt=system_prompt,
            user_prompt=final_user_prompt,
            raw_user_question=question,
            temperature=0.15,
            max_tokens=3072,
            use_history=False
        )
        log_agent_section("PASS 5: ФИНАЛЬНЫЙ ОТВЕТ QWEN", ai_answer)

        # Build verified CodeCitations: prioritize inline [File:Line] cited by model, then top MergedBlocks
        citations: List[CodeCitation] = []
        seen_cits: Set[Tuple[str, int]] = set()

        for m in CITATION_RE.finditer(ai_answer):
            f_ref = m.group(1)
            line_num = int(m.group(2))
            resolved = self._resolve_file_ref(f_ref, display_to_full, base_to_fulls)
            if resolved:
                key = (resolved, line_num)
                if key not in seen_cits:
                    seen_cits.add(key)
                    citations.append(CodeCitation(
                        file=resolved,
                        line=line_num,
                        symbol=f_ref,
                        snippet=f"Cited in answer at line {line_num}"
                    ))

        for blk in final_blocks:
            key = (blk.file_path, blk.line_start)
            if key not in seen_cits:
                seen_cits.add(key)
                sym_label = ", ".join(blk.symbols[:3]) if blk.symbols else os.path.basename(blk.file_path)
                reason_snip = blk.reasons[0][:100] if blk.reasons else f"Lines {blk.line_start}-{blk.line_end}"
                citations.append(CodeCitation(
                    file=blk.file_path,
                    line=blk.line_start,
                    symbol=sym_label,
                    snippet=reason_snip
                ))

        total_elapsed = budget.elapsed()
        telemetry = (
            f"\n\n---\n📊 **[Reducing: {mode_tag} | "
            f"Pass 1: {pass1_batches_done} батч. ({len(pass1_targets)} хед.) | "
            f"Pass 2: {len(pass2_targets)} .cpp | "
            f"Pass 3 Rescue: {pass3_files_scanned} файл. | "
            f"Reduce: {reduce_rounds} раунд. | "
            f"Итого кода: {final_line_count} строк | "
            f"Время: {total_elapsed:.1f}с]**"
        )

        return SearchResult(
            answer=ai_answer + telemetry,
            citations=citations,
            success=True
        )
