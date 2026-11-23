# Offline C++ Coding Agent

> **100% local, offline AI code intelligence for C/C++ projects.** Combines an exhaustive **Map-Reduce + Rescue** agent pipeline, Tree-sitter AST call graph, and SQLite FTS5 BM25 search — running locally on **Vulkan, NVIDIA CUDA, or CPU** via `llama.cpp`.

Works with **Visual Studio Code** (and Cursor / VSCodium), **Classic Visual Studio (2005 / 2008 / 2010 / 2012 / 2013)**, and any other editor (**VS 2015–2022, CLion, Qt Creator, Vim**) through the built-in Web UI.

---

## Core Architecture

### 1. Adaptive Semantic Map (`MapBuilder`) & 3-Stage Smart Funnel (`SemanticFunnelAgent`)
For large C/C++ repositories (500+ files), scanning every raw header on every question is slow. The **Adaptive Semantic Map + 3-Stage Funnel** architecture achieves **100% file coverage in ~1–2 minutes per query**:

1. **One-Time Incremental Map Build (`MapBuilder`):**
   - Stores a persistent, `file_mtime`-cached semantic map in SQLite (`file_summaries`), with resume-on-cancel and instant re-use across re-indexes.
   - Normal files (`<= 400` lines) compress to **1 dense line** with an architectural role tag (`[CORE]`, `[DRIVER]`, `[UI]`, `[TEST]`, `[SDK]`, `[GENERATED]`, `[UTIL]`), key symbols, and a 1-sentence responsibility summary.
   - Large monolithic files (`> 400` lines) are **adaptively split into 2–5 logical sections** (grouped by class/scope or line blocks).
   - Standard SDK/vendor headers (`pthread.h`, `zlib.h`, `resource.h`, etc.), macro-only tables (`>= 80%` macros), and static data tables are classified **instantaneously via heuristics (0 LLM calls)**, while small files are batched 3–4 per LLM prompt.
2. **Stage 1 — Map Triage + SQLite Insurance:** Scans 100% of the semantic map in 2–4 compact ~3k-token batches (distinguishing `[CORE]`/`[DRIVER]` from `[TEST]`/`[SDK]`) while SQLite (`FTS5` + `symbols` + `call_relations`) reinforces exact C++ identifiers in <50ms → selects 10–15 target files.
3. **Stage 2 — AST Method Skeleton:** Extracts function signatures and outgoing calls from SQLite for the selected files and lets the LLM pick 10–18 target methods in 1–2 batches.
4. **Stage 3 — Raw Source Bodies + Call-Graph Rescue:** Loads exact line-numbered source bodies from disk and automatically rescues missing callee implementations (`%::MethodName`) and working callers via the AST Call-Graph.
5. **Stage 4 — Grounded Final Synthesis:** Produces detailed answers with clickable `[FileName.ext:LineNumber]` citations and an explicit anti-hallucination guard forbidding fabrication of unshown function bodies.

### 2. Multi-Pass Stateless Map-Reduce + Rescue (`ReducingAgent`)
When no semantic map has been built yet (or in fallback mode), `ReducingAgent` systematically scans your repository within a configurable time budget (**5 minutes Standard** / **10 minutes Deep**) without exceeding the local LLM context window:

1. **Pass 0 — Queue Ordering:** Extracts search keywords only to prioritize relevant headers first (zero files are excluded).
2. **Pass 1 — Headers Map:** Scans all header files (`.h`, `.hpp`, `.hxx`, `.hh`, `.inl`) and compact outlines of standalone `.c`/`.cpp` files in isolated stateless batches, retaining only `{file, line_range, symbols, reason}`.
3. **Pass 2 — Implementation Mapping:** Maps discovered headers and symbols to `.cpp`/`.c` implementations via filename stem, SQLite symbol definitions, and guarded `#include` graph (protecting against common header fan-out). Also records external `missing_symbols`.
4. **Pass 3 — Rescue Pass:** Combines LLM-reported `missing_symbols` with static Call-Graph callers/callees (`get_callees` / `get_callers`) to recover and scan files that were skipped in Pass 1 (with 2-hop rescue in Deep mode).
5. **Pass 4 — Recursive Reduce:** Merges overlapping line ranges (`±5` context lines), reads exact line-numbered source from disk, and iteratively compresses blocks if they exceed the active LLM context window.
6. **Pass 5 — Final Synthesis:** Generates grounded technical answers where every referenced class, method, or file has a clickable `[FileName.ext:LineNumber]` citation.

### 3. Hybrid Fast Engines
- **AST Call Graph (`tree-sitter-cpp` + MFC/Win32 fallback lexer):** Instant lookup of callers, callees, and symbol definitions.
- **SQLite FTS5 BM25 & Pure-NumPy Vector Index:** Exact identifier, constant, and semantic search.
- **Zero-Trash File Scanner & Multi-Encoding Support:** Automatically skips `.pch`, `.obj`, `.pdb`, `Debug/`, `Release/`, `ipch/`, `.git/` and safely decodes `utf-8`, `cp1251`, `cp866`, and `latin-1`.

---

## Supported Local Models (`model_catalog.json`)

