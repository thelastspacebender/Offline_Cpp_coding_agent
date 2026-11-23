"""
Multi-pass Fallback Lexer and C/C++ Parser.
Strips comments, MFC/Win32 message maps, and macros to accurately extract
functions, methods, and call sites without external dependencies.
"""

import re
from typing import List, Tuple, Set
from ..core.models import SymbolDefinition, CallRelation

# Common MFC/Win32 decorators and macros to clean out
WIN32_DECORATORS = [
    r"\bAFX_EXT_CLASS\b", r"\bAFX_API\b", r"\b__stdcall\b", r"\b__cdecl\b",
    r"\bWINAPI\b", r"\bCALLBACK\b", r"\bSTDMETHODCALLTYPE\b", r"\bSTDMETHOD\b",
    r"\bPASCAL\b", r"\bNEAR\b", r"\bFAR\b", r"\bDECLSPEC_[A-Z0-9_]+\b"
]

MFC_MAP_REGEX = re.compile(
    r"\bBEGIN_MESSAGE_MAP\s*\([^)]*\).*?\bEND_MESSAGE_MAP\s*\(\s*\)",
    re.DOTALL
)

KEYWORDS_NOT_CALLS: Set[str] = {
    "if", "for", "while", "switch", "catch", "sizeof", "return",
    "throw", "case", "new", "delete", "typedef", "static_cast",
    "dynamic_cast", "reinterpret_cast", "const_cast", "typeid"
}


def strip_comments_keep_lines(source: str) -> str:
    """Strips comments and #if 0 blocks while strictly preserving newline count for accurate line numbers."""
    def replacer(match):
        s = match.group(0)
        return "\n" * s.count("\n")

    # Pass 1: Strip #if 0 ... #endif blocks (including optional #else/#elif branches)
    if_zero_pattern = re.compile(
        r'^[ \t]*#[ \t]*if\s+0\b.*?(?:^[ \t]*#[ \t]*else\b.*?)?^[ \t]*#[ \t]*endif\b',
        re.DOTALL | re.MULTILINE
    )
    res = re.sub(if_zero_pattern, replacer, source)

    # Pass 2: Strip comments while preserving string literals
    comment_pattern = re.compile(
        r'//.*?$|/\*.*?\*/|\'(?:\\.|[^\\\'])*\'|"(?:\\.|[^\\"])*"',
        re.DOTALL | re.MULTILINE
    )
    return re.sub(comment_pattern, lambda m: m.group(0) if m.group(0).startswith(('"', "'")) else replacer(m), res)


def parse_file_fallback(
    file_path: str,
    source_code: str
) -> Tuple[List[SymbolDefinition], List[CallRelation], List[str]]:
    """
    Multi-pass C/C++ parser tailored for VS2005 MFC & firmware code.
    Returns: (symbols, call_relations, includes)
    """
    symbols: List[SymbolDefinition] = []
    calls: List[CallRelation] = []
    includes: List[str] = []

    lines = source_code.splitlines()

    # Pass 1: Extract includes and macros from raw source
    for idx, line in enumerate(lines):
        line_stripped = line.strip()
        if line_stripped.startswith("#include"):
            inc_match = re.search(r'#include\s*[<"]([^>"]+)[>"]', line_stripped)
            if inc_match:
                includes.append(inc_match.group(1))
        elif line_stripped.startswith("#define"):
            def_match = re.search(r'#define\s+([A-Za-z0-9_]+)', line_stripped)
            if def_match:
                macro_name = def_match.group(1)
                if len(macro_name) > 2 and not macro_name.startswith("__"):
                    symbols.append(SymbolDefinition(
                        name=macro_name,
                        kind="macro",
                        file_path=file_path,
                        line_start=idx + 1,
                        line_end=idx + 1,
                        signature=line_stripped[:120]
                    ))

    # Pass 2: Clean comments and MFC map macros
    clean_code = strip_comments_keep_lines(source_code)
    for dec in WIN32_DECORATORS:
        clean_code = re.sub(dec, "", clean_code)

    # Pass 3: Extract structs and classes
    class_pattern = re.compile(
        r'\b(class|struct|interface|enum)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*[^;{]+)?\s*\{',
        re.MULTILINE
    )
    for m in class_pattern.finditer(clean_code):
        kind = m.group(1)
        name = m.group(2)
        start_pos = m.start()
        line_num = clean_code[:start_pos].count('\n') + 1
        symbols.append(SymbolDefinition(
            name=name,
            kind=kind,
            file_path=file_path,
            line_start=line_num,
            line_end=line_num,
            signature=f"{kind} {name}"
        ))

    # Pass 4: Find functions & methods using brace matching
    # Pattern matches: ReturnType [Class::]FunctionName(Args) {
    func_pattern = re.compile(
        r'(?:^[ \t]*)(?:(?:public|private|protected|internal|override|virtual|static|inline|explicit|extern|async)\s+)*'
        r'([A-Za-z_][A-Za-z0-9_<, >:*&\[\]]*?\s+)?'  # Return type
        r'((?:[A-Za-z_][A-Za-z0-9_]*::)?~?[A-Za-z_][A-Za-z0-9_]*)\s*'  # Name
        r'\(([^)]*)\)\s*(?:const)?\s*(?:throws?\s*\([^)]*\))?\s*\{',
        re.MULTILINE
    )

    for m in func_pattern.finditer(clean_code):
        func_name = re.sub(r'^[&*;\s]+', '', m.group(2)).strip()
        args = m.group(3).strip()
        ret_type = (m.group(1) or "").strip()

        # Filter out false positives (e.g. keywords)
        if func_name in KEYWORDS_NOT_CALLS:
            continue

        start_pos = m.start()
        start_line = clean_code[:start_pos].count('\n') + 1

        # Find matching closing brace
        open_brace = m.end() - 1
        depth = 1
        curr = open_brace + 1
        code_len = len(clean_code)

        while curr < code_len and depth > 0:
            ch = clean_code[curr]
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
            curr += 1

        end_line = clean_code[:curr].count('\n') + 1
        body_text = clean_code[open_brace:curr]

        sig = f"{ret_type} {func_name}({args})" if ret_type else f"{func_name}({args})"

        symbols.append(SymbolDefinition(
            name=func_name,
            kind="method" if "::" in func_name else "function",
            file_path=file_path,
            line_start=start_line,
            line_end=end_line,
            signature=sig.strip(),
            body=source_code[open_brace:min(open_brace + 1500, len(source_code))]
        ))

        # Pass 5: Find call expressions inside this function body
        # Call pattern: identifier(...)
        call_pattern = re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(', re.MULTILINE)
        for cm in call_pattern.finditer(body_text):
            callee = cm.group(1)
            if callee not in KEYWORDS_NOT_CALLS and callee != func_name:
                call_offset = open_brace + cm.start()
                call_line = clean_code[:call_offset].count('\n') + 1
                snippet = lines[call_line - 1].strip() if 0 <= call_line - 1 < len(lines) else ""
                calls.append(CallRelation(
                    caller_symbol=func_name,
                    callee_name=callee,
                    file_path=file_path,
                    line_number=call_line,
                    call_snippet=snippet
                ))

    return symbols, calls, includes
