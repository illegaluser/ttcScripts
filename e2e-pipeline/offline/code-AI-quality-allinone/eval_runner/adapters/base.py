"""
base.py — AI 평가 어댑터 공통 인터페이스 및 데이터 모델

이 모듈은 eval_runner의 어댑터 계층에서 사용하는 두 가지 핵심 요소를 정의합니다:

1. UniversalEvalOutput: 어떤 방식(HTTP API / 웹 브라우저)으로 AI와 통신하든,
   평가 결과를 동일한 구조로 표현하기 위한 데이터 클래스입니다.
   test_runner.py의 평가 로직은 이 표준 출력만 다루므로,
   새로운 통신 방식이 추가되어도 평가 코드를 수정할 필요가 없습니다.

2. BaseAdapter: 모든 어댑터가 구현해야 하는 추상 인터페이스입니다.
   invoke() 메서드로 질문을 전송하고 UniversalEvalOutput을 반환합니다.
   close() 메서드로 세션 자원(브라우저 등)을 정리합니다.
"""

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
    tool_calls: List[Dict] = field(default_factory=list)
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
            "tool_calls": self.tool_calls,
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

    def close(self) -> None:
        """
        conversation 단위 자원을 정리하는 훅입니다.
        정리할 것이 없는 어댑터는 기본 구현을 그대로 사용하고, UI 어댑터는 브라우저 세션 해제에 사용합니다.
        """
        return None