| Model (`GGUF Q4_K_M`) | Disk Size | Min RAM | Recommended VRAM | Best Use Case |
| :--- | :---: | :---: | :---: | :--- |
| `Qwen2.5-Coder-1.5B-Instruct` | ~1.1 GB | 6 GB | 2 GB / CPU | Ultra-fast scanning on lightweight laptops |
| `Qwen2.5-Coder-3B-Instruct` | ~2.0 GB | 8 GB | 4 GB | Fast Map-Reduce on 8–12 GB laptops |
| `Qwen2.5-Coder-7B-Instruct` ★ | ~4.68 GB | 16 GB | 6–8 GB / iGPU | Recommended balance of speed & accuracy |
| `Qwen2.5-Coder-14B-Instruct` | ~9.0 GB | 24 GB | 12 GB | Complex C++ architecture & refactoring |
| `DeepSeek-Coder-V2-Lite-Instruct` (MoE) | ~10.4 GB | 16 GB | 12 GB | Fast 16B MoE (2.4B active params) |
| `Qwen2.5-Coder-32B-Instruct` | ~19.8 GB | 48 GB | 24 GB | Workstation maximum local accuracy |

You can also connect **any existing `.gguf` file on disk** via the GUI installer, Web UI Model Manager (`Browse .gguf`), VS Code (`Offline C++ Agent: Select Local .gguf Model File`), or the Visual Studio `.gguf` button without redownloading anything.

---

## IDE Support Matrix

| IDE / Editor | Integration Method | Features |
| :--- | :--- | :--- |
| **Visual Studio Code / Cursor / VSCodium** | Native Sidebar Extension (`vscode-extension/`) | Index Workspace, Reducing 5m/10m, hot-swap `.gguf`, clickable `[File:Line]` jump |
| **Visual Studio 2005 / 2008 / 2010 / 2012 / 2013** | Native x86 COM ToolWindow Add-in (`addin/`) | Embedded panel, Auto DTE navigation to line, hot-swap `.gguf`, RU/EN/HE UI |
| **Visual Studio 2015–2022, CLion, Qt Creator, Any** | Standalone Web UI (`http://127.0.0.1:18080`) | Index any directory path, Hardware & Model Manager, downloadable catalog |

---

## One-Click Installation

```cmd
git clone https://github.com/thelastspacebender/Offline_Cpp_coding_agent.git
cd Offline_Cpp_coding_agent
install.bat
```

### GUI Setup Wizard (`installer_gui.ps1`)
Running `install.bat` launches a multi-step setup wizard (use `install.bat --cli` for headless terminal mode):
1. **Hardware Diagnostics:** Detects CPU, RAM, disk space, GPUs (NVIDIA/AMD/Intel), and installed IDEs; recommends **CUDA**, **Vulkan**, or **CPU** and optimal context window (`8k` / `16k` / `32k`).
2. **Model Selection:** Displays all catalog models with compatibility badges (`[OK]`, `[TIGHT]`, `[HEAVY]`), checkboxes for downloading via Hugging Face with resume support (`curl -C -`), and a **Browse `.gguf`...** option for existing local files.
3. **IDE Integration:** Installs the VS Code extension and/or compiles and registers the Classic Visual Studio Add-in (`VS 8.0–12.0`).

---

## Quick Start

1. Run **`start_assistant.bat`** (or use the Desktop shortcut **`Offline C++ Coding Agent`**).
   - Starts `llama-server` dynamically using `config.json` on `http://127.0.0.1:8080`.
   - Starts FastAPI Code-Graph & Model Manager backend + Web UI on `http://127.0.0.1:18080`.
2. Open your C/C++ project in **VS Code**, **Visual Studio**, or **`http://127.0.0.1:18080`**.
3. Click **Index Workspace / Index Solution**.
4. Ask questions in **Reducing AI (5m)** or **Deep Reducing (10m)** mode.
5. Run **`stop_assistant.bat`** to shut down background processes.

---

## Repository Layout

```text
Offline_Cpp_coding_agent/
├── install.bat                   # Launches GUI Setup Wizard (or --cli fallback)
├── installer_gui.ps1             # Inno-style WinForms Setup Wizard with hardware check
├── install.ps1                   # CLI installer fallback
├── model_catalog.json            # Curated GGUF catalog, specs & verified URLs
├── config.json                   # Runtime settings (active model, n_ctx, backend)
├── start_assistant.bat           # Starts local llama-server + backend API
├── stop_assistant.bat            # Stops background servers
├── run_llama.bat / run_backend.bat
├── backend/
│   ├── main.py                   # FastAPI server + Web UI mount
│   ├── llama_launcher.py         # Dynamic llama-server runner reading config.json
│   ├── api/                      # /api/query, /api/index, /api/map/*, /api/models/*, /api/system/hardware
│   ├── core/                     # hardware.py, file_scanner.py, encoding_utils.py, logger
│   ├── parser/                   # tree_sitter_cpp.py + multi-pass MFC fallback lexer
│   ├── storage/                  # SQLite FTS5, Call-Graph store, file_summaries, NumPy vector index
│   ├── services/                 # map_builder.py, funnel_agent.py, reducing_agent.py, model_manager.py, search_service.py
│   └── tests/                    # Unit tests & live download link verifier
├── vscode-extension/             # Zero-dependency VS Code / Cursor extension
├── addin/                        # C# 2.0 Add-in for Visual Studio 2005-2013
└── web_ui/                       # Dark-theme browser UI & Model Manager
```

---

## Running Verification Tests

```cmd
python backend/tests/test_semantic_map.py -v
python backend/tests/test_reducing_agent.py -v
python backend/tests/test_model_manager.py -v
python backend/tests/verify_links.py
```
