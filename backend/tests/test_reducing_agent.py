"""
Unit tests for ReducingAgent (Pass 0 -> Pass 1 -> Pass 2 -> Pass 3 Rescue -> Pass 4 Reduce -> Pass 5 Synthesis).
Uses a completely synthetic temporary C/C++ project and a mock QwenService.
"""

import os
import sys
import json
import time
import tempfile
import shutil
import unittest

# Ensure repo root is on sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from backend.storage.sqlite_db import DatabaseManager
from backend.storage.graph_store import GraphStore
from backend.storage.fts_index import FtsIndex
from backend.services.indexing_service import IndexingService
from backend.services.reducing_agent import ReducingAgent, _parse_llm_json, BudgetTracker


class MockQwenService:
    """Deterministic mock simulating stateless llama-server Qwen responses."""

    def __init__(self, n_ctx: int = 8192, slow_delay: float = 0.0, force_reduce: bool = False):
        self.n_ctx = n_ctx
        self.slow_delay = slow_delay
        self.force_reduce = force_reduce
        self.raw_calls = []
        self.final_calls = []
        self.history = []

    def get_context_size(self, force_refresh: bool = False) -> int:
        return self.n_ctx

    def generate_raw(self, system_prompt: str, user_prompt: str, temperature: float = 0.0,
                     max_tokens: int = 256, timeout: float = 45.0, stop=None) -> str:
        if self.slow_delay > 0:
            time.sleep(self.slow_delay)
        self.raw_calls.append((system_prompt, user_prompt))

        # Pass 0 keyword extraction
        if "keywords" in system_prompt and "Extract 5 to 12" in system_prompt:
            return json.dumps({"keywords": ["SensorReader", "AcquireData"]})

        # Pass 4 Reduce
        if "compressing C/C++ evidence blocks" in system_prompt:
            if "BigModule.cpp" in user_prompt:
                return json.dumps({
                    "keep": [{"file": "BigMod/BigModule.cpp", "lines": [5, 18], "symbols": ["BigProcess"]}],
                    "summary": "BigProcess validates buffer header at [BigModule.cpp:8]."
                })
            return json.dumps({"keep": [], "summary": "Trimmed."})

        # Pass 2 (.cpp map)
        if "missing_symbols" in system_prompt:
            rel = []
            miss = []
            if "SensorReader.cpp" in user_prompt:
                rel.append({
                    "file": "ModuleA/SensorReader.cpp",
                    "lines": [4, 14],
                    "symbols": ["CSensorReader::AcquireData"],
                    "reason": "Acquires sample and delegates offset adjustment"
                })
                miss.extend(["CalibrationTable", "ComputeOffset"])
            if "CalibrationTable.cpp" in user_prompt:
                rel.append({
                    "file": "ModuleB/CalibrationTable.cpp",
                    "lines": [3, 12],
                    "symbols": ["CalibrationTable::ComputeOffset"],
                    "reason": "Rescued offset calculation implementation"
                })
            return json.dumps({"relevant": rel, "missing_symbols": miss})

        # Pass 1 / Pass 3 (headers & rescue)
        rel = []
        if "SensorReader.h" in user_prompt:
            rel.append({
                "file": "ModuleA/SensorReader.h",
                "lines": [4, 12],
                "symbols": ["CSensorReader", "AcquireData"],
                "reason": "Main sensor acquisition interface"
            })
        # Notice: intentionally DO NOT match CalibrationTable.h in Pass 1 (simulate false-negative skip)
        # Only match CalibrationTable.cpp or CalibrationTable.h when asked in Pass 3!
        if "CalibrationTable.cpp" in user_prompt:
            rel.append({
                "file": "ModuleB/CalibrationTable.cpp",
                "lines": [3, 12],
                "symbols": ["CalibrationTable::ComputeOffset"],
                "reason": "Computes calibrated float offset"
            })
        if "BigModule.h" in user_prompt:
            rel.append({
                "file": "BigMod/BigModule.h",
                "lines": [1, 8],
                "symbols": ["BigProcess"],
                "reason": "Big module entry"
            })
        if "CommonDefs.h" in user_prompt:
            rel.append({
                "file": "Shared/CommonDefs.h",
                "lines": [1, 6],
                "symbols": ["SHARED_FLAG"],
                "reason": "Shared defs"
            })
        return json.dumps({"relevant": rel})

    def generate_response(self, system_prompt: str, user_prompt: str, raw_user_question=None,
                          temperature: float = 0.2, max_tokens: int = 3072, use_history: bool = True) -> str:
        self.final_calls.append((system_prompt, user_prompt, use_history))
        if "CalibrationTable.cpp" in user_prompt:
            return (
                "Чтение сенсора реализовано в `CSensorReader::AcquireData` [SensorReader.cpp:4], "
                "который вызывает калибровку `CalibrationTable::ComputeOffset` [CalibrationTable.cpp:3]."
            )
        return "Проанализирован код [SensorReader.h:4]."


