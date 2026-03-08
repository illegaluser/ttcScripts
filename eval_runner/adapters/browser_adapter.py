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
        """
        UI 자동화에 사용할 셀렉터를 환경변수에서 읽습니다.
        사이트마다 DOM 구조가 달라질 수 있으므로 코드 수정 없이 조정할 수 있게 합니다.
        """
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
        """
        브라우저를 열어 질문을 입력하고 화면에서 보이는 답변을 수집합니다.
        UI마다 구조가 제각각이므로 기본 전략은 단순하게 두고 셀렉터로 보정합니다.
        """
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
                # CI/Jenkins 환경을 고려해 headless Chromium을 사용합니다.
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(self.target_url, wait_until="networkidle")

                # 질문 입력 후, 전송 버튼이 있으면 클릭하고 없으면 Enter를 사용합니다.
                page.fill(selectors["input"], input_text)
                if selectors["submit"]:
                    page.click(selectors["submit"])
                else:
                    page.press(selectors["input"], "Enter")

                # 답변이 DOM에 반영될 시간을 기본적으로 잠시 대기합니다.
                page.wait_for_timeout(int(os.environ.get("UI_RESPONSE_WAIT_MS", "3000")))

                actual_output = "Browser interaction success"
                if selectors["response"]:
                    # 응답 셀렉터가 있으면 마지막 응답만 골라 실제 답변으로 사용합니다.
                    response_locator = page.locator(selectors["response"]).last
                    response_locator.wait_for(timeout=int(os.environ.get("UI_RESPONSE_TIMEOUT_MS", "10000")))
                    extracted = response_locator.inner_text().strip()
                    if extracted:
                        actual_output = extracted
                else:
                    # 셀렉터가 없으면 body 텍스트 일부를 백업 출력으로 사용합니다.
                    body_text = page.locator("body").inner_text().strip()
                    if body_text:
                        actual_output = body_text[-2000:]

                # raw_response에는 디버깅 가능한 HTML 스냅샷을 짧게 저장합니다.
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
            # UI 자동화 실패도 표준 구조로 감싸 상위 테스트가 동일하게 처리할 수 있게 합니다.
            return UniversalEvalOutput(
                input=input_text,
                actual_output="",
                error=f"Browser Error: {exc}",
                http_status=500,
                latency_ms=int((time.time() - start_time) * 1000),
            )
