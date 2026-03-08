from dataclasses import dataclass, field
from typing import List, Optional, Dict

@dataclass
class UniversalEvalOutput:
    """
    [데이터 표준화 바구니]
    API 통신이든 웹 브라우저 스크래핑이든, 결과물을 이 바구니에 동일한 형태로 담아야 합니다.
    """
    input: str                          # 평가관이 던진 질문 텍스트
    actual_output: str                  # 통신원이 수집해 온 AI의 최종 답변
    retrieval_context: List[str] = field(default_factory=list) # AI가 답변을 위해 찾아본 문서(RAG용)
    http_status: int = 0                # 통신 결과 코드 (예: 200, 404, 500)
    raw_response: str = ""              # 파싱하기 전 날것의 응답 (보안 금칙어 검사를 위해 원본 보존)
    error: Optional[str] = None         # 통신 에러가 발생했다면 그 이유를 담는 공간
    latency_ms: int = 0                 # 질문부터 답변 완료까지 걸린 소요 시간(밀리초)
    usage: Dict[str, int] = field(default_factory=dict) # 토큰 사용량

    def to_dict(self):
        # Langfuse 서버에 전송하기 쉽도록 파이썬 딕셔너리로 변환해주는 함수
        return {
            "input": self.input,
            "actual_output": self.actual_output,
            "retrieval_context": self.retrieval_context,
            "http_status": self.http_status,
            "raw_response": self.raw_response,
            "error": self.error,
            "latency_ms": self.latency_ms,
            "usage": self.usage
        }

class BaseAdapter:
    """모든 통신원 클래스의 기본 뼈대입니다."""
    def __init__(self, target_url: str, api_key: str = None):
        self.target_url = target_url
        self.api_key = api_key

    def invoke(self, input_text: str, history: Optional[List[Dict]] = None, **kwargs) -> UniversalEvalOutput:
        raise NotImplementedError("통신 방식을 구현하세요.")