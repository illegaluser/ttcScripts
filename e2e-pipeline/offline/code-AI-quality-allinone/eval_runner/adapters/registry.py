"""
registry.py — 어댑터 팩토리 (문자열 → 어댑터 인스턴스 변환)

Jenkins 파이프라인에서 TARGET_TYPE 환경변수로 전달되는 문자열("http" 또는 "ui_chat")을
실제 어댑터 클래스(GenericHttpAdapter 또는 BrowserUIAdapter)로 변환합니다.
알 수 없는 타입이 입력되면 기본값으로 HTTP 어댑터를 사용합니다.
"""

from .browser_adapter import BrowserUIAdapter
from .http_adapter import GenericHttpAdapter


class AdapterRegistry:
    @classmethod
    def get_instance(
        cls,
        name: str,
        target_url: str,
        api_key: str = None,
        auth_header: str = None,
    ):
        """
        문자열 기반 어댑터 타입을 실제 구현 클래스로 바꿔 반환합니다.
        알 수 없는 타입이 들어와도 평가 전체를 멈추지 않도록 HTTP 어댑터를 기본값으로 둡니다.
        """
        adapter_map = {
            "http": GenericHttpAdapter,
            "ui_chat": BrowserUIAdapter,
        }
        adapter_class = adapter_map.get(name, GenericHttpAdapter)
        return adapter_class(target_url, api_key, auth_header)
