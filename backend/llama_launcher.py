"""
Dynamic llama-server Launcher.
Reads active model path, context size, GPU layers, and thread settings from config.json.
Allows hot-switching any local or custom .gguf model without editing batch files.
"""

import os
import sys
import json
import glob
import subprocess

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT_DIR, "config.json")


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def resolve_model_path(cfg: dict) -> str:
    llama_cfg = cfg.get("llama", {})
    raw_path = str(llama_cfg.get("model_path", "")).strip()
    if raw_path:
        candidate = raw_path if os.path.isabs(raw_path) else os.path.join(ROOT_DIR, raw_path)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)

    # Fallback: pick first .gguf in models/
    models_dir = os.path.join(ROOT_DIR, "models")
    ggufs = sorted(glob.glob(os.path.join(models_dir, "*.gguf")))
    if ggufs:
        return os.path.abspath(ggufs[0])

    return os.path.join(ROOT_DIR, raw_path) if raw_path else os.path.join(models_dir, "qwen2.5-coder-7b-instruct-q4_k_m.gguf")


def build_llama_command(cfg: dict) -> tuple:
    llama_cfg = cfg.get("llama", {})
    llama_exe = os.path.join(ROOT_DIR, "bin", "llama-server.exe")
    model_path = resolve_model_path(cfg)
    backend = str(llama_cfg.get("backend", "vulkan")).lower()
    ngl = 0 if backend == "cpu" else int(llama_cfg.get("gpu_layers", 99))
    ctx = int(llama_cfg.get("context_size", 16384))
    threads = int(llama_cfg.get("threads", 8))
    host = str(llama_cfg.get("host", "127.0.0.1"))
    port = int(llama_cfg.get("port", 8080))

    cmd = [
        llama_exe,
        "-m", model_path,
        "-ngl", str(ngl),
        "-c", str(ctx),
        "-t", str(threads),
        "--port", str(port),
        "--host", host,
    ]
    return llama_exe, model_path, cmd


def main() -> int:
    cfg = load_config()
    llama_exe, model_path, cmd = build_llama_command(cfg)

    if not os.path.isfile(llama_exe):
        print(f"[ERROR] llama-server.exe not found at: {llama_exe}")
        return 1
    if not os.path.isfile(model_path):
        print(f"[ERROR] Model file not found at: {model_path}")
        print("Select or download a .gguf model via install.bat or the Web UI Model Manager.")
        return 1

    print("=" * 70, flush=True)
    print(f"   Local LLM Server ({os.path.basename(model_path)})", flush=True)
    print(f"   Backend: {cfg.get('llama', {}).get('backend', 'vulkan').upper()} | Context: {cfg.get('llama', {}).get('context_size', 16384)}", flush=True)
    print("=" * 70, flush=True)
    proc = subprocess.run(cmd)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
