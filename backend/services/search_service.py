"""
Search Service and Hybrid Query Dispatcher.
Implements Agentic RAG + Vector Embeddings + Fuzzy Call-Graph.
Merges results with hybrid multi-engine scoring and produces a transparent diagnostic comparison.
"""

import re
import os
from typing import List, Optional, Set, Dict, Any, Tuple
from ..core.models import SearchResult, CodeCitation, SymbolDefinition, CallRelation
from ..core.agent_logger import log_agent_boundary, log_agent_section
from ..storage.sqlite_db import DatabaseManager
from ..storage.fts_index import FtsIndex
from ..storage.graph_store import GraphStore
from ..storage.vector_index import VectorIndex
from .qwen_service import QwenService
from .prompt_builder import build_system_prompt, build_context_prompt
from .reducing_agent import ReducingAgent
from .map_builder import MapBuilder
from .funnel_agent import SemanticFunnelAgent


STOP_WORDS: Set[str] = {
    # English conversational & generic stopwords
    "what", "how", "where", "which", "who", "when", "why", "does", "did", "do",
    "use", "used", "using", "this", "that", "these", "those", "code", "project",
    "file", "files", "function", "functions", "class", "classes", "method", "methods",
    "check", "find", "show", "tell", "please", "man", "can", "you", "all", "places",
    "appear", "connected", "connection", "type", "is", "are", "was", "were", "been",
    "have", "has", "had", "a", "an", "the", "and", "or", "in", "on", "at", "to",
    "for", "of", "with", "by", "about", "into", "through", "during", "before", "after",
    "above", "below", "from", "up", "down", "out", "off", "over", "under", "again",
    "further", "then", "once", "here", "there", "any", "both", "each", "few", "more",
    "most", "other", "some", "such", "no", "nor", "not", "only", "own", "same", "so",
    "than", "too", "very", "don", "should", "now", "ok", "hello", "hi", "hey", "thanks", "thank",
    # Russian conversational & generic stopwords
    "что", "как", "где", "какой", "какая", "какие", "какое", "делает", "проект", "проекта",
    "проекте", "коде", "код", "функции", "функция", "классы", "класс", "посмотри", "найди",
    "все", "места", "место", "для", "чего", "это", "этот", "эта", "эти", "более", "развернутый",
    "ответ", "подробно", "расскажи", "покажи", "привет", "здравствуйте", "пожалуйста",
    "скажи", "поясни", "объясни", "в", "на", "с", "по", "к", "у", "о", "об", "обо", "из",
    "за", "от", "до", "под", "над", "через", "между", "не", "ни", "да", "нет", "ли", "бы", "же",
    "ну", "че", "чо", "а", "и", "у", "нас", "нам", "вами", "вам", "ими", "им", "их",
    "он", "она", "они", "оно", "мы", "вы", "ты", "я", "тут", "там", "так", "тоже", "то",
    "уже", "еще", "ещё", "ведь", "просто", "давай", "сейчас", "тогда", "вообще",
    "nu", "che", "cho", "nas", "nam", "vam", "imi", "tam", "tut"
}

