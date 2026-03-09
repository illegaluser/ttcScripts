#!/usr/bin/env python3
import json
import os
import random
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests


HOST = os.environ.get("GEMINI_WRAPPER_HOST", "0.0.0.0")
PORT = int(os.environ.get("GEMINI_WRAPPER_PORT", "8000"))
GEMINI_BASE_URL = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_GENERATE_URL = f"{GEMINI_BASE_URL}/models/{quote(GEMINI_MODEL, safe='')}:generateContent"
GEMINI_MAX_RETRIES = int(os.environ.get("GEMINI_MAX_RETRIES", "5"))
GEMINI_RETRY_BASE_SEC = float(os.environ.get("GEMINI_RETRY_BASE_SEC", "1.0"))
GEMINI_RETRY_MAX_SEC = float(os.environ.get("GEMINI_RETRY_MAX_SEC", "20.0"))
GEMINI_MIN_INTERVAL_SEC = float(os.environ.get("GEMINI_MIN_INTERVAL_SEC", "1.0"))

_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_TS = 0.0


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    content_length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(content_length) if content_length > 0 else b"{}"
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _normalize_messages(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    messages = payload.get("messages")
    normalized: List[Dict[str, str]] = []
    if isinstance(messages, list) and messages:
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "user"))
            content = str(item.get("content", "")).strip()
            if content:
                normalized.append({"role": role, "content": content})
        if normalized:
            return normalized

    text = str(payload.get("query") or payload.get("input") or "").strip()
    return [{"role": "user", "content": text}]


def _build_gemini_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    messages = _normalize_messages(payload)
    contents: List[Dict[str, Any]] = []
    system_chunks: List[str] = []

    for msg in messages:
        role = (msg.get("role") or "user").lower()
        text = msg.get("content") or ""
        if not text:
            continue

        if role == "system":
            system_chunks.append(text)
            continue

        gemini_role = "model" if role == "assistant" else "user"
        contents.append({"role": gemini_role, "parts": [{"text": text}]})

    if not contents:
        fallback = "ping"
        contents = [{"role": "user", "parts": [{"text": fallback}]}]

    request_payload: Dict[str, Any] = {"contents": contents}
    if system_chunks:
        request_payload["systemInstruction"] = {
            "parts": [{"text": "\n\n".join(system_chunks)}]
        }
    return request_payload


def _extract_text_from_candidate(candidate: Dict[str, Any]) -> str:
    content = candidate.get("content") or {}
    parts = content.get("parts") or []
    chunks: List[str] = []
    for part in parts:
        if isinstance(part, dict):
            text = part.get("text")
            if text is not None:
                chunks.append(str(text))
        elif part is not None:
            chunks.append(str(part))
    return "".join(chunks)


def _extract_block_reason(data: Dict[str, Any]) -> Optional[str]:
    prompt_feedback = data.get("promptFeedback") or {}
    block_reason = prompt_feedback.get("blockReason")
    if block_reason:
        return str(block_reason)
    return None


def _response_detail(response: requests.Response) -> str:
    text = (response.text or "").strip()
    if not text:
        return f"HTTP {response.status_code}"
    compact = " ".join(text.split())
    return compact[:600]


def _looks_like_quota_exhausted(detail_text: str) -> bool:
    text = detail_text.lower()
    quota_hints = [
        "exceeded your current quota",
        "quota",
        "billing",
        "insufficient",
        "resource_exhausted",
    ]
    return any(hint in text for hint in quota_hints)


def _wait_min_interval() -> None:
    global _LAST_REQUEST_TS
    with _REQUEST_LOCK:
        now = time.monotonic()
        wait_sec = GEMINI_MIN_INTERVAL_SEC - (now - _LAST_REQUEST_TS)
        if wait_sec > 0:
            time.sleep(wait_sec)
        _LAST_REQUEST_TS = time.monotonic()


