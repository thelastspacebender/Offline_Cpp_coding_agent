"""
3-Stage Semantic Funnel + Call-Graph Rescue Agent.

Fast, 100%-coverage codebase analysis powered by the pre-built Adaptive Semantic Map:
- Stage 1 (Map Triage + SQLite Insurance): Scans 100% of project files in 2-4 compact
  ~3k-token batches over the semantic map, reinforced by instant SQLite FTS5/symbols/call-graph
  matches for exact C++ identifiers -> narrows down to 10-15 target files.
- Stage 2 (AST Method Skeleton): Builds a zero-cost signature + outgoing-call skeleton from
  SQLite for the selected files -> LLM selects 10-18 specific methods/classes in 1-2 batches.
- Stage 3 (Raw Source Bodies + Call-Graph Rescue): Loads full line-numbered raw bodies from
  disk and automatically rescues missing callee implementations (`%::MethodName`) and callers
  across the entire codebase via AST + Call-Graph.
- Stage 4 (Grounded Final Synthesis): Generates a strictly grounded answer with exact
  [File:Line] citations and an explicit anti-hallucination rule.
"""

import os
import re
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple, Set

from ..core.models import CodeCitation, SearchResult
from ..core.agent_logger import (
    log_agent_boundary,
    log_agent_section,
    log_agent_line,
    log_agent_file_only,
)
from ..storage.sqlite_db import DatabaseManager
from ..storage.graph_store import GraphStore
from ..storage.fts_index import FtsIndex
from .qwen_service import QwenService
from .prompt_builder import build_system_prompt, smart_truncate_code
from .map_builder import MapBuilder, _make_display_path, _read_file_lines_safe
from .reducing_agent import (
    CALL_TOKEN_RE,
    CITATION_RE,
    CPP_CONTROL_WORDS,
    GENERIC_NOISE_WORDS,
    HEADER_EXTS,
    SOURCE_EXTS,
    ReducingAgent,
    _parse_llm_json,
)


MAP_BATCH_MAX_LINES = 180
MAP_BATCH_MAX_CHARS = 9500
SKELETON_BATCH_MAX_CHARS = 9500


@dataclass
class SkeletonItem:
    item_id: str
    file_path: str
    display_path: str
    name: str
    kind: str
    line_start: int
    line_end: int
    signature: str
    calls: List[str] = field(default_factory=list)
    role_tag: str = "core"


@dataclass
class RawCodeBlock:
    file_path: str
    display_path: str
    line_start: int
    line_end: int
    symbols: List[str] = field(default_factory=list)
    source_tag: str = "stage2"  # stage2 | rescue_callee | rescue_caller | fallback
    role_tag: str = "core"
    priority: int = 1
    code: str = ""