# Generic C/C++ Domain Concept Synonyms Mapping
DOMAIN_CONCEPTS: Dict[str, List[str]] = {
    # Communication / Networking / IO
    "connection": ["Connect", "Serial", "COM", "Socket", "Port", "Comm"],
    "connected": ["Connect", "Serial", "COM", "Socket", "Port", "Comm"],
    "connect": ["Connect", "Serial", "COM", "Socket", "Port", "Comm"],
    "protocol": ["Protocol", "Packet", "Frame", "Serial", "Socket"],
    "communication": ["Serial", "COM", "Socket", "Comm", "Transport"],
    "comm": ["Serial", "COM", "Socket", "Comm"],
    "register": ["Register", "Reg", "Offset", "Field"],
    "registers": ["Register", "Reg", "Offset", "Field"],
    "float": ["Float", "Double", "Real", "IEEE"],
    "serial": ["Serial", "COM", "Port", "UART"],
    "port": ["Port", "COM", "Serial", "Socket"],
    "socket": ["Socket", "TCP", "UDP", "IP", "Client", "Server"],
    "packet": ["Packet", "Send", "Recv", "Write", "Read", "Parse"],
    "firmware": ["Download", "Firmware", "FW", "Flash", "Boot"],
    "gui": ["Dialog", "Dlg", "Frame", "View", "Window", "MainFrm"],
    "hardware": ["Driver", "Device", "Port", "Hardware", "IO"],
    # Russian concepts
    "регистр": ["Register", "Reg", "Offset"],
    "регистры": ["Register", "Reg", "Offset"],
    "регистров": ["Register", "Reg", "Offset"],
    "флоат": ["Float", "Double", "Real", "IEEE"],
    "связь": ["Connect", "Serial", "COM", "Socket", "Comm"],
    "соединение": ["Connect", "Serial", "COM", "Socket", "Comm"],
    "протокол": ["Protocol", "Packet", "Frame", "Serial"],
    "прошивка": ["Download", "Firmware", "FW", "Flash", "Boot"],
    "порт": ["Port", "COM", "Serial", "Socket"],
    "обмен": ["Socket", "Send", "Recv", "Packet", "Comm"],
    "подключение": ["Connect", "Serial", "Socket", "Comm"],
    "интерфейс": ["Dialog", "Dlg", "Window", "Interface", "Serial"],
    "железо": ["Driver", "Device", "Hardware", "IO"],
    "сокет": ["Socket", "TCP", "IP", "Client", "Server"],
    "клиент": ["Client", "Session", "Socket"],
    "сервер": ["Server", "Listener", "Socket"],
    "реализован": ["Init", "Create", "Start", "Open", "Connect", "Read", "Write"],
    "реализована": ["Init", "Create", "Start", "Open", "Connect", "Read", "Write"],
}

CYR_TO_LAT: Dict[str, str] = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '',
    'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'
}

def transliterate_cyrillic(text: str) -> str:
    res = []
    for ch in text.lower():
        res.append(CYR_TO_LAT.get(ch, ch))
    return "".join(res)


OVERVIEW_TRIGGERS: Set[str] = {
    "что делает проект", "для чего", "архитектур", "overview", "what does project do",
    "purpose", "структур", "назначение", "суть проекта", "обзор"
}

FOLLOWUP_TRIGGERS: Set[str] = {
    "более развернутый", "подробнее", "где вызывается", "where is it called",
    "покажи подробнее", "а где", "еще раз", "уточни", "explain more", "details"
}


