"""
Full-Text Search Engine with BM25 Ranking powered by SQLite FTS5.
"""

from typing import List, Dict, Any, Optional
from .sqlite_db import DatabaseManager

COMMON_STOP_WORDS = {
    # English
    "what", "how", "where", "which", "who", "when", "why", "does", "did", "do",
    "use", "used", "using", "this", "that", "these", "those", "code", "project",
    "file", "files", "function", "class", "method", "check", "find", "show", "tell",
    "please", "man", "can", "you", "all", "places", "appear", "connected", "connection",
    "type", "is", "are", "was", "were", "been", "have", "has", "had", "a", "an", "the",
    "and", "or", "in", "on", "at", "to", "for", "of", "with", "by", "about", "into",
    "through", "during", "before", "after", "above", "below", "from", "up", "down",
    "out", "off", "over", "under", "again", "further", "then", "once", "here", "there",
    "any", "both", "each", "few", "more", "most", "other", "some", "such", "no", "nor",
    "not", "only", "own", "same", "so", "than", "too", "very", "s", "t", "don", "should",
    "now", "ok", "hello", "hi", "hey", "thanks", "thank",
    # Russian
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


class FtsIndex:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def index_document(
        self,
        file_path: str,
        line_start: int,
        line_end: int,
        symbol_name: str,
        symbol_kind: str,
        content: str
    ):
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO rag_fts (file_path, line_start, line_end, symbol_name, symbol_kind, content)
                VALUES (?, ?, ?, ?, ?, ?);
            """, (file_path, line_start, line_end, symbol_name, symbol_kind, content))
            conn.commit()

    def search_bm25(
        self,
        query: str,
        limit: int = 15,
        custom_tokens: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Executes BM25 ranked full-text search.
        Sanitizes query tokens for FTS5 syntax, strips conversational stop-words,
        and applies custom domain-expanded tokens if provided.
        """
        if custom_tokens is not None and len(custom_tokens) > 0:
            tokens_to_use = [t.strip().replace('"', '') for t in custom_tokens if len(t.strip()) > 1]
        else:
            raw_tokens = [
                t for t in "".join(c if c.isalnum() or c == '_' else " " for c in query).split()
                if len(t) > 1
            ]
            # Strip stop words first, then fallback to raw if everything was filtered
            filtered = [t for t in raw_tokens if t.lower() not in COMMON_STOP_WORDS]
            tokens_to_use = filtered if filtered else raw_tokens

        if not tokens_to_use:
            return []

        # Take up to 20 meaningful tokens
        fts_query = " OR ".join(f'"{t}"*' for t in tokens_to_use[:20])

        results: List[Dict[str, Any]] = []
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute("""
                    SELECT file_path, line_start, line_end, symbol_name, symbol_kind, content,
                           bm25(rag_fts) as rank
                    FROM rag_fts
                    WHERE rag_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?;
                """, (fts_query, limit))

                for row in cur.fetchall():
                    results.append({
                        "file_path": row["file_path"],
                        "line_start": row["line_start"],
                        "line_end": row["line_end"],
                        "symbol_name": row["symbol_name"],
                        "symbol_kind": row["symbol_kind"],
                        "content": row["content"],
                        "rank": row["rank"]
                    })
            except Exception:
                # Fallback if match expression fails on special chars
                pass

        return results
