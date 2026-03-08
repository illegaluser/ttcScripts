import time
import json
import requests
from typing import List, Dict, Optional
from .base import BaseAdapter, UniversalEvalOutput

class GenericHttpAdapter(BaseAdapter):
    """
    대상 AI가 API일 때 작동하며, 대화 기록(history)을 포함한 다중 턴 요청을 지원합니다.
    """
    def invoke(self, input_text: str, history: Optional[List[Dict]] = None, **kwargs) -> UniversalEvalOutput:
        start_time = time.time()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # 다중 턴 대화를 위해 이전 대화 기록을 messages에 추가
        messages = []
        if history:
            for turn in history:
                messages.append({"role": "user", "content": turn["input"]})
                messages.append({"role": "assistant", "content": turn["actual_output"]})
        messages.append({"role": "user", "content": input_text})

        payload = {
            "messages": messages,
            "query": input_text, # 하위 호환성을 위한 필드
            "user": "eval-runner",
        }

        try:
            res = requests.post(self.target_url, json=payload, headers=headers, timeout=60)
            latency_ms = int((time.time() - start_time) * 1000)

            try:
                data = res.json()
                raw_response = json.dumps(data, ensure_ascii=False)
            except json.JSONDecodeError:
                data = {}
                raw_response = res.text

            actual_output = data.get("answer") or data.get("response") or data.get("text") or ""
            
            if res.status_code >= 400:
                return UniversalEvalOutput(input=input_text, actual_output=str(data), http_status=res.status_code, raw_response=raw_response, error=f"HTTP {res.status_code}", latency_ms=latency_ms)

            docs = data.get("docs", [])
            if isinstance(docs, str):
                docs = [docs]

            # API 응답에 'usage' 필드가 있으면 토큰 사용량 추출
            parsed_usage = {}
            usage_data = data.get("usage", {})
            if usage_data:
                parsed_usage = {
                    "promptTokens": usage_data.get("prompt_tokens", 0),
                    "completionTokens": usage_data.get("completion_tokens", 0),
                    "totalTokens": usage_data.get("total_tokens", 0),
                }

            return UniversalEvalOutput(
                input=input_text, actual_output=str(actual_output), retrieval_context=[str(c) for c in docs],
                http_status=res.status_code, raw_response=raw_response, latency_ms=latency_ms,
                usage=parsed_usage
            )

        except requests.exceptions.RequestException as e:
            return UniversalEvalOutput(
                input=input_text, actual_output="", error=f"Connection Error: {e}",
                latency_ms=int((time.time() - start_time) * 1000)
            )