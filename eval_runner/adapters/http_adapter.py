import json
import time
from typing import Dict, List, Optional

import requests

from .base import BaseAdapter, UniversalEvalOutput


class GenericHttpAdapter(BaseAdapter):
    """
    대상 AI가 API일 때 작동하며, 대화 기록(history)을 포함한 다중 턴 요청을 지원합니다.
    """

    @staticmethod
    def _extract_usage(data: Dict) -> Dict[str, int]:
        """
        응답 JSON의 usage 필드를 러너 공통 포맷으로 정규화합니다.
        공급자마다 키 이름이 달라질 수 있어 여러 후보를 허용합니다.
        """
        usage_data = data.get("usage", {}) if isinstance(data, dict) else {}
        if not isinstance(usage_data, dict):
            return {}

        prompt_tokens = usage_data.get("prompt_tokens")
        completion_tokens = usage_data.get("completion_tokens")
        total_tokens = usage_data.get("total_tokens")

        if prompt_tokens is None:
            prompt_tokens = usage_data.get("input_tokens", 0)
        if completion_tokens is None:
            completion_tokens = usage_data.get("output_tokens", 0)
        if total_tokens is None:
            total_tokens = usage_data.get("total", 0) or (prompt_tokens or 0) + (completion_tokens or 0)

        return {
            "promptTokens": int(prompt_tokens or 0),
            "completionTokens": int(completion_tokens or 0),
            "totalTokens": int(total_tokens or 0),
        }

    @staticmethod
    def _extract_actual_output(data: Dict) -> str:
        """
        실제 답변 텍스트가 들어 있을 가능성이 높은 필드들을 순서대로 탐색합니다.
        가장 먼저 발견된 값을 평가용 actual_output으로 사용합니다.
        """
        if not isinstance(data, dict):
            return ""

        for key in ("answer", "response", "text", "output", "message"):
            value = data.get(key)
            if value is not None:
                return str(value)
        return ""

    @staticmethod
    def _extract_contexts(data: Dict) -> List[str]:
        """
        RAG 평가용 검색 문맥을 docs 또는 retrieval_context 필드에서 꺼냅니다.
        문자열 단일값도 리스트로 감싸 DeepEval 입력 형식을 맞춥니다.
        """
        if not isinstance(data, dict):
            return []

        docs = data.get("docs")
        if docs is None:
            docs = data.get("retrieval_context", [])

        if isinstance(docs, str):
            return [docs]
        if isinstance(docs, list):
            return [str(item) for item in docs]
        return []

    def _build_headers(self) -> Dict[str, str]:
        """
        요청 헤더를 조립합니다.
        TARGET_AUTH_HEADER가 주어지면 우선 사용하고, 없을 때만 API_KEY를 Bearer 토큰으로 변환합니다.
        """
        headers = {"Content-Type": "application/json"}

        if self.auth_header:
            if ":" in self.auth_header:
                key, value = self.auth_header.split(":", 1)
                headers[key.strip()] = value.strip()
            else:
                headers["Authorization"] = self.auth_header.strip()
        elif self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        return headers

    def invoke(
        self,
        input_text: str,
        history: Optional[List[Dict]] = None,
        **kwargs,
    ) -> UniversalEvalOutput:
        """
        대상 HTTP API를 호출하고 결과를 UniversalEvalOutput으로 표준화해 반환합니다.
        멀티턴 평가에서는 history를 messages 배열로 변환해 함께 보냅니다.
        """
        start_time = time.time()
        headers = self._build_headers()

        # 이전 대화 이력을 messages에 쌓아 대상 모델이 컨텍스트를 유지할 수 있게 합니다.
        messages = []
        if history:
            for turn in history:
                messages.append({"role": "user", "content": turn["input"]})
                messages.append({"role": "assistant", "content": turn["actual_output"]})
        messages.append({"role": "user", "content": input_text})

        # 다양한 외부 API와의 호환성을 위해 query, input, messages를 함께 보냅니다.
        payload = {
            "messages": messages,
            "query": input_text,
            "input": input_text,
            "user": "eval-runner",
        }

        try:
            response = requests.post(
                self.target_url,
                json=payload,
                headers=headers,
                timeout=60,
            )
            latency_ms = int((time.time() - start_time) * 1000)

            try:
                # JSON 응답이면 구조화 데이터와 원문 문자열을 모두 보존합니다.
                data = response.json()
                raw_response = json.dumps(data, ensure_ascii=False)
            except json.JSONDecodeError:
                # 비JSON 응답도 정책 검사를 위해 원문 그대로 저장합니다.
                data = {}
                raw_response = response.text

            actual_output = self._extract_actual_output(data)
            usage = self._extract_usage(data)

            if response.status_code >= 400:
                # 실패 응답도 리포트에 남길 수 있도록 가능한 정보를 최대한 담아 반환합니다.
                return UniversalEvalOutput(
                    input=input_text,
                    actual_output=actual_output or str(data),
                    http_status=response.status_code,
                    raw_response=raw_response,
                    error=f"HTTP {response.status_code}",
                    latency_ms=latency_ms,
                    usage=usage,
                )

            return UniversalEvalOutput(
                input=input_text,
                actual_output=str(actual_output),
                retrieval_context=self._extract_contexts(data),
                http_status=response.status_code,
                raw_response=raw_response,
                latency_ms=latency_ms,
                usage=usage,
            )
        except requests.exceptions.RequestException as exc:
            # 네트워크 예외를 표준 출력 구조로 감싸면 상위 평가 로직이 동일하게 처리할 수 있습니다.
            return UniversalEvalOutput(
                input=input_text,
                actual_output="",
                error=f"Connection Error: {exc}",
                latency_ms=int((time.time() - start_time) * 1000),
            )
