"""
Model Manager Service.
Handles discovering local/custom .gguf files, evaluating hardware compatibility,
hot-switching active models via llama-server restart, opening a native .gguf file
picker, and downloading models from model_catalog.json with progress tracking.
"""

import os
import sys
import json
import time
import glob
import threading
import subprocess
from typing import Dict, Any, List, Optional

from ..core.hardware import get_hardware_profile, evaluate_model_compatibility
from ..llama_launcher import build_llama_command, resolve_model_path
from .qwen_service import QwenService, safe_print


class ModelManager:
    def __init__(self, root_dir: str, qwen_service: QwenService):
        self.root_dir = os.path.abspath(root_dir)
        self.config_path = os.path.join(self.root_dir, "config.json")
        self.catalog_path = os.path.join(self.root_dir, "model_catalog.json")
        self.models_dir = os.path.join(self.root_dir, "models")
        self.qwen = qwen_service
        self._lock = threading.Lock()
        self._download_state: Dict[str, Any] = {
            "active": False,
            "model_id": "",
            "filename": "",
            "downloaded_mb": 0.0,
            "total_mb": 0.0,
            "percent": 0.0,
            "status": "idle",
            "error": None
        }

    def _read_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "server": {"host": "127.0.0.1", "port": 18080},
            "llama": {
                "host": "127.0.0.1",
                "port": 8080,
                "model_path": "models/qwen2.5-coder-7b-instruct-q4_k_m.gguf",
                "context_size": 16384,
                "gpu_layers": 99,
                "threads": 8,
                "backend": "vulkan",
                "custom_models": []
            }
        }

    def _write_config(self, cfg: Dict[str, Any]):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)

    def _read_catalog(self) -> Dict[str, Any]:
        if os.path.exists(self.catalog_path):
            with open(self.catalog_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"models": []}

    def list_models(self) -> Dict[str, Any]:
        cfg = self._read_config()
        llama_cfg = cfg.get("llama", {})
        active_path = resolve_model_path(cfg)
        hw = get_hardware_profile(self.root_dir)
        catalog = self._read_catalog().get("models", [])

        local_files: List[Dict[str, Any]] = []
        seen_paths = set()

        os.makedirs(self.models_dir, exist_ok=True)
        candidates = sorted(glob.glob(os.path.join(self.models_dir, "*.gguf")))
        for custom_p in llama_cfg.get("custom_models", []):
            if custom_p:
                candidates.append(custom_p)
        if active_path and os.path.isfile(active_path):
            candidates.append(active_path)

        for p in candidates:
            ap = os.path.abspath(p)
            ap_key = ap.lower()
            if ap_key in seen_paths or not os.path.isfile(ap):
                continue
            seen_paths.add(ap_key)
            size_gb = round(os.path.getsize(ap) / (1024 ** 3), 2)
            local_files.append({
                "name": os.path.basename(ap),
                "path": ap,
                "size_gb": size_gb,
                "active": ap_key == active_path.lower()
            })

        catalog_items = []
        for item in catalog:
            expected_path = os.path.abspath(os.path.join(self.models_dir, item["filename"]))
            installed = os.path.isfile(expected_path) and os.path.getsize(expected_path) > 100 * 1024 * 1024
            compat = evaluate_model_compatibility(item, hw["ram_gb"], hw["best_vram_gb"], hw["has_igpu"])
            entry = dict(item)
            entry["installed"] = installed
            entry["local_path"] = expected_path if installed else ""
            entry["compatibility"] = compat
            catalog_items.append(entry)

        return {
            "active_model_path": active_path if os.path.isfile(active_path) else "",
            "active_model_name": os.path.basename(active_path) if active_path else "",
            "context_size": int(llama_cfg.get("context_size", 16384)),
            "backend": llama_cfg.get("backend", "vulkan"),
            "local_models": local_files,
            "catalog": catalog_items
        }

    def select_model(
        self,
        model_path: str,
        context_size: Optional[int] = None,
        restart_server: bool = True
    ) -> Dict[str, Any]:
        raw = (model_path or "").strip().strip('"')
        if not raw:
            return {"success": False, "message": "Model path is empty."}

        resolved = os.path.abspath(raw if os.path.isabs(raw) else os.path.join(self.root_dir, raw))
        if not os.path.isfile(resolved):
            return {"success": False, "message": f"GGUF file not found: {resolved}"}

        with self._lock:
            cfg = self._read_config()
            llama_cfg = cfg.setdefault("llama", {})

            # Keep relative path when inside models/ for portability, absolute otherwise
            rel_candidate = os.path.relpath(resolved, self.root_dir)
            if not rel_candidate.startswith("..") and resolved.lower().startswith(self.models_dir.lower()):
                llama_cfg["model_path"] = rel_candidate.replace("\\", "/")
            else:
                llama_cfg["model_path"] = resolved
                customs = list(llama_cfg.get("custom_models", []))
                if resolved not in customs:
                    customs.append(resolved)
                llama_cfg["custom_models"] = customs

            if context_size is not None and int(context_size) >= 2048:
                llama_cfg["context_size"] = int(context_size)

            self._write_config(cfg)

        if restart_server:
            restarted, msg = self._restart_llama_server(cfg)
            self.qwen.get_context_size(force_refresh=True)
            return {
                "success": restarted,
                "message": msg,
                "active_model_path": resolved,
                "context_size": int(cfg["llama"].get("context_size", 16384))
            }

        return {
            "success": True,
            "message": f"Configured model: {os.path.basename(resolved)}",
            "active_model_path": resolved,
            "context_size": int(cfg["llama"].get("context_size", 16384))
        }

    def _restart_llama_server(self, cfg: Dict[str, Any]) -> tuple:
        llama_exe, model_path, cmd = build_llama_command(cfg)
        if not os.path.isfile(llama_exe):
            return False, f"llama-server.exe not found at {llama_exe}"

        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/IM", "llama-server.exe"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            time.sleep(1.0)

            creation_flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
            subprocess.Popen(cmd, cwd=self.root_dir, creationflags=creation_flags)

            # Wait up to 45s for llama-server /health
            deadline = time.time() + 45.0
            while time.time() < deadline:
                if self.qwen.is_server_reachable():
                    safe_print(f"✅ [ModelManager] Switched LLM to {os.path.basename(model_path)}", flush=True)
                    return True, f"Model loaded: {os.path.basename(model_path)}"
                time.sleep(1.0)

            return False, "llama-server started but did not respond on /health within 45s."
        except Exception as e:
            return False, f"Failed to restart llama-server: {e}"

    def browse_gguf_file(self) -> Dict[str, Any]:
        """Opens native Windows file picker for .gguf selection from local UI."""
        if os.name != "nt":
            return {"selected": False, "path": "", "message": "Native picker only supported on Windows."}
        ps_cmd = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$d = New-Object System.Windows.Forms.OpenFileDialog; "
            "$d.Title = 'Select Local GGUF Model'; "
            "$d.Filter = 'GGUF Models (*.gguf)|*.gguf|All Files (*.*)|*.*'; "
            "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $d.FileName }"
        )
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-STA", "-Command", ps_cmd],
                timeout=120,
                text=True
            ).strip()
            if out and os.path.isfile(out):
                return {"selected": True, "path": out, "message": "OK"}
            return {"selected": False, "path": "", "message": "Cancelled"}
        except Exception as e:
            return {"selected": False, "path": "", "message": str(e)}

    def start_download(self, model_id: str) -> Dict[str, Any]:
        with self._lock:
            if self._download_state["active"]:
                return {"success": False, "message": f"Already downloading {self._download_state['filename']}"}

            catalog = self._read_catalog().get("models", [])
            model_meta = next((m for m in catalog if m["id"] == model_id), None)
            if not model_meta:
                return {"success": False, "message": f"Unknown model id: {model_id}"}

            os.makedirs(self.models_dir, exist_ok=True)
            target_path = os.path.join(self.models_dir, model_meta["filename"])
            expected_mb = round(float(model_meta.get("size_gb", 4.0)) * 1024.0, 1)

            self._download_state = {
                "active": True,
                "model_id": model_id,
                "filename": model_meta["filename"],
                "downloaded_mb": 0.0,
                "total_mb": expected_mb,
                "percent": 0.0,
                "status": "downloading",
                "error": None
            }

        t = threading.Thread(
            target=self._download_worker,
            args=(model_meta, target_path, expected_mb),
            daemon=True
        )
        t.start()
        return {"success": True, "message": f"Started downloading {model_meta['name']}"}

    def _download_worker(self, model_meta: Dict[str, Any], target_path: str, expected_mb: float):
        urls = [model_meta["url"]]
        if model_meta.get("fallback_url"):
            urls.append(model_meta["fallback_url"])

        success = False
        last_err = ""
        for url in urls:
            proc = subprocess.Popen(
                ["curl.exe", "-L", "-C", "-", "--retry", "5", url, "-o", target_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE
            )
            while proc.poll() is None:
                if os.path.exists(target_path):
                    dl_mb = os.path.getsize(target_path) / (1024 * 1024)
                    pct = min(99.5, round((dl_mb / max(1.0, expected_mb)) * 100.0, 1))
                    with self._lock:
                        self._download_state["downloaded_mb"] = round(dl_mb, 1)
                        self._download_state["percent"] = pct
                time.sleep(0.8)

            if proc.returncode == 0 and os.path.isfile(target_path) and os.path.getsize(target_path) > 100 * 1024 * 1024:
                success = True
                break
            else:
                last_err = f"curl exited with {proc.returncode}"

        with self._lock:
            self._download_state["active"] = False
            if success:
                dl_mb = round(os.path.getsize(target_path) / (1024 * 1024), 1)
                self._download_state["downloaded_mb"] = dl_mb
                self._download_state["total_mb"] = dl_mb
                self._download_state["percent"] = 100.0
                self._download_state["status"] = "completed"
                self._download_state["error"] = None
            else:
                self._download_state["status"] = "failed"
                self._download_state["error"] = last_err or "Download failed"

    def get_download_status(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._download_state)
