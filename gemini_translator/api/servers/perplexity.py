"""
Perplexity Server Integration for Gemini Translator
(Enhanced with Model Switch Detection & Quota Handling)
"""

from __future__ import annotations

import json
import logging
import os

import platform
import re
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple
# Импортируем базовый класс
from ..base import BaseServer

from curl_cffi import requests as cffi_requests
from flask import Flask, Response, jsonify, request
from werkzeug.serving import make_server

# Fix: Import the Uploader class
try:
    from .perplexity_utils.perplexity_uploader import PerplexityUploader
except ImportError:
    PerplexityUploader = None

# -------------------- Config --------------------


@dataclass(frozen=True)
class Config:
    host: str = "127.0.0.1"
    port: int = int(os.getenv("PPLX_PORT", "0"))
    concurrent_requests: int = int(os.getenv("PPLX_CONCURRENCY", "70"))
    acquire_timeout_s: int = int(os.getenv("PPLX_ACQUIRE_TIMEOUT", "2000"))
    ask_timeout_s: int = int(os.getenv("PPLX_ASK_TIMEOUT", "2000"))
    max_retries: int = int(os.getenv("PPLX_MAX_RETRIES", "10"))
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    auto_upload_threshold: int = 40000


def _get_app_data_dir() -> str:
    if platform.system() == "Windows":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "GeminiTranslator")
    return os.path.join(os.path.expanduser("~"), ".gemini_translator")


CFG = Config()
APP_DIR = _get_app_data_dir()
os.makedirs(APP_DIR, exist_ok=True)

SESSION_FILE = os.path.join(APP_DIR, ".perplexity_session")
ENDPOINT_FILE = os.path.join(APP_DIR, ".perplexity_server_endpoint.json")
REQUEST_LOG_FILE = os.path.join(APP_DIR, "perplexity_requests.log")

HOST = CFG.host
PORT = CFG.port
CONCURRENT_REQUESTS = CFG.concurrent_requests
REQUEST_TIMEOUT = CFG.acquire_timeout_s
USER_AGENT = CFG.user_agent

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("PerplexityServer")
audit_logger = logging.getLogger("PerplexityAudit")
audit_logger.setLevel(logging.INFO)
audit_logger.propagate = False
if not audit_logger.handlers:
    _fh = logging.FileHandler(REQUEST_LOG_FILE, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
    audit_logger.addHandler(_fh)

app = Flask(__name__)
_api_semaphore = threading.Semaphore(CFG.concurrent_requests)
_active_lock = threading.Lock()
_active_requests = 0



import sys
# 1. Хендлер для консоли (Вернули вывод!)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter(
    "\033[96m[PPLX-SRV]\033[0m %(asctime)s - %(message)s", datefmt="%H:%M:%S"
))

# 2. Включаем логи самого Flask (werkzeug), чтобы видеть запросы
log_werkzeug = logging.getLogger('werkzeug')
log_werkzeug.setLevel(logging.INFO)
# Если у werkzeug нет хендлеров, добавляем наш консольный
if not log_werkzeug.handlers:
    log_werkzeug.addHandler(console_handler)
    

def _inc_active() -> None:
    global _active_requests
    with _active_lock:
        _active_requests += 1


def _dec_active() -> None:
    global _active_requests
    with _active_lock:
        _active_requests = max(0, _active_requests - 1)


def _get_active() -> int:
    with _active_lock:
        return _active_requests


