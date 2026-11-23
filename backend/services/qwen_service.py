"""
Local LLM Client for llama-server (OpenAI-compatible API).
Works with any GGUF model on Vulkan, CUDA, or CPU via llama.cpp.
Supports Agentic Search Planning, Map-Reduce, and Stateful Chat History.
"""

import re
import json
from typing import List, Dict, Any, Optional
import urllib.request
import urllib.error
from ..core.agent_logger import log_agent_section


def safe_print(*args, **kwargs):
    try:
        print(*args, **kwargs)
    except Exception:
        try:
            cleaned = [str(a).encode('ascii', errors='replace').decode('ascii') for a in args]
            print(*cleaned, **kwargs)
        except Exception:
            pass


class QwenService:
    def __init__(self, endpoint_url: str = "http://127.0.0.1:8080/v1/chat/completions"):
        self.endpoint_url = endpoint_url
        self.history: List[Dict[str, str]] = []
        self._cached_n_ctx: Optional[int] = None

    def get_context_size(self, force_refresh: bool = False) -> int:
        """Detects active llama-server context window (n_ctx) via GET /props."""
        if self._cached_n_ctx is not None and not force_refresh:
            return self._cached_n_ctx
        try:
            props_url = self.endpoint_url.replace("/v1/chat/completions", "/props")
            req = urllib.request.Request(props_url, headers={"User-Agent": "VS2005QwenClient"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                n_ctx = None
                if isinstance(data, dict):
                    dgs = data.get("default_generation_settings")
                    if isinstance(dgs, dict) and isinstance(dgs.get("n_ctx"), int):
                        n_ctx = dgs["n_ctx"]
                    elif isinstance(data.get("n_ctx"), int):
                        n_ctx = data["n_ctx"]
                if n_ctx and n_ctx >= 1024:
                    self._cached_n_ctx = n_ctx
                    return n_ctx
        except Exception:
            pass
        return 8192

    def is_server_reachable(self) -> bool:
        """Checks if llama-server is currently online."""
        try:
            health_url = self.endpoint_url.replace("/v1/chat/completions", "/health")
            req = urllib.request.Request(health_url, headers={"User-Agent": "VS2005QwenClient"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def clear_history(self):
        """Clears dialog history for a fresh chat session."""
        self.history.clear()

    def get_history(self) -> List[Dict[str, str]]:
        return list(self.history)

    def generate_raw(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 256,
        timeout: float = 45.0,
        stop: Optional[List[str]] = None
    ) -> str:
        """Isolated single completion call without touching conversational history."""
        payload: Dict[str, Any] = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "repeat_penalty": 1.08,
            "stream": False
        }
        if stop:
            payload["stop"] = stop
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "VS2005QwenClient"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                choices = resp_data.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "").strip()
        except Exception as e:
            safe_print(f"⚠️ [QwenService.generate_raw] Ошибка вызова LLM: {e}", flush=True)
        return ""

    def plan_search_query(self, project_map: str, user_question: str) -> Dict[str, Any]:
        """
        Agentic query router: fast 0-temp planning step.
        Translates user question (e.g. in Russian or with typos) to canonical project terms & files.
        """
        import time
        now_str = time.strftime("%H:%M:%S")
        safe_print(f"[{now_str}] 🧭 [Шаг 1: Agentic Router] Анализ структуры проекта через Qwen (0-temp)...", flush=True)

        system_prompt = (
            "You are a C/C++ query planning router. "
            "Based on the project architecture map, analyze the user's question and output a JSON search plan. "
            "1. Correct technical typos or transliterations (e.g. сокет -> Socket). "
            "2. Identify candidate target files, classes, or symbols from the project map that are most relevant to the question. "
            "3. If the user asks a broad/high-level architectural question, set is_overview_question to true. "
            "Respond ONLY with a valid JSON object matching this schema:\n"
            "{\"corrected_terms\": [\"term1\"], \"target_files\": [\"file1.h\"], \"target_symbols\": [\"ClassName\"], \"is_overview_question\": false}"
        )
        user_prompt = f"PROJECT ARCHITECTURE MAP:\n{project_map}\n\nUSER QUESTION:\n{user_question}\n\nJSON:"

        log_agent_section(
            "ШАГ 1: ПРОМПТ АГЕНТНОГО РОУТЕРА (ВХОД В QWEN)",
            f"[SYSTEM PROMPT]\n{system_prompt}\n\n[USER PROMPT]\n{user_prompt}"
        )

        t_plan0 = time.time()
        try:
            raw = self.generate_raw(system_prompt, user_prompt, temperature=0.0, max_tokens=150, timeout=15.0)
            log_agent_section("ШАГ 1: СЫРОЙ ОТВЕТ QWEN (СГЕНЕРИРОВАННЫЙ ПЛАН)", raw or "<пустой ответ>")
            cleaned = re.sub(r'```(?:json)?\s*|\s*```', '', raw).strip()
            start = cleaned.find('{')
            end = cleaned.rfind('}')
            if start != -1 and end != -1:
                cleaned = cleaned[start:end + 1]
                data = json.loads(cleaned)
                plan = {
                    "corrected_terms": list(data.get("corrected_terms", [])),
                    "target_files": list(data.get("target_files", [])),
                    "target_symbols": list(data.get("target_symbols", [])),
                    "is_overview_question": bool(data.get("is_overview_question", False))
                }
                dt_plan = time.time() - t_plan0
                now_plan = time.strftime("%H:%M:%S")
                safe_print(f"[{now_plan}] 🧭 [Шаг 1: Agentic Router] План готов за {dt_plan:.1f} сек! JSON:\n"
                           f"       {json.dumps(plan, ensure_ascii=False)}", flush=True)
                return plan
        except Exception as e:
            now_err = time.strftime("%H:%M:%S")
            safe_print(f"[{now_err}] ⚠️ [Шаг 1: Agentic Router] Ошибка парсинга плана ({e}), используем авто-эвристику.", flush=True)

        now_fallback = time.strftime("%H:%M:%S")
        safe_print(f"[{now_fallback}] 🧭 [Шаг 1: Agentic Router] План сформирован по локальным правилам проекта.", flush=True)
        return {
            "corrected_terms": [],
            "target_files": [],
            "target_symbols": [],
            "is_overview_question": False
        }

    def generate_response(
        self,
        system_prompt: str,
        user_prompt: str,
        raw_user_question: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 3072,
        use_history: bool = True
    ) -> str:
        """
        Sends query with system prompt, optional history buffer, and enriched user prompt
        to local Qwen 7B model running on llama-server.
        """
        import time
        now_str = time.strftime("%H:%M:%S")
        approx_tokens = len(user_prompt) // 4
        safe_print(f"[{now_str}] 🤖 [Шаг 4: Генерация ответа] Отправка контекста в локальную LLM (символов: {len(user_prompt)}, токенов: ~{approx_tokens})...", flush=True)
        safe_print(f"[{now_str}] ⏳ [Local LLM] Модель генерирует ответ...", flush=True)

        t_gen0 = time.time()
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_prompt}
        ]

        # Append recent conversational context (up to last 6 messages) when enabled
        history_slice = self.history[-6:] if use_history else []
        for msg in history_slice:
            messages.append(msg)

        # Append current enriched user prompt
        messages.append({"role": "user", "content": user_prompt})

        # Calculate approximate total tokens and clamp max_tokens to fit active n_ctx
        n_ctx = self.get_context_size()
        approx_prompt_tokens = int((len(system_prompt) + len(user_prompt)) / 2.5)
        for m in history_slice:
            approx_prompt_tokens += int(len(m.get("content", "")) / 2.5)

        safe_max_tokens = min(max_tokens, max(512, n_ctx - 350 - approx_prompt_tokens))

        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": safe_max_tokens,
            "repeat_penalty": 1.15,
            "stream": False
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "VS2005QwenClient"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                choices = resp_data.get("choices", [])
                if choices:
                    answer = choices[0].get("message", {}).get("content", "").strip()

                    # Save concise turn in history (use raw question to avoid prompt explosion)
                    q_text = (raw_user_question or user_prompt).strip()
                    if len(q_text) > 400:
                        q_text = q_text[:400] + "..."

                    self.history.append({"role": "user", "content": q_text})
                    self.history.append({"role": "assistant", "content": answer})
                    if len(self.history) > 6:
                        self.history = self.history[-6:]

                    dt_gen = time.time() - t_gen0
                    now_done = time.strftime("%H:%M:%S")
                    safe_print(f"[{now_done}] ✅ [Шаг 4: Генерация ответа] Ответ от Qwen 7B успешно получен за {dt_gen:.1f} сек! ({len(answer)} симв.)", flush=True)
                    return answer
                return "Не получен ответ от модели."
        except urllib.error.URLError as e:
            now_err = time.strftime("%H:%M:%S")
            safe_print(f"[{now_err}] ❌ Ошибка соединения с llama-server: {e.reason}", flush=True)
            return (
                "⚠️ Локальная модель Qwen 7B не отвечает (сервер не запущен).\n"
                "Запустите 'start_assistant.bat', чтобы включить локальный сервер инференса.\n"
                f"Детали ошибки: {e.reason}"
            )
        except Exception as e:
            now_err = time.strftime("%H:%M:%S")
            safe_print(f"[{now_err}] ❌ Ошибка при вызове модели: {str(e)}", flush=True)
            return f"Ошибка при обращении к модели: {str(e)}"