def _compute_retry_wait(response: requests.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After", "").strip()
    if retry_after:
        try:
            retry_after_sec = float(retry_after)
            if retry_after_sec > 0:
                return min(retry_after_sec, GEMINI_RETRY_MAX_SEC)
        except ValueError:
            pass
    exponential = GEMINI_RETRY_BASE_SEC * (2 ** max(attempt - 1, 0))
    jitter = random.uniform(0.0, 0.5)
    return min(exponential + jitter, GEMINI_RETRY_MAX_SEC)


def _call_gemini(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is empty.")

    request_payload = _build_gemini_request(payload)
    response: Optional[requests.Response] = None
    detail = ""
    for attempt in range(1, GEMINI_MAX_RETRIES + 1):
        _wait_min_interval()
        response = requests.post(
            f"{GEMINI_GENERATE_URL}?key={GEMINI_API_KEY}",
            json=request_payload,
            headers={"Content-Type": "application/json"},
            timeout=300,
        )
        if response.status_code != 429:
            break

        detail = _response_detail(response)
        if _looks_like_quota_exhausted(detail):
            raise RuntimeError(f"gemini quota exceeded (HTTP 429): {detail}")

        if attempt >= GEMINI_MAX_RETRIES:
            break
        time.sleep(_compute_retry_wait(response, attempt))

    if response is None:
        raise RuntimeError("gemini request failed before response was created.")

    if response.status_code == 429:
        detail = detail or _response_detail(response)
        raise RuntimeError(
            f"gemini rate limit exceeded (HTTP 429) after {GEMINI_MAX_RETRIES} retries: {detail}"
        )
    response.raise_for_status()
    data = response.json() or {}

    candidates = data.get("candidates") or []
    content = _extract_text_from_candidate(candidates[0]) if candidates else ""
    if not content:
        block_reason = _extract_block_reason(data)
        if block_reason:
            raise RuntimeError(f"gemini blocked response: {block_reason}")

    usage = data.get("usageMetadata") or {}
    prompt_tokens = int(usage.get("promptTokenCount") or 0)
    completion_tokens = int(usage.get("candidatesTokenCount") or 0)
    total_tokens = int(usage.get("totalTokenCount") or (prompt_tokens + completion_tokens))

    return {
        "answer": content,
        "docs": [],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
        "model": GEMINI_MODEL,
    }


class GeminiWrapperHandler(BaseHTTPRequestHandler):
    server_version = "GeminiWrapper/1.0"

    def do_GET(self) -> None:
        if self.path == "/health":
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "model": GEMINI_MODEL,
                    "gemini_base_url": GEMINI_BASE_URL,
                    "has_api_key": bool(GEMINI_API_KEY),
                    "max_retries": GEMINI_MAX_RETRIES,
                    "min_interval_sec": GEMINI_MIN_INTERVAL_SEC,
                },
            )
            return

        _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/invoke":
            _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})
            return

        try:
            payload = _read_json(self)
            result = _call_gemini(payload)
            _json_response(self, HTTPStatus.OK, result)
        except json.JSONDecodeError as exc:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"error": f"invalid json: {exc}"})
        except requests.HTTPError as exc:
            detail = ""
            try:
                detail = exc.response.text
            except Exception:
                detail = str(exc)
            status_code = exc.response.status_code if exc.response is not None else HTTPStatus.BAD_GATEWAY
            _json_response(self, status_code, {"error": f"gemini http error: {detail}"})
        except requests.RequestException as exc:
            _json_response(self, HTTPStatus.BAD_GATEWAY, {"error": f"gemini connection error: {exc}"})
        except RuntimeError as exc:
            message = str(exc)
            lowered = message.lower()
            if "quota exceeded" in lowered or "rate limit exceeded" in lowered:
                _json_response(self, HTTPStatus.TOO_MANY_REQUESTS, {"error": message})
            elif "api_key is empty" in lowered:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"error": message})
            else:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": message})
        except Exception as exc:
            _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def log_message(self, format: str, *args: Any) -> None:
        print("%s - - [%s] %s" % (self.address_string(), self.log_date_time_string(), format % args), flush=True)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), GeminiWrapperHandler)
    print(
        f"Gemini wrapper listening on http://{HOST}:{PORT} -> {GEMINI_GENERATE_URL} "
        f"(model={GEMINI_MODEL})",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