class SemanticFunnelAgent:
    """Executes the 3-Stage Semantic Funnel + Call-Graph Rescue pipeline."""

    def __init__(
        self,
        db_manager: DatabaseManager,
        qwen_service: QwenService,
        graph_store: GraphStore,
        fts_index: FtsIndex,
        map_builder: MapBuilder,
    ):
        self.db = db_manager
        self.qwen = qwen_service
        self.graph = graph_store
        self.fts = fts_index
        self.map_builder = map_builder
        self._file_lines_cache: Dict[str, List[str]] = {}

    def _get_lines(self, file_path: str, encoding: Optional[str] = None) -> List[str]:
        if file_path not in self._file_lines_cache:
            self._file_lines_cache[file_path] = _read_file_lines_safe(file_path, encoding)
        return self._file_lines_cache[file_path]

    def _format_numbered_slice(self, file_path: str, line_start: int, line_end: int, encoding: Optional[str] = None) -> str:
        lines = self._get_lines(file_path, encoding)
        if not lines:
            return ""
        total = len(lines)
        ls = max(1, min(line_start, total))
        le = max(ls, min(line_end, total))
        return "\n".join(f"{idx}| {lines[idx - 1]}" for idx in range(ls, le + 1))

    @staticmethod
    def _resolve_file_ref(
        ref: str,
        display_to_full: Dict[str, str],
        base_to_fulls: Dict[str, List[str]],
    ) -> Optional[str]:
        if not ref:
            return None
        norm = ref.strip().strip('"\'[]').replace("\\", "/")
        # Strip trailing (L1-20) or :123 if model echoed map formatting
        norm = re.sub(r'\s*\[[A-Z]+\].*$', '', norm).strip()
        norm = re.sub(r':\d+$', '', norm).strip()
        low = norm.lower()

        if low in display_to_full:
            return display_to_full[low]

        base_low = os.path.basename(low)
        cands = base_to_fulls.get(base_low, [])
        if cands:
            for c in cands:
                if low in c.replace("\\", "/").lower():
                    return c
            return cands[0]

        for full in display_to_full.values():
            if low in full.replace("\\", "/").lower():
                return full
        return None

    # ------------------------------------------------------------------
    # Main Entrypoint
    # ------------------------------------------------------------------
    def run(
        self,
        question: str,
        current_file: Optional[str] = None,
        current_line: Optional[int] = None,
        selected_code: Optional[str] = None,
        language: str = "ru",
        budget_sec: int = 180,
        deep: bool = False,
    ) -> SearchResult:
        t0 = time.time()
        self._file_lines_cache.clear()
        mode_tag = "Deep" if deep else "Standard"

        log_agent_boundary(f"SMART FUNNEL AGENT ({mode_tag}): '{question}'")

        # Load indexed file metadata & formatted semantic map
        all_files = self._load_indexed_files()
        if not all_files:
            return SearchResult(
                answer="⚠️ Индекс проекта пуст. Сначала выполните индексацию проекта.",
                citations=[],
                success=False,
                error="Empty index",
            )

        map_entries = self.map_builder.get_formatted_map_lines()
        if not map_entries:
            return SearchResult(
                answer="⚠️ Семантическая карта проекта ещё не построена. Нажмите «Собрать карту».",
                citations=[],
                success=False,
                error="Empty semantic map",
            )

        display_to_full: Dict[str, str] = {}
        base_to_fulls: Dict[str, List[str]] = {}
        file_encodings: Dict[str, str] = {}
        file_line_counts: Dict[str, int] = {}
        file_roles: Dict[str, str] = {}

        for rec in all_files:
            fp = rec["file_path"]
            disp = _make_display_path(fp)
            display_to_full[disp.lower()] = fp
            base_to_fulls.setdefault(os.path.basename(fp).lower(), []).append(fp)
            file_encodings[fp] = rec["encoding"]
            file_line_counts[fp] = rec["line_count"]

        for entry in map_entries:
            fp = entry["file_path"]
            if fp not in file_roles:
                file_roles[fp] = entry["role_tag"]

        # Context window sizing
        n_ctx = self.qwen.get_context_size()
        final_char_budget = max(9000, min(32000, int((n_ctx - 4500) * 2.4)))

        excluded_stems = ReducingAgent._extract_exclusions(question)
        q_idents = ReducingAgent._extract_question_identifiers(question, excluded_stems)

        log_agent_line(
            f"[{time.strftime('%H:%M:%S')}] 🗺️ [Smart Funnel] Старт ({mode_tag}) | "
            f"n_ctx={n_ctx} | Строк карты: {len(map_entries)} | "
            f"Идентификаторы: {q_idents[:6] or 'нет'} | Исключения: {sorted(excluded_stems) or 'нет'}"
        )

        # ---------------- STAGE 1: Map Triage + SQLite Insurance ----------------
        max_target_files = 20 if deep else 14
        selected_files, stage1_batches_done, stage1_reasons = self._stage1_select_files(
            question=question,
            map_entries=map_entries,
            q_idents=q_idents,
            excluded_stems=excluded_stems,
            display_to_full=display_to_full,
            base_to_fulls=base_to_fulls,
            file_roles=file_roles,
            current_file=current_file,
            max_files=max_target_files,
        )

        log_agent_line(
            f"[{time.strftime('%H:%M:%S')}] 📂 [Stage 1 -> {len(selected_files)} файлов за {time.time() - t0:.1f}с]: "
            f"{', '.join(os.path.basename(f) for f in selected_files[:10])}"
        )

        # ---------------- STAGE 2: AST Method Skeleton Selection ----------------
        max_methods = 24 if deep else 16
        selected_items, extra_symbols, total_skeleton_items = self._stage2_select_methods(
            question=question,
            selected_files=selected_files,
            q_idents=q_idents,
            file_roles=file_roles,
            file_line_counts=file_line_counts,
            max_methods=max_methods,
        )

        log_agent_line(
            f"[{time.strftime('%H:%M:%S')}] 🧩 [Stage 2 -> {len(selected_items)}/{total_skeleton_items} методов за {time.time() - t0:.1f}с]: "
            f"{', '.join(it.name for it in selected_items[:8])} | доп. символы: {extra_symbols[:6] or 'нет'}"
        )

        # ---------------- STAGE 3: Raw Bodies + Call-Graph Rescue ----------------
        max_rescue = 12 if deep else 8
        raw_blocks, rescued_count = self._stage3_load_and_rescue(
            selected_items=selected_items,
            extra_symbols=extra_symbols,
            q_idents=q_idents,
            excluded_stems=excluded_stems,
            file_encodings=file_encodings,
            file_roles=file_roles,
            max_rescue=max_rescue,
            final_char_budget=final_char_budget,
        )

        total_code_lines = sum(blk.code.count("\n") + 1 for blk in raw_blocks if blk.code)
        log_agent_line(
            f"[{time.strftime('%H:%M:%S')}] 🛟 [Stage 3 -> Блоков: {len(raw_blocks)} (Rescue: +{rescued_count}), "
            f"сырого кода: {total_code_lines} строк за {time.time() - t0:.1f}с]"
        )

        # ---------------- STAGE 4: Grounded Final Synthesis ----------------
        system_prompt = build_system_prompt(language=language) + (
            "\n\nSTRICT ANTI-HALLUCINATION RULE:\n"
            "- Base your answer EXCLUSIVELY on the exact raw code blocks provided below.\n"
            "- Never fabricate, guess, or reconstruct a function's implementation body from a call site.\n"
            "- If a function is called in the provided code (e.g. `obj->Foo()`) but its implementation body "
            "is NOT present in the provided blocks, explicitly state that only the call site is shown and the "
            "implementation body is not in the retrieved context.\n"
            "- Every file, class, method, and line number MUST match the numbered lines in the provided blocks."
        )

        ctx_parts: List[str] = []
        if current_file:
            ctx_parts.append(
                f"--- АКТИВНЫЙ ФАЙЛ В РЕДАКТОРЕ ---\n"
                f"ССЫЛКА ДЛЯ ЦИТИРОВАНИЯ: [{os.path.basename(current_file)}:{current_line or 1}]"
            )
        if selected_code:
            ctx_parts.append(f"--- ВЫДЕЛЕННЫЙ ПОЛЬЗОВАТЕЛЕМ КОД ---\n{selected_code}")

        if stage1_reasons:
            summary_lines = [
                f"• {_make_display_path(fp)} [{file_roles.get(fp, 'core').upper()}]: {stage1_reasons[fp]}"
                for fp in selected_files[:10] if fp in stage1_reasons
            ]
            if summary_lines:
                ctx_parts.append("--- КАРТА ОТОБРАННЫХ МОДУЛЕЙ ---\n" + "\n".join(summary_lines))

        ctx_parts.append("--- ПРОВЕРЕННЫЙ СЫРОЙ КОД ПРОЕКТА (STAGE 1 КАРТА -> STAGE 2 AST -> STAGE 3 CALL-GRAPH RESCUE) ---")
        for blk in raw_blocks:
            short_name = os.path.basename(blk.file_path)
            ext = os.path.splitext(short_name)[1][1:] or "cpp"
            sym_str = ", ".join(blk.symbols[:4]) if blk.symbols else short_name
            ctx_parts.append(
                f"ССЫЛКА ДЛЯ ЦИТИРОВАНИЯ: [{short_name}:{blk.line_start}] "
                f"(строки {blk.line_start}-{blk.line_end}, путь: {blk.display_path}, "
                f"роль: [{blk.role_tag.upper()}], символы: {sym_str}, источник: {blk.source_tag})\n"
                f"```{ext}\n{blk.code}\n```"
            )

        ctx_parts.append(
            f"--- ВОПРОС ПОЛЬЗОВАТЕЛЯ И ЗАДАЧА ---\n"
            f"Вопрос: {question}\n\n"
            f"Инструкция: Дай точный, технически подробный ответ исключительно по приведённым выше фрагментам кода. "
            f"Каждый класс, функцию и файл сопровождай точной ссылкой [FileName.ext:LineNumber]. "
            f"Не выдумывай код, которого нет в фрагментах выше.\n"
            f"Ответ:"
        )
        final_user_prompt = "\n\n".join(ctx_parts)

        log_agent_section(
            "SMART FUNNEL STAGE 4: ФИНАЛЬНЫЙ ПРОМПТ SYNTHESIS",
            f"[SYSTEM PROMPT]\n{system_prompt}\n\n[USER PROMPT]\n{final_user_prompt}"
        )

        ai_answer = self.qwen.generate_response(
            system_prompt=system_prompt,
            user_prompt=final_user_prompt,
            raw_user_question=question,
            temperature=0.15,
            max_tokens=3072,
            use_history=False,
        )
        log_agent_section("SMART FUNNEL STAGE 4: ФИНАЛЬНЫЙ ОТВЕТ QWEN", ai_answer)

        # Build verified CodeCitations
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
                        snippet=f"Cited in answer at line {line_num}",
                    ))

        for blk in raw_blocks:
            key = (blk.file_path, blk.line_start)
            if key not in seen_cits:
                seen_cits.add(key)
                sym_label = ", ".join(blk.symbols[:3]) if blk.symbols else os.path.basename(blk.file_path)
                citations.append(CodeCitation(
                    file=blk.file_path,
                    line=blk.line_start,
                    symbol=sym_label,
                    snippet=f"Lines {blk.line_start}-{blk.line_end} ({blk.source_tag})",
                ))

        total_elapsed = time.time() - t0
        telemetry = (
            f"\n\n---\n📊 **[Smart Funnel: {mode_tag} | "
            f"Карта: {len(map_entries)} строк ({stage1_batches_done} батч.) -> "
            f"Файлов: {len(selected_files)} -> "
            f"AST-методов: {len(selected_items)} -> "
            f"Rescue: +{rescued_count} | "
            f"Кода: {total_code_lines} строк | "
            f"Время: {total_elapsed:.1f}с]**"
        )

        return SearchResult(
            answer=ai_answer + telemetry,
            citations=citations,
            success=True,
        )

    # ------------------------------------------------------------------
    # Stage 1: Semantic Map Triage + Parallel SQLite Insurance
    # ------------------------------------------------------------------
    def _stage1_select_files(
        self,
        question: str,
        map_entries: List[Dict[str, Any]],
        q_idents: List[str],
        excluded_stems: Set[str],
        display_to_full: Dict[str, str],
        base_to_fulls: Dict[str, List[str]],
        file_roles: Dict[str, str],
        current_file: Optional[str],
        max_files: int = 14,
    ) -> Tuple[List[str], int, Dict[str, str]]:
        # Filter out user-excluded files before sending to LLM
        filtered_entries = [
            e for e in map_entries
            if os.path.splitext(os.path.basename(e["file_path"]))[0].lower() not in excluded_stems
        ]
        if not filtered_entries:
            filtered_entries = map_entries

        # Pack formatted map lines into ~3k-token batches (<=180 lines or <=9500 chars)
        batches: List[List[Dict[str, Any]]] = []
        cur_batch: List[Dict[str, Any]] = []
        cur_chars = 0
        for e in filtered_entries:
            line_len = len(e["formatted_line"]) + 1
            if cur_batch and (len(cur_batch) >= MAP_BATCH_MAX_LINES or cur_chars + line_len > MAP_BATCH_MAX_CHARS):
                batches.append(cur_batch)
                cur_batch = []
                cur_chars = 0
            cur_batch.append(e)
            cur_chars += line_len
        if cur_batch:
            batches.append(cur_batch)

        log_agent_line(
            f"[{time.strftime('%H:%M:%S')}] 🗺️ [Stage 1: Обзор карты] "
            f"{len(filtered_entries)} строк карты упаковано в {len(batches)} батч."
        )

        llm_picks: List[Tuple[int, str, str]] = []  # (priority, file_path, reason)
        reasons_map: Dict[str, str] = {}

        sys_p = (
            "You are a senior C/C++ architect performing Stage 1 triage over a project semantic map.\n"
            "Each line has format: `Dir/File [ROLE] (Lstart-Lend) | Syms: ... | Summary`.\n"
            "Select the files MOST relevant to answering the user's question (up to 8 files from this batch).\n"
            "RULES:\n"
            "1. Respect role tags: strongly prefer [CORE], [DRIVER], [UI], [UTIL] over [TEST], [SDK], [GENERATED] "
            "unless the user explicitly asks about tests or vendor headers.\n"
            "2. Respect any user instruction to ignore/exclude specific files or modules.\n"
            "3. Prefer implementation files (.cpp/.c) AND key declarations (.h) for relevant classes/methods.\n"
            "Respond ONLY with valid JSON:\n"
            "{\"files\": [{\"file\": \"Dir/File.cpp\", \"priority\": 1, \"reason\": \"short reason\"}]}\n"
            "If nothing in this batch is relevant, respond: {\"files\": []}"
        )

        for b_idx, batch in enumerate(batches, 1):
            map_block = "\n".join(e["formatted_line"] for e in batch)
            usr_p = f"USER QUESTION:\n{question}\n\nPROJECT SEMANTIC MAP (Batch {b_idx}/{len(batches)}):\n{map_block}\n\nJSON:"
            bt0 = time.time()
            raw = self.qwen.generate_raw(sys_p, usr_p, temperature=0.0, max_tokens=500, timeout=65.0)
            bdt = time.time() - bt0
            log_agent_file_only(
                f"STAGE 1 MAP BATCH {b_idx}/{len(batches)} RAW RESPONSE",
                raw or "<empty>"
            )

            parsed = _parse_llm_json(raw)
            batch_files = parsed.get("files") or parsed.get("relevant") or []
            if not isinstance(batch_files, list):
                batch_files = []

            batch_hits = 0
            for item in batch_files:
                if isinstance(item, str):
                    f_ref, prio, reason = item, 2, "selected from map"
                elif isinstance(item, dict):
                    f_ref = str(item.get("file", "")).strip()
                    try:
                        prio = int(item.get("priority", 2))
                    except Exception:
                        prio = 2
                    reason = str(item.get("reason", "")).strip()
                else:
                    continue

                resolved = self._resolve_file_ref(f_ref, display_to_full, base_to_fulls)
                if not resolved:
                    continue
                stem_low = os.path.splitext(os.path.basename(resolved))[0].lower()
                if stem_low in excluded_stems:
                    continue
                llm_picks.append((max(1, min(3, prio)), resolved, reason))
                if resolved not in reasons_map and reason:
                    reasons_map[resolved] = reason
                batch_hits += 1

            log_agent_line(
                f"[{time.strftime('%H:%M:%S')}]    ✓ [Stage 1 | батч {b_idx}/{len(batches)} | {bdt:.1f}с] отобрано файлов: {batch_hits}"
            )

        # Stage 1B: Instant SQLite Insurance (symbols + call_relations + FTS5 BM25)
        sqlite_insurance = self._sqlite_insurance_files(question, q_idents, excluded_stems, file_roles, limit=6)
        for fp in sqlite_insurance:
            if fp not in reasons_map:
                reasons_map[fp] = "SQLite exact identifier / BM25 insurance match"

        # Merge pool: LLM picks (sorted by priority + non-test role) -> paired .cpp for picked .h -> SQLite insurance
        llm_picks.sort(key=lambda t: (
            t[0],
            1 if file_roles.get(t[1], "core") in ("test", "sdk", "generated") else 0,
            0 if os.path.splitext(t[1])[1].lower() in SOURCE_EXTS else 1,
        ))

        selected: List[str] = []
        seen: Set[str] = set()

        def _add(fp: str):
            if fp and fp not in seen and len(selected) < max_files:
                stem_low = os.path.splitext(os.path.basename(fp))[0].lower()
                if stem_low in excluded_stems:
                    return
                seen.add(fp)
                selected.append(fp)

        # 1. Top LLM picks
        for _, fp, _ in llm_picks:
            _add(fp)
            # If LLM picked a header (.h), automatically pair its matching .cpp/.c implementation
            ext = os.path.splitext(fp)[1].lower()
            if ext in HEADER_EXTS:
                stem = os.path.splitext(os.path.basename(fp))[0].lower()
                for s_ext in (".cpp", ".c", ".cc", ".cxx"):
                    for cand in base_to_fulls.get(stem + s_ext, []):
                        _add(cand)

        # 2. Always blend in SQLite insurance hits (guarantees exact identifiers are never missed)
        for fp in sqlite_insurance:
            if fp not in seen:
                if len(selected) >= max_files and len(selected) > 4:
                    # Reserve up to 3 slots for exact SQLite insurance matches if pool is full
                    selected[-1] = fp
                    seen.add(fp)
                else:
                    _add(fp)

        # 3. Include active editor file if provided
        if current_file and current_file in file_roles:
            _add(current_file)

        # 4. Ultimate fallback if both LLM and SQLite returned nothing
        if not selected:
            for e in filtered_entries[:min(8, max_files)]:
                _add(e["file_path"])

        return selected[:max_files], len(batches), reasons_map

    def _sqlite_insurance_files(
        self,
        question: str,
        q_idents: List[str],
        excluded_stems: Set[str],
        file_roles: Dict[str, str],
        limit: int = 6,
    ) -> List[str]:
        """Fast (<50ms) SQLite safety net: finds files defining or calling exact question identifiers + FTS5 BM25."""
        scores: Dict[str, int] = {}

        with self.db.get_connection() as conn:
            cur = conn.cursor()
            for ident in q_idents[:10]:
                short_id = ident.split("::")[-1]
                # Exact or class-qualified symbol definitions get highest boost
                cur.execute(
                    "SELECT DISTINCT file_path, kind FROM symbols WHERE name = ? OR name LIKE ? LIMIT 12;",
                    (ident, f"%::{short_id}")
                )
                for r in cur.fetchall():
                    fp = r[0]
                    boost = 6 if r[1] in ("function", "method") else 4
                    scores[fp] = scores.get(fp, 0) + boost

                # Call-graph references
                cur.execute(
                    "SELECT DISTINCT file_path FROM call_relations WHERE callee_name = ? OR callee_name LIKE ? OR caller_symbol LIKE ? LIMIT 12;",
                    (short_id, f"%{short_id}%", f"%{short_id}%")
                )
                for r in cur.fetchall():
                    fp = r[0]
                    scores[fp] = scores.get(fp, 0) + 3

        # FTS5 BM25 hits
        bm25_hits = self.fts.search_bm25(question, limit=10, custom_tokens=q_idents or None)
        for h in bm25_hits:
            fp = h["file_path"]
            scores[fp] = scores.get(fp, 0) + 2

        ranked: List[Tuple[int, int, str]] = []
        for fp, sc in scores.items():
            stem_low = os.path.splitext(os.path.basename(fp))[0].lower()
            if stem_low in excluded_stems:
                continue
            role = file_roles.get(fp, "core")
            role_penalty = -3 if role in ("test", "sdk", "generated") else 0
            ext_bonus = 1 if os.path.splitext(fp)[1].lower() in SOURCE_EXTS else 0
            ranked.append((sc + role_penalty + ext_bonus, sc, fp))

        ranked.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [fp for _, _, fp in ranked[:limit]]

    # ------------------------------------------------------------------
    # Stage 2: AST Method Skeleton from SQLite -> Select 10-18 Methods
    # ------------------------------------------------------------------
    def _stage2_select_methods(
        self,
        question: str,
        selected_files: List[str],
        q_idents: List[str],
        file_roles: Dict[str, str],
        file_line_counts: Dict[str, int],
        max_methods: int = 16,
    ) -> Tuple[List[SkeletonItem], List[str], int]:
        skeleton: List[SkeletonItem] = []
        item_counter = 1

        with self.db.get_connection() as conn:
            cur = conn.cursor()
            for fp in selected_files:
                disp = _make_display_path(fp)
                role = file_roles.get(fp, "core")
                line_cnt = file_line_counts.get(fp, 100)

                cur.execute("""
                    SELECT name, kind, line_start, line_end, signature
                    FROM symbols
                    WHERE file_path = ?
                      AND kind IN ('function', 'method', 'class', 'struct')
                    ORDER BY line_start ASC;
                """, (fp,))
                sym_rows = cur.fetchall()

                # Load outgoing calls per line for this file
                cur.execute("""
                    SELECT caller_symbol, callee_name, line_number
                    FROM call_relations
                    WHERE file_path = ?
                    ORDER BY line_number ASC;
                """, (fp,))
                call_rows = cur.fetchall()

                if sym_rows:
                    for sr in sym_rows:
                        ls = int(sr["line_start"] or 1)
                        le = int(sr["line_end"] or ls)
                        sig = re.sub(r'\s+', ' ', (sr["signature"] or sr["name"] or "").strip())[:115]

                        # Match outgoing calls by line span or caller_symbol name
                        m_calls: List[str] = []
                        sym_name = sr["name"] or ""
                        for cr in call_rows:
                            ln = int(cr["line_number"] or 0)
                            if (ls <= ln <= le) or (cr["caller_symbol"] == sym_name):
                                cn = cr["callee_name"]
                                if cn and cn not in CPP_CONTROL_WORDS and cn not in m_calls:
                                    m_calls.append(cn)

                        skeleton.append(SkeletonItem(
                            item_id=f"M{item_counter}",
                            file_path=fp,
                            display_path=disp,
                            name=sym_name,
                            kind=sr["kind"] or "function",
                            line_start=ls,
                            line_end=max(ls, le),
                            signature=sig,
                            calls=m_calls[:12],
                            role_tag=role,
                        ))
                        item_counter += 1
                else:
                    # File has no parsed functions/classes (e.g. header constants or plain C block)
                    skeleton.append(SkeletonItem(
                        item_id=f"M{item_counter}",
                        file_path=fp,
                        display_path=disp,
                        name=os.path.basename(fp),
                        kind="file_slice",
                        line_start=1,
                        line_end=min(max(1, line_cnt), 220),
                        signature=f"<full/top file slice: {disp} ({line_cnt} lines)>",
                        calls=[],
                        role_tag=role,
                    ))
                    item_counter += 1

        if not skeleton:
            return [], [], 0

        # If skeleton is already small (<= max_methods), we can still ask LLM or load all directly!
        if len(skeleton) <= min(8, max_methods):
            extra_syms: List[str] = []
            for it in skeleton:
                for c in it.calls:
                    if c not in extra_syms:
                        extra_syms.append(c)
            return skeleton, extra_syms[:10], len(skeleton)

        # Format skeleton grouped by file into 1-2 compact batches
        by_file: Dict[str, List[SkeletonItem]] = {}
        for it in skeleton:
            by_file.setdefault(it.file_path, []).append(it)

        file_blocks: List[str] = []
        for fp in selected_files:
            items = by_file.get(fp)
            if not items:
                continue
            disp = _make_display_path(fp)
            role = file_roles.get(fp, "core").upper()
            lines_out = [f"=== {disp} [{role}] ==="]
            for it in items:
                call_part = f" -> calls: [{', '.join(it.calls[:8])}]" if it.calls else ""
                lines_out.append(f"[{it.item_id}] L{it.line_start}-{it.line_end} ({it.kind}) {it.signature}{call_part}")
            file_blocks.append("\n".join(lines_out))

        batches: List[str] = []
        cur_parts: List[str] = []
        cur_len = 0
        for fb in file_blocks:
            if cur_parts and cur_len + len(fb) > SKELETON_BATCH_MAX_CHARS:
                batches.append("\n\n".join(cur_parts))
                cur_parts = []
                cur_len = 0
            cur_parts.append(fb)
            cur_len += len(fb) + 2
        if cur_parts:
            batches.append("\n\n".join(cur_parts))

        sys_p = (
            "You are a C/C++ code analyst performing Stage 2 method selection.\n"
            "Below is an AST skeleton of functions/methods/classes and their outgoing calls.\n"
            f"Select up to {max_methods} item IDs (e.g. \"M1\", \"M4\") whose full raw source code MUST be inspected "
            "to answer the user's question accurately.\n"
            "Also list in \"extra_symbols\" any external functions seen in `calls: [...]` or the question "
            "whose implementations are NOT in the skeleton above and should be fetched.\n"
            "Respond ONLY with valid JSON:\n"
            "{\"selected\": [\"M1\", \"M4\"], \"extra_symbols\": [\"ExternalMethod\"]}"
        )

        id_to_item = {it.item_id.upper(): it for it in skeleton}
        picked_ids: List[str] = []
        extra_symbols: List[str] = []

        for b_idx, skel_text in enumerate(batches, 1):
            usr_p = f"USER QUESTION:\n{question}\n\nAST METHOD SKELETON (Batch {b_idx}/{len(batches)}):\n{skel_text}\n\nJSON:"
            bt0 = time.time()
            raw = self.qwen.generate_raw(sys_p, usr_p, temperature=0.0, max_tokens=450, timeout=65.0)
            bdt = time.time() - bt0
            log_agent_file_only(
                f"STAGE 2 SKELETON BATCH {b_idx}/{len(batches)} RAW RESPONSE",
                raw or "<empty>"
            )

            parsed = _parse_llm_json(raw)
            sel_raw = parsed.get("selected") or parsed.get("methods") or []
            if isinstance(sel_raw, list):
                for sid in sel_raw:
                    key = str(sid).strip().upper()
                    if key in id_to_item and key not in picked_ids:
                        picked_ids.append(key)

            # Regex fallback in case JSON was slightly malformed
            if not picked_ids and raw:
                for m in re.finditer(r'\b(M\d+)\b', raw):
                    key = m.group(1).upper()
                    if key in id_to_item and key not in picked_ids:
                        picked_ids.append(key)

            ext_raw = parsed.get("extra_symbols") or parsed.get("missing_symbols") or []
            if isinstance(ext_raw, list):
                for es in ext_raw:
                    s = str(es).strip()
                    if len(s) >= 3 and s not in extra_symbols:
                        extra_symbols.append(s)

            log_agent_line(
                f"[{time.strftime('%H:%M:%S')}]    ✓ [Stage 2 | батч {b_idx}/{len(batches)} | {bdt:.1f}с] "
                f"выбрано ID: {len(picked_ids)}"
            )

        selected_items = [id_to_item[k] for k in picked_ids[:max_methods]]

        # Always guarantee exact question identifier definitions present in skeleton are included
        q_lows = [q.lower() for q in q_idents]
        selected_set = {it.item_id for it in selected_items}
        for it in skeleton:
            if it.item_id in selected_set:
                continue
            name_low = it.name.lower()
            calls_low = " ".join(it.calls).lower()
            if any(ql in name_low or ql in calls_low for ql in q_lows):
                selected_items.append(it)
                selected_set.add(it.item_id)
                if len(selected_items) >= max_methods:
                    break

        # Fallback if LLM picked nothing: select top non-destructor methods
        if not selected_items:
            ranked = sorted(
                skeleton,
                key=lambda it: (
                    1 if "~" in it.name else 0,
                    0 if it.kind in ("function", "method") else 1,
                    -(it.line_end - it.line_start),
                )
            )
            selected_items = ranked[:max_methods]

        return selected_items[:max_methods], extra_symbols[:12], len(skeleton)

    # ------------------------------------------------------------------
    # Stage 3: Load Raw Bodies + Automatic Call-Graph Rescue
    # ------------------------------------------------------------------
    def _stage3_load_and_rescue(
        self,
        selected_items: List[SkeletonItem],
        extra_symbols: List[str],
        q_idents: List[str],
        excluded_stems: Set[str],
        file_encodings: Dict[str, str],
        file_roles: Dict[str, str],
        max_rescue: int,
        final_char_budget: int,
    ) -> Tuple[List[RawCodeBlock], int]:
        blocks: List[RawCodeBlock] = []
        loaded_symbol_names: Set[str] = set()
        loaded_spans: Set[Tuple[str, int, int]] = set()
        called_in_raw: Set[str] = set()

        # 1. Load full raw numbered bodies for all Stage 2 selected items
        for it in selected_items:
            enc = file_encodings.get(it.file_path, "utf-8")
            ls = max(1, it.line_start - 2)
            le = it.line_end + 2
            code = self._format_numbered_slice(it.file_path, ls, le, enc)
            if not code:
                continue

            q_hit = any(qi.lower() in it.name.lower() or qi.lower() in code.lower() for qi in q_idents)
            blocks.append(RawCodeBlock(
                file_path=it.file_path,
                display_path=it.display_path,
                line_start=ls,
                line_end=le,
                symbols=[it.name],
                source_tag="stage2",
                role_tag=it.role_tag,
                priority=0 if q_hit else 1,
                code=code,
            ))
            loaded_symbol_names.add(it.name)
            loaded_symbol_names.add(it.name.split("::")[-1])
            loaded_spans.add((it.file_path, ls, le))

            # Extract called identifiers directly from raw C++ body
            for m in CALL_TOKEN_RE.finditer(code):
                tok = m.group(1)
                if tok not in CPP_CONTROL_WORDS and tok.lower() not in GENERIC_NOISE_WORDS and len(tok) >= 3:
                    called_in_raw.add(tok)

        # 2. Call-Graph Rescue: fetch missing implementations (%::MethodName) and working callers!
        rescue_candidates: List[str] = []
        for sym in list(q_idents) + list(extra_symbols) + sorted(called_in_raw):
            short = sym.split("::")[-1]
            if len(short) < 3 or short in CPP_CONTROL_WORDS or short.lower() in GENERIC_NOISE_WORDS:
                continue
            if sym not in rescue_candidates:
                rescue_candidates.append(sym)

        rescued_blocks: List[RawCodeBlock] = []

        with self.db.get_connection() as conn:
            cur = conn.cursor()

            # 2a. Rescue external callee definitions (especially those NOT yet loaded)
            for sym in rescue_candidates:
                if len(rescued_blocks) >= max_rescue:
                    break
                short = sym.split("::")[-1]
                # Skip if exact implementation is already loaded (unless it's a primary q_ident)
                if short in loaded_symbol_names and sym not in q_idents:
                    continue

                cur.execute("""
                    SELECT name, kind, file_path, line_start, line_end
                    FROM symbols
                    WHERE (name = ? OR name LIKE ?)
                      AND kind IN ('function', 'method', 'class', 'struct')
                    ORDER BY (CASE WHEN kind IN ('function', 'method') THEN 0 ELSE 1 END),
                             (line_end - line_start) DESC
                    LIMIT 4;
                """, (sym, f"%::{short}"))

                for r in cur.fetchall():
                    if len(rescued_blocks) >= max_rescue:
                        break
                    fp = r["file_path"]
                    stem_low = os.path.splitext(os.path.basename(fp))[0].lower()
                    if stem_low in excluded_stems:
                        continue
                    ls = max(1, int(r["line_start"] or 1) - 2)
                    le = int(r["line_end"] or ls) + 2
                    if self._overlaps_loaded(fp, ls, le, loaded_spans):
                        continue

                    code = self._format_numbered_slice(fp, ls, le, file_encodings.get(fp))
                    if not code:
                        continue
                    role = file_roles.get(fp, "core")
                    rescued_blocks.append(RawCodeBlock(
                        file_path=fp,
                        display_path=_make_display_path(fp),
                        line_start=ls,
                        line_end=le,
                        symbols=[r["name"]],
                        source_tag="rescue_callee",
                        role_tag=role,
                        priority=0 if sym in q_idents else 2,
                        code=code,
                    ))
                    loaded_spans.add((fp, ls, le))
                    loaded_symbol_names.add(r["name"])
                    loaded_symbol_names.add(short)

            # 2b. Rescue callers of exact question identifiers (e.g. who calls a target method in working code)
            for sym in (list(q_idents) + list(extra_symbols))[:6]:
                if len(rescued_blocks) >= max_rescue:
                    break
                callers = self.graph.get_callers(sym, limit=8)
                # Prefer non-test callers first
                callers.sort(key=lambda c: 1 if file_roles.get(c.file_path, "core") in ("test", "sdk") else 0)
                for c in callers:
                    if len(rescued_blocks) >= max_rescue:
                        break
                    fp = c.file_path
                    stem_low = os.path.splitext(os.path.basename(fp))[0].lower()
                    if stem_low in excluded_stems:
                        continue
                    # Resolve full enclosing caller symbol bounds if available
                    cur.execute("""
                        SELECT name, line_start, line_end FROM symbols
                        WHERE file_path = ? AND (name = ? OR (line_start <= ? AND line_end >= ?))
                        ORDER BY (line_end - line_start) ASC
                        LIMIT 1;
                    """, (fp, c.caller_symbol, c.line_number, c.line_number))
                    enc_sym = cur.fetchone()
                    if enc_sym:
                        ls = max(1, int(enc_sym["line_start"]) - 2)
                        le = int(enc_sym["line_end"]) + 2
                        sym_name = enc_sym["name"]
                    else:
                        ls = max(1, c.line_number - 18)
                        le = c.line_number + 22
                        sym_name = c.caller_symbol or os.path.basename(fp)

                    if self._overlaps_loaded(fp, ls, le, loaded_spans):
                        continue

                    code = self._format_numbered_slice(fp, ls, le, file_encodings.get(fp))
                    if not code:
                        continue
                    rescued_blocks.append(RawCodeBlock(
                        file_path=fp,
                        display_path=_make_display_path(fp),
                        line_start=ls,
                        line_end=le,
                        symbols=[sym_name],
                        source_tag="rescue_caller",
                        role_tag=file_roles.get(fp, "core"),
                        priority=1,
                        code=code,
                    ))
                    loaded_spans.add((fp, ls, le))

        all_blocks = self._merge_adjacent_blocks(blocks + rescued_blocks, file_encodings)
        fitted_blocks = self._fit_blocks_to_budget(all_blocks, final_char_budget)
        return fitted_blocks, len(rescued_blocks)

    @staticmethod
    def _overlaps_loaded(fp: str, ls: int, le: int, loaded_spans: Set[Tuple[str, int, int]]) -> bool:
        for l_fp, l_s, l_e in loaded_spans:
            if l_fp == fp and not (le < l_s - 2 or ls > l_e + 2):
                return True
        return False

    def _merge_adjacent_blocks(
        self,
        blocks: List[RawCodeBlock],
        file_encodings: Dict[str, str],
    ) -> List[RawCodeBlock]:
        """Merges overlapping or closely adjacent blocks (<= 8 lines gap) in the same file."""
        by_file: Dict[str, List[RawCodeBlock]] = {}
        for b in blocks:
            by_file.setdefault(b.file_path, []).append(b)

        merged: List[RawCodeBlock] = []
        for fp, fblocks in by_file.items():
            fblocks.sort(key=lambda b: b.line_start)
            cur = fblocks[0]
            for nxt in fblocks[1:]:
                if nxt.line_start <= cur.line_end + 8:
                    cur.line_end = max(cur.line_end, nxt.line_end)
                    for s in nxt.symbols:
                        if s not in cur.symbols:
                            cur.symbols.append(s)
                    cur.priority = min(cur.priority, nxt.priority)
                    if cur.source_tag != nxt.source_tag and nxt.source_tag not in cur.source_tag:
                        cur.source_tag = f"{cur.source_tag}+{nxt.source_tag}"
                    cur.code = self._format_numbered_slice(fp, cur.line_start, cur.line_end, file_encodings.get(fp))
                else:
                    merged.append(cur)
                    cur = nxt
            merged.append(cur)

        # Sort final blocks: highest priority (0 -> 1 -> 2), core/driver before test/sdk
        merged.sort(key=lambda b: (
            b.priority,
            1 if b.role_tag in ("test", "sdk", "generated") else 0,
            b.display_path.lower(),
            b.line_start,
        ))
        return merged

    @staticmethod
    def _fit_blocks_to_budget(blocks: List[RawCodeBlock], char_budget: int) -> List[RawCodeBlock]:
        """Ensures total raw code fits comfortably inside the active LLM context window."""
        fitted: List[RawCodeBlock] = []
        used = 0
        # Cap any single massive function to at most 35% of budget so other methods remain visible
        per_block_cap = max(3500, int(char_budget * 0.35))

        for blk in blocks:
            code = blk.code
            if len(code) > per_block_cap:
                code = smart_truncate_code(code, per_block_cap)
            cost = len(code) + 160
            if fitted and used + cost > char_budget:
                remaining = char_budget - used - 160
                if remaining >= 800:
                    blk.code = smart_truncate_code(code, remaining)
                    fitted.append(blk)
                break
            blk.code = code
            fitted.append(blk)
            used += cost
        return fitted

    def _load_indexed_files(self) -> List[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT file_path, encoding, line_count FROM indexed_files ORDER BY file_path ASC;")
            return [
                {
                    "file_path": r["file_path"],
                    "encoding": r["encoding"] or "utf-8",
                    "line_count": int(r["line_count"] or 0),
                }
                for r in cur.fetchall()
            ]
