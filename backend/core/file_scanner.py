"""
File Scanner and Filter for Visual Studio 2005 projects.
Filters out hundreds of megabytes of build trash (.pch, .obj, .pdb, Debug/, Release/)
and isolates genuine C/C++ source code.
"""

import os
from typing import List, Set

CPP_EXTENSIONS: Set[str] = {
    ".c", ".cpp", ".cxx", ".cc",
    ".h", ".hpp", ".hxx", ".inl",
    ".cs", ".rc", ".def", ".idl"
}

IGNORED_DIRECTORIES: Set[str] = {
    "debug", "release", "x64", "x86", "obj", "bin",
    ".git", ".svn", ".vs", "ipch", "_upgradereport_files",
    "intermediate", "temp", "tmp", "node_modules", "packages",
    "build", "dist", "env", "venv", ".venv"
}

IGNORED_EXTENSIONS: Set[str] = {
    ".pch", ".obj", ".pdb", ".idb", ".ilk", ".ncb", ".suo",
    ".user", ".aps", ".sbr", ".bsc", ".res", ".exp", ".lib",
    ".dll", ".exe", ".zip", ".tar", ".gz", ".7z", ".rar",
    ".bak", ".tmp", ".log", ".tlog", ".lastbuildstate"
}


def scan_source_files(root_dir: str) -> List[str]:
    """
    Recursively scans root_dir, strictly ignoring build artifacts and non-source files.
    Returns sorted list of absolute paths to valid C/C++ source files.
    """
    valid_files: List[str] = []
    root_dir = os.path.abspath(root_dir)

    if not os.path.exists(root_dir):
        return []

    for dirpath, dirnames, filenames in os.walk(root_dir):
        # Exclude ignored directories in-place to prevent os.walk from entering them
        dirnames[:] = [
            d for d in dirnames
            if d.lower() not in IGNORED_DIRECTORIES and not d.startswith(".")
        ]

        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext in IGNORED_EXTENSIONS:
                continue
            if ext in CPP_EXTENSIONS:
                full_path = os.path.join(dirpath, fname)
                try:
                    # Ignore huge files (> 10MB) as they are likely generated tables or binaries
                    if os.path.getsize(full_path) < 10 * 1024 * 1024:
                        valid_files.append(full_path)
                except OSError:
                    continue

    return sorted(valid_files)
