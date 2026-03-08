import os
import time
from typing import Dict, List, Optional

from .base import BaseAdapter, UniversalEvalOutput


class BrowserUIAdapter(BaseAdapter):
    """
    Playwright를 사용하여 웹 UI 기반의 에이전트를 평가하는 어댑터.
    """

    @staticmethod
    def _selectors() -> Dict[str, str]:
        return {
            "input": os.environ.get("UI_INPUT_SELECTOR", "textarea, input[type='text']"),
            "submit": os.environ.get("UI_SUBMIT_SELECTOR", ""),
            "response": os.environ.get("UI_RESPONSE_SELECTOR", ""),
        }

    def invoke(
        self,
        input_text: str,
        history: Optional[List[Dict]] = None,
        **kwargs,
    ) -> UniversalEvalOutput:
        start_time = time.time()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return UniversalEvalOutput(
                input=input_text,
                actual_output="",
                error="Playwright not installed",
                http_status=500,
            )

        try:
            selectors = self._selectors()
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(self.target_url, wait_until="networkidle")

                page.fill(selectors["input"], input_text)
                if selectors["submit"]:
                    page.click(selectors["submit"])
                else:
                    page.press(selectors["input"], "Enter")

                page.wait_for_timeout(int(os.environ.get("UI_RESPONSE_WAIT_MS", "3000")))

                actual_output = "Browser interaction success"
                if selectors["response"]:
                    response_locator = page.locator(selectors["response"]).last
                    response_locator.wait_for(timeout=int(os.environ.get("UI_RESPONSE_TIMEOUT_MS", "10000")))
                    extracted = response_locator.inner_text().strip()
                    if extracted:
                        actual_output = extracted
                else:
                    body_text = page.locator("body").inner_text().strip()
                    if body_text:
                        actual_output = body_text[-2000:]

                content = page.content()
                browser.close()

                return UniversalEvalOutput(
                    input=input_text,
                    actual_output=actual_output,
                    http_status=200,
                    raw_response=content[:2000],
                    latency_ms=int((time.time() - start_time) * 1000),
                )
        except Exception as exc:
            return UniversalEvalOutput(
                input=input_text,
                actual_output="",
                error=f"Browser Error: {exc}",
                http_status=500,
                latency_ms=int((time.time() - start_time) * 1000),
            )
