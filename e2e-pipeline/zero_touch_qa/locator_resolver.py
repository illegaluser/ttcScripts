import re
import logging

from playwright.sync_api import Page, Locator

log = logging.getLogger(__name__)


class LocatorResolver:
    """
    Dify가 생성한 target을 Playwright Locator로 변환하는 7단계 시맨틱 탐색 엔진.

    탐색 순서:
      1. role + name   (접근성 역할 기반, 가장 안정적)
      2. text          (화면 표시 텍스트)
      3. label         (입력 폼 라벨)
      4. placeholder   (입력 필드 힌트)
      5. testid        (data-testid 속성)
      6. CSS / XPath   (구조적 폴백)
      7. 존재 검증     (count > 0 확인 후 반환, 실패 시 None)
    """

    def __init__(self, page: Page):
        self.page = page

    def resolve(self, target) -> Locator | None:
        """target을 Locator로 변환한다. 실패 시 None."""
        if not target:
            return None

        # Dict 타겟 (Dify가 JSON 객체로 보낸 경우)
        if isinstance(target, dict):
            return self._resolve_dict(target)

        target_str = str(target).strip()

        # 1단계: role + name
        loc = self._resolve_role(target_str)
        if loc:
            return loc

        # 2~5단계: 시맨틱 접두사
        loc = self._resolve_semantic_prefix(target_str)
        if loc:
            return loc

        # 6~7단계: CSS/XPath + 존재 검증
        return self._resolve_css_xpath(target_str)

    def _resolve_dict(self, target: dict) -> Locator | None:
        if target.get("role"):
            return self.page.get_by_role(
                target["role"], name=target.get("name", "")
            ).first
        if target.get("label"):
            return self.page.get_by_label(target["label"]).first
        if target.get("text"):
            return self.page.get_by_text(target["text"]).first
        if target.get("placeholder"):
            return self.page.get_by_placeholder(target["placeholder"]).first
        if target.get("testid"):
            return self.page.get_by_test_id(target["testid"]).first
        # 폴백: selector 키 또는 문자열 변환
        fallback = target.get("selector", str(target))
        return self._resolve_css_xpath(str(fallback).strip())

    # [변경] ROLE_ALIASES 딕셔너리 추가
    # 원본: role 문자열을 그대로 get_by_role() 에 전달 → 실제 DOM role 과 불일치 시 None
    # 사유: LLM 이 "role=searchbox" 로 타겟을 생성하지만 Google 검색창의 실제 DOM 은
    #       role="combobox" 이다. LLM 의 추측(searchbox)과 실제 DOM(combobox) 간 격차를
    #       ROLE_ALIASES 로 자동 매핑하여 추가 LLM 호출 없이 해결한다.
    #       button↔link 도 동일하게 상호 폴백한다.
    ROLE_ALIASES = {
        "searchbox": ["searchbox", "combobox"],
        "combobox":  ["combobox", "searchbox"],
        "button":    ["button", "link"],
        "link":      ["link", "button"],
    }

    def _resolve_role(self, target_str: str) -> Locator | None:
        if not target_str.startswith("role="):
            return None
        m = re.match(r"role=(.+?),\s*name=(.+)", target_str)
        if m:
            role = m.group(1).strip()
            name = m.group(2).strip()
            # [변경] 단일 role → ROLE_ALIASES 로 순차 시도
            # 원본: get_by_role(role, name=name) 1회 호출 → 불일치 시 None
            # 사유: 위 ROLE_ALIASES 참조
            for alias in self.ROLE_ALIASES.get(role, [role]):
                try:
                    loc = self.page.get_by_role(alias, name=name)
                    if loc.count() > 0:
                        return loc.first
                except Exception:
                    continue
            return None
        # role만 있고 name이 없는 경우
        role_only = target_str.replace("role=", "", 1).strip()
        for alias in self.ROLE_ALIASES.get(role_only, [role_only]):
            try:
                loc = self.page.get_by_role(alias)
                if loc.count() > 0:
                    return loc.first
            except Exception:
                continue
        return None

    def _resolve_semantic_prefix(self, target_str: str) -> Locator | None:
        if target_str.startswith("label="):
            value = target_str.replace("label=", "", 1).strip()
            # [변경] 편집 가능한 요소 우선 필터링 추가
            # 원본: self.page.get_by_label(value).first — 매칭되는 첫 번째 요소 반환
            # 사유: Playwright get_by_label() 은 <label> 텍스트뿐 아니라 aria-label 속성도
            #       매칭한다. Google 검색 페이지에서 label="검색" 을 조회하면
            #       실제 입력창보다 먼저 "이미지 검색" 아이콘(img 태그, aria-label="검색")이
            #       매칭되어 fill() 이 실패했다.
            #       input/textarea/select/[contenteditable] 로 필터를 걸어 편집 가능한
            #       요소를 우선 반환하도록 변경했다.
            editable = "input, textarea, select, [contenteditable='true']"
            try:
                filtered = self.page.get_by_label(value).filter(
                    has=self.page.locator(editable)
                )
                if filtered.count() > 0:
                    return filtered.first
            except Exception:
                pass
            loc = self.page.get_by_label(value)
            if loc.count() > 0:
                return loc.first
            return None

        prefix_map = {
            "text=": self.page.get_by_text,
            "placeholder=": self.page.get_by_placeholder,
            "testid=": self.page.get_by_test_id,
        }
        for prefix, method in prefix_map.items():
            if target_str.startswith(prefix):
                value = target_str.replace(prefix, "", 1).strip()
                return method(value).first
        return None

    def _resolve_css_xpath(self, target_str: str) -> Locator | None:
        try:
            loc = self.page.locator(target_str)
            if loc.count() > 0:
                return loc.first
        except Exception:
            log.debug("CSS/XPath 탐색 실패: %s", target_str)
        return None
