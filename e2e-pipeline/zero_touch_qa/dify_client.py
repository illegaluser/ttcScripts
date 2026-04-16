import json
import logging
import os

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

    def __init__(self, config: Config):
        self.base_url = config.dify_base_url
        self.headers = {"Authorization": f"Bearer {config.dify_api_key}"}
        # [변경] timeout 을 Config 에서 읽도록 변경
        # 원본: _call() 에 timeout=120 하드코딩 → 로컬 LLM 추론 시간 초과로 ReadTimeout 반복
        # Config.dify_timeout(기본 1800s) 을 저장해 _call() 에서 재사용한다.
        self.timeout = config.dify_timeout

    # ── Doc 모드: 문서 파일 업로드 ──
    def upload_file(self, file_path: str) -> str:
        """Dify Files API에 문서를 업로드하고 upload_file_id를 반환한다."""
        log.info("[Doc] 문서 업로드 중... (%s)", file_path)
        try:
            with open(file_path, "rb") as f:
                res = requests.post(
                    f"{self.base_url}/files/upload",
                    headers=self.headers,
                    files={"file": (os.path.basename(file_path), f, "application/pdf")},
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
        """Dify Chatflow에 시나리오 생성을 요청하고 DSL 스텝 배열을 반환한다."""
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

        # [변경] 3회 재시도 루프 추가
        # 원본: LLM 응답 1회 파싱 후 실패 시 즉시 예외 발생
        # 사유: qwen3:8b 같은 추론 모델은 <think> 블록, 마크다운 펜스, 잘못된 JSON 등을
        #       일시적으로 출력할 수 있다. extract_json_safely 보정 후에도 실패하는 경우를
        #       재시도로 커버한다.
        last_answer = ""
        for attempt in range(1, 4):
            last_answer = self._call(payload)
            scenario = extract_json_safely(last_answer)
            # [변경] list[dict] 타입 검증 추가
            # 원본: scenario 가 truthy 하면 그대로 반환 → list[str] 도 통과되어
            #       executor 가 step["action"] 에서 KeyError 발생
            # 사유: LLM 이 fallback_targets 배열처럼 ["role=button", ...] 만 반환하는
            #       오파싱 사례가 실제로 발생했다. 첫 원소가 dict 인지 명시적으로 확인한다.
            if (scenario
                    and isinstance(scenario, list)
                    and len(scenario) > 0
                    and isinstance(scenario[0], dict)):
                return scenario
            log.warning(
                "[generate_scenario] 시나리오 파싱 실패 (attempt %d/3). "
                "타입=%s 앞부분: %s",
                attempt,
                type(scenario).__name__ if scenario is not None else "None",
                last_answer[:300],
            )
        raise DifyConnectionError(
            f"시나리오 파싱 실패 (3회 재시도). Dify 원본 응답:\n{last_answer[:500]}"
        )

    # ── 치유 요청 (heal 모드) ──
    def request_healing(
        self,
        error_msg: str,
        dom_snapshot: str,
        failed_step: dict,
    ) -> dict | None:
        """실패한 스텝의 치유를 요청하고, 새 target 정보를 반환한다."""
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
        try:
            res = requests.post(
                f"{self.base_url}/chat-messages",
                json=payload,
                headers={
                    **self.headers,
                    "Content-Type": "application/json",
                },
                # [변경] timeout=120(하드코딩) → self.timeout(Config 에서 주입)
                # 사유: 로컬 LLM 추론이 수 분 소요되어 120s 에서 ReadTimeout 이 발생했다.
                #       Config.dify_timeout 기본값 1800s 로 변경하고,
                #       nginx/dify.conf 의 proxy_read_timeout 도 동일하게 맞췄다.
                timeout=self.timeout,
            )
            res.raise_for_status()
            return res.json().get("answer", "")
        except requests.RequestException as e:
            raise DifyConnectionError(f"Dify API 통신 실패: {e}") from e
