from dataclasses import dataclass, field
from typing import List, Dict, Optional

@dataclass
class UniversalEvalOutput:
    input: str
    actual_output: str
    retrieval_context: List[str] = field(default_factory=list)
    tool_calls: List[Dict] = field(default_factory=list)
    http_status: int = 0
    raw_response: str = ""  # 원본 응답 저장 (Policy/Format 검증용)
    error: Optional[str] = None
    latency_ms: int = 0

    def to_dict(self):
        return {
            "input": self.input,
            "actual_output": self.actual_output,
            "retrieval_context": self.retrieval_context,
            "tool_calls": self.tool_calls,
            "http_status": self.http_status,
            "raw_response": self.raw_response,
            "error": self.error,
            "latency_ms": self.latency_ms,
        }

class BaseAdapter:
    def __init__(self, target_url: str, api_key: str = None):
        self.target_url = target_url
        self.api_key = api_key

    def invoke(self, input_text: str, **kwargs) -> UniversalEvalOutput:
        raise NotImplementedError