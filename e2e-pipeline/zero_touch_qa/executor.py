import os
import random
import re
import time
import logging
from dataclasses import dataclass, field

from playwright.sync_api import sync_playwright, Page, Locator

from .config import Config
from .dify_client import DifyClient, DifyConnectionError
from .locator_resolver import LocatorResolver
from .local_healer import LocalHealer

log = logging.getLogger(__name__)


@dataclass
class StepResult:
    """단일 DSL 스텝의 실행 결과를 담는 데이터클래스.

    Attributes:
        step_id: 시나리오 내 스텝 번호 또는 식별자.
        action: 수행된 DSL 액션 이름 (click, fill, navigate 등).
        target: 실제로 사용된 로케이터 문자열.
        value: 액션에 전달된 값 (입력 텍스트, URL, 키 이름 등).
        description: 스텝에 대한 사람이 읽을 수 있는 설명.
        status: 실행 결과. ``"PASS"`` | ``"HEALED"`` | ``"FAIL"`` | ``"SKIP"``.
        heal_stage: 치유 성공 시 어느 단계에서 복구되었는지. ``"none"`` | ``"fallback"`` | ``"local"`` | ``"dify"``.
        timestamp: 스텝 실행 시각 (Unix epoch).
        screenshot_path: 스크린샷 파일 경로. 없으면 ``None``.
    """

    step_id: int | str
    action: str
    target: str
    value: str
    description: str
    status: str  # "PASS" | "HEALED" | "FAIL" | "SKIP"
    heal_stage: str = "none"  # "none" | "fallback" | "local" | "dify"
    timestamp: float = field(default_factory=time.time)
    screenshot_path: str | None = None


class QAExecutor:
    """
    DSL 시나리오를 받아 실행하고, 3단계 하이브리드 자가 치유를 수행한다.

    치유 루프:
      1. fallback_targets 순회 (무비용)
      2. LocalHealer DOM 유사도 매칭
      3. DifyClient LLM 치유
    """

    def __init__(self, config: Config):
        self.config = config
        self.dify = DifyClient(config)

    def execute(
        self, scenario: list[dict], headed: bool = True
    ) -> list[StepResult]:
        """Playwright 브라우저를 실행하고 DSL 시나리오를 순차 실행한다.

        Args:
            scenario: DSL 스텝 dict 의 리스트.
            headed: True 면 브라우저 창을 표시, False 면 headless.

        Returns:
            각 스텝의 실행 결과 ``StepResult`` 리스트. FAIL 발생 시 이후 스텝은 포함되지 않는다.
        """
        results: list[StepResult] = []
        artifacts = self.config.artifacts_dir
        os.makedirs(artifacts, exist_ok=True)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not headed, slow_mo=self.config.slow_mo)
            page = browser.new_page(
                locale="ko-KR",
                viewport={
                    "width": self.config.viewport[0],
                    "height": self.config.viewport[1],
                },
            )
            resolver = LocatorResolver(page)
            healer = LocalHealer(page, self.config.heal_threshold)

            try:
                for idx, step in enumerate(scenario):
                    result = self._execute_step(
                        page, step, resolver, healer, artifacts
                    )
                    results.append(result)
                    if result.status == "FAIL":
                        # 최종 실패 스크린샷
                        fail_path = os.path.join(artifacts, "error_final.png")
                        self._safe_screenshot(page, fail_path)
                        break
                    # 스텝 간 random jitter — 봇 패턴(즉시 연속 액션) 회피.
                    # reCAPTCHA 등이 fill→press 100ms 이내 시퀀스를 트리거.
                    # 마지막 스텝 또는 max==0 이면 sleep 생략.
                    if (
                        idx < len(scenario) - 1
                        and self.config.step_interval_max_ms > 0
                    ):
                        jitter_s = random.uniform(
                            self.config.step_interval_min_ms,
                            self.config.step_interval_max_ms,
                        ) / 1000.0
                        time.sleep(jitter_s)
            finally:
                browser.close()

        return results

    def _execute_step(
        self,
        page: Page,
        step: dict,
        resolver: LocatorResolver,
        healer: LocalHealer,
        artifacts: str,
    ) -> StepResult:
        """단일 스텝을 실행하고 결과를 반환한다.

        3단계 자가 치유 순서: 1) fallback_targets → 2) LocalHealer DOM 유사도 → 3) Dify LLM.
        """
        action = step["action"].lower()
        step_id = step.get("step", "-")
        desc = step.get("description", "")

        # ── 메타 액션 (타겟 불필요) ──
        if action in ("navigate", "maps"):
            raw_url = step.get("value") or step.get("target", "")
            url = self._normalize_url(str(raw_url))
            if url != str(raw_url):
                log.info("[Step %s] URL 자동 normalize: %r → %r", step_id, raw_url, url)
            page.goto(url)
            page.wait_for_load_state("domcontentloaded")
            ss = self._screenshot(page, artifacts, step_id, "pass")
            log.info("[Step %s] navigate -> PASS", step_id)
            return StepResult(
                step_id, action, str(url), str(url), desc,
                "PASS", screenshot_path=ss,
            )

        if action == "wait":
            ms = int(step.get("value", 1000))
            page.wait_for_timeout(ms)
            log.info("[Step %s] wait %dms -> PASS", step_id, ms)
            return StepResult(step_id, action, "", str(ms), desc, "PASS")

        # ── LLM 출력 보정 ──
        self._normalize_step(step)
        action = step["action"].lower()

        # ── press + 타겟 없음: 페이지 전체에 키 입력 ──
        if action == "press" and not step.get("target"):
            key = step.get("value", "")
            page.keyboard.press(key)
            ss = self._screenshot(page, artifacts, step_id, "pass")
            log.info("[Step %s] press '%s' (keyboard) -> PASS", step_id, key)
            return StepResult(
                step_id, action, "", key, desc,
                "PASS", screenshot_path=ss,
            )

        # ── 타겟 필요 액션: 실행 + 다단계 자가 치유 ──
        log.info("[Step %s] %s: %s", step_id, action, desc)
        original_target = step.get("target")

        # 1차 시도: 기본 타겟 (Resolver 가 healed_aliases 를 자동 적용)
        locator = resolver.resolve(original_target)
        if locator:
            try:
                self._perform_action(page, locator, step)
                ss = self._screenshot(page, artifacts, step_id, "pass")
                return StepResult(
                    step_id, action, str(original_target or ""),
                    str(step.get("value", "")), desc,
                    "PASS", screenshot_path=ss,
                )
            except Exception as e:
                log.warning("[Step %s] 기본 타겟 실패: %s", step_id, e)

        # ── [치유 1단계] fallback_targets ──
        for fb_target in step.get("fallback_targets", []):
            fb_loc = resolver.resolve(fb_target)
            if fb_loc:
                try:
                    self._perform_action(page, fb_loc, step)
                    # A: 후속 스텝이 같은 target 을 만나면 즉시 fb_target 사용
                    resolver.record_alias(original_target, fb_target)
                    ss = self._screenshot(page, artifacts, step_id, "healed")
                    log.info("[Step %s] fallback 복구 성공: %s", step_id, fb_target)
                    return StepResult(
                        step_id, action, str(fb_target),
                        str(step.get("value", "")), desc,
                        "HEALED", heal_stage="fallback", screenshot_path=ss,
                    )
                except Exception:
                    continue

        # ── [치유 2단계] DSL action_alternatives (C) ──
        # Planner LLM 이 명시한 등가 액션 (예: press Enter → click 검색버튼).
        # LocalHealer/Dify heal 보다 먼저 시도 — 명시 의도가 가장 신뢰도 높음.
        for alt in step.get("action_alternatives", []) or []:
            if not isinstance(alt, dict) or not alt.get("action"):
                continue
            alt_step = {**step, **alt}
            self._normalize_step(alt_step)
            alt_loc = resolver.resolve(alt_step.get("target"))
            if not alt_loc:
                continue
            try:
                self._perform_action(page, alt_loc, alt_step)
                ss = self._screenshot(page, artifacts, step_id, "healed")
                log.info(
                    "[Step %s] action_alternatives 복구 성공: %s %s",
                    step_id, alt_step.get("action"), alt_step.get("target"),
                )
                return StepResult(
                    step_id, alt_step.get("action", action),
                    str(alt_step.get("target", "")),
                    str(alt_step.get("value", "")), desc,
                    "HEALED", heal_stage="alternative", screenshot_path=ss,
                )
            except Exception:
                continue

        # ── [치유 3단계] 로컬 DOM 유사도 매칭 ──
        healed_loc = healer.try_heal(step)
        if healed_loc:
            try:
                self._perform_action(page, healed_loc, step)
                ss = self._screenshot(page, artifacts, step_id, "healed")
                return StepResult(
                    step_id, action, str(original_target or ""),
                    str(step.get("value", "")), desc,
                    "HEALED", heal_stage="local", screenshot_path=ss,
                )
            except Exception as e:
                log.warning("[Step %s] 로컬 치유 실행 실패: %s", step_id, e)

        # ── [치유 4단계] Dify LLM 치유 (timeout 단축, retry 0) ──
        log.info("[Step %s] Dify LLM 치유 요청 중 (timeout=%ds)...",
                 step_id, self.config.heal_timeout_sec)
        try:
            dom_snapshot = page.content()[: self.config.dom_snapshot_limit]
            new_target_info = self.dify.request_healing(
                error_msg=f"요소 탐색/실행 실패: {original_target}",
                dom_snapshot=dom_snapshot,
                failed_step=step,
            )
        except DifyConnectionError as e:
            log.error("[Step %s] Dify 치유 통신 실패: %s", step_id, e)
            new_target_info = None

        if new_target_info:
            step.update(new_target_info)
            healed_loc = resolver.resolve(step.get("target"))
            if healed_loc:
                try:
                    self._perform_action(page, healed_loc, step)
                    resolver.record_alias(original_target, step.get("target"))
                    ss = self._screenshot(page, artifacts, step_id, "healed")
                    log.info(
                        "[Step %s] LLM 치유 성공. 새 타겟: %s",
                        step_id, step.get("target"),
                    )
                    return StepResult(
                        step_id, action, str(step.get("target", "")),
                        str(step.get("value", "")), desc,
                        "HEALED", heal_stage="dify", screenshot_path=ss,
                    )
                except Exception as e:
                    log.error("[Step %s] LLM 치유 후 실행 실패: %s", step_id, e)

        # ── [치유 5단계] press(Enter/Return) 휴리스틱 — 검색버튼 click (B) ──
        # 사람이라면 엔터 안 먹을 때 검색버튼을 누른다. 이 마지막 안전망이
        # Naver/Google 류 검색 페이지에서 가장 자주 PASS 를 살린다.
        if action == "press" and str(step.get("value", "")).lower() in ("enter", "return"):
            for sel in self._SEARCH_BUTTON_CANDIDATES:
                try:
                    btn = page.locator(sel)
                    if btn.count() == 0:
                        continue
                    btn.first.click(timeout=3000)
                    ss = self._screenshot(page, artifacts, step_id, "healed")
                    log.info("[Step %s] press→click 휴리스틱 성공: %s", step_id, sel)
                    return StepResult(
                        step_id, "click", sel, "",
                        desc, "HEALED",
                        heal_stage="press_to_click", screenshot_path=ss,
                    )
                except Exception:
                    continue

        # ── 모든 치유 실패 ──
        log.error("[Step %s] FAIL — 모든 치유 실패", step_id)
        return StepResult(
            step_id, action, str(original_target or ""),
            str(step.get("value", "")), desc,
            "FAIL",
        )

    # B: press(Enter) 가 모든 치유 다 실패했을 때 click 으로 시도해볼 검색/제출 버튼 후보.
    # 가시성 필터와 한/영 라벨을 함께 고려. 우선순위는 좁은 것 → 넓은 것 순.
    _SEARCH_BUTTON_CANDIDATES = (
        "form[role=search] button:visible, [role=search] button:visible",
        "button[type=submit]:visible",
        "button[aria-label*='검색']:visible, button[aria-label*='Search' i]:visible",
        "button:has-text(/^(검색|Search|검색하기|Go|확인|Submit)$/i):visible",
        "[role=button]:has-text(/^(검색|Search|검색하기)$/i):visible",
    )

    # ── LLM 출력 보정 ──
    KNOWN_KEYS = {
        "enter", "tab", "escape", "backspace", "delete", "arrowup",
        "arrowdown", "arrowleft", "arrowright", "space", "home", "end",
        "pageup", "pagedown", "f1", "f2", "f3", "f4", "f5", "f6",
        "f7", "f8", "f9", "f10", "f11", "f12",
    }

    # 사설망/로컬 IP 패턴 — 자동 normalize 시 https 가 아닌 http 적용
    _LOCAL_HOST_PREFIXES = ("localhost", "127.", "0.0.0.0", "10.", "192.168.", "172.16.",
                            "172.17.", "172.18.", "172.19.", "172.20.", "172.21.",
                            "172.22.", "172.23.", "172.24.", "172.25.", "172.26.",
                            "172.27.", "172.28.", "172.29.", "172.30.", "172.31.")
    _IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}(:\d+)?(/.*)?$")

    @staticmethod
    def _normalize_url(raw: str) -> str:
        """스킴 없는 URL 에 자동으로 https:// (또는 로컬은 http://) 를 붙인다.

        사용자가 Jenkins 파라미터에 ``www.naver.com`` 만 넣거나 LLM 이 스킴 없이
        반환해도 ``page.goto()`` 의 'invalid URL' 에러를 막는다.

        Examples:
            >>> QAExecutor._normalize_url("www.naver.com")
            'https://www.naver.com'
            >>> QAExecutor._normalize_url("localhost:3000")
            'http://localhost:3000'
            >>> QAExecutor._normalize_url("https://x.com")
            'https://x.com'
        """
        url = (raw or "").strip()
        if not url:
            return url
        if url.startswith(("http://", "https://", "file://", "data:", "about:")):
            return url
        if url.startswith("//"):
            return "https:" + url
        lower = url.lower()
        if lower.startswith(QAExecutor._LOCAL_HOST_PREFIXES) or QAExecutor._IPV4_RE.match(lower):
            return "http://" + url
        return "https://" + url

    @staticmethod
    def _normalize_step(step: dict):
        """
        LLM이 생성한 DSL 스텝의 흔한 오류를 자동 보정한다.
        - press: target에 키 이름이 들어가고 value가 비어 있는 경우 swap
        - navigate: value가 비고 target에 URL이 있는 경우 swap
        """
        action = step.get("action", "").lower()
        target = str(step.get("target", "")).strip()
        value = str(step.get("value", "")).strip()

        if action == "press" and not value and target.lower() in QAExecutor.KNOWN_KEYS:
            step["value"] = target
            step["target"] = ""
            log.debug("[보정] press: target '%s' → value로 이동", target)

        # navigate 의 흔한 LLM 실수: URL 을 target 에 넣음.
        # 스킴 없어도 'foo.com', 'localhost:3000' 등 URL 같으면 swap.
        if action == "navigate" and not value and target:
            host_part = target.split("/", 1)[0].split("?", 1)[0]
            looks_url = (
                target.startswith(("http://", "https://", "//"))
                or "." in host_part
                or host_part.startswith("localhost")
            )
            if looks_url:
                step["value"] = target
                step["target"] = ""
                log.debug("[보정] navigate: target → value로 이동")

    # ── 9대 DSL 액션 수행 ──
    @staticmethod
    def _perform_action(page: Page, locator: Locator, step: dict):
        """9대 DSL 액션(click, fill, press, select, check, hover, verify, navigate, wait)을 수행한다.

        Args:
            page: Playwright Page (verify 에서 사용).
            locator: 대상 요소의 Playwright Locator.
            step: DSL 스텝 dict. ``action`` 과 ``value`` 키를 참조한다.

        Raises:
            ValueError: 미지원 액션일 때.
            AssertionError: verify 액션에서 조건 불일치 시.
        """
        action = step["action"].lower()
        value = step.get("value", "")

        if action == "click":
            locator.click(timeout=5000)
        elif action == "fill":
            locator.fill(str(value))
        elif action == "press":
            locator.press(str(value))
        elif action == "select":
            locator.select_option(label=str(value))
        elif action == "check":
            if str(value).lower() == "off":
                locator.uncheck()
            else:
                locator.check()
        elif action == "hover":
            locator.hover()
        elif action == "verify":
            if not value:
                assert locator.is_visible(), (
                    f"요소가 보이지 않습니다: {step.get('target')}"
                )
            else:
                actual = locator.inner_text() or locator.input_value()
                assert str(value) in actual, (
                    f"텍스트 불일치: 기대='{value}', 실제='{actual}'"
                )
        else:
            raise ValueError(
                f"미지원 DSL 액션: '{action}'. "
                f"허용: navigate, click, fill, press, select, check, hover, wait, verify"
            )

    # ── 스크린샷 ──
    @staticmethod
    def _screenshot(page: Page, artifacts: str, step_id, suffix: str) -> str:
        """스텝 실행 후 스크린샷을 저장하고 파일 경로를 반환한다."""
        path = os.path.join(artifacts, f"step_{step_id}_{suffix}.png")
        page.screenshot(path=path)
        return path

    @staticmethod
    def _safe_screenshot(page: Page, path: str):
        """스크린샷을 저장하되, 실패해도 예외를 무시한다."""
        try:
            page.screenshot(path=path)
        except Exception:
            pass
