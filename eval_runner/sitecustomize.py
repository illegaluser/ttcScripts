"""
deepeval==1.3.5 는 구 LangChain 경로인 ``langchain.schema`` 를 import 한다.
현재 Jenkins 이미지에는 LangChain 1.x 가 설치되어 있어 ``langchain.schema`` 가 없으므로,
Python 시작 시점에 최소 호환 shim 을 주입해 deepeval import 를 살린다.
"""

import sys
import types


def _install_langchain_schema_shim() -> None:
    if "langchain.schema" in sys.modules:
        return

    try:
        from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
    except Exception:
        return

    schema_module = types.ModuleType("langchain.schema")
    schema_module.AIMessage = AIMessage
    schema_module.BaseMessage = BaseMessage
    schema_module.HumanMessage = HumanMessage
    schema_module.SystemMessage = SystemMessage
    sys.modules["langchain.schema"] = schema_module


_install_langchain_schema_shim()
