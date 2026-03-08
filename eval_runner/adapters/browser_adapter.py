import time
from .base import BaseAdapter, UniversalEvalOutput
from typing import List, Dict, Optional

class BrowserUIAdapter(BaseAdapter):
    """
    Playwright를 사용하여 웹 UI 기반의 에이전트를 평가하는 어댑터.
    """
    def invoke(self, input_text: str, history: Optional[List[Dict]] = None, **kwargs) -> UniversalEvalOutput:
        start_time = time.time()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return UniversalEvalOutput(input=input_text, actual_output="", error="Playwright not installed", http_status=500)

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(self.target_url, wait_until="networkidle")

                input_selector = "textarea, input[type='text']"
                page.fill(input_selector, input_text)
                page.press(input_selector, "Enter")
                
                time.sleep(3) # 답변 생성 대기 (실제 환경에서는 더 정교한 대기 필요)
                
                content = page.content()
                actual_output = "Browser interaction success"
                
                return UniversalEvalOutput(
                    input=input_text, actual_output=actual_output, http_status=200,
                    raw_response=content[:2000], latency_ms=int((time.time() - start_time) * 1000)
                )
        except Exception as e:
            return UniversalEvalOutput(
                input=input_text, actual_output="", error=f"Browser Error: {e}",
                http_status=500, latency_ms=int((time.time() - start_time) * 1000)
            )