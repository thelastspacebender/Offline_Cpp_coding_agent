"""
Backend Entry Point for VS2005 Qwen Code Assistant & RAG.
"""

import sys
import os

# Ensure the project root directory is always on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.api.routes import router
from backend.config import config

app = FastAPI(
    title="Offline C++ Coding Agent API",
    version="1.0.0",
    description="Local offline Map-Reduce & Call-Graph AI coding agent for Visual Studio, VS Code, and any C/C++ project"
)

# Enable CORS for local Visual Studio Add-in and Web UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

# Mount Web UI if available
if os.path.exists(config.web_ui_dir):
    app.mount("/static", StaticFiles(directory=config.web_ui_dir), name="static")

    @app.get("/", include_in_schema=False)
    def serve_web_ui():
        index_file = os.path.join(config.web_ui_dir, "index.html")
        if os.path.exists(index_file):
            return FileResponse(index_file)
        return {"status": "ok", "message": "VS2005 Qwen Backend is running"}


if __name__ == "__main__":
    print(f"[BACKEND] Starting API server on http://{config.host}:{config.port}...")
    uvicorn.run(
        "backend.main:app",
        host=config.host,
        port=config.port,
        reload=True
    )
