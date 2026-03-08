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
        if not isinstance(data, dict):
            return ""

        for key in ("answer", "response", "text", "output", "message"):
            value = data.get(key)
            if value is not None:
                return str(value)
        return ""

    @staticmethod
    def _extract_contexts(data: Dict) -> List[str]:
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
        start_time = time.time()
        headers = self._build_headers()

        messages = []
        if history:
            for turn in history:
                messages.append({"role": "user", "content": turn["input"]})
                messages.append({"role": "assistant", "content": turn["actual_output"]})
        messages.append({"role": "user", "content": input_text})

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
                data = response.json()
                raw_response = json.dumps(data, ensure_ascii=False)
            except json.JSONDecodeError:
                data = {}
                raw_response = response.text

            actual_output = self._extract_actual_output(data)
            usage = self._extract_usage(data)

            if response.status_code >= 400:
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
            return UniversalEvalOutput(
                input=input_text,
                actual_output="",
                error=f"Connection Error: {exc}",
                latency_ms=int((time.time() - start_time) * 1000),
            )
