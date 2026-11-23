"""
Unit tests for hardware evaluation, model_catalog.json integrity, dynamic llama_launcher,
and ModelManager custom .gguf selection.
"""

import os
import sys
import json
import tempfile
import shutil
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from backend.core.hardware import evaluate_model_compatibility, get_hardware_profile
from backend.llama_launcher import build_llama_command, resolve_model_path
from backend.services.model_manager import ModelManager
from backend.services.qwen_service import QwenService


class TestModelManagerAndHardware(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="model_mgr_test_")
        os.makedirs(os.path.join(self.tmp_dir, "models"), exist_ok=True)
        os.makedirs(os.path.join(self.tmp_dir, "bin"), exist_ok=True)
        shutil.copy2(
            os.path.join(REPO_ROOT, "model_catalog.json"),
            os.path.join(self.tmp_dir, "model_catalog.json")
        )
        with open(os.path.join(self.tmp_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({
                "server": {"host": "127.0.0.1", "port": 18080},
                "llama": {
                    "host": "127.0.0.1",
                    "port": 8080,
                    "backend": "vulkan",
                    "model_path": "models/qwen2.5-coder-7b-instruct-q4_k_m.gguf",
                    "context_size": 16384,
                    "gpu_layers": 99,
                    "threads": 8,
                    "custom_models": []
                }
            }, f)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_catalog_structure(self):
        with open(os.path.join(REPO_ROOT, "model_catalog.json"), "r", encoding="utf-8") as f:
            cat = json.load(f)
        self.assertGreaterEqual(len(cat["models"]), 6)
        ids = set()
        for m in cat["models"]:
            self.assertNotIn(m["id"], ids)
            ids.add(m["id"])
            self.assertTrue(m["url"].startswith("https://"))
            self.assertGreater(m["size_gb"], 0)
            self.assertGreater(m["min_ram_gb"], 0)

    def test_hardware_compatibility_badges(self):
        small_model = {"min_ram_gb": 6, "rec_vram_gb": 2}
        big_model = {"min_ram_gb": 48, "rec_vram_gb": 24}

        r_ok = evaluate_model_compatibility(small_model, ram_gb=16.0, best_vram_gb=4.0, has_igpu=True)
        self.assertEqual(r_ok["status"], "recommended")

        r_heavy = evaluate_model_compatibility(big_model, ram_gb=16.0, best_vram_gb=4.0, has_igpu=True)
        self.assertEqual(r_heavy["status"], "too_heavy")

        hw = get_hardware_profile(self.tmp_dir)
        self.assertIn(hw["recommended_backend"], ("vulkan", "cuda", "cpu"))
        self.assertIn(hw["recommended_ctx"], (8192, 16384, 32768))

    def test_select_custom_external_gguf_and_list(self):
        ext_dir = os.path.join(self.tmp_dir, "external_models")
        os.makedirs(ext_dir, exist_ok=True)
        custom_gguf = os.path.join(ext_dir, "my-custom-coder.gguf")
        with open(custom_gguf, "wb") as f:
            f.write(b"GGUF_DUMMY_HEADER")

        mgr = ModelManager(self.tmp_dir, QwenService())
        res = mgr.select_model(custom_gguf, context_size=32768, restart_server=False)
        self.assertTrue(res["success"])
        self.assertEqual(res["context_size"], 32768)

        listed = mgr.list_models()
        self.assertEqual(listed["active_model_name"], "my-custom-coder.gguf")
        self.assertEqual(listed["context_size"], 32768)
        self.assertTrue(any(m["path"] == os.path.abspath(custom_gguf) and m["active"] for m in listed["local_models"]))

    def test_select_missing_file_returns_error(self):
        mgr = ModelManager(self.tmp_dir, QwenService())
        res = mgr.select_model(os.path.join(self.tmp_dir, "does_not_exist.gguf"), restart_server=False)
        self.assertFalse(res["success"])

    def test_build_llama_command_cpu_backend(self):
        cfg = {
            "llama": {
                "backend": "cpu",
                "model_path": "models/test.gguf",
                "context_size": 8192,
                "gpu_layers": 99,
                "threads": 6,
                "host": "127.0.0.1",
                "port": 8080
            }
        }
        _, _, cmd = build_llama_command(cfg)
        ngl_idx = cmd.index("-ngl")
        self.assertEqual(cmd[ngl_idx + 1], "0")
        c_idx = cmd.index("-c")
        self.assertEqual(cmd[c_idx + 1], "8192")


if __name__ == "__main__":
    unittest.main()
