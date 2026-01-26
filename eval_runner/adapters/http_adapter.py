import time
import json
import requests
from .base import BaseAdapter, UniversalEvalOutput

class GenericHttpAdapter(BaseAdapter):
    def invoke(self, input_text: str, **kwargs) -> UniversalEvalOutput:
        start_time = time.time()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "query": input_text,
            "inputs": kwargs.get("inputs", {}),
            "user": "eval-runner",
        }

        try:
            response = requests.post(
                self.target_url,
                json=payload,
                headers=headers,
                timeout=60,
            )
            latency = int((time.time() - start_time) * 1000)
            status_code = response.status_code

            try:
                data = response.json()
                raw_response = json.dumps(data, ensure_ascii=False)
            except Exception:
                data = {}
                raw_response = response.text

            actual_output = data.get("answer") or data.get("response") or data.get("text") or ""

            if status_code >= 400:
                return UniversalEvalOutput(
                    input=input_text,
                    actual_output=str(data),
                    http_status=status_code,
                    raw_response=raw_response,
                    error=f"HTTP {status_code}",
                    latency_ms=latency,
                )

            contexts = data.get("docs", [])
            if isinstance(contexts, str):
                contexts = [contexts]

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
            return UniversalEvalOutput(
                input=input_text,
                actual_output="",
                error=f"ConnError: {str(e)}",
                latency_ms=int((time.time() - start_time) * 1000),
            )