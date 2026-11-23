"""
FastAPI REST Routes.
"""

import os
from fastapi import APIRouter
from .schemas import (
    IndexRequest, IndexResponse,
    QueryRequest, QueryResponse, CitationSchema,
    HealthResponse, ModelSelectRequest, ModelDownloadRequest,
    MapBuildRequest
)
from ..storage.sqlite_db import DatabaseManager
from ..storage.graph_store import GraphStore
from ..services.indexing_service import IndexingService
from ..services.qwen_service import QwenService
from ..services.search_service import SearchService
from ..services.model_manager import ModelManager
from ..core.hardware import get_hardware_profile
from ..config import config

router = APIRouter(prefix="/api")

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
db_manager = DatabaseManager(config.db_path)
graph_store = GraphStore(db_manager)
indexing_service = IndexingService(db_manager)
qwen_service = QwenService(config.llama_endpoint)
search_service = SearchService(db_manager, qwen_service)
model_manager = ModelManager(ROOT_DIR, qwen_service)


@router.get("/health", response_model=HealthResponse)
def get_health():
    stats = graph_store.get_stats()
    qwen_ok = qwen_service.is_server_reachable()
    return HealthResponse(
        status="ok",
        qwen_online=qwen_ok,
        files_indexed=stats["files_count"],
        symbols_indexed=stats["symbols_count"],
        calls_indexed=stats["calls_count"]
    )


@router.post("/index", response_model=IndexResponse)
def index_solution(req: IndexRequest):
    res = indexing_service.index_directory(req.path)
    return IndexResponse(
        success=res["success"],
        message=res["message"],
        files_count=res["files_count"],
        symbols_count=res["symbols_count"],
        calls_count=res["calls_count"],
        duration_seconds=res["duration_seconds"]
    )


@router.post("/query", response_model=QueryResponse)
def execute_query(req: QueryRequest):
    import time
    t0 = time.time()
    now_str = time.strftime("%H:%M:%S")
    print(f"\n{'='*75}", flush=True)
    print(f"[{now_str}] 📩 [API /api/query] ПОСТУПИЛ ЗАПРОС ИЗ VISUAL STUDIO 2005", flush=True)
    budget_info = f" | Бюджет: {req.time_budget_sec}с" if req.time_budget_sec else ""
    print(f"[{now_str}]    Режим: {req.mode.upper()} | Язык: {req.language or 'ru'}{budget_info}", flush=True)
    print(f"[{now_str}]    Вопрос: \"{req.question}\"", flush=True)
    if req.current_file:
        import os
        print(f"[{now_str}]    Контекст редактора: {os.path.basename(req.current_file)}:{req.current_line}", flush=True)
    print(f"{'-'*75}", flush=True)

    res = search_service.query(
        question=req.question,
        mode=req.mode,
        current_file=req.current_file,
        current_line=req.current_line,
        selected_code=req.selected_code,
        language=req.language or "ru",
        time_budget_sec=req.time_budget_sec
    )

    elapsed = time.time() - t0
    now_end = time.strftime("%H:%M:%S")
    print(f"[{now_end}] 🚀 [API /api/query] ЗАПРОС ОБРАБОТАН ЗА {elapsed:.1f} сек! Цитат: {len(res.citations)}. Отправка в Add-in.", flush=True)
    print(f"{'='*75}\n", flush=True)

    citations = [
        CitationSchema(
            file=c.file,
            line=c.line,
            symbol=c.symbol,
            snippet=c.snippet
        )
        for c in res.citations
    ]

    return QueryResponse(
        success=res.success,
        answer=res.answer,
        citations=citations,
        error=res.error
    )


@router.get("/stats")
def get_stats():
    return graph_store.get_stats()


@router.post("/history/clear")
def clear_history():
    qwen_service.clear_history()
    search_service.clear_history()
    return {"success": True, "message": "History cleared"}


@router.get("/system/hardware")
def get_system_hardware():
    return get_hardware_profile(ROOT_DIR)


@router.get("/models")
def list_models():
    return model_manager.list_models()


@router.post("/models/select")
def select_model(req: ModelSelectRequest):
    return model_manager.select_model(
        model_path=req.model_path,
        context_size=req.context_size,
        restart_server=req.restart_server
    )


@router.post("/models/browse")
def browse_model_file():
    return model_manager.browse_gguf_file()


@router.post("/models/download")
def download_model(req: ModelDownloadRequest):
    return model_manager.start_download(req.model_id)


@router.get("/models/download/status")
def download_model_status():
    return model_manager.get_download_status()


@router.get("/map/status")
def get_map_status():
    return search_service.map_builder.get_status()


@router.post("/map/build")
def start_map_build(req: MapBuildRequest = MapBuildRequest()):
    return search_service.map_builder.start_build(force_rebuild=req.force_rebuild)


@router.post("/map/cancel")
def cancel_map_build():
    return search_service.map_builder.cancel_build()

