"""
AST Parser using tree-sitter and tree-sitter-cpp.
Extracts functions, methods, classes, structs, and call sites accurately.
"""

from typing import List, Optional, Tuple
from ..core.models import SymbolDefinition, CallRelation

HAS_TREE_SITTER = False
_parser = None

try:
    from tree_sitter import Language, Parser
    import tree_sitter_cpp
    
    # Modern tree-sitter API (>=0.22)
    try:
        CPP_LANGUAGE = Language(tree_sitter_cpp.language())
        _parser = Parser(CPP_LANGUAGE)
        HAS_TREE_SITTER = True
    except Exception:
        try:
            # Older tree-sitter API
            CPP_LANGUAGE = Language(tree_sitter_cpp.language(), "cpp")
            _parser = Parser()
            _parser.set_language(CPP_LANGUAGE)
            HAS_TREE_SITTER = True
        except Exception:
            HAS_TREE_SITTER = False
except Exception:
    HAS_TREE_SITTER = False


def is_available() -> bool:
    return HAS_TREE_SITTER and _parser is not None


def parse_file_tree_sitter(
    file_path: str,
    source_code: str
) -> Tuple[List[SymbolDefinition], List[CallRelation], List[str]]:
    """
    Parses C/C++ source code using Tree-sitter AST.
    Returns: (symbols, call_relations, includes)
    """
    if not is_available():
        raise RuntimeError("Tree-sitter C++ is not available.")

    symbols: List[SymbolDefinition] = []
    calls: List[CallRelation] = []
    includes: List[str] = []

    lines = source_code.splitlines()
    tree = _parser.parse(bytes(source_code, "utf-8", errors="replace"))
    root_node = tree.root_node

    def get_node_text(node) -> str:
        return source_code[node.start_byte:node.end_byte]

    def extract_calls(func_node, caller_name: str):
        # Walk inside the function body to find call_expression nodes
        cursor = func_node.walk()
        visited_children = False
        stack = [func_node]

        while stack:
            curr = stack.pop()
            if curr.type == "call_expression":
                # First child is usually the function/callee expression
                func_expr = curr.child_by_field_name("function")
                if func_expr:
                    callee_text = get_node_text(func_expr).strip()
                    # Clean pointer/member calls: obj->Foo() or obj.Foo() -> Foo
                    if "->" in callee_text:
                        callee_name = callee_text.split("->")[-1].strip()
                    elif "." in callee_text:
                        callee_name = callee_text.split(".")[-1].strip()
                    elif "::" in callee_text:
                        callee_name = callee_text.split("::")[-1].strip()
                    else:
                        callee_name = callee_text

                    # Ignore noise / keywords
                    if callee_name and callee_name not in {"sizeof", "if", "for", "while", "switch", "catch"}:
                        call_line = curr.start_point[0] + 1
                        snippet = lines[curr.start_point[0]].strip() if curr.start_point[0] < len(lines) else ""
                        calls.append(CallRelation(
                            caller_symbol=caller_name,
                            callee_name=callee_name,
                            file_path=file_path,
                            line_number=call_line,
                            call_snippet=snippet
                        ))

            for child in curr.children:
                stack.append(child)

    def visit(node, scope: str = ""):
        nonlocal symbols, calls, includes

        if node.type == "preproc_include":
            path_node = node.child_by_field_name("path")
            if path_node:
                inc_text = get_node_text(path_node).strip(' "<>')
                includes.append(inc_text)

        elif node.type == "function_definition":
            decl_node = node.child_by_field_name("declarator")
            func_name = ""
            if decl_node:
                # Find the identifier or qualified_identifier
                name_node = decl_node.child_by_field_name("declarator")
                if not name_node:
                    name_node = decl_node
                func_name = get_node_text(name_node).strip()
                # If qualified like CDataProcessor::Init
                if "(" in func_name:
                    func_name = func_name.split("(")[0].strip()

            if not func_name:
                func_name = f"anonymous_{node.start_point[0]+1}"

            full_name = f"{scope}::{func_name}" if scope else func_name
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1

            sig_line = lines[node.start_point[0]].strip() if node.start_point[0] < len(lines) else ""
            body_text = get_node_text(node)

            symbols.append(SymbolDefinition(
                name=full_name,
                kind="method" if scope else "function",
                file_path=file_path,
                line_start=start_line,
                line_end=end_line,
                signature=sig_line,
                body=body_text[:1500],  # compact body snippet
                parent_scope=scope
            ))

            # Extract call relations from inside this function
            extract_calls(node, full_name)

        elif node.type in ("class_specifier", "struct_specifier"):
            name_node = node.child_by_field_name("name")
            class_name = get_node_text(name_node).strip() if name_node else ""
            if class_name:
                symbols.append(SymbolDefinition(
                    name=class_name,
                    kind="class" if node.type == "class_specifier" else "struct",
                    file_path=file_path,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    signature=f"{node.type.replace('_specifier', '')} {class_name}",
                    parent_scope=scope
                ))
                new_scope = f"{scope}::{class_name}" if scope else class_name
                for child in node.children:
                    visit(child, new_scope)
                return

        for child in node.children:
            visit(child, scope)

    visit(root_node)
    return symbols, calls, includes
