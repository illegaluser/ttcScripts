import time
import json
import requests
from .base import BaseAdapter, UniversalEvalOutput

class GenericHttpAdapter(BaseAdapter):
    """
    표준 HTTP POST 방식을 사용하는 외부 AI 에이전트용 어댑터입니다.
    응답에서 답변, 검색 문맥, 도구 호출 정보를 추출하여 CDM 형식으로 변환합니다.
    """
    def invoke(self, input_text: str, **kwargs) -> UniversalEvalOutput:
        start_time = time.time()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # 외부 AI가 기대하는 표준 페이로드 구성 (필요 시 수정 가능)
        payload = {
            "query": input_text,
            "inputs": kwargs.get("inputs", {}),
            "user": "eval-runner",
        }

        try:
            # 1. 외부 API 호출 (타임아웃 60초 설정)
            response = requests.post(
                self.target_url,
                json=payload,
                headers=headers,
                timeout=60,
            )
            latency = int((time.time() - start_time) * 1000)
            status_code = response.status_code

            # 2. 원본 응답 캡처 (보안 검증용)
            try:
                data = response.json()
                raw_response = json.dumps(data, ensure_ascii=False)
            except Exception:
                data = {}
                raw_response = response.text

            # 3. 필드 매핑 (벤더별로 상이한 필드명을 표준 필드명으로 매핑)
            actual_output = data.get("answer") or data.get("response") or data.get("text") or ""
            
            # 에러 응답 처리 (4xx, 5xx)
            if status_code >= 400:
                return UniversalEvalOutput(
                    input=input_text,
                    actual_output=str(data),
                    http_status=status_code,
                    raw_response=raw_response,
                    error=f"HTTP {status_code}",
                    latency_ms=latency,
                )

            # RAG 문맥 추출
            contexts = data.get("docs", [])
            if isinstance(contexts, str): contexts = [contexts]

            return UniversalEvalOutput(
                input=input_text,
                actual_output=str(actual_output),
                retrieval_context=[str(c) for c in contexts],
                tool_calls=data.get("tools", []),
                http_status=status_code,
                raw_response=raw_response,
                latency_ms=latency,
            )

        except Exception as e:
            # 네트워크 연결 실패 등 예외 처리
            return UniversalEvalOutput(
                input=input_text,
                actual_output="",
                error=f"ConnError: {str(e)}",
                latency_ms=int((time.time() - start_time) * 1000),
            )