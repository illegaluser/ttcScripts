from typing import Dict, Type
from .base import BaseAdapter
from .http_adapter import GenericHttpAdapter
from .browser_adapter import BrowserUIAdapter

class AdapterRegistry:
    _registry: Dict[str, Type[BaseAdapter]] = {}

    @classmethod
    def register(cls, name: str, adapter_cls: Type[BaseAdapter]):
        cls._registry[name] = adapter_cls

    @classmethod
    def get_instance(cls, name: str, target_url: str, api_key: str = None) -> BaseAdapter:
        adapter_cls = cls._registry.get(name)
        if not adapter_cls:
            raise ValueError(
                f"Unknown TARGET_TYPE(adapter): {name}. Registered: {list(cls._registry.keys())}"
            )
        return adapter_cls(target_url=target_url, api_key=api_key)

# 기본 HTTP 어댑터 등록
AdapterRegistry.register("http", GenericHttpAdapter)
AdapterRegistry.register("browser", BrowserUIAdapter)