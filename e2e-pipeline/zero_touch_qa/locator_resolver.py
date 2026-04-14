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

    def _resolve_role(self, target_str: str) -> Locator | None:
        if not target_str.startswith("role="):
            return None
        m = re.match(r"role=(.+?),\s*name=(.+)", target_str)
        if m:
            return self.page.get_by_role(
                m.group(1).strip(), name=m.group(2).strip()
            ).first
        # role만 있고 name이 없는 경우
        role_only = target_str.replace("role=", "", 1).strip()
        if role_only:
            return self.page.get_by_role(role_only).first
        return None

    def _resolve_semantic_prefix(self, target_str: str) -> Locator | None:
        prefix_map = {
            "text=": self.page.get_by_text,
            "label=": self.page.get_by_label,
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
