import time
from .base import BaseAdapter, UniversalEvalOutput

class BrowserUIAdapter(BaseAdapter):
    """
    Playwright를 사용하여 웹 UI 기반의 에이전트를 평가하는 어댑터.
    API가 제공되지 않는 경우 사용하며, 대상 사이트의 DOM 구조에 따라 Selector 수정이 필요할 수 있음.
    """
    def invoke(self, input_text: str, **kwargs) -> UniversalEvalOutput:
        # 런타임에만 Playwright 의존성 필요
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return UniversalEvalOutput(
                input=input_text,
                actual_output="",
                error="Playwright not installed. Run: pip install playwright && playwright install",
                http_status=500
            )

        start_time = time.time()
        
        try:
            with sync_playwright() as p:
                # 브라우저 실행 (CI 환경에서는 headless=True 필수)
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                
                # 1. 대상 URL 접속
                page.goto(self.target_url)
                page.wait_for_load_state("networkidle")

                # 2. 질문 입력 (일반적인 챗봇 입력창 Selector 예시)
                # 실제 운영 시: page.fill("#specific-input-id", input_text) 형태로 구체화 권장
                input_selector = "textarea, input[type='text']"
                if page.is_visible(input_selector):
                    page.fill(input_selector, input_text)
                    page.press(input_selector, "Enter")
                else:
                    raise Exception(f"Input selector '{input_selector}' not found on {self.target_url}")

                # 3. 답변 대기 (단순 대기보다는 특정 요소 출현 대기가 정확함)
                time.sleep(3) 

                # 4. 답변 스크래핑 (마지막 메시지 추출)
                # 실제 운영 시: .bot-message 등 구체적인 클래스명 사용 권장
                content = page.content()
                actual_output = "Browser interaction success (Check raw_response for details)"
                
                return UniversalEvalOutput(
                    input=input_text,
                    actual_output=actual_output,
                    http_status=200,
                    raw_response=content[:2000],  # 디버깅용 HTML 스냅샷
                    latency_ms=int((time.time() - start_time) * 1000)
                )
        except Exception as e:
            return UniversalEvalOutput(
                input=input_text,
                actual_output="",
                error=f"Browser Error: {str(e)}",
                http_status=500,
                latency_ms=int((time.time() - start_time) * 1000)
            )
