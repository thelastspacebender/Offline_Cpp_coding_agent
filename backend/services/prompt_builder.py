"""
Prompt Builder for Qwen2.5-Coder-7B.
Synthesizes retrieved code graph relationships, symbol definitions,
project architecture map, and BM25 code snippets into a high-context prompt.
"""

import os
from typing import List, Dict, Any, Optional
from ..core.models import SymbolDefinition, CallRelation


def smart_truncate_code(text: str, max_chars: int = 2200) -> str:
    """
    Truncates code strictly on a newline boundary (\n) to prevent cutting lines mid-sentence.
    Appends a comment indicating truncation if code exceeds max_chars.
    """
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_chars:
        return text

    # Search for the last newline within the budget
    last_newline = text.rfind("\n", 0, max_chars)
    if last_newline != -1 and last_newline > max_chars // 2:
        truncated = text[:last_newline].rstrip()
    else:
        truncated = text[:max_chars].rstrip()

    return f"{truncated}\n// ... [код сокращен, см. исходный файл]"


def build_system_prompt(language: str = "ru") -> str:
    lang = (language or "ru").lower()
    if lang in ("he", "hebrew", "עברית"):
        return (
            "אתה ארכיטקט תוכנה בכיר ומומחה ב-C/C++ (Embedded/Firmware, Linux, MFC/Win32), העוזר מתוך סביבת הפיתוח.\n"
            "כללי תשובה:\n"
            "1. ענה בעברית ברורה, טכנית ומדויקת.\n"
            "2. חובה: כל מחלקה, מתודה או קובץ המוזכרים בתשובה חייבים להיות מלווים בהפניה מדויקת בסוגריים מרובעים [FileName.ext:LineNumber]!\n"
            "3. שמות פונקציות, קבצים, ספריות וקוד מקור יש להשאיר באנגלית באיות המקורי שלהם.\n"
            "4. התעלם לחלוטין מקוד בהערות (//, /* */, #if 0) בעת תיאור הלוגיקה הפעילה של התוכנית.\n"
            "5. כלל קריטי: אסור להמציא מחלקות בדיוניות כמו MyClass או קבצים כמו main.cpp! התבסס אך ורק על המחלקות והקבצים האמיתיים מתוך מפת הפרויקט וההקשר."
        )
    elif lang in ("en", "english"):
        return (
            "You are a senior C/C++ software architect (Embedded/Firmware, Linux, MFC/Win32) analyzing a real project codebase.\n"
            "Guidelines:\n"
            "1. Answer technically, thoroughly and accurately in English. Dive straight into implementation, key classes and code.\n"
            "2. MANDATORY CITATION RULE: EVERY mentioned class, method, or file MUST be accompanied by an exact bracket citation with line number, e.g.: Class CDataProcessor [DataProcessor.h:24], method ProcessPacket [DataProcessor.cpp:85]. Use exact file names and line numbers from the context below.\n"
            "3. For call graph and caller inquiries, trace the exact call chain.\n"
            "4. Ignore commented-out code (//, /* */, #if 0) when explaining active program logic.\n"
            "5. CRITICAL: Never invent fictional classes like MyClass or files like main.cpp! Rely strictly on the actual classes, headers, and functions provided in the project map and context."
        )
    else:
        return (
            "Ты — ведущий системный архитектор и эксперт по C/C++ (Embedded/Firmware, Linux, MFC/Win32). "
            "Ты анализируешь реальную кодовую базу проекта.\n"
            "Правила ответа:\n"
            "1. Отвечай технически точно, глубоко и строго по существу. Сразу переходи к сути реализации, ключевым классам и коду.\n"
            "2. ОБЯЗАТЕЛЬНОЕ ТРЕБОВАНИЕ К ССЫЛКАМ: КАЖДЫЙ упомянутый класс, метод или файл ОБЯЗАН сопровождаться точной ссылкой в квадратных скобках с номером строки, например: Класс CDataProcessor [DataProcessor.h:24], метод ProcessPacket [DataProcessor.cpp:85]. Бери точные имена файлов и номера строк из контекста ниже.\n"
            "3. Игнорируй закомментированный код (//, /* */, #if 0) при описании текущей логики работы программы. "
            "Опирайся только на активный выполняемый код.\n"
            "4. Если вопрос о вызовах и связях — покажи четкую цепочку: кто вызывает, откуда, с какими параметрами.\n"
            "5. КРИТИЧЕСКОЕ ПРАВИЛО: Категорически запрещено выдумывать вымышленные классы вроде MyClass, AnotherClass или файлы вроде main.cpp! "
            "Анализируй только реальные классы, структуры, протоколы и функции из контекста и архитектурной карты проекта.\n"
            "6. ВАЖНО: Вся необходимая информация и фрагменты кода проекта УЖЕ ПРИВЕДЕНЫ НИЖЕ в контексте. Категорически запрещено писать отговорки вроде 'предоставьте код', 'чем могу помочь' или 'задайте вопрос'. Сразу давай развернутый технический ответ на вопрос пользователя, детально описывая реализацию по приведенным сниппетам!"
        )


