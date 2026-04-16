import re
import difflib
import logging

from playwright.sync_api import Page, Locator

log = logging.getLogger(__name__)


class LocalHealer:
    """
    LLM 호출 없이(비용 0) 현재 페이지의 DOM을 스캔하여
    실패한 타겟과 가장 유사한 요소를 찾아 반환한다.

    치유 순서:
      1. role 유사 매칭 — ``role=searchbox`` 실패 시 ``combobox``, ``textbox`` 등 시도
      2. 텍스트 유사도 — ``difflib.SequenceMatcher`` 로 DOM 텍스트 비교

    액션별 검색 대상:
      - fill/press:     input, textarea, [role='textbox'], [role='searchbox'], [contenteditable]
      - select:         select, [role='listbox'], [role='combobox'], option, [role='option']
      - hover:          button, a, [role='menuitem'], [role='tab'], nav a, [aria-haspopup]
      - click/check 등: button, a, [role='button'], [role='link'], [role='menuitem'], [role='tab']
    """

    # 의미적으로 유사한 ARIA role 매핑 (LLM 이 잘못된 role 을 지정했을 때 사용)
    _ROLE_GROUPS: dict[str, list[str]] = {
        "searchbox": ["combobox", "textbox"],
        "textbox": ["combobox", "searchbox"],
        "combobox": ["searchbox", "textbox", "listbox"],
        "listbox": ["combobox"],
        "button": ["link", "menuitem"],
        "link": ["button", "menuitem"],
        "menuitem": ["button", "link", "tab"],
        "tab": ["menuitem", "button"],
    }

    SELECTOR_MAP = {
        "fill": (
            "input, textarea, [role='textbox'], [role='searchbox'], "
            "[contenteditable='true']"
        ),
        "press": (
            "input, textarea, [role='textbox'], [role='searchbox'], "
            "[contenteditable='true']"
        ),
        "select": (
            "select, [role='listbox'], [role='combobox'], "
            "option, [role='option']"
        ),
        "hover": (
            "button, a, [role='button'], [role='link'], "
            "[role='menuitem'], [role='tab'], [role='menu'], "
            "nav a, [aria-haspopup], [role='tooltip']"
        ),
    }

    DEFAULT_SELECTOR = (
        "button, a, [role='button'], [role='link'], "
        "[role='menuitem'], [role='tab']"
    )

    def __init__(self, page: Page, threshold: float = 0.8):
        self.page = page
        self.threshold = threshold

    def try_heal(self, step: dict) -> Locator | None:
        """step 의 target 과 유사한 요소를 현재 페이지 DOM 에서 검색한다.

        2단계 매칭:
          1. ``role=`` 타겟이면 의미적으로 유사한 ARIA role 로 재시도.
          2. ``difflib.SequenceMatcher`` 로 문자열 유사도 매칭.

        Args:
            step: DSL 스텝 dict. ``action`` 과 ``target`` 키가 필요하다.

        Returns:
            매칭에 성공한 Playwright Locator. 실패 시 ``None``.
        """
        action = step["action"].lower()
        target = step.get("target", "")

        # ── 1단계: role 유사 매칭 ──
        loc = self._try_role_fallback(target)
        if loc:
            return loc

        # ── 2단계: 텍스트 유사도 매칭 ──
        selector = self.SELECTOR_MAP.get(action, self.DEFAULT_SELECTOR)
        clean_target = self._clean_target(target)
        if len(clean_target) <= 1:
            return None

        best_match = None
        highest_ratio = 0.0

        for el in self.page.locator(selector).all():
            text = self._extract_text(el)
            if not text:
                continue
            ratio = difflib.SequenceMatcher(None, clean_target, text).ratio()
            if ratio > self.threshold and ratio > highest_ratio:
                highest_ratio = ratio
                best_match = el

        if best_match:
            log.info("  [로컬복구 성공] 유사도 %.0f%% 매칭", highest_ratio * 100)
        return best_match

    def _try_role_fallback(self, target) -> Locator | None:
        """``role=`` 타겟 실패 시 의미적으로 유사한 ARIA role 로 재시도한다.

        예: ``role=searchbox`` → ``combobox``, ``textbox`` 순으로 시도.
        Google 검색창처럼 LLM 이 ``searchbox`` 를 지정했지만
        실제 요소가 ``combobox`` 인 경우를 처리한다.

        Args:
            target: DSL 스텝의 target 문자열.

        Returns:
            매칭된 Locator. 유사 role 이 없으면 ``None``.
        """
        target_str = str(target).strip()
        if not target_str.startswith("role="):
            return None

        m = re.match(r"role=(.+?)(?:,\s*name=(.+))?$", target_str)
        if not m:
            return None

        role = m.group(1).strip()
        name = m.group(2).strip() if m.group(2) else None

        for fb_role in self._ROLE_GROUPS.get(role, []):
            try:
                if name:
                    loc = self.page.get_by_role(fb_role, name=name)
                else:
                    loc = self.page.get_by_role(fb_role)
                if loc.count() > 0:
                    log.info(
                        "  [로컬복구 성공] role 유사 매칭: %s → %s",
                        role, fb_role,
                    )
                    return loc.first
            except Exception:
                continue

        return None

    @staticmethod
    def _clean_target(target) -> str:
        """시맨틱 접두사(text=, role=, label= 등)를 제거하여 순수 비교 텍스트를 추출한다.

        Args:
            target: DSL target 문자열 또는 기타 객체.

        Returns:
            접두사가 제거된 순수 텍스트. 예: ``"role=button, name=확인"`` → ``"확인"``.
        """
        s = re.sub(r"^(text|role|label|placeholder|testid)=", "", str(target))
        s = re.sub(r"role=.+?,\s*name=", "", s)
        return s.strip()

    @staticmethod
    def _extract_text(el) -> str:
        """DOM 요소에서 비교용 텍스트를 추출한다.

        우선순위: inner_text → placeholder → value → aria-label.
        어떤 방법으로도 실패하면 빈 문자열을 반환한다.

        Args:
            el: Playwright Locator (DOM 요소).

        Returns:
            추출된 텍스트. 실패 시 빈 문자열.
        """
        try:
            return (
                el.inner_text()
                or el.get_attribute("placeholder")
                or el.get_attribute("value")
                or el.get_attribute("aria-label")
                or ""
            ).strip()
        except Exception:
            return ""
