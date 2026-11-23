"""
Encoding Utilities for legacy Visual Studio 2005 projects.
Handles Cyrillic (windows-1251), UTF-8, and fallback encodings safely.
"""

from typing import Tuple, List

CANDIDATE_ENCODINGS: List[str] = [
    "utf-8",
    "cp1251",      # Standard Windows Cyrillic for Russian VS2005 projects
    "cp866",       # DOS Cyrillic (sometimes in old firmware headers)
    "latin1",      # ISO-8859-1
]


def read_file_safe(file_path: str) -> Tuple[str, str]:
    """
    Reads a file using a cascading encoding detection strategy.
    Guarantees no UnicodeDecodeError on Russian/legacy codebases.
    Returns: (file_content, detected_encoding)
    """
    raw_bytes: bytes
    try:
        with open(file_path, "rb") as f:
            raw_bytes = f.read()
    except Exception as e:
        return "", f"error: {str(e)}"

    if not raw_bytes:
        return "", "empty"

    # Strip UTF-8 BOM if present
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        try:
            return raw_bytes[3:].decode("utf-8"), "utf-8-bom"
        except UnicodeDecodeError:
            pass

    # Try candidate encodings in priority order
    for enc in CANDIDATE_ENCODINGS:
        try:
            text = raw_bytes.decode(enc)
            return text, enc
        except UnicodeDecodeError:
            continue

    # Final guaranteed fallback with replacement
    return raw_bytes.decode("latin1", errors="replace"), "latin1_lossy"
