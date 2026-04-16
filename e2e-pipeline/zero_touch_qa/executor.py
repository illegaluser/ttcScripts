import os
import time
import logging
from dataclasses import dataclass, field

from playwright.sync_api import sync_playwright, Page, Locator
# [변경] TargetClosedError import 추가
# 사유: Playwright 페이지가 팝업·리다이렉트 등으로 닫힐 때 발생하는 예외를 명시적으로
#       처리하지 않으면 치유 루프 전체가 비정상 종료된다.
#       (실제 사례: Google 홈→google.co.kr 리다이렉트 중 탭이 닫히며 크래시 발생)
from playwright._impl._errors import TargetClosedError

from .config import Config
from .dify_client import DifyClient, DifyConnectionError
from .locator_resolver import LocatorResolver
from .local_healer import LocalHealer

log = logging.getLogger(__name__)


@dataclass
class StepResult:
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
        """
        Playwright 브라우저를 실행하고 DSL 시나리오를 순차 실행한다.
        모든 스텝의 결과를 list[StepResult]로 반환한다.
        """
        results: list[StepResult] = []
        artifacts = self.config.artifacts_dir
        os.makedirs(artifacts, exist_ok=True)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not headed, slow_mo=self.config.slow_mo)
            page = browser.new_page(
                viewport={
                    "width": self.config.viewport[0],
                    "height": self.config.viewport[1],
                }
            )
            resolver = LocatorResolver(page)
            healer = LocalHealer(page, self.config.heal_threshold)

            try:
                for step in scenario:
                    result = self._execute_step(
                        page, step, resolver, healer, artifacts
                    )
                    results.append(result)
                    if result.status == "FAIL":
                        # 최종 실패 스크린샷
                        fail_path = os.path.join(artifacts, "error_final.png")
                        self._safe_screenshot(page, fail_path)
                        break
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
        """단일 스텝을 실행하고 결과를 반환한다."""
        action = step["action"].lower()
        step_id = step.get("step", "-")
        desc = step.get("description", "")

        # ── 메타 액션 (타겟 불필요) ──
        if action in ("navigate", "maps"):
            url = step.get("value") or step.get("target", "")
            # [변경] page.goto(url) + wait_for_load_state("domcontentloaded") →
            #        page.goto(url, wait_until="networkidle", timeout=30000)
            # 사유: Google 은 https://www.google.com 접속 시 지역 도메인
            #       (예: google.co.kr) 으로 리다이렉트한다.
            #       domcontentloaded 는 최초 응답 HTML 파싱 직후 이벤트이므로
            #       리다이렉트가 완료되기 전에 다음 스텝이 실행되어 DOM 탐색 실패 → 탭 강제 종료
            #       (TargetClosedError) 가 발생했다.
            #       networkidle 은 네트워크 요청이 500ms 동안 없을 때 완료로 판단하므로
            #       리다이렉트 체인이 모두 끝난 뒤 다음 스텝으로 진행된다.
            page.goto(url, wait_until="networkidle", timeout=30000)
            final_url = page.url
            if final_url != url:
                log.info("[Step %s] 리다이렉트 감지: %s → %s", step_id, url, final_url)
            ss = self._screenshot(page, artifacts, step_id, "pass")
            log.info("[Step %s] navigate -> PASS", step_id)
            return StepResult(
                step_id, action, str(url), str(final_url), desc,
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

        # ── 타겟 필요 액션: 실행 + 3단계 자가 치유 ──
        log.info("[Step %s] %s: %s", step_id, action, desc)

        # 1차 시도: 기본 타겟
        locator = resolver.resolve(step.get("target"))
        if locator:
            try:
                self._perform_action(page, locator, step)
                ss = self._screenshot(page, artifacts, step_id, "pass")
                return StepResult(
                    step_id, action, str(step.get("target", "")),
                    str(step.get("value", "")), desc,
                    "PASS", screenshot_path=ss,
                )
            except Exception as e:
                log.warning("[Step %s] 기본 타겟 실패: %s", step_id, e)

        # ── [치유 1단계] fallback_targets ──
        for fb_target in step.get("fallback_targets", []):
            try:
                fb_loc = resolver.resolve(fb_target)
                if fb_loc:
                    self._perform_action(page, fb_loc, step)
                    ss = self._screenshot(page, artifacts, step_id, "healed")
                    log.info("[Step %s] fallback 복구 성공: %s", step_id, fb_target)
                    return StepResult(
                        step_id, action, str(fb_target),
                        str(step.get("value", "")), desc,
                        "HEALED", heal_stage="fallback", screenshot_path=ss,
                    )
            # [변경] TargetClosedError 개별 처리 추가 (fallback 단계)
            # 사유: 원본은 except Exception: continue 만 있어 브라우저가 닫혀도
            #       다음 fallback 을 계속 시도했고, 결국 알 수 없는 오류로 종료됐다.
            #       TargetClosedError 를 먼저 잡아 즉시 FAIL 로 기록하고 루프를 탈출한다.
            except TargetClosedError:
                log.error("[Step %s] 브라우저가 닫혔습니다 (fallback 중)", step_id)
                return StepResult(step_id, action, str(fb_target), "", desc, "FAIL")
            except Exception:
                continue

        # ── [치유 2단계] 로컬 DOM 유사도 매칭 ──
        try:
            healed_loc = healer.try_heal(step)
        # [변경] TargetClosedError 처리 추가 (로컬 치유 DOM 스캔 단계)
        # 사유: healer.try_heal() 내부에서 page.locator().all() 을 호출하는데,
        #       이미 브라우저가 닫혀 있으면 TargetClosedError 가 발생한다.
        #       원본은 이 예외를 처리하지 않아 전체 실행이 비정상 종료됐다.
        except TargetClosedError:
            log.error("[Step %s] 브라우저가 닫혔습니다 (로컬 치유 중)", step_id)
            return StepResult(step_id, action, str(step.get("target", "")), "", desc, "FAIL")
        except Exception as e:
            log.warning("[Step %s] 로컬 치유 DOM 스캔 실패: %s", step_id, e)
            healed_loc = None

        if healed_loc:
            try:
                self._perform_action(page, healed_loc, step)
                ss = self._screenshot(page, artifacts, step_id, "healed")
                return StepResult(
                    step_id, action, str(step.get("target", "")),
                    str(step.get("value", "")), desc,
                    "HEALED", heal_stage="local", screenshot_path=ss,
                )
            # [변경] TargetClosedError 처리 추가 (로컬 치유 실행 단계)
            except TargetClosedError:
                log.error("[Step %s] 브라우저가 닫혔습니다 (로컬 치유 실행 중)", step_id)
                return StepResult(step_id, action, str(step.get("target", "")), "", desc, "FAIL")
            except Exception as e:
                log.warning("[Step %s] 로컬 치유 실행 실패: %s", step_id, e)

        # ── [치유 3단계] Dify LLM 치유 ──
        log.info("[Step %s] Dify LLM 치유 요청 중...", step_id)
        try:
            dom_snapshot = page.content()[: self.config.dom_snapshot_limit]
            new_target_info = self.dify.request_healing(
                error_msg=f"요소 탐색/실행 실패: {step.get('target')}",
                dom_snapshot=dom_snapshot,
                failed_step=step,
            )
        # [변경] TargetClosedError 처리 추가 (LLM 치유 준비 단계)
        # 사유: page.content() 호출 시 브라우저가 이미 닫혀 있으면 TargetClosedError 발생.
        #       원본은 처리 없이 전체 비정상 종료.
        except TargetClosedError:
            log.error("[Step %s] 브라우저가 닫혔습니다 (LLM 치유 준비 중)", step_id)
            return StepResult(step_id, action, str(step.get("target", "")), "", desc, "FAIL")
        except DifyConnectionError as e:
            log.error("[Step %s] Dify 치유 통신 실패: %s", step_id, e)
            new_target_info = None

        if new_target_info:
            step.update(new_target_info)
            healed_loc = resolver.resolve(step.get("target"))
            if healed_loc:
                try:
                    self._perform_action(page, healed_loc, step)
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
                # [변경] TargetClosedError 처리 추가 (LLM 치유 실행 단계)
                except TargetClosedError:
                    log.error("[Step %s] 브라우저가 닫혔습니다 (LLM 치유 실행 중)", step_id)
                    return StepResult(step_id, action, str(step.get("target", "")), "", desc, "FAIL")
                except Exception as e:
                    log.error("[Step %s] LLM 치유 후 실행 실패: %s", step_id, e)

        # ── 모든 치유 실패 ──
        log.error("[Step %s] FAIL — 모든 치유 실패", step_id)
        return StepResult(
            step_id, action, str(step.get("target", "")),
            str(step.get("value", "")), desc,
            "FAIL",
        )

    # ── LLM 출력 보정 ──
    KNOWN_KEYS = {
        "enter", "tab", "escape", "backspace", "delete", "arrowup",
        "arrowdown", "arrowleft", "arrowright", "space", "home", "end",
        "pageup", "pagedown", "f1", "f2", "f3", "f4", "f5", "f6",
        "f7", "f8", "f9", "f10", "f11", "f12",
    }

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

        if action == "navigate" and not value and target.startswith(("http://", "https://")):
            step["value"] = target
            step["target"] = ""
            log.debug("[보정] navigate: target → value로 이동")

    # ── 9대 DSL 액션 수행 ──
    @staticmethod
    def _perform_action(page: Page, locator: Locator, step: dict):
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
        path = os.path.join(artifacts, f"step_{step_id}_{suffix}.png")
        page.screenshot(path=path)
        return path

    @staticmethod
    def _safe_screenshot(page: Page, path: str):
        try:
            page.screenshot(path=path)
        except Exception:
            pass
