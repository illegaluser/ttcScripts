import json
import logging
import os
import time

import requests

from .config import Config
from .utils import extract_json_safely

log = logging.getLogger(__name__)


class DifyConnectionError(Exception):
    """Dify API 통신 실패 시 발생한다."""


class DifyClient:
    """
    Dify Chatflow API 통신 계층.
    - /v1/files/upload : Doc 모드 문서 업로드
    - /v1/chat-messages : 시나리오 생성 및 치유 요청 (blocking)
    """

    # 일시적 오류 시 재시도할 HTTP 상태 코드
    _RETRYABLE_STATUS_CODES = {502, 503, 504}

    def __init__(self, config: Config):
        self.base_url = config.dify_base_url
        self.headers = {"Authorization": f"Bearer {config.dify_api_key}"}

    def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        max_retries: int = 3,
        backoff_base: float = 5.0,
        timeout: int = 120,
        **kwargs,
    ) -> requests.Response:
        """HTTP 요청을 전송하되, 일시적 오류 시 지수 백오프로 재시도한다.

        재시도 대상:
            - ``requests.ConnectionError`` (연결 거부, DNS 실패 등)
            - ``requests.Timeout`` (읽기/연결 타임아웃)
            - HTTP 502, 503, 504 (업스트림 일시 장애)

        4xx 클라이언트 에러는 즉시 반환하여 호출부에서 처리한다.

        Args:
            method: HTTP 메서드 (``"POST"`` 등).
            url: 요청 URL.
            max_retries: 최대 재시도 횟수. 초회 포함하지 않음.
            backoff_base: 첫 재시도 대기 시간(초). 이후 2배씩 증가.
            timeout: 요청 타임아웃(초).
            **kwargs: ``requests.request()`` 에 전달할 추가 인자.

        Returns:
            성공한 ``requests.Response`` 객체.

        Raises:
            requests.RequestException: 모든 재시도 소진 후에도 실패 시.
        """
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                res = requests.request(method, url, timeout=timeout, **kwargs)
                if res.status_code not in self._RETRYABLE_STATUS_CODES:
                    return res
                last_exc = requests.HTTPError(
                    f"HTTP {res.status_code}", response=res,
                )
            except (requests.ConnectionError, requests.Timeout) as e:
                last_exc = e

            if attempt < max_retries:
                wait = backoff_base * (2 ** attempt)
                log.warning(
                    "[Retry] %s %s — %d/%d 재시도 (%.0f초 후). 원인: %s",
                    method, url, attempt + 1, max_retries, wait, last_exc,
                )
                time.sleep(wait)

        raise last_exc  # type: ignore[misc]

    # ── Doc 모드: 문서 파일 업로드 ──
    def upload_file(self, file_path: str) -> str:
        """Dify Files API 에 문서를 업로드하고 upload_file_id 를 반환한다.

        Args:
            file_path: 업로드할 PDF 등 문서 파일 경로.

        Returns:
            Dify 가 부여한 파일 ID 문자열.

        Raises:
            DifyConnectionError: HTTP 에러 또는 네트워크 실패 시.
        """
        log.info("[Doc] 문서 업로드 중... (%s)", file_path)
        filename = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            file_bytes = f.read()
        try:
            res = self._request_with_retry(
                "POST",
                f"{self.base_url}/files/upload",
                headers=self.headers,
                files={"file": (filename, file_bytes, "application/pdf")},
                data={"user": "mac-agent"},
                timeout=60,
            )
            res.raise_for_status()
        except requests.RequestException as e:
            raise DifyConnectionError(f"파일 업로드 실패: {e}") from e

        file_id = res.json().get("id")
        log.info("[Doc] 문서 업로드 완료 (ID: %s)", file_id)
        return file_id

    # ── 시나리오 생성 (chat / doc 모드) ──
    def generate_scenario(
        self,
        run_mode: str,
        srs_text: str,
        target_url: str,
        file_id: str | None = None,
    ) -> list[dict]:
        """Dify Chatflow 에 시나리오 생성을 요청하고 DSL 스텝 배열을 반환한다.

        Args:
            run_mode: 실행 모드 (``"chat"`` 또는 ``"doc"``).
            srs_text: 자연어 요구사항 텍스트.
            target_url: 테스트 대상 URL.
            file_id: Doc 모드에서 ``upload_file()`` 이 반환한 파일 ID. 없으면 None.

        Returns:
            DSL 스텝 dict 의 리스트.

        Raises:
            DifyConnectionError: API 통신 실패 또는 JSON 파싱 실패 시.
        """
        payload = {
            "inputs": {
                "run_mode": run_mode,
                "srs_text": srs_text,
                "target_url": target_url,
            },
            "query": "실행을 요청합니다.",
            "response_mode": "blocking",
            "user": "mac-agent",
        }
        if file_id:
            payload["files"] = [
                {
                    "type": "document",
                    "transfer_method": "local_file",
                    "upload_file_id": file_id,
                }
            ]

        answer = self._call(payload)
        log.info("Dify 응답 길이: %d자, <think> 포함: %s", len(answer), "<think>" in answer)
        scenario = extract_json_safely(answer)
        if not scenario or not isinstance(scenario, list):
            # <think> 블록 제거 후 실제 내용이 있는지 표시
            cleaned = answer
            if "<think>" in cleaned:
                import re
                cleaned = re.sub(r"<think>.*?</think>", "[THINK_BLOCK_REMOVED]", cleaned, flags=re.S)
                cleaned = re.sub(r"<think>.*", "[UNCLOSED_THINK_REMOVED]", cleaned, flags=re.S)
            raise DifyConnectionError(
                f"시나리오 파싱 실패.\n"
                f"  응답 길이: {len(answer)}자\n"
                f"  <think> 블록 제거 후 내용:\n{cleaned[:500]}"
            )
        return scenario

    # ── 치유 요청 (heal 모드) ──
    def request_healing(
        self,
        error_msg: str,
        dom_snapshot: str,
        failed_step: dict,
    ) -> dict | None:
        """실패한 스텝의 치유를 LLM 에 요청하고 새 target 정보를 반환한다.

        Args:
            error_msg: 실패 원인 에러 메시지.
            dom_snapshot: 현재 페이지의 HTML DOM (잘린 길이).
            failed_step: 실패한 DSL 스텝 dict.

        Returns:
            새 target 이 포함된 dict. 파싱 실패 시 ``None``.
        """
        payload = {
            "inputs": {
                "run_mode": "heal",
                "error": error_msg,
                "dom": dom_snapshot,
                "failed_step": json.dumps(failed_step, ensure_ascii=False),
            },
            "query": "실행을 요청합니다.",
            "response_mode": "blocking",
            "user": "mac-agent",
        }
        answer = self._call(payload)
        return extract_json_safely(answer)

    # ── 내부: Chatflow API 호출 ──
    def _call(self, payload: dict) -> str:
        """Dify /chat-messages 엔드포인트에 blocking 요청을 보내고 answer 를 반환한다.

        일시적 오류(타임아웃, 502/503/504) 시 지수 백오프로 최대 3회 재시도한다.

        Raises:
            DifyConnectionError: HTTP 에러, 타임아웃, 네트워크 실패 시.
        """
        try:
            res = self._request_with_retry(
                "POST",
                f"{self.base_url}/chat-messages",
                json=payload,
                headers={
                    **self.headers,
                    "Content-Type": "application/json",
                },
                timeout=120,
            )
            res.raise_for_status()
            return res.json().get("answer", "")
        except requests.RequestException as e:
            raise DifyConnectionError(f"Dify API 통신 실패: {e}") from e
