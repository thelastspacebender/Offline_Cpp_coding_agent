"""
Unified AST Visitor / Parser Interface.
Automatically selects Tree-sitter AST or the robust multi-pass fallback lexer.
"""

from typing import List, Tuple
from ..core.models import SymbolDefinition, CallRelation
from . import tree_sitter_cpp
from . import fallback_lexer


def parse_code(
    file_path: str,
    source_code: str
) -> Tuple[List[SymbolDefinition], List[CallRelation], List[str]]:
    """
    Parses C/C++ code. Attempts Tree-sitter AST first, falling back to multi-pass lexer.
    Returns: (symbols, calls, includes)
    """
    if tree_sitter_cpp.is_available():
        try:
            return tree_sitter_cpp.parse_file_tree_sitter(file_path, source_code)
        except Exception:
            # Fallback on any parser error
            pass

    return fallback_lexer.parse_file_fallback(file_path, source_code)
