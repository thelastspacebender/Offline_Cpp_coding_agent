"""
Domain Models for Code-Graph and Search Engine.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class CodeCitation:
    file: str
    line: int
    symbol: str = ""
    snippet: str = ""


@dataclass
class SymbolDefinition:
    name: str
    kind: str  # "function", "method", "class", "struct", "enum", "macro", "typedef"
    file_path: str
    line_start: int
    line_end: int
    signature: str = ""
    doc_comment: str = ""
    body: str = ""
    parent_scope: str = ""  # Class or namespace if applicable


@dataclass
class CallRelation:
    caller_symbol: str
    callee_name: str
    file_path: str
    line_number: int
    call_snippet: str = ""


@dataclass
class FileIndex:
    file_path: str
    encoding: str
    symbols: List[SymbolDefinition] = field(default_factory=list)
    calls: List[CallRelation] = field(default_factory=list)
    includes: List[str] = field(default_factory=list)
    line_count: int = 0


@dataclass
class SearchResult:
    answer: str
    citations: List[CodeCitation] = field(default_factory=list)
    success: bool = True
    error: Optional[str] = None
