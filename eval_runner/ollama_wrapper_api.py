#!/usr/bin/env python3
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


HOST = os.environ.get("OLLAMA_WRAPPER_HOST", "0.0.0.0")
PORT = int(os.environ.get("OLLAMA_WRAPPER_PORT", "8000"))
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3-coder:30b")
OLLAMA_CHAT_URL = f"{OLLAMA_BASE_URL.rstrip('/')}/api/chat"


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
    eval runner가 보내는 messages를 그대로 사용하고,
    없으면 query/input 하나만 있는 단일턴 요청으로 변환합니다.
    """
    messages = payload.get("messages")
    if isinstance(messages, list) and messages:
        normalized = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "user"))
            content = str(item.get("content", ""))
            normalized.append({"role": role, "content": content})
        if normalized:
            return normalized

    text = payload.get("query") or payload.get("input") or ""
    return [{"role": "user", "content": str(text)}]


def _call_ollama_chat(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ollama /api/chat 호출 결과를 eval runner가 기대하는 answer 스키마로 변환합니다.
    """
    request_payload = {
        "model": OLLAMA_MODEL,
        "messages": _build_messages(payload),
        "stream": False,
    }

    request = Request(
        OLLAMA_CHAT_URL,
        data=json.dumps(request_payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urlopen(request, timeout=300) as response:
        raw_data = response.read().decode("utf-8")
        data = json.loads(raw_data)

    content = str(((data.get("message") or {}).get("content")) or "")
    prompt_tokens = int(data.get("prompt_eval_count") or 0)
    completion_tokens = int(data.get("eval_count") or 0)

    return {
        "answer": content,
        "docs": [],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "model": OLLAMA_MODEL,
    }


class OllamaWrapperHandler(BaseHTTPRequestHandler):
    server_version = "OllamaWrapper/1.0"

    def do_GET(self) -> None:
        if self.path == "/health":
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "model": OLLAMA_MODEL,
                    "ollama_base_url": OLLAMA_BASE_URL,
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
            result = _call_ollama_chat(payload)
            _json_response(self, HTTPStatus.OK, result)
        except json.JSONDecodeError as exc:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"error": f"invalid json: {exc}"})
        except HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8")
            except Exception:
                detail = str(exc)
            _json_response(self, exc.code, {"error": f"ollama http error: {detail}"})
        except URLError as exc:
            _json_response(self, HTTPStatus.BAD_GATEWAY, {"error": f"ollama connection error: {exc}"})
        except Exception as exc:
            _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def log_message(self, format: str, *args: Any) -> None:
        # Jenkins/terminal에서 접근 로그만 간단히 남깁니다.
        print("%s - - [%s] %s" % (self.address_string(), self.log_date_time_string(), format % args), flush=True)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), OllamaWrapperHandler)
    print(
        f"Ollama wrapper listening on http://{HOST}:{PORT} -> {OLLAMA_CHAT_URL} "
        f"(model={OLLAMA_MODEL})",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
