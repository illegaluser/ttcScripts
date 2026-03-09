#!/usr/bin/env python3
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List

import requests


HOST = os.environ.get("OPENAI_WRAPPER_HOST", "0.0.0.0")
PORT = int(os.environ.get("OPENAI_WRAPPER_PORT", "8000"))
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_CHAT_URL = f"{OPENAI_BASE_URL}/chat/completions"


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


def _build_messages(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    eval runner가 보내는 messages를 우선 사용하고,
    없으면 query/input을 단일 user 메시지로 변환합니다.
    """
    messages = payload.get("messages")
    if isinstance(messages, list) and messages:
        normalized = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "user"))
            content = str(item.get("content", ""))
            if content:
                normalized.append({"role": role, "content": content})
        if normalized:
            return normalized

    text = payload.get("query") or payload.get("input") or ""
    return [{"role": "user", "content": str(text)}]


def _extract_content(choice_message: Dict[str, Any]) -> str:
    """
    OpenAI 응답 content는 문자열 또는 파트 배열일 수 있어 안전하게 텍스트를 추출합니다.
    """
    content = choice_message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for part in content:
            if isinstance(part, dict):
                text_value = part.get("text")
                if text_value is not None:
                    chunks.append(str(text_value))
            elif part is not None:
                chunks.append(str(part))
        return "".join(chunks)
    return str(content or "")


def _call_openai_chat(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is empty.")

    request_payload = {
        "model": OPENAI_MODEL,
        "messages": _build_messages(payload),
    }

    response = requests.post(
        OPENAI_CHAT_URL,
        json=request_payload,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        timeout=300,
    )
    response.raise_for_status()
    data = response.json() or {}

    choices = data.get("choices") or []
    message = ((choices[0] or {}).get("message") or {}) if choices else {}
    content = _extract_content(message)
    usage = data.get("usage") or {}

    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))

    return {
        "answer": content,
        "docs": [],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
        "model": OPENAI_MODEL,
    }


class OpenAIWrapperHandler(BaseHTTPRequestHandler):
    server_version = "OpenAIWrapper/1.0"

    def do_GET(self) -> None:
        if self.path == "/health":
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "model": OPENAI_MODEL,
                    "openai_base_url": OPENAI_BASE_URL,
                    "has_api_key": bool(OPENAI_API_KEY),
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
            result = _call_openai_chat(payload)
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
            _json_response(self, status_code, {"error": f"openai http error: {detail}"})
        except requests.RequestException as exc:
            _json_response(self, HTTPStatus.BAD_GATEWAY, {"error": f"openai connection error: {exc}"})
        except Exception as exc:
            _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def log_message(self, format: str, *args: Any) -> None:
        print("%s - - [%s] %s" % (self.address_string(), self.log_date_time_string(), format % args), flush=True)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), OpenAIWrapperHandler)
    print(
        f"OpenAI wrapper listening on http://{HOST}:{PORT} -> {OPENAI_CHAT_URL} "
        f"(model={OPENAI_MODEL})",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()

