"""
Hardware Inspector & Model Compatibility Engine.
Detects CPU, RAM, Free Disk, GPU (NVIDIA/AMD/Intel + VRAM), and installed IDEs
(VS Code, Visual Studio 2005-2022) without requiring third-party Python packages.
"""

import os
import sys
import json
import shutil
import platform
import subprocess
from typing import Dict, Any, List


def _get_ram_gb() -> float:
    """Returns total physical RAM in GB using Win32 API (zero dependencies)."""
    try:
        if os.name == "nt":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return round(stat.ullTotalPhys / (1024 ** 3), 1)
    except Exception:
        pass
    return 16.0


def _get_free_disk_gb(path: str = ".") -> float:
    try:
        usage = shutil.disk_usage(os.path.abspath(path))
        return round(usage.free / (1024 ** 3), 1)
    except Exception:
        return 0.0


def _detect_gpus() -> List[Dict[str, Any]]:
    """Detects GPUs on Windows via nvidia-smi (if present) + PowerShell/Registry fallback."""
    gpus: List[Dict[str, Any]] = []

    # 1. Try nvidia-smi for exact NVIDIA VRAM
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL,
            timeout=3,
            text=True
        )
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                vram_gb = round(float(parts[1]) / 1024.0, 1)
                gpus.append({"name": parts[0], "vendor": "NVIDIA", "vram_gb": vram_gb})
    except Exception:
        pass

    # 2. Windows Registry display adapters (works even when WMI/CIM is restricted)
    if os.name == "nt" and not gpus:
        try:
            import winreg
            base_key = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base_key) as root:
                for i in range(16):
                    try:
                        sub_name = winreg.EnumKey(root, i)
                        if not sub_name.isdigit():
                            continue
                        with winreg.OpenKey(root, sub_name) as sub:
                            desc = ""
                            vram_bytes = 0
                            try:
                                desc = str(winreg.QueryValueEx(sub, "DriverDesc")[0])
                            except Exception:
                                continue
                            for reg_val in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                                try:
                                    val = winreg.QueryValueEx(sub, reg_val)[0]
                                    if isinstance(val, int) and val > 0:
                                        vram_bytes = val
                                        break
                                    elif isinstance(val, bytes) and len(val) >= 4:
                                        vram_bytes = int.from_bytes(val[:8], "little")
                                        break
                                except Exception:
                                    pass
                            d_low = desc.lower()
                            if "microsoft basic" in d_low or "remote" in d_low or "virtual" in d_low:
                                continue
                            vendor = "NVIDIA" if "nvidia" in d_low else ("AMD" if ("amd" in d_low or "radeon" in d_low) else ("Intel" if "intel" in d_low else "Other"))
                            vram_gb = round(vram_bytes / (1024 ** 3), 1) if vram_bytes > 0 else 0.0
                            gpus.append({"name": desc, "vendor": vendor, "vram_gb": vram_gb})
                    except OSError:
                        break
        except Exception:
            pass

    return gpus


def _detect_ides() -> Dict[str, Any]:
    """Detects installed VS Code and Visual Studio versions."""
    vscode_installed = bool(shutil.which("code")) or os.path.exists(
        os.path.join(os.path.expanduser("~"), ".vscode")
    )
    vs_classic = []
    vs_modern = []
    if os.name == "nt":
        prog86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        prog64 = os.environ.get("ProgramFiles", r"C:\Program Files")
        classic_map = [
            ("8.0", "Visual Studio 2005", os.path.join(prog86, "Microsoft Visual Studio 8", "Common7", "IDE", "devenv.exe")),
            ("9.0", "Visual Studio 2008", os.path.join(prog86, "Microsoft Visual Studio 9.0", "Common7", "IDE", "devenv.exe")),
            ("10.0", "Visual Studio 2010", os.path.join(prog86, "Microsoft Visual Studio 10.0", "Common7", "IDE", "devenv.exe")),
            ("11.0", "Visual Studio 2012", os.path.join(prog86, "Microsoft Visual Studio 11.0", "Common7", "IDE", "devenv.exe")),
            ("12.0", "Visual Studio 2013", os.path.join(prog86, "Microsoft Visual Studio 12.0", "Common7", "IDE", "devenv.exe")),
        ]
        for ver, label, exe in classic_map:
            if os.path.exists(exe):
                vs_classic.append({"version": ver, "name": label})

        for year in ("2017", "2019", "2022"):
            for base in (prog64, prog86):
                p = os.path.join(base, "Microsoft Visual Studio", year)
                if os.path.exists(p):
                    vs_modern.append(f"Visual Studio {year}")
                    break

    return {
        "vscode": vscode_installed,
        "vs_classic": vs_classic,
        "vs_modern": vs_modern,
    }


def evaluate_model_compatibility(model: Dict[str, Any], ram_gb: float, best_vram_gb: float, has_igpu: bool = False) -> Dict[str, Any]:
    """
    Rates a catalog model against detected PC specs.
    Returns status: 'recommended' (green), 'tight' (yellow), or 'too_heavy' (red).
    """
    min_ram = float(model.get("min_ram_gb", 8))
    rec_vram = float(model.get("rec_vram_gb", 4))
    effective_mem = max(best_vram_gb, ram_gb * 0.55 if has_igpu else 0.0)

    if ram_gb >= min_ram and (effective_mem >= rec_vram or ram_gb >= min_ram + 8):
        return {"status": "recommended", "badge": "green", "note": "Optimal for your PC"}
    elif ram_gb >= max(6.0, min_ram * 0.75):
        return {"status": "tight", "badge": "yellow", "note": "Will run, may partially offload to RAM"}
    else:
        return {"status": "too_heavy", "badge": "red", "note": f"Requires ~{int(min_ram)} GB RAM"}


def get_hardware_profile(base_dir: str = ".") -> Dict[str, Any]:
    """Collects full PC specs and computes recommended llama.cpp backend and context size."""
    ram_gb = _get_ram_gb()
    free_disk_gb = _get_free_disk_gb(base_dir)
    cpu_cores = os.cpu_count() or 4
    cpu_name = platform.processor() or "x64 CPU"
    gpus = _detect_gpus()
    ides = _detect_ides()

    nvidia_gpus = [g for g in gpus if g["vendor"] == "NVIDIA"]
    amd_intel_gpus = [g for g in gpus if g["vendor"] in ("AMD", "Intel")]
    best_vram_gb = max((g.get("vram_gb", 0.0) for g in gpus), default=0.0)
    has_igpu = any(g["vendor"] in ("AMD", "Intel") for g in gpus)

    if nvidia_gpus and max(g.get("vram_gb", 0.0) for g in nvidia_gpus) >= 4.0:
        rec_backend = "cuda"
    elif gpus:
        rec_backend = "vulkan"
    else:
        rec_backend = "cpu"

    if ram_gb >= 32:
        rec_ctx = 32768
    elif ram_gb >= 16:
        rec_ctx = 16384
    else:
        rec_ctx = 8192

    return {
        "os": f"{platform.system()} {platform.release()}",
        "cpu_name": cpu_name,
        "cpu_cores": cpu_cores,
        "ram_gb": ram_gb,
        "free_disk_gb": free_disk_gb,
        "gpus": gpus,
        "best_vram_gb": best_vram_gb,
        "has_igpu": has_igpu,
        "recommended_backend": rec_backend,
        "recommended_ctx": rec_ctx,
        "recommended_threads": max(4, min(16, cpu_cores // 2 if cpu_cores >= 8 else cpu_cores)),
        "ides": ides,
    }


if __name__ == "__main__":
    print(json.dumps(get_hardware_profile(), indent=2, ensure_ascii=False))