def save_endpoint_info(port: int) -> None:
    safe_host = "127.0.0.1"
    data = {
        "host": safe_host,
        "port": port,
        "base_url": f"http://{safe_host}:{port}",
        "v1_chat_url": f"http://{safe_host}:{port}/v1/chat/completions",
        "timestamp": time.time(),
    }
    try:
        with open(ENDPOINT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("Endpoint saved: %s:%s", safe_host, port)
    except Exception as e:
        logger.error("Failed to save endpoint: %s", e)

# -------------------- Backend helpers --------------------


def _read_token(path: str) -> Optional[str]:
    try:
        if os.path.exists(path):
            t = open(path, "r", encoding="utf-8").read().strip()
            return t or None
    except Exception:
        return None
    return None


def _auth_cookie(token: str) -> Dict[str, str]:
    return {"__Secure-next-auth.session-token": token}


def _base_headers(user_agent: str) -> Dict[str, str]:
    return {
        "Origin": "https://www.perplexity.ai",
        "Referer": "https://www.perplexity.ai/",
        "User-Agent": user_agent,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }


def _delay_seconds(error_msg: str, attempt: int) -> int:
    if "429" in error_msg:
        return 180 + (10 * attempt)
    return 5 * attempt


def _extract_answer_from_event(event: Dict[str, Any]) -> str:
    text_field = event.get("text")
    if text_field:
        try:
            nested = json.loads(text_field) if isinstance(
                text_field, str) else text_field
            if isinstance(nested, list):
                final_step = next((s for s in nested if isinstance(
                    s, dict) and s.get("step_type") == "FINAL"), None)
                if final_step:
                    answer_payload = (final_step.get(
                        "content") or {}).get("answer")
                    if answer_payload:
                        try:
                            answer_obj = json.loads(answer_payload) if isinstance(
                                answer_payload, str) else answer_payload
                            if isinstance(answer_obj, dict):
                                return answer_obj.get("answer") or ""
                            return str(answer_obj)
                        except Exception:
                            pass
        except Exception:
            pass
    ans: Any = ""
    if "answer" in event:
        ans = event.get("answer", "")
        try:
            if isinstance(ans, str) and ans.strip().startswith("{") and "answer" in ans:
                parsed = json.loads(ans)
                if isinstance(parsed, dict) and "answer" in parsed:
                    ans = parsed["answer"]
        except Exception:
            pass
    return ans if isinstance(ans, str) else (str(ans) if ans is not None else "")


def _extract_final_answer_and_model_from_sse(lines: Iterable[bytes]) -> Tuple[str, Optional[str]]:
    data_lines: List[str] = []
    last_payloads: List[str] = []
    found_answer = ""
    detected_model = None

    _HEX_U4_RE = re.compile(r"^(?:[0-9a-fA-F]{4})+$")

    def _decode_u4_hex(s: str) -> str:
        s = s.strip()
        if not s or not _HEX_U4_RE.match(s):
            return s
        chars = [chr(int(s[i: i + 4], 16)) for i in range(0, len(s), 4)]
        return "".join(chars)

    def _extract_answer_kv(payload: str) -> str:
        m = re.search(r"(?:^|,)\s*answer\s+(?:answer\s+)?([^,]+)", payload)
        if not m:
            return ""
        return _decode_u4_hex(m.group(1).strip())

    def _keep_payload(payload: str) -> None:
        nonlocal last_payloads
        last_payloads.append(payload)
        if len(last_payloads) > 10:
            last_payloads = last_payloads[-10:]

    def _try_parse_single_piece(piece: str) -> Optional[str]:
        nonlocal found_answer, detected_model
        if not piece:
            return None
        if piece == "[DONE]":
            return "[DONE]"
        _keep_payload(piece)
        try:
            obj = json.loads(piece)
            if isinstance(obj, dict):
                if obj.get("error_code") == "INVALID_MODEL_SELECTION":
                    detected_model = False
                    return "[DONE]" # Прерываем парсинг, ошибка найдена
                
                elif "model" in obj:
                    detected_model = obj["model"]
                elif "model_preference" in obj:
                    detected_model = obj["model_preference"]

                if not detected_model and "detail" in obj and isinstance(obj["detail"], dict):
                    if "model" in obj["detail"]:
                        detected_model = obj["detail"]["model"]

                ans = _extract_answer_from_event(obj)
                if ans:
                    found_answer = ans
                    return ans
        except Exception:
            pass
        ans_kv = _extract_answer_kv(piece)
        if ans_kv:
            found_answer = ans_kv
            return ans_kv
        return None

    def _flush_event() -> Optional[str]:
        nonlocal data_lines, found_answer, detected_model
        if not data_lines:
            return None
        payload = "\n".join(data_lines).strip()
        data_lines = []
        if not payload:
            return None
        if payload == "[DONE]":
            return "[DONE]"
        _keep_payload(payload)
        try:
            event = json.loads(payload)
            if isinstance(event, dict):
                if "model" in event:
                    detected_model = event["model"]
                ans = _extract_answer_from_event(event)
                if ans:
                    found_answer = ans
                    return ans
        except Exception:
            pass
        ans_kv = _extract_answer_kv(payload)
        if ans_kv:
            found_answer = ans_kv
            return ans_kv
        return None

    for raw in lines:
        if raw is None:
            continue
        
        try:
            if b"INVALID_MODEL_SELECTION" in raw:
                 line_str = raw.decode('utf-8', 'ignore')
                 if line_str.startswith("data:"):
                     json_part = line_str[5:].strip()
                     data = json.loads(json_part)
                     detected_model = False
                     break # Немедленно выходим из цикла
        except:
            pass # Игнорируем ошибки декодирования/парсинга здесь, основной парсер справится
        
        if raw == b"":
            out = _flush_event()
            if out == "[DONE]":
                break
            if isinstance(out, str) and out:
                continue
            continue
        try:
            line = raw.decode("utf-8", errors="ignore")
        except Exception:
            continue
        if line.startswith(":"):
            continue
        if (line.startswith("event:") or line.startswith("id:") or line.startswith("retry:")) and data_lines:
            out = _flush_event()
            if out == "[DONE]":
                break
            continue

        if line.startswith("data:"):
            piece = line[len("data:"):].lstrip()
            out = _try_parse_single_piece(piece)
            if out == "[DONE]":
                break
            data_lines.append(piece)
            continue

        if line.startswith("data "):
            piece = line[len("data "):].lstrip()
            out = _try_parse_single_piece(piece)
            if out == "[DONE]":
                break
            data_lines.append(piece)
            continue

    out = _flush_event()
    final_ans = found_answer if found_answer else (
        out if isinstance(out, str) and out != "[DONE]" else "")
    return final_ans, detected_model


class PerplexityBackend:
    def __init__(self) -> None:
        self.session = cffi_requests.Session(impersonate="chrome120")
        self.token: Optional[str] = _read_token(SESSION_FILE)
        self.is_authenticated: bool = False
        self.read_write_token: Optional[str] = None
        if self.token:
            self._validate_session(self.token)

    def validate_token(self, token: str) -> Dict[str, Any]:
        if not token or len(token) < 10:
            return {"valid": False, "message": "Токен слишком короткий"}
        try:
            resp = self.session.get(
                "https://www.perplexity.ai/api/auth/session",
                cookies=_auth_cookie(token),
                headers={"User-Agent": CFG.user_agent},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and "user" in data:
                    user = data.get("user", {})
                    rwt = data.get("read_write_token") or data.get(
                        "readWriteToken")
                    if rwt:
                        self.read_write_token = rwt
                    return {"valid": True, "message": "Авторизован", "email": user.get("email", "unknown"), "is_pro": user.get("is_pro", False)}
                return {"valid": False, "message": "Сессия истекла"}
            elif resp.status_code in (401, 403):
                return {"valid": False, "message": "Недействительный токен"}
            else:
                return {"valid": False, "message": f"Ошибка сервера: {resp.status_code}"}
        except Exception as e:
            return {"valid": False, "message": f"Ошибка сети: {str(e)}"}

    def _validate_session(self, token: str) -> None:
        status = self.validate_token(token)
        self.is_authenticated = status["valid"]
        if self.is_authenticated:
            logger.info("Сессия подтверждена: %s (Pro: %s)",
                        status.get("email"), status.get("is_pro"))
        else:
            logger.warning("Внимание: %s", status.get("message"))

    def _upload_large_text(self, text: str, active_token: str) -> Optional[str]:
        if not active_token:
            logger.error("Upload error: No token provided")
            return None

        if not PerplexityUploader:
            logger.error(
                "PerplexityUploader class not found. Cannot upload file.")
            return None

        temp_uploader = PerplexityUploader(self.session, active_token)
        s3_filename = f"CONTEXT_{int(time.time())}.txt"
        tf = tempfile.NamedTemporaryFile(
            mode="w+", encoding="utf-8", delete=False, suffix=".txt")
        temp_path = tf.name
        try:
            tf.write(text)
            tf.close()
            logger.info("Uploading large context (%s chars)...", len(text))
            result = temp_uploader.upload_file(temp_path, s3_filename)
            return result.get("url")
        except Exception as e:
            logger.error("File upload failed: %s", e)
            raise
        finally:
            if os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass

    def _ask_once(self, *, text: str, model: str, token: str, search_focus: str) -> Dict[str, Any]:
        attachments: List[str] = []
        final_query = text

        if len(text) > CFG.auto_upload_threshold:
            try:
                file_url = self._upload_large_text(text, token)
                if file_url:
                    attachments.append(file_url)
                    final_query = "[CRITICAL: ПРОЧТИ ПРИЛОЖЕННЫЙ ФАЙЛ!]\n1. ПОЛНОСТЬЮ ОСОЗНАЙ ПРИЛОЖЕННЫЙ ФАЙЛ.\n2. СЛЕДУЙ ИНСТРУКЦИИ В ФАЙЛЕ → создать JSON или XHTML по правилам из файла.\n3. Не отвечай о чтении файла, просто выполни запрос.\n"
            except Exception as e:
                return {"success": False, "error": f"Upload error: {e}", "retry_needed": True}

        params: Dict[str, Any] = {
            "version": "2.18",
            "model_preference": model,
            "mode": "copilot",
            "language": "ru-RU",
            "timezone": "Europe/Moscow",
            "frontend_uuid": str(uuid.uuid4()),
            "use_schematized_api": True,
            "send_back_text_in_streaming_api": False,
            "prompt_source": "user",
            "query_source": "default",
            "search_focus": search_focus,
            "sources": [],
            "attachments": attachments,
        }
        if self.read_write_token:
            params["read_write_token"] = self.read_write_token

        try:
            resp = self.session.post(
                "https://www.perplexity.ai/rest/sse/perplexity_ask",
                json={"params": params, "query_str": final_query},
                headers=_base_headers(CFG.user_agent),
                cookies=_auth_cookie(token),
                stream=True,
                timeout=CFG.ask_timeout_s,
            )
        except Exception as e:
            return {"success": False, "error": f"Request failed: {e}", "retry_needed": True}

        if resp.status_code != 200:
            return {"success": False, "error": f"API HTTP {resp.status_code}", "retry_needed": (resp.status_code == 429 or resp.status_code >= 500)}

        line_iter = resp.iter_lines() if hasattr(
            resp, "iter_lines") else resp.iterlines()

        answer, used_model = _extract_final_answer_and_model_from_sse(
            line_iter)

        if used_model and model != used_model:
            is_downgrade = False
            requested_lower = model.lower()
            used_lower = used_model.lower()

            if ("pro" in requested_lower or "r1" in requested_lower) and ("pro" not in used_lower and "r1" not in used_lower):
                is_downgrade = True

            if is_downgrade:
                error_msg = f"Вам включили лимиты на пользование сервисом и принудительно сменили модель на {used_model}"
                logger.warning(
                    f"MODEL SWITCH DETECTED: Requested {model}, got {used_model}. Triggering quota exhaustion.")
                return {
                    "success": False,
                    "error": error_msg,
                    "retry_needed": False
                }
        if used_model == False:
            logger.error(f"PPLX Model Error")
            return {
                "success": False, 
                "error": f"Модель не найдена или недоступна", 
                "retry_needed": False,
                "error_type": "model_not_found" # Специальный флаг для Flask
            }
        
        if not answer:
            return {"success": False, "error": "Empty response from API", "retry_needed": True}

        return {"success": True, "response": answer}

    def query_blocking_retry(self, *, text: str, model: str, temporary_token: Optional[str], search_focus: str = "writing") -> Dict[str, Any]:
        token = temporary_token or self.token
        if not token:
            return {"success": False, "error": "No auth token provided"}

        for attempt in range(1, CFG.max_retries + 1):
            result = self._ask_once(
                text=text, model=model, token=token, search_focus=search_focus)
            if result.get("success"):
                return result
            if not result.get("retry_needed", False):
                return result
            err = str(result.get("error", ""))
            delay = _delay_seconds(err, attempt)
            logger.warning("Retry in %ss (attempt %s/%s): %s",
                           delay, attempt, CFG.max_retries, err)
            time.sleep(delay)
        return {"success": False, "error": "Max retries exceeded."}


backend = PerplexityBackend()


def _openai_stream(chat_id: str, created: int, model: str, content: str) -> Iterable[str]:
    chunk_size = 200
    for i in range(0, len(content), chunk_size):
        part = content[i: i + chunk_size]
        chunk = {"id": chat_id, "object": "chat.completion.chunk", "created": created,
                 "model": model, "choices": [{"index": 0, "delta": {"content": part}}]}
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    final_chunk = {"id": chat_id, "object": "chat.completion.chunk", "created": created,
                   "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


def _extract_bearer() -> Optional[str]:
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.startswith("Bearer "):
        return auth.split(" ", 1)[1].strip() or None
    return None


def _get_last_user_text(messages: Any) -> str:
    if not isinstance(messages, list) or not messages:
        return ""
    last = messages[-1]
    return last.get("content", "") if isinstance(last, dict) else ""


@app.route("/status", methods=["GET"])
def status() -> Tuple[Any, int]:
    return jsonify({"status": "online", "authenticated": backend.is_authenticated, "concurrent_limit": CFG.concurrent_requests, "active_requests": _get_active()}), 200


@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions() -> Any:
    data = request.get_json(silent=True) or {}
    model = data.get("model") or "gemini30pro"
    stream = bool(data.get("stream", True))
    text = _get_last_user_text(data.get("messages"))
    if not text:
        return jsonify({"error": "messages required"}), 400

    client_token = _extract_bearer()
    audit_logger.info("Request: model=%s stream=%s len=%s",
                      model, stream, len(text))

    if not _api_semaphore.acquire(timeout=CFG.acquire_timeout_s):
        return jsonify({"error": "Server busy"}), 503

    _inc_active()
    try:
        result = backend.query_blocking_retry(
            text=text, model=model, temporary_token=client_token, search_focus="writing")
    finally:
        _dec_active()
        _api_semaphore.release()

    if not result.get("success"):
        code = 500
        err_msg = result.get("error", "")
        error_type = result.get("error_type", "") # <--- Получаем наш флаг
        if error_type == "model_not_found":
            code = 404 # Not Found
            error_payload = {"message": err_msg, "type": "invalid_request_error", "code": 404}

        elif "Вам включили лимиты" in err_msg:
            return jsonify({"error": {"message": err_msg, "type": "quota_exceeded", "code": 402}}), 402
        return jsonify({"error": err_msg}), code

    answer = result.get("response", "")
    chat_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    if stream:
        return Response(_openai_stream(chat_id, created, model, answer), mimetype="text/event-stream")

    return jsonify({"id": chat_id, "object": "chat.completion", "created": created, "model": model, "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}]}), 200


@app.route("/validate_batch", methods=["POST"])
def validate_batch() -> Any:
    data = request.get_json(silent=True) or {}
    tokens = data.get("tokens", [])
    if not isinstance(tokens, list):
        return jsonify({"error": "tokens must be a list"}), 400
    results = []
    for token in tokens:
        status = backend.validate_token(token)
        results.append({"token": token, "valid": status.get("valid", False), "email": status.get(
            "email", "unknown"), "is_pro": status.get("is_pro", False), "message": status.get("message", "")})
    return jsonify({"results": results}), 200


@app.route("/auth", methods=["POST"])
def auth_endpoint() -> Any:
    data = request.get_json(silent=True) or {}
    token = data.get("token")
    if token:
        try:
            with open(SESSION_FILE, "w", encoding="utf-8") as f:
                f.write(token)
            backend.token = token
            backend._validate_session(token)
            return jsonify({"success": True, "authenticated": backend.is_authenticated})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
    return jsonify({"error": "token required"}), 400


class FlaskServerThread(threading.Thread):
    def __init__(self, flask_app: Flask, host: str, port: int) -> None:
        super().__init__(daemon=True)
        self._flask_app = flask_app
        self._host = host
        self._port = port
        self._server = None
        self._actual_port: Optional[int] = None

    def run(self) -> None:
        try:
            self._server = make_server(
                self._host, self._port, self._flask_app, threaded=True)
            self._actual_port = self._server.server_port
            logger.info("Running on http://%s:%s",
                        self._host, self._actual_port)
            save_endpoint_info(self._actual_port)
            self._server.serve_forever()
        except Exception as exc:
            if "Bad file descriptor" not in str(exc):
                logger.exception("Server crashed: %s", exc)

    def shutdown(self) -> None:
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None

    def get_url(self) -> Optional[str]:
        return f"http://{self._host}:{self._actual_port}" if self._actual_port else None

    def get_port(self) -> Optional[int]:
        return self._actual_port

# ==================================================================================
# SERVER INTERFACE IMPL
# ==================================================================================


class PerplexityServer(BaseServer):
    def __init__(self) -> None:
        self.host = HOST
        self.port = PORT
        self.thread: Optional[FlaskServerThread] = None

    def start(self, anonymous: bool = True) -> None:
        """
        Starts the local Flask server.
        The 'anonymous' param is kept for compatibility with the ServerManager interface
        but is not strictly used here (authentication depends on the backend session).
        """
        if self.thread and self.thread.is_alive():
            return
        self.thread = FlaskServerThread(app, self.host, self.port)
        self.thread.start()
        # Give it a moment to bind
        time.sleep(1)

    def stop(self) -> None:
        if self.thread:
            self.thread.shutdown()
            self.thread = None

    def is_running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def get_url(self) -> str:
        if self.thread:
            url = self.thread.get_url()
            if url:
                return url
        return f"http://{self.host}:{self.port}"

    def validate_batch(self, tokens: List[str]) -> List[Dict[str, Any]]:
        """
        Directly access the backend logic to validate tokens,
        satisfying the ServerManager interface.
        """
        results = []
        for token in tokens:
            status = backend.validate_token(token)
            results.append({
                "token": token,
                "valid": status.get("valid", False),
                "email": status.get("email", "unknown"),
                "is_pro": status.get("is_pro", False),
                "message": status.get("message", "")
            })
        return results
    
    def validate_token(self, token: str) -> dict:
        if not self.backend: return {"valid": False, "message": "Server not running"}
        return self.backend.validate_token(token)

if __name__ == "__main__":
    thread = FlaskServerThread(app, HOST, PORT)
    thread.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        thread.shutdown()
