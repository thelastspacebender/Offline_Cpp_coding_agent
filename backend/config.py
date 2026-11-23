"""
Application Configuration.
"""

import os
from dataclasses import dataclass


@dataclass
class AppConfig:
    host: str = "127.0.0.1"
    port: int = 18080
    llama_endpoint: str = "http://127.0.0.1:8080/v1/chat/completions"
    db_path: str = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "storage",
        "rag_index.db"
    )
    web_ui_dir: str = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "web_ui"
    )


config = AppConfig()