class TestReducingAgent(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="reducing_test_")
        self.proj_dir = os.path.join(self.tmp_dir, "proj")
        os.makedirs(self.proj_dir)
        self.db_path = os.path.join(self.tmp_dir, "test_rag.db")
        self.db = DatabaseManager(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write_file(self, rel_path: str, content: str) -> str:
        full = os.path.join(self.proj_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return full

    def _build_agent(self, qwen_mock: MockQwenService) -> ReducingAgent:
        IndexingService(self.db).index_directory(self.proj_dir)
        return ReducingAgent(self.db, qwen_mock, GraphStore(self.db), FtsIndex(self.db))

    def test_fault_tolerant_json_parser(self):
        raw1 = "```json\n{\"relevant\": [{\"file\": \"A/Foo.h\", \"lines\": \"10-42\", \"symbols\": [\"CFoo\",], \"reason\": \"ok\",},]}\n```"
        p1 = _parse_llm_json(raw1)
        self.assertEqual(len(p1["relevant"]), 1)
        self.assertEqual(p1["relevant"][0]["file"], "A/Foo.h")

        # Truncated JSON recovery
        raw2 = '{"relevant": [{"file": "B/Bar.cpp", "lines": [5, 25], "symbols": ["Init"]}, {"file": "C/Trunc'
        p2 = _parse_llm_json(raw2)
        self.assertEqual(len(p2["relevant"]), 1)
        self.assertEqual(p2["relevant"][0]["file"], "B/Bar.cpp")
        self.assertEqual(p2["relevant"][0]["lines"], [5, 25])

    def test_full_pass1_pass2_pass3_rescue_and_pass5(self):
        self._write_file("ModuleA/SensorReader.h", (
            "#pragma once\n"
            "class CSensorReader {\n"
            "public:\n"
            "    float AcquireData(int ch);\n"
            "};\n"
        ))
        self._write_file("ModuleA/SensorReader.cpp", (
            '#include "SensorReader.h"\n'
            '#include "../ModuleB/CalibrationTable.h"\n'
            "float CSensorReader::AcquireData(int ch) {\n"
            "    CalibrationTable tbl;\n"
            "    float raw = (float)ch * 1.5f;\n"
            "    return tbl.ComputeOffset(raw);\n"
            "}\n"
        ))
        self._write_file("ModuleB/CalibrationTable.h", (
            "#pragma once\n"
            "class CalibrationTable {\n"
            "public:\n"
            "    float ComputeOffset(float val);\n"
            "};\n"
        ))
        self._write_file("ModuleB/CalibrationTable.cpp", (
            '#include "CalibrationTable.h"\n'
            "float CalibrationTable::ComputeOffset(float val) {\n"
            "    if (val < 0.0f) return 0.0f;\n"
            "    return val + 2.75f;\n"
            "}\n"
        ))
        self._write_file("StandaloneEntry.c", (
            "int main_entry(void) {\n"
            "    return 0;\n"
            "}\n"
        ))

        qwen = MockQwenService()
        agent = self._build_agent(qwen)
        res = agent.run("How does AcquireData calibrate sensor values?", budget_sec=120, deep=False)

        self.assertTrue(res.success)
        self.assertEqual(len(qwen.final_calls), 1)
        final_prompt = qwen.final_calls[0][1]
        # Verify CalibrationTable.cpp was rescued in Pass 3 despite CalibrationTable.h being skipped in Pass 1!
        self.assertIn("SensorReader.cpp", final_prompt)
        self.assertIn("CalibrationTable.cpp", final_prompt)
        self.assertIn("Pass 3 Rescue: ", res.answer)
        cited_basenames = {os.path.basename(c.file) for c in res.citations}
        self.assertIn("SensorReader.cpp", cited_basenames)
        self.assertIn("CalibrationTable.cpp", cited_basenames)

    def test_pass4_recursive_reduce_compression(self):
        self._write_file("BigMod/BigModule.h", "void BigProcess(int n);\n")
        # Create a 500-line file (~25,000 chars) so n_ctx=4096 final window triggers Pass 4 Reduce
        body_lines = ['#include "BigModule.h"', "void BigProcess(int n) {"]
        for i in range(1, 500):
            body_lines.append(f"    int var_{i:03d} = n + {i}; // padding line for realistic size")
        body_lines.append("}")
        self._write_file("BigMod/BigModule.cpp", "\n".join(body_lines))

        class LargeHitMock(MockQwenService):
            def generate_raw(self, system_prompt, user_prompt, temperature=0.0, max_tokens=256, timeout=45.0, stop=None):
                if "missing_symbols" in system_prompt:
                    return json.dumps({
                        "relevant": [{"file": "BigMod/BigModule.cpp", "lines": [1, 480], "symbols": ["BigProcess"], "reason": "Full body"}],
                        "missing_symbols": []
                    })
                return super().generate_raw(system_prompt, user_prompt, temperature, max_tokens, timeout, stop)

        qwen = LargeHitMock(n_ctx=4096)
        agent = self._build_agent(qwen)
        res = agent.run("Explain BigProcess", budget_sec=120, deep=False)

        self.assertTrue(res.success)
        self.assertIn("Reduce: 1 раунд", res.answer)
        final_prompt = qwen.final_calls[0][1]
        self.assertIn("ВЫЖИМКА ПО РЕЗУЛЬТАТАМ МНОГОПРОХОДНОГО СЖАТИЯ", final_prompt)
        self.assertIn("BigModule.cpp:8", final_prompt)

    def test_budget_tracker_terminates_cleanly(self):
        for i in range(6):
            self._write_file(f"H{i}/Mod{i}.h", f"void Func{i}(void);\n")
            self._write_file(f"H{i}/Mod{i}.cpp", f"void Func{i}(void) {{ }}\n")

        # Very tight budget with simulated LLM delay; must finish without hanging and run Pass 5
        qwen = MockQwenService(slow_delay=0.12)
        agent = self._build_agent(qwen)
        t0 = time.time()
        res = agent.run("Explain architecture", budget_sec=2, deep=False)
        elapsed = time.time() - t0

        self.assertTrue(res.success)
        self.assertLess(elapsed, 6.0)
        self.assertEqual(len(qwen.final_calls), 1)

    def test_include_fanout_guard(self):
        self._write_file("Shared/CommonDefs.h", "#define SHARED_FLAG 1\nvoid CommonInit(void);\n")
        for i in range(15):
            self._write_file(
                f"Dummy/Dummy{i:02d}.cpp",
                f'#include "../Shared/CommonDefs.h"\nvoid DummyFn{i}(void) {{ }}\n'
            )

        qwen = MockQwenService()
        agent = self._build_agent(qwen)
        all_files = agent._load_indexed_files()
        # Pass 1 evidence pointing only to CommonDefs.h (included by 15 files > fanout cap 12)
        from backend.services.reducing_agent import EvidenceItem
        p1 = [EvidenceItem(
            file_path=os.path.join(self.proj_dir, "Shared", "CommonDefs.h"),
            display_path="Shared/CommonDefs.h",
            line_start=1,
            line_end=2,
            symbols=[],
            reason="hit",
            source_pass="pass1_headers"
        )]
        mapped = agent._map_headers_to_sources(p1, all_files, current_file=None, deep=False)
        # Should NOT explode with all 15 Dummy*.cpp files
        self.assertEqual(len(mapped), 0)


if __name__ == "__main__":
    unittest.main()
