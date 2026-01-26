from dataclasses import dataclass, field
from typing import List, Dict, Optional

@dataclass
class UniversalEvalOutput:
    """
    [CDM: Canonical Data Model]
    외부 AI의 다양한 응답 형식을 하나로 통일한 표준 데이터 구조입니다.
    이 구조를 통해 평가 엔진(test_runner)은 벤더에 상관없이 동일한 로직으로 채점할 수 있습니다.
    """
    input: str                         # AI에게 전달된 원본 질문
    actual_output: str                 # AI가 생성한 최종 답변 텍스트
    retrieval_context: List[str] = field(default_factory=list) # RAG에서 검색된 문서 리스트
    tool_calls: List[Dict] = field(default_factory=list)       # 에이전트가 호출한 도구 로그
    http_status: int = 0               # API 호출 결과 상태 코드
    raw_response: str = ""             # 보안/형식 검증을 위한 원본 JSON/텍스트 응답
    error: Optional[str] = None        # 네트워크 오류 등 발생 시 에러 메시지
    latency_ms: int = 0                # 응답 소요 시간 (밀리초)

    def to_dict(self):
        """결과 기록(Langfuse 등)을 위해 객체를 딕셔너리로 변환합니다."""
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
    """
    모든 외부 AI 연동 어댑터의 추상 부모 클래스입니다.
    새로운 벤더가 추가될 때 이 클래스를 상속받아 invoke 메서드를 구현합니다.
    """
    def __init__(self, target_url: str, api_key: str = None):
        self.target_url = target_url
        self.api_key = api_key

    def invoke(self, input_text: str, **kwargs) -> UniversalEvalOutput:
        """외부 API를 호출하고 결과를 UniversalEvalOutput으로 반환해야 합니다."""
        raise NotImplementedError("상속받은 클래스에서 invoke를 구현해야 합니다.")