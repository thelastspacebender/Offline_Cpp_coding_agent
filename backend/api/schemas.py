"""
API Request and Response Pydantic Schemas.
"""

from typing import List, Optional
from pydantic import BaseModel


class IndexRequest(BaseModel):
    path: str


class IndexResponse(BaseModel):
    success: bool
    message: str
    files_count: int
    symbols_count: int
    calls_count: int
    duration_seconds: float


class CitationSchema(BaseModel):
    file: str
    line: int
    symbol: Optional[str] = ""
    snippet: Optional[str] = ""


class QueryRequest(BaseModel):
    question: str
    mode: Optional[str] = "ask"
    current_file: Optional[str] = ""
    current_line: Optional[int] = 1
    selected_code: Optional[str] = ""
    language: Optional[str] = "ru"
    time_budget_sec: Optional[int] = None


class QueryResponse(BaseModel):
    success: bool
    answer: str
    citations: List[CitationSchema] = []
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    qwen_online: bool
    files_indexed: int
    symbols_indexed: int
    calls_indexed: int


class ModelSelectRequest(BaseModel):
    model_path: str
    context_size: Optional[int] = None
    restart_server: bool = True


class ModelDownloadRequest(BaseModel):
    model_id: str


class MapBuildRequest(BaseModel):
    force_rebuild: bool = False