def build_context_prompt(
    question: str,
    definitions: List[SymbolDefinition],
    calls: List[CallRelation],
    bm25_snippets: List[Dict[str, Any]],
    project_map: Optional[str] = None,
    full_files: Optional[List[Dict[str, Any]]] = None,
    current_file: Optional[str] = None,
    current_line: Optional[int] = None,
    selected_code: Optional[str] = None
) -> str:
    parts: List[str] = []

    # 1. Project Architecture Passport (Always present)
    if project_map:
        parts.append(f"--- АРХИТЕКТУРНАЯ КАРТА ПРОЕКТА (ОБЗОР) ---\n{project_map}")

    # 2. Active Editor Context
    if current_file:
        parts.append(f"--- АКТИВНЫЙ ФАЙЛ В РЕДАКТОРЕ ---\nССЫЛКА ДЛЯ ЦИТИРОВАНИЯ: [{os.path.basename(current_file)}:{current_line}]")
    if selected_code:
        parts.append(f"--- ВЫДЕЛЕННЫЙ ПОЛЬЗОВАТЕЛЕМ КОД ---\n{selected_code}")

    # 2.5. Full Target Files (headers or key requested files)
    if full_files:
        parts.append("--- ПОЛНОЕ СОДЕРЖИМОЕ ЗАПРОШЕННЫХ ФАЙЛОВ (ЗАГОЛОВКИ И СТРУКТУРЫ) ---")
        for ff in full_files:
            fn = ff.get('file_name', '')
            cnt = ff.get('content', '')
            parts.append(
                f"ССЫЛКА ДЛЯ ЦИТИРОВАНИЯ: [{fn}:1]\n"
                f"```{os.path.splitext(fn)[1][1:] or 'cpp'}\n"
                f"{cnt}\n"
                f"```"
            )

    # 3. Definitions (top 5, body up to 1200 chars using smart truncation)
    if definitions:
        parts.append("--- НАЙДЕННЫЕ ОПРЕДЕЛЕНИЯ ФУНКЦИЙ / СТРУКТУР / КЛАССОВ ---")
        for d in definitions[:5]:
            short_fname = os.path.basename(d.file_path)
            parts.append(
                f"ССЫЛКА ДЛЯ ЦИТИРОВАНИЯ: [{short_fname}:{d.line_start}]\n"
                f"Символ: {d.name} ({d.kind})\n"
                f"Сигнатура: {d.signature}\n"
                f"Тело:\n{smart_truncate_code(d.body, max_chars=1200)}\n"
            )

    # 4. Call graph relations (up to 6)
    if calls:
        parts.append("--- ГРАФ СВЯЗЕЙ И ВЫЗОВОВ (CALL GRAPH) ---")
        for c in calls[:6]:
            short_fname = os.path.basename(c.file_path)
            parts.append(
                f"ССЫЛКА ДЛЯ ЦИТИРОВАНИЯ: [{short_fname}:{c.line_number}] Функция '{c.caller_symbol}' -> вызывает '{c.callee_name}'\n"
                f"Строка вызова: {c.call_snippet}"
            )

    # 5. BM25 lexical snippets (up to 3, content up to 600 chars using smart truncation)
    if bm25_snippets:
        parts.append("--- РЕЛЕВАНТНЫЕ ФРАГМЕНТЫ КОДА (FTS5 BM25) ---")
        for s in bm25_snippets[:3]:
            short_fname = os.path.basename(s['file_path'])
            parts.append(
                f"ССЫЛКА ДЛЯ ЦИТИРОВАНИЯ: [{short_fname}:{s['line_start']}]\n"
                f"{smart_truncate_code(s['content'], max_chars=600)}\n"
            )

    # 6. User Question & Directive
    parts.append(
        f"--- ВОПРОС ПОЛЬЗОВАТЕЛЯ И ЗАДАЧА ---\n"
        f"Вопрос: {question}\n\n"
        f"Инструкция: Опираясь на приведенные выше определения классов, методов и связей, дай подробный технический ответ на вопрос с указанием конкретных классов и ссылок [Файл:Строка].\n"
        f"Ответ:"
    )
    return "\n\n".join(parts)

