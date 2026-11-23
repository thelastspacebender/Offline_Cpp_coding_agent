"""
Unit and Integration Tests for Adaptive Semantic Map (MapBuilder) and
3-Stage Smart Funnel + Call-Graph Rescue (SemanticFunnelAgent).
Uses a completely synthetic temporary C++ project and a deterministic mock LLM.
"""

import os
import sys
import json
import time
import shutil
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.storage.sqlite_db import DatabaseManager
from backend.storage.graph_store import GraphStore
from backend.storage.fts_index import FtsIndex
from backend.services.indexing_service import IndexingService
from backend.services.map_builder import MapBuilder
from backend.services.funnel_agent import SemanticFunnelAgent
from backend.services.search_service import SearchService


class MockFunnelQwen:
    """Deterministic mock LLM covering MapBuilder, Stage 1, Stage 2, and Stage 4."""

    def __init__(self, n_ctx: int = 16384):
        self.n_ctx = n_ctx
        self.raw_calls = []
        self.final_prompts = []

    def get_context_size(self, force_refresh: bool = False) -> int:
        return self.n_ctx

    def is_server_reachable(self) -> bool:
        return True

    def clear_history(self):
        pass

    def generate_raw(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        self.raw_calls.append({"sys": system_prompt, "usr": user_prompt})

        # 1. MapBuilder batch summarization prompt
        if "building a compact semantic map" in system_prompt:
            items = []
            for idx, part in enumerate(user_prompt.split("--- ITEM id=\"")[1:]):
                id_val = part.split('"', 1)[0]
                if "TestConsole.cpp" in part:
                    items.append({
                        "id": id_val,
                        "role": "test",
                        "summary": "Interactive console test menu for manual calibration",
                        "key_symbols": ["RunConsoleMenu"]
                    })
                elif "DeviceDriver.cpp" in part:
                    items.append({
                        "id": id_val,
                        "role": "driver",
                        "summary": "Low-level hardware ioctl driver setting target sample rate",
                        "key_symbols": ["CDevice::SetTargetRate"]
                    })
                elif "CoreLoop.cpp" in part:
                    items.append({
                        "id": id_val,
                        "role": "core",
                        "summary": "Main worker acquisition loop with automatic target rate tuning",
                        "key_symbols": ["CCoreController::RunCycle", "CCoreController::AutoTuneRate"]
                    })
                elif "BigEngine.cpp" in part:
                    items.append({
                        "id": id_val,
                        "role": "core",
                        "summary": "Engine subsystem processing section",
                        "key_symbols": ["CAlphaEngine", "CBetaEngine"]
                    })
                else:
                    items.append({
                        "id": id_val,
                        "role": "core",
                        "summary": "Core C++ module definitions and helpers",
                        "key_symbols": ["CWorker"]
                    })
            return json.dumps({"items": items})

        # 2. Stage 1 Map Triage: deliberately pick ONLY CoreLoop.cpp (NOT DeviceDriver.cpp)
        #    so Stage 3 Call-Graph Rescue must recover DeviceDriver.cpp!
        if "Stage 1 triage over a project semantic map" in system_prompt:
            picked = []
            if "CoreLoop.cpp" in user_prompt:
                picked.append({
                    "file": "CoreLoop.cpp",
                    "priority": 1,
                    "reason": "Main worker loop and auto rate tuning"
                })
            return json.dumps({"files": picked})

        # 3. Stage 2 AST Skeleton Selection
        if "Stage 2 method selection" in system_prompt:
            selected_ids = []
            for line in user_prompt.splitlines():
                if "[M" in line and ("RunCycle" in line or "AutoTuneRate" in line):
                    mid = line.split("]", 1)[0].lstrip("[")
                    selected_ids.append(mid)
            return json.dumps({
                "selected": selected_ids,
                "extra_symbols": ["SetTargetRate"]
            })

        return "{}"

    def generate_response(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        self.final_prompts.append({"sys": system_prompt, "usr": user_prompt})
        return (
            "Автоматический подбор выполняется в `CCoreController::AutoTuneRate` [CoreLoop.cpp:12], "
            "которая вызывает драйверную реализацию `CDevice::SetTargetRate` [DeviceDriver.cpp:6]."
        )


class TestSemanticMapAndFunnel(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="semantic_map_test_")
        self.src_dir = os.path.join(self.tmp_dir, "proj")
        os.makedirs(self.src_dir, exist_ok=True)
        self.db_path = os.path.join(self.tmp_dir, "test_map.db")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write(self, rel_path: str, content: str) -> str:
        full = os.path.join(self.src_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return full

    def test_incremental_mtime_cache_and_heuristic_sdk_classification(self):
        # 1. Vendor SDK header -> 0 LLM calls
        self._write("pthread.h", "#pragma once\nint pthread_create(void* t);\n")
        # 2. Macro-only constant table (>=20 macros) -> 0 LLM calls
        macro_lines = ["#pragma once"] + [f"#define ERR_CODE_{i} {1000 + i}" for i in range(30)]
        self._write("MacroDefs.h", "\n".join(macro_lines) + "\n")
        # 3. Giant static data table (>600 lines, 0 functions) -> 0 LLM calls
        data_lines = ["static const int kLookupTable[] = {"] + [f"    {i}, // row {i}" for i in range(620)] + ["};"]
        self._write("DataTable.cpp", "\n".join(data_lines) + "\n")
        # 4. Two normal small user files -> batched into 1 LLM call
        self._write("Worker.h", "class CWorker { public: void Start(); int Compute(int x); };\n")
        worker_cpp = self._write("Worker.cpp", "void CWorker::Start() { Compute(42); }\nint CWorker::Compute(int x) { return x * 2; }\n")

        db = DatabaseManager(self.db_path)
        IndexingService(db).index_directory(self.src_dir)
        qwen = MockFunnelQwen()
        mb = MapBuilder(db, qwen, GraphStore(db))

        # First build
        st1 = mb.build_sync()
        self.assertEqual(st1["state"], "completed")
        self.assertEqual(st1["total_files"], 5)
        self.assertEqual(st1["mapped_files"], 5)
        self.assertEqual(st1["heuristic_tagged"], 3)
        self.assertEqual(st1["llm_processed"], 2)
        self.assertEqual(len(qwen.raw_calls), 1, "Both small user files should pack into 1 batch")

        # Verify heuristic files were NEVER sent to LLM
        sent_text = qwen.raw_calls[0]["usr"]
        self.assertNotIn("pthread.h", sent_text)
        self.assertNotIn("MacroDefs.h", sent_text)
        self.assertNotIn("DataTable.cpp", sent_text)

        # Second build with no file changes -> 0 LLM calls, 100% cached
        qwen.raw_calls.clear()
        st2 = mb.build_sync()
        self.assertEqual(st2["skipped_cached"], 5)
        self.assertEqual(st2["llm_processed"], 0)
        self.assertEqual(len(qwen.raw_calls), 0)

        # Modify 1 file's mtime -> only that 1 file rebuilt
        new_mtime = os.path.getmtime(worker_cpp) + 10.0
        os.utime(worker_cpp, (new_mtime, new_mtime))
        st3 = mb.build_sync()
        self.assertEqual(st3["skipped_cached"], 4)
        self.assertEqual(st3["llm_processed"], 1)
        self.assertEqual(len(qwen.raw_calls), 1)
        self.assertIn("Worker.cpp", qwen.raw_calls[0]["usr"])
        self.assertNotIn("Worker.h", qwen.raw_calls[0]["usr"])

    def test_adaptive_large_file_splitting(self):
        # Build a >450 line file with two distinct classes
        lines = []
        for i in range(1, 240):
            if i == 1:
                lines.append("void CAlphaEngine::ProcessAlpha() {")
            elif i == 230:
                lines.append("    return;")
                lines.append("}")
            else:
                lines.append(f"    int a_{i} = {i};")
        for i in range(1, 240):
            if i == 1:
                lines.append("void CBetaEngine::ProcessBeta() {")
            elif i == 230:
                lines.append("    return;")
                lines.append("}")
            else:
                lines.append(f"    int b_{i} = {i};")
        self._write("BigEngine.cpp", "\n".join(lines) + "\n")

        db = DatabaseManager(self.db_path)
        IndexingService(db).index_directory(self.src_dir)
        qwen = MockFunnelQwen()
        mb = MapBuilder(db, qwen, GraphStore(db))

        st = mb.build_sync()
        self.assertEqual(st["mapped_files"], 1)
        self.assertGreaterEqual(st["summary_entries"], 2, "Large file >400 lines must produce >=2 adaptive sections")
        self.assertLessEqual(st["summary_entries"], 5)

    def test_funnel_end_to_end_stage1_stage2_stage3_rescue_and_exclusions(self):
        # 1. Excluded [TEST] console menu calling SetTargetRate
        self._write("TestConsole.cpp", (
            "void RunConsoleMenu(CDevice* pDev) {\n"
            "    float fRate = 0.05f;\n"
            "    pDev->SetTargetRate(fRate);\n"
            "}\n"
        ))
        # 2. [SDK] header
        self._write("pthread.h", "#pragma once\nint pthread_mutex_lock(void* m);\n")
        # 3. [DRIVER] actual implementation of SetTargetRate (Stage 1 mock intentionally skips it)
        self._write("DeviceDriver.cpp", (
            "#include \"DeviceDriver.h\"\n"
            "void CDevice::SetTargetRate(float fRate) {\n"
            "    m_iRateMs = (int)(fRate * 1000.0f + 0.5f);\n"
            "    ioctl(m_fd, CMD_SET_RATE, &m_iRateMs);\n"
            "}\n"
        ))
        # 4. [CORE] working loop with >8 methods so Stage 2 LLM skeleton selector definitely executes
        core_methods = [
            "void CCoreController::RunCycle() {\n"
            "    while (m_running) {\n"
            "        AutoTuneRate();\n"
            "    }\n"
            "}\n",
            "void CCoreController::AutoTuneRate() {\n"
            "    float nextRate = m_lastRate * 1.25f;\n"
            "    m_pDev->SetTargetRate(nextRate);\n"
            "}\n",
        ]
        for k in range(1, 9):
            core_methods.append(f"int CCoreController::Helper{k}(int v) {{ return v + {k}; }}\n")
        self._write("CoreLoop.cpp", "\n".join(core_methods))

        db = DatabaseManager(self.db_path)
        IndexingService(db).index_directory(self.src_dir)
        qwen = MockFunnelQwen()
        svc = SearchService(db, qwen)

        # Build Semantic Map first
        map_st = svc.map_builder.build_sync()
        self.assertTrue(map_st["is_ready"])
        qwen.raw_calls.clear()

        # Query in funnel mode explicitly excluding TestConsole
        res = svc.query(
            question="Найди реализацию SetTargetRate и рабочий цикл автоподбора (игнорируй TestConsole)",
            mode="funnel",
            language="ru"
        )

        self.assertTrue(res.success)
        self.assertIn("Smart Funnel", res.answer)
        self.assertEqual(len(qwen.final_prompts), 1)

        final_sys = qwen.final_prompts[0]["sys"]
        final_usr = qwen.final_prompts[0]["usr"]

        # Verify anti-hallucination guard is injected
        self.assertIn("ANTI-HALLUCINATION", final_sys)

        # Verify Stage 1 respected exclusion (TestConsole never leaked into final context)
        self.assertNotIn("RunConsoleMenu", final_usr)

        # Verify Stage 2 selected AutoTuneRate / RunCycle AND Stage 3 rescued CDevice::SetTargetRate!
        self.assertIn("AutoTuneRate", final_usr)
        self.assertIn("CMD_SET_RATE", final_usr, "Stage 3 Call-Graph Rescue must pull CDevice::SetTargetRate implementation!")

        # Verify citations point to real indexed files
        cit_files = {os.path.basename(c.file) for c in res.citations}
        self.assertIn("CoreLoop.cpp", cit_files)
        self.assertIn("DeviceDriver.cpp", cit_files)

    def test_stage3_call_graph_rescue_without_question_mention(self):
        # Question does NOT mention SetTargetRate at all -> Stage 1B SQLite insurance won't add DeviceDriver.cpp;
        # Stage 1 picks only CoreLoop.cpp -> Stage 2 picks AutoTuneRate -> Stage 3 Call-Graph Rescue must fetch CDevice::SetTargetRate!
        self._write("DeviceDriver.cpp", (
            "void CDevice::SetTargetRate(float fRate) {\n"
            "    int ms = (int)(fRate * 1000.0f);\n"
            "    ioctl(m_fd, CMD_HW_RATE_ONLY, &ms);\n"
            "}\n"
        ))
        core_methods = [
            "void CCoreController::RunCycle() {\n"
            "    while (m_running) { AutoTuneRate(); }\n"
            "}\n",
            "void CCoreController::AutoTuneRate() {\n"
            "    m_pDev->SetTargetRate(0.025f);\n"
            "}\n",
        ] + [f"int CCoreController::Pad{k}(int v) {{ return v + {k}; }}\n" for k in range(1, 9)]
        self._write("CoreLoop.cpp", "\n".join(core_methods))

        db = DatabaseManager(self.db_path)
        IndexingService(db).index_directory(self.src_dir)
        qwen = MockFunnelQwen()
        svc = SearchService(db, qwen)
        svc.map_builder.build_sync()
        qwen.raw_calls.clear()

        res = svc.query(
            question="Как рабочий цикл автоподбора общается с железом?",
            mode="funnel",
            language="ru"
        )
        self.assertTrue(res.success)
        final_usr = qwen.final_prompts[0]["usr"]
        self.assertIn("CMD_HW_RATE_ONLY", final_usr, "Stage 3 Call-Graph Rescue must recover unmentioned callee body!")
        self.assertIn("rescue_callee", final_usr)

    def test_auto_funnel_dispatch_and_db_clear_keeps_summaries(self):
        self._write("Alpha.cpp", "void CAlpha::Run() { int x = 1; }\n")
        db = DatabaseManager(self.db_path)
        IndexingService(db).index_directory(self.src_dir)
        qwen = MockFunnelQwen()
        svc = SearchService(db, qwen)

        # Build map -> mode="reduce" should automatically dispatch to Smart Funnel
        svc.map_builder.build_sync()
        res_funnel = svc.query("What does CAlpha::Run do?", mode="reduce", language="en")
        self.assertIn("Smart Funnel", res_funnel.answer)

        # Default db.clear() must preserve file_summaries across re-indexing
        db.clear()
        with db.get_connection() as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM file_summaries;").fetchone()[0]
        self.assertGreater(cnt, 0)

        # Explicit db.clear(keep_summaries=False) wipes summaries -> fallback to ReducingAgent
        db.clear(keep_summaries=False)
        IndexingService(db).index_directory(self.src_dir)
        self.assertFalse(svc.map_builder.is_ready())
        res_reduce = svc.query("What does CAlpha::Run do?", mode="reduce", language="en")
        self.assertIn("Reducing:", res_reduce.answer)

    def test_grep_mode_returns_over_20_deduped_matches(self):
        # Write 35 separate files containing a shared token so FTS5 has >20 unique matches
        for idx in range(35):
            self._write(f"Mod{idx:02d}.cpp", f"void Func{idx}() {{ int MODE_MARKER_{idx} = 1; }}\n")
        db = DatabaseManager(self.db_path)
        IndexingService(db).index_directory(self.src_dir)
        svc = SearchService(db, MockFunnelQwen())

        res = svc.query("MODE_MARKER", mode="grep")
        self.assertGreaterEqual(len(res.citations), 35)
        locs = [(c.file, c.line) for c in res.citations]
        self.assertEqual(len(locs), len(set(locs)), "Duplicate (file, line) FTS5 hits should be deduplicated")

    def test_explicit_file_line_range_and_noisy_helper_skip(self):
        # Large file where target lines 55-64 are explicitly requested in question
        lines = [f"// line {i}\n" for i in range(1, 55)]
        lines.append("int CHardware::CheckRange(int mode) {\n")
        lines.append("    GetProp(123);\n")
        lines.append("    RealDriverHelper(mode);\n")
        lines.append("    if (mode == 1) return TARGET_MARKER_57;\n")
        lines.append("    return 0;\n")
        lines.append("}\n")
        lines.extend([f"// tail {i}\n" for i in range(61, 120)])
        self._write("Hardware.cpp", "".join(lines))
        # Noisy GetProp + real helper in another file (Stage 1 doesn't pick Props.cpp)
        self._write("Props.cpp", (
            "int CProps::GetProp(int id) {\n"
            + "".join(f"    if (id == {i}) return {i};\n" for i in range(1, 20))
            + "    return -1;\n}\n"
            "int RealDriverHelper(int m) {\n"
            "    return HELPER_MARKER_OK + m;\n"
            "}\n"
        ))

        db = DatabaseManager(self.db_path)
        IndexingService(db).index_directory(self.src_dir)
        qwen = MockFunnelQwen()
        svc = SearchService(db, qwen)
        svc.map_builder.build_sync()
        qwen.raw_calls.clear()

        res = svc.query("Покажи код в Hardware.cpp вокруг строк 55-64", mode="funnel", language="ru")
        self.assertTrue(res.success)
        final_usr = qwen.final_prompts[0]["usr"]
        self.assertIn("TARGET_MARKER_57", final_usr, "Explicit file:line range must be loaded in Stage 3")
        self.assertIn("HELPER_MARKER_OK", final_usr, "Real called helper must be rescued in Stage 3")
        self.assertNotIn("CProps::GetProp", final_usr, "Noisy boilerplate GetProp must not waste Stage 3 rescue budget")


if __name__ == "__main__":
    unittest.main()
