"""
browser_adapter.py — 웹 브라우저 UI 기반 AI 에이전트 평가 어댑터

평가 대상 AI가 웹 채팅 UI(예: Dify 챗봇, 사내 AI 어시스턴트)인 경우 이 어댑터를 사용합니다.
실제 사용자와 동일하게 브라우저에서 질문을 입력하고 화면에 표시되는 답변을 수집합니다.

[주요 기능]
- Playwright 기반 브라우저 자동화 (Chromium headless)
- 멀티턴 대화를 위한 세션 유지: 같은 conversation 동안 동일한 브라우저 탭 유지
- 환경변수 기반 셀렉터 설정: 사이트마다 다른 DOM 구조에 코드 수정 없이 대응
- 입력(textarea) → 전송(버튼/Enter) → 응답 대기 → 텍스트 추출의 자연스러운 흐름

[환경변수 설정]
- UI_INPUT_SELECTOR: 질문 입력 필드 CSS 셀렉터 (기본: "textarea, input[type='text']")
- UI_SUBMIT_SELECTOR: 전송 버튼 CSS 셀렉터 (빈 값이면 Enter 키 사용)
- UI_RESPONSE_SELECTOR: AI 응답 영역 CSS 셀렉터 (빈 값이면 body 텍스트 사용)
- UI_RESPONSE_WAIT_MS: 응답 대기 시간 (기본: 3000ms)
- UI_RESPONSE_TIMEOUT_MS: 응답 타임아웃 (기본: 10000ms)
"""

import os
import time
from typing import Dict, List, Optional

from .base import BaseAdapter, UniversalEvalOutput


class BrowserUIAdapter(BaseAdapter):
    """
    Playwright를 사용하여 웹 UI 기반의 에이전트를 평가하는 어댑터.
    """

    def __init__(self, target_url: str, api_key: str = None, auth_header: str = None):
        """
        UI 다중턴 검증에서는 같은 대화 동안 브라우저 세션을 유지해야 하므로,
        Playwright 객체를 인스턴스 상태로 보관할 준비를 합니다.
        """
        super().__init__(target_url, api_key, auth_header)
        self._playwright = None
        self._browser = None
        self._page = None

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

    def _ensure_session(self):
        """
        conversation 전체에서 동일한 브라우저/페이지를 재사용합니다.
        이 동작이 있어야 웹 UI 대상도 이전 턴 문맥을 실제 서비스 쪽에 유지할 수 있습니다.
        """
        if self._page is not None:
            return self._page

        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        self._page = self._browser.new_page()
        self._page.goto(self.target_url, wait_until="networkidle")
        return self._page

    def close(self) -> None:
        """
        conversation 종료 시 브라우저 자원을 정리합니다.
        test_runner가 finally에서 호출해 세션 누수를 막습니다.
        """
        if self._page is not None:
            self._page.close()
            self._page = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

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
            page = self._ensure_session()

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
