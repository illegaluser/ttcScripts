from .http_adapter import GenericHttpAdapter
from .browser_adapter import BrowserUIAdapter

class AdapterRegistry:
    @classmethod
    def get_instance(cls, name: str, target_url: str, api_key: str = None):
        adapter_map = {
            "http": GenericHttpAdapter,
            "ui_chat": BrowserUIAdapter 
        }
        adapter_class = adapter_map.get(name, GenericHttpAdapter)
        return adapter_class(target_url, api_key)