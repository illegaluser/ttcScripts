#!/usr/bin/env python3

# ==================================================================================
# 파일명: ollama_wrapper_api.py
# 버전: 1.0
#
# [시스템 개요]
# 이 스크립트는 AI 평가 파이프라인(Phase 5)에서 사용되는 **Ollama 래퍼 API 서버**입니다.
# 평가 대상 AI가 Ollama 기반의 로컬 LLM인 경우, 이 서버가 eval_runner와 Ollama 사이에서
# 프로토콜 변환 역할을 수행합니다.
#
# [존재 이유]
# eval_runner(test_runner.py)의 GenericHttpAdapter는 표준화된 JSON 포맷
# ({query, messages} → {answer, docs, usage})으로 통신합니다.
# 그런데 Ollama의 /api/chat 엔드포인트는 다른 응답 형식을 사용하므로,
# 이 래퍼가 중간에서 요청/응답 형식을 변환해줍니다.
#
# [동작 흐름]
# eval_runner  →  POST /invoke  →  ollama_wrapper_api  →  Ollama /api/chat
#              ←  {answer, usage}  ←                    ←  {message, eval_count}
#
# [엔드포인트]
# - GET  /health  : 서버 상태 및 연결 모델 정보 반환
# - POST /invoke  : 질문을 받아 Ollama에 전달하고 표준 포맷으로 응답 반환
#
# [실행 예시]
# OLLAMA_MODEL=qwen3-coder:30b python3 ollama_wrapper_api.py
# ==================================================================================

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ============================================================================
# [설정] 환경 변수 기반 서버 구성
# ============================================================================
HOST = os.environ.get("OLLAMA_WRAPPER_HOST", "0.0.0.0")        # 바인딩 호스트 (모든 인터페이스)
PORT = int(os.environ.get("OLLAMA_WRAPPER_PORT", "8000"))       # 래퍼 서버 포트
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")  # Ollama API 주소
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3-coder:30b")  # 사용할 LLM 모델명
OLLAMA_CHAT_URL = f"{OLLAMA_BASE_URL.rstrip('/')}/api/chat"    # Ollama 채팅 엔드포인트


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Dict[str, Any]) -> None:
    """
    HTTP 응답을 JSON 형식으로 전송하는 공통 헬퍼입니다.

    Content-Type과 Content-Length 헤더를 자동으로 설정하며,
    한글 등 비ASCII 문자를 그대로 유지합니다 (ensure_ascii=False).
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    """
    HTTP 요청 본문에서 JSON 데이터를 읽어 파싱합니다.

    본문이 비어있으면 빈 딕셔너리를 반환하여 호출부에서 안전하게 처리할 수 있습니다.
    """
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
    """
    HTTP 요청을 처리하는 핸들러 클래스입니다.

    두 가지 엔드포인트를 제공합니다:
    - GET  /health : 서버 상태 확인 (모니터링/헬스체크용)
    - POST /invoke : eval_runner의 질문을 받아 Ollama에 전달하고 결과 반환
    """
    server_version = "OllamaWrapper/1.0"

    def do_GET(self) -> None:
        """
        GET /health: 서버 상태, 사용 중인 모델명, Ollama 주소를 반환합니다.
        Jenkins 파이프라인에서 래퍼 서버가 정상 동작 중인지 확인하는 데 사용됩니다.
        """
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
        """
        POST /invoke: eval_runner가 보낸 질문을 Ollama에 전달하고 결과를 반환합니다.

        에러 유형별로 적절한 HTTP 상태 코드를 반환하여,
        eval_runner가 실패 원인을 정확히 파악할 수 있도록 합니다:
        - 400: JSON 파싱 실패 (잘못된 요청 본문)
        - 502: Ollama 연결 실패 (Ollama 서버 미가동)
        - Ollama 응답 코드: Ollama 내부 오류 전달
        - 500: 기타 예상치 못한 오류
        """
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
        """Jenkins/터미널에서 접근 로그를 간단히 출력합니다."""
        print("%s - - [%s] %s" % (self.address_string(), self.log_date_time_string(), format % args), flush=True)


def main() -> None:
    """
    래퍼 서버를 시작합니다.

    ThreadingHTTPServer를 사용하여 동시 요청을 처리할 수 있습니다.
    eval_runner가 여러 테스트 케이스를 순차 실행할 때도 안정적으로 동작합니다.
    """
    server = ThreadingHTTPServer((HOST, PORT), OllamaWrapperHandler)
    print(
        f"Ollama wrapper listening on http://{HOST}:{PORT} -> {OLLAMA_CHAT_URL} "
        f"(model={OLLAMA_MODEL})",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
