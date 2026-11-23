"""
Full Agent Execution Logger & Debug Trace.
Logs every raw prompt, every model response, and every agent decision
both to the live console and to logs/agent_trace.log.
"""

import os
import sys
import time

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "logs")
LOG_FILE = os.path.join(LOG_DIR, "agent_trace.log")


def _ensure_log_dir():
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except Exception:
        pass


def log_agent_boundary(title: str):
    _ensure_log_dir()
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    border = "=" * 80
    msg = f"\n{border}\n[{now_str}] 🚀 {title}\n{border}\n"
    
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(msg)
    except Exception:
        pass

    try:
        print(msg, flush=True)
    except Exception:
        try:
            print(msg.encode('ascii', errors='replace').decode('ascii'), flush=True)
        except Exception:
            pass


def log_agent_line(msg: str):
    """Logs a single progress line to both logs/agent_trace.log and live console."""
    _ensure_log_dir()
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass

    try:
        print(msg, flush=True)
    except Exception:
        try:
            print(msg.encode('ascii', errors='replace').decode('ascii'), flush=True)
        except Exception:
            pass


def log_agent_file_only(section_name: str, content: str):
    """Writes verbose trace details (per-batch files / JSON) to logs/agent_trace.log only."""
    _ensure_log_dir()
    sub_border = "-" * 60
    msg = f"\n{sub_border}\n>>> {section_name}\n{sub_border}\n{content.strip()}\n"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(msg)
    except Exception:
        pass


def log_agent_section(section_name: str, content: str):
    _ensure_log_dir()
    sub_border = "-" * 80
    msg = f"\n{sub_border}\n>>> {section_name}\n{sub_border}\n{content.strip()}\n"

    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(msg)
    except Exception:
        pass

    try:
        print(msg, flush=True)
    except Exception:
        try:
            print(msg.encode('ascii', errors='replace').decode('ascii'), flush=True)
        except Exception:
            pass
