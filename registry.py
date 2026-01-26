from typing import Dict, Type
from .base import BaseAdapter
from .http_adapter import GenericHttpAdapter

class AdapterRegistry:
    """
    다양한 벤더용 어댑터를 관리하고, 요청에 따라 인스턴스를 생성합니다.
    """
    _registry: Dict[str, Type[BaseAdapter]] = {}

    @classmethod
    def register(cls, name: str, adapter_cls: Type[BaseAdapter]):
        """새로운 어댑터 클래스를 등록합니다."""
        cls._registry[name] = adapter_cls

    @classmethod
    def get_instance(cls, name: str, target_url: str, api_key: str = None) -> BaseAdapter:
        """등록된 이름(TARGET_TYPE)에 해당하는 어댑터 객체를 반환합니다."""
        adapter_cls = cls._registry.get(name)
        if not adapter_cls:
            raise ValueError(f"등록되지 않은 어댑터 타입입니다: {name}")
        return adapter_cls(target_url=target_url, api_key=api_key)

# 기본 HTTP 어댑터 등록
AdapterRegistry.register("http", GenericHttpAdapter)