"""
Live Download URL Verifier.
Verifies HTTP reachability (200/206/302) for every URL in model_catalog.json,
Python embeddable installer, and ggml-org/llama.cpp GitHub Releases API.
"""

import os
import sys
import json
import urllib.request
import urllib.error

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CATALOG_PATH = os.path.join(REPO_ROOT, "model_catalog.json")


def check_url(label: str, url: str) -> bool:
    if not url:
        return True
    headers = {"User-Agent": "OfflineCppAgentLinkVerifier/1.0"}
    # Try HEAD first, then lightweight Range GET
    for method in ("HEAD", "GET"):
        try:
            req_headers = dict(headers)
            if method == "GET":
                req_headers["Range"] = "bytes=0-15"
            req = urllib.request.Request(url, headers=req_headers, method=method)
            with urllib.request.urlopen(req, timeout=15) as resp:
                status = resp.status
                clen = resp.headers.get("Content-Length", "unknown")
                if status in (200, 206, 301, 302):
                    print(f"[OK]   {status} | {label} -> {url} (size={clen})")
                    return True
        except urllib.error.HTTPError as e:
            if method == "HEAD" and e.code in (403, 405):
                continue
            print(f"[FAIL] HTTP {e.code} | {label} -> {url}")
            return False
        except Exception as e:
            print(f"[FAIL] {type(e).__name__}: {e} | {label} -> {url}")
            return False
    return False


def main() -> int:
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        cat = json.load(f)

    all_ok = True
    all_ok &= check_url("Python 3.11.9 Embed", cat["python_embed_url"])
    all_ok &= check_url("get-pip.py", cat["get_pip_url"])

    # Check GitHub Releases API for llama.cpp and verify Vulkan/CUDA/CPU assets exist
    api_url = cat["llama_cpp"]["releases_api"]
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "OfflineCppAgentLinkVerifier/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            releases = json.loads(resp.read().decode("utf-8"))
            if isinstance(releases, dict):
                releases = [releases]
            matched = None
            for rel in releases:
                assets = [a.get("name", "") for a in rel.get("assets", [])]
                if any("win-vulkan-x64.zip" in a for a in assets):
                    matched = (rel.get("tag_name", "?"), assets)
                    break
            if matched:
                tag, assets = matched
                has_vulkan = any("win-vulkan-x64.zip" in a for a in assets)
                has_cpu = any("win-cpu-x64.zip" in a for a in assets)
                has_cuda = any("win-cuda-12" in a and "x64.zip" in a for a in assets)
                print(f"[OK]   llama.cpp release={tag} | vulkan={has_vulkan} cuda={has_cuda} cpu={has_cpu}")
                if not (has_vulkan and has_cpu and has_cuda):
                    all_ok = False
            else:
                print("[FAIL] No Windows binary release found in llama.cpp releases API")
                all_ok = False
    except Exception as e:
        print(f"[FAIL] llama.cpp Releases API: {e}")
        all_ok = False

    for k, fb_url in cat["llama_cpp"]["fallback_urls"].items():
        all_ok &= check_url(f"llama.cpp fallback ({k})", fb_url)

    for m in cat["models"]:
        ok_main = check_url(f"{m['id']} (primary)", m["url"])
        ok_fb = check_url(f"{m['id']} (fallback)", m["fallback_url"]) if m.get("fallback_url") else False
        if not ok_main and not ok_fb:
            all_ok = False
        elif not ok_main:
            print(f"[WARN] Primary failed for {m['id']}, fallback OK.")

    print("\nRESULT:", "ALL LINKS VERIFIED OK" if all_ok else "SOME LINKS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
