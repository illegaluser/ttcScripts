from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class UniversalEvalOutput:
    """
    [데이터 표준화 바구니]
    API 통신이든 웹 브라우저 스크래핑이든, 결과물을 이 바구니에 동일한 형태로 담아야 합니다.
    """

    input: str
    actual_output: str
    retrieval_context: List[str] = field(default_factory=list)
    http_status: int = 0
    raw_response: str = ""
    error: Optional[str] = None
    latency_ms: int = 0
    usage: Dict[str, int] = field(default_factory=dict)

    def to_dict(self):
        """
        Langfuse, 로그, 디버깅 출력에서 바로 사용할 수 있도록 dataclass를 dict로 변환합니다.
        출력 포맷을 한 곳에 모아두면 상위 호출부는 필드 이름을 다시 조립할 필요가 없습니다.
        """
        return {
            "input": self.input,
            "actual_output": self.actual_output,
            "retrieval_context": self.retrieval_context,
            "http_status": self.http_status,
            "raw_response": self.raw_response,
            "error": self.error,
            "latency_ms": self.latency_ms,
            "usage": self.usage,
        }


class BaseAdapter:
    """모든 통신원 클래스의 기본 뼈대입니다."""

    def __init__(self, target_url: str, api_key: str = None, auth_header: str = None):
        # 어댑터 공통 입력값입니다.
        # 실제 인증 헤더 조립 방식은 하위 어댑터가 결정하므로 원본만 저장합니다.
        self.target_url = target_url
        self.api_key = api_key
        self.auth_header = auth_header

    def invoke(
        self,
        input_text: str,
        history: Optional[List[Dict]] = None,
        **kwargs,
    ) -> UniversalEvalOutput:
        """
        모든 어댑터가 동일한 호출 규약을 따르도록 강제하는 인터페이스입니다.
        - input_text: 현재 턴 질문
        - history: 멀티턴 대화 이력
        - kwargs: 개별 어댑터 확장 입력
        """
        raise NotImplementedError("통신 방식을 구현하세요.")