class SearchService:
    def __init__(self, db_manager: DatabaseManager, qwen_service: QwenService):
        self.db = db_manager
        self.fts = FtsIndex(db_manager)
        self.graph = GraphStore(db_manager)
        self.vector = VectorIndex(db_manager)
        self.qwen = qwen_service
        self.reducer = ReducingAgent(db_manager, qwen_service, self.graph, self.fts)
        self.map_builder = MapBuilder(db_manager, qwen_service, self.graph)
        self.funnel = SemanticFunnelAgent(db_manager, qwen_service, self.graph, self.fts, self.map_builder)
        self.last_query_symbols: List[str] = []

    def clear_history(self):
        """Clears dialog memory and last tracked query symbols."""
        self.last_query_symbols.clear()
        self.qwen.clear_history()

    def query(
        self,
        question: str,
        mode: str = "ask",
        current_file: Optional[str] = None,
        current_line: Optional[int] = None,
        selected_code: Optional[str] = None,
        language: str = "ru",
        time_budget_sec: Optional[int] = None
    ) -> SearchResult:
        """
        Executes query based on selected mode.
        Modes: 'funnel', 'funnel_deep', 'ask'/'reduce', 'reduce_deep', 'reduce_raw',
               'fast', 'callers', 'definition', 'grep'
        """
        mode = mode.lower().strip()

        if mode == "callers":
            return self._handle_callers_mode(question)
        elif mode == "definition":
            return self._handle_definition_mode(question)
        elif mode == "grep":
            return self._handle_grep_mode(question)
        elif mode == "fast":
            return self._handle_fast_rag_mode(
                question=question,
                current_file=current_file,
                current_line=current_line,
                selected_code=selected_code,
                language=language
            )
        elif mode in ("funnel", "funnel_deep"):
            is_deep = (mode == "funnel_deep")
            if self.map_builder.is_ready(min_coverage=0.1):
                return self.funnel.run(
                    question=question,
                    current_file=current_file,
                    current_line=current_line,
                    selected_code=selected_code,
                    language=language,
                    budget_sec=time_budget_sec or (300 if is_deep else 180),
                    deep=is_deep
                )
            res = self.reducer.run(
                question=question,
                current_file=current_file,
                current_line=current_line,
                selected_code=selected_code,
                language=language,
                budget_sec=time_budget_sec or (600 if is_deep else 300),
                deep=is_deep
            )
            res.answer = (
                "ℹ️ *Семантическая карта проекта ещё не собрана — выполнен резервный проход Map-Reduce. "
                "Соберите карту (1 раз), чтобы режим Smart Funnel работал за 1–2 мин со 100% охватом.*\n\n"
                + res.answer
            )
            return res
        elif mode == "reduce_raw":
            return self.reducer.run(
                question=question,
                current_file=current_file,
                current_line=current_line,
                selected_code=selected_code,
                language=language,
                budget_sec=time_budget_sec or 300,
                deep=False
            )
        elif mode == "reduce_deep":
            if self.map_builder.is_ready(min_coverage=0.5):
                return self.funnel.run(
                    question=question,
                    current_file=current_file,
                    current_line=current_line,
                    selected_code=selected_code,
                    language=language,
                    budget_sec=time_budget_sec or 300,
                    deep=True
                )
            return self.reducer.run(
                question=question,
                current_file=current_file,
                current_line=current_line,
                selected_code=selected_code,
                language=language,
                budget_sec=time_budget_sec or 600,
                deep=True
            )
        else:
            if self.map_builder.is_ready(min_coverage=0.5):
                return self.funnel.run(
                    question=question,
                    current_file=current_file,
                    current_line=current_line,
                    selected_code=selected_code,
                    language=language,
                    budget_sec=time_budget_sec or 180,
                    deep=False
                )
            return self.reducer.run(
                question=question,
                current_file=current_file,
                current_line=current_line,
                selected_code=selected_code,
                language=language,
                budget_sec=time_budget_sec or 300,
                deep=False
            )

    def _handle_callers_mode(self, symbol_name: str) -> SearchResult:
        clean_name = symbol_name.strip(" ();\n\r\t")
        callers = self.graph.get_callers(clean_name, limit=50)

        if not callers:
            fuzzy_defs, _ = self.graph.fuzzy_search([clean_name], limit=3)
            if fuzzy_defs:
                clean_name = fuzzy_defs[0].name
                callers = self.graph.get_callers(clean_name, limit=50)

        if not callers:
            return SearchResult(
                answer=f"Вызовы символа '{clean_name}' в проекте не найдены.",
                citations=[]
            )

        citations: List[CodeCitation] = []
        lines: List[str] = [f"📍 Найдено {len(callers)} вызовов функции/символа '{clean_name}':\n"]

        for idx, c in enumerate(callers, 1):
            citations.append(CodeCitation(
                file=c.file_path,
                line=c.line_number,
                symbol=c.caller_symbol,
                snippet=c.call_snippet
            ))
            lines.append(f"{idx}. [{c.file_path}:{c.line_number}] в '{c.caller_symbol}':")
            lines.append(f"   {c.call_snippet}\n")

        return SearchResult(
            answer="\n".join(lines),
            citations=citations
        )

    def _handle_definition_mode(self, symbol_name: str) -> SearchResult:
        clean_name = symbol_name.strip(" ();\n\r\t")
        defn = self.graph.get_definition(clean_name)

        if not defn:
            fuzzy_defs, _ = self.graph.fuzzy_search([clean_name], limit=5)
            if fuzzy_defs:
                defn = fuzzy_defs[0]

        if not defn:
            return SearchResult(
                answer=f"Определение для '{clean_name}' в коде не найдено.",
                citations=[]
            )

        callers = self.graph.get_callers(defn.name, limit=10)

        answer_lines = [
            f"📑 Определение символа '{defn.name}' ({defn.kind}):",
            f"Файл: [{defn.file_path}:{defn.line_start}]",
            f"Сигнатура: {defn.signature}\n",
            "Тело/Фрагмент:",
            defn.body[:1500]
        ]

        if callers:
            answer_lines.append(f"\n🔗 Вызывается в {len(callers)} местах:")
            for c in callers[:5]:
                answer_lines.append(f"  • [{c.file_path}:{c.line_number}] из '{c.caller_symbol}'")

        citation = CodeCitation(
            file=defn.file_path,
            line=defn.line_start,
            symbol=defn.name,
            snippet=defn.signature
        )

        return SearchResult(
            answer="\n".join(answer_lines),
            citations=[citation]
        )

    def _handle_grep_mode(self, query_text: str) -> SearchResult:
        matches = self.fts.search_bm25(query_text, limit=20)
        if not matches:
            return SearchResult(
                answer=f"По запросу '{query_text}' совпадений в коде не найдено.",
                citations=[]
            )

        citations: List[CodeCitation] = []
        answer_lines = [f"🔎 Найдено {len(matches)} совпадений по коду (FTS5 BM25):\n"]

        for idx, m in enumerate(matches, 1):
            citations.append(CodeCitation(
                file=m["file_path"],
                line=m["line_start"],
                symbol=m["symbol_name"],
                snippet=m["content"][:100].replace("\n", " ")
            ))
            snippet_clean = m["content"][:250].strip()
            answer_lines.append(f"{idx}. [{m['file_path']}:{m['line_start']}] ({m['symbol_name']}):")
            answer_lines.append(f"   {snippet_clean}\n")

        return SearchResult(
            answer="\n".join(answer_lines),
            citations=citations
        )

    def _extract_and_expand_tokens(self, question: str) -> Tuple[List[str], List[str]]:
        raw_words = re.findall(r'[A-Za-z0-9_]+|[А-Яа-яЁё]+', question)
        meaningful_words = [w for w in raw_words if w.lower() not in STOP_WORDS and len(w) > 1]

        cpp_identifiers: List[str] = []
        other_words: List[str] = []

        for w in meaningful_words:
            # If word contains Cyrillic, transliterate to Latin for fuzzy & FTS5 matching
            if re.search(r'[А-Яа-яЁё]', w):
                lat = transliterate_cyrillic(w)
                if len(lat) > 1 and lat not in other_words:
                    other_words.append(lat)

            is_cpp = (
                re.search(r'[a-z][A-Z]', w) is not None or
                '_' in w or
                w.startswith(('C', 'I', 'm_')) or
                w.isupper() or
                w.endswith(('.h', '.cpp', '.c'))
            )
            if is_cpp:
                cpp_identifiers.append(w)
            else:
                other_words.append(w)

        # Domain Concept Expansion
        expanded_domain_symbols: List[str] = []
        question_lower = question.lower()
        for trigger, expansions in DOMAIN_CONCEPTS.items():
            if trigger in question_lower:
                for exp in expansions:
                    if exp not in expanded_domain_symbols:
                        expanded_domain_symbols.append(exp)

        # Combine candidates with priority
        all_symbol_candidates = cpp_identifiers + expanded_domain_symbols + other_words

        # Check follow-up context
        is_followup = any(t in question_lower for t in FOLLOWUP_TRIGGERS) or len(question.strip()) < 25
        if is_followup and self.last_query_symbols:
            for s in self.last_query_symbols:
                if s not in all_symbol_candidates:
                    all_symbol_candidates.append(s)

        fts_tokens = [w for w in (cpp_identifiers + other_words + expanded_domain_symbols) if len(w) > 1]
        return all_symbol_candidates, fts_tokens

    def _handle_fast_rag_mode(
        self,
        question: str,
        current_file: Optional[str],
        current_line: Optional[int],
        selected_code: Optional[str],
        language: str = "ru"
    ) -> SearchResult:
        log_agent_boundary(f"ЗАПРОС ПОЛЬЗОВАТЕЛЯ: '{question}'")

        # Step 0: Project Architecture Passport
        compact_project_map = self.graph.get_compact_project_map()

        # Step 1: Agentic Query Router (fast 0-temp plan by Qwen)
        agent_plan = self.qwen.plan_search_query(compact_project_map, question)
        agentic_terms = agent_plan.get("corrected_terms", [])
        agentic_files = agent_plan.get("target_files", [])
        agentic_symbols = agent_plan.get("target_symbols", [])
        is_overview_agent = agent_plan.get("is_overview_question", False)


        # Step 1.5: Load Full Target Files requested by Agent Router (Headers or compact files <= 300 lines)
        full_files_data: List[Dict[str, Any]] = []
        loaded_file_paths = set()
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            for f_target in agentic_files:
                clean_base = os.path.basename(f_target).strip()
                cur.execute("SELECT file_path FROM indexed_files WHERE file_path LIKE ? LIMIT 1;", (f"%{clean_base}",))
                row = cur.fetchone()
                if row and row[0] not in loaded_file_paths:
                    fp = row[0]
                    loaded_file_paths.add(fp)
                    try:
                        with open(fp, "r", encoding="latin-1") as f_obj:
                            lines = f_obj.readlines()
                        if clean_base.lower().endswith(('.h', '.hpp')) or len(lines) <= 300:
                            full_files_data.append({
                                "file_name": clean_base,
                                "file_path": fp,
                                "content": "".join(lines).strip(),
                                "line_count": len(lines)
                            })
                    except Exception:
                        pass

        # Step 2: Extract & Expand Tokens with Domain Knowledge
        import time
        now_str = time.strftime("%H:%M:%S")
        candidate_symbols, fts_tokens = self._extract_and_expand_tokens(question)

        # Merge Agentic terms into candidate symbols
        for t in agentic_terms + agentic_symbols:
            if t not in candidate_symbols:
                candidate_symbols.insert(0, t)
            if t not in fts_tokens:
                fts_tokens.append(t)

        print(f"[{now_str}] 🔎 [Шаг 2: Анализ запроса] Кандидаты: {candidate_symbols[:8]} | FTS5 токены: {fts_tokens[:8]}", flush=True)

        # Step 3: Engine 1: Agentic + Graph Store
        agentic_definitions: List[SymbolDefinition] = []
        calls: List[CallRelation] = []
        seen_keys: Set[Tuple[str, int]] = set()
        multi_engine_hits: Dict[Tuple[str, int], int] = {}  # key -> count of engines that found it

        for sym in (agentic_symbols + candidate_symbols)[:10]:
            d = self.graph.get_definition(sym)
            if d:
                k = (d.file_path, d.line_start)
                if k not in seen_keys:
                    seen_keys.add(k)
                    agentic_definitions.append(d)
                    multi_engine_hits[k] = multi_engine_hits.get(k, 0) + 1
                    calls.extend(self.graph.get_callers(d.name, limit=8))

        # Check target files suggested by Agentic router
        for f in agentic_files:
            fuzzy_from_file, _ = self.graph.fuzzy_search([f], limit=4)
            for d in fuzzy_from_file:
                k = (d.file_path, d.line_start)
                if k not in seen_keys:
                    seen_keys.add(k)
                    agentic_definitions.append(d)
                    multi_engine_hits[k] = multi_engine_hits.get(k, 0) + 1

        # Overview triggers
        question_lower = question.lower()
        is_overview = is_overview_agent or any(t in question_lower for t in OVERVIEW_TRIGGERS)
        if is_overview or len(agentic_definitions) == 0:
            for ep in self.graph.get_entry_points():
                k = (ep.file_path, ep.line_start)
                if k not in seen_keys:
                    seen_keys.add(k)
                    agentic_definitions.append(ep)
                    multi_engine_hits[k] = multi_engine_hits.get(k, 0) + 1

        # Step 4: Engine 2: Fuzzy Matching + FTS5 BM25
        terms_to_fuzzy = candidate_symbols[:8] + agentic_terms[:4]
        fuzzy_definitions, typo_corrections = self.graph.fuzzy_search(terms_to_fuzzy, limit=8)
        for fd in fuzzy_definitions:
            k = (fd.file_path, fd.line_start)
            multi_engine_hits[k] = multi_engine_hits.get(k, 0) + 1
            if k not in seen_keys:
                seen_keys.add(k)
                agentic_definitions.append(fd)
                calls.extend(self.graph.get_callers(fd.name, limit=4))

        bm25_snippets = self.fts.search_bm25(question, limit=8, custom_tokens=fts_tokens)

        # Step 5: Engine 3: Vector Embeddings / Pure-NumPy Cosine
        clean_vector_terms = [
            t for t in (agentic_terms[:4] + candidate_symbols[:6] + fts_tokens[:6])
            if t.lower() not in STOP_WORDS and len(t) > 1
        ]
        if clean_vector_terms:
            vector_query = " ".join(dict.fromkeys(clean_vector_terms))
        else:
            vector_query = question
        vector_results = self.vector.search(vector_query, top_k=6)
        top_vector_names = [f"{v['name']} ({os.path.basename(v['file_path'])})" for v in vector_results[:3]]

        for vr in vector_results:
            k = (vr["file_path"], vr["line_start"])
            multi_engine_hits[k] = multi_engine_hits.get(k, 0) + 1
            if k not in seen_keys:
                vd = self.graph.get_definition(vr["name"])
                if vd:
                    seen_keys.add(k)
                    agentic_definitions.append(vd)

        # Log tri-engine results
        now_engines = time.strftime("%H:%M:%S")
        print(f"[{now_engines}] 🔍 [Шаг 2: Поисковые движки]", flush=True)
        print(f"       • Graph Store: {len(agentic_definitions)} определений, {len(calls)} связей вызовов", flush=True)
        print(f"       • Fuzzy Match: опечатки={typo_corrections or 'нет'}, FTS5 BM25={len(bm25_snippets)} сниппетов", flush=True)
        print(f"       • Vector Search ({self.vector.backend_name}): {len(vector_results)} совпадений ({', '.join(top_vector_names) if top_vector_names else 'нет'})", flush=True)

        # Step 5.5: Class Methods Expansion (Prioritize real working methods of matched classes)
        classes_to_expand = set()
        for d in list(agentic_definitions):
            if d.kind in ('class', 'struct'):
                classes_to_expand.add(d.name)

        for sym in (agentic_symbols + candidate_symbols)[:8]:
            clean_s = re.sub(r'^[&*~;\s]+', '', sym).strip()
            if clean_s.startswith('C') and len(clean_s) > 3 and clean_s[1].isupper():
                classes_to_expand.add(clean_s)

        for cls_name in classes_to_expand:
            methods = self.graph.get_class_methods(cls_name, limit=6)
            for m in methods:
                k = (m.file_path, m.line_start)
                if k not in seen_keys:
                    seen_keys.add(k)
                    agentic_definitions.append(m)
                    multi_engine_hits[k] = multi_engine_hits.get(k, 0) + 2  # boost class methods
                    calls.extend(self.graph.get_callers(m.name, limit=2))

        # Step 6: Hybrid Sorting & Deduplication (Prioritize active logic over trivial destructors)
        def get_rank(d: SymbolDefinition):
            is_destructor = 1 if ("~" in d.name) else 0
            hits = multi_engine_hits.get((d.file_path, d.line_start), 1)
            # Bonus if symbol matches files or symbols requested by the agent plan
            matches_agent_file = 2 if any(os.path.basename(d.file_path).lower() == os.path.basename(f).lower() for f in agentic_files) else 0
            matches_agent_sym = 2 if any(s.lower() in d.name.lower() for s in agentic_symbols) else 0
            body_len = len(d.body or "")
            return (0 if is_destructor else 1, hits + matches_agent_file + matches_agent_sym, body_len)

        agentic_definitions.sort(key=get_rank, reverse=True)
        final_definitions = agentic_definitions[:10]

        overlap_count = sum(1 for cnt in multi_engine_hits.values() if cnt >= 2)
        now_rank = time.strftime("%H:%M:%S")
        print(f"[{now_rank}] 📊 [Шаг 3: Гибридное ранжирование] Согласованность={overlap_count} элементов, отобрано {len(final_definitions)} классов/функций.", flush=True)

        # Update last tracked symbols for conversational follow-ups
        found_names = [d.name for d in final_definitions[:6]]
        if found_names:
            self.last_query_symbols = found_names

        # Citations collection
        citations: List[CodeCitation] = []
        seen_citations = set()

        for d in final_definitions:
            key = (d.file_path, d.line_start)
            if key not in seen_citations:
                seen_citations.add(key)
                citations.append(CodeCitation(
                    file=d.file_path,
                    line=d.line_start,
                    symbol=d.name,
                    snippet=d.signature
                ))

        for c in calls[:15]:
            key = (c.file_path, c.line_number)
            if key not in seen_citations:
                seen_citations.add(key)
                citations.append(CodeCitation(
                    file=c.file_path,
                    line=c.line_number,
                    symbol=c.caller_symbol,
                    snippet=c.call_snippet
                ))

        for s in bm25_snippets[:8]:
            key = (s["file_path"], s["line_start"])
            if key not in seen_citations:
                seen_citations.add(key)
                citations.append(CodeCitation(
                    file=s["file_path"],
                    line=s["line_start"],
                    symbol=s["symbol_name"],
                    snippet=s["content"][:100].replace("\n", " ")
                ))

        # Step 7: Formulate High-Context Prompt
        system_prompt = build_system_prompt(language=language)
        context_prompt = build_context_prompt(
            question=question,
            project_map=compact_project_map,
            definitions=final_definitions,
            calls=calls[:30],
            bm25_snippets=bm25_snippets[:10],
            full_files=full_files_data,
            current_file=current_file,
            current_line=current_line,
            selected_code=selected_code
        )

        files_loaded_str = "\n".join([f"• {ff['file_name']} ({ff['line_count']} строк) -> {ff['file_path']}" for ff in full_files_data]) or "нет"
        defs_loaded_str = "\n".join([f"• {d.name} ({os.path.basename(d.file_path)}:{d.line_start})" for d in final_definitions[:8]]) or "нет"
        log_agent_section(
            "ШАГ 2: ДЕЙСТВИЯ АГЕНТА И СОБРАННЫЙ КОНТЕКСТ",
            f"ПОЛНЫЕ ЗАГРУЖЕННЫЕ ФАЙЛЫ:\n{files_loaded_str}\n\nОТОБРАННЫЕ ОПРЕДЕЛЕНИЯ КЛАССОВ/ФУНКЦИЙ:\n{defs_loaded_str}\n\nFTS5 BM25 СНИППЕТЫ: {len(bm25_snippets)} шт.\nVECTOR СОВПАДЕНИЯ: {', '.join(top_vector_names) if top_vector_names else 'нет'}"
        )

        log_agent_section(
            "ШАГ 3: ИТОГОВЫЙ ПРОМПТ В QWEN (ПОЛНЫЙ КОНТЕКСТ ДЛЯ ОТВЕТА)",
            f"[SYSTEM PROMPT]\n{system_prompt}\n\n[USER CONTEXT PROMPT]\n{context_prompt}"
        )

        # Step 8: Generate response via Qwen 7B
        ai_answer = self.qwen.generate_response(
            system_prompt=system_prompt,
            user_prompt=context_prompt,
            raw_user_question=question
        )

        log_agent_section("ШАГ 4: ИТОГОВЫЙ ОТВЕТ QWEN", ai_answer)

        # Step 9: Build Transparent Diagnostic Comparison Block
        typo_text = ", ".join(f"'{k}' -> '{v}'" for k, v in typo_corrections.items()) if typo_corrections else "нет"
        agent_terms_text = ", ".join(agentic_terms) if agentic_terms else "авто-эвристика"
        agent_files_text = ", ".join(agentic_files) if agentic_files else "-"
        vector_text = ", ".join(top_vector_names) if top_vector_names else "нет"

        diag_lines = [
            "\n\n---\n**[Диагностика RAG и Сравнение методов]**",
            f"• **Agentic Router (Qwen Plan):** термины=[{agent_terms_text}], файлы=[{agent_files_text}], overview={is_overview}",
            f"• **Fuzzy + FTS5:** исправлены опечатки: [{typo_text}], найдено {len(bm25_snippets)} сниппетов BM25",
            f"• **Vector Search ({self.vector.backend_name}):** топ совпадения: [{vector_text}]",
            f"• **Согласованность:** {overlap_count} символов/файлов найдены одновременно несколькими поисковыми движками."
        ]
        complete_answer = ai_answer + "\n" + "\n".join(diag_lines)

        return SearchResult(
            answer=complete_answer,
            citations=citations
        )
