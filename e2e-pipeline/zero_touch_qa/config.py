import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Zero-Touch QA 실행에 필요한 설정을 담는 불변 데이터클래스.

    모든 값은 환경변수에서 로드하거나 직접 지정할 수 있다.
    frozen=True 이므로 생성 후 값을 변경할 수 없다.

    Attributes:
        dify_base_url: Dify Chatflow API 의 베이스 URL (예: ``http://localhost:18081/v1``).
        dify_api_key: Dify API 인증 Bearer 토큰.
        artifacts_dir: 스크린샷, 리포트, 시나리오 JSON 등 산출물 저장 디렉터리.
        viewport: 브라우저 뷰포트 크기 ``(width, height)`` 픽셀.
        slow_mo: Playwright slow_mo 값(밀리초). 디버깅 시 동작을 느리게 한다.
        heal_threshold: 로컬 DOM 유사도 매칭 임계값 (0.0~1.0). 이 값 이상이면 치유 성공.
        dom_snapshot_limit: Dify 치유 요청 시 전송할 DOM HTML 최대 문자 수.
    """

    dify_base_url: str
    dify_api_key: str
    artifacts_dir: str
    viewport: tuple[int, int]
    slow_mo: int
    step_interval_min_ms: int
    step_interval_max_ms: int
    heal_threshold: float
    heal_timeout_sec: int
    dom_snapshot_limit: int

    @classmethod
    def from_env(cls) -> "Config":
        """환경변수에서 설정 값을 읽어 Config 인스턴스를 생성한다.

        각 환경변수와 기본값:
            - ``DIFY_BASE_URL`` → ``http://localhost/v1``
            - ``DIFY_API_KEY`` → ``""`` (빈 문자열)
            - ``ARTIFACTS_DIR`` → ``artifacts``
            - ``VIEWPORT_WIDTH`` / ``VIEWPORT_HEIGHT`` → ``1440`` / ``900``
            - ``SLOW_MO`` → ``800`` (Playwright 액션 단위 지연, 봇 패턴 회피)
            - ``STEP_INTERVAL_MIN_MS`` / ``STEP_INTERVAL_MAX_MS`` → ``800`` / ``1500``
              (DSL 스텝 간 random sleep, 0 이면 비활성)
            - ``HEAL_THRESHOLD`` → ``0.8``
            - ``HEAL_TIMEOUT_SEC`` → ``60`` (Dify LLM 치유 호출 단일 timeout, 재시도 없음)
            - ``DOM_SNAPSHOT_LIMIT`` → ``10000``

        Returns:
            환경변수 값이 반영된 Config 인스턴스.
        """
        return cls(
            dify_base_url=os.getenv("DIFY_BASE_URL", "http://localhost/v1"),
            dify_api_key=os.getenv("DIFY_API_KEY", ""),
            artifacts_dir=os.getenv("ARTIFACTS_DIR", "artifacts"),
            viewport=(
                int(os.getenv("VIEWPORT_WIDTH", "1440")),
                int(os.getenv("VIEWPORT_HEIGHT", "900")),
            ),
            slow_mo=int(os.getenv("SLOW_MO", "800")),
            step_interval_min_ms=int(os.getenv("STEP_INTERVAL_MIN_MS", "800")),
            step_interval_max_ms=int(os.getenv("STEP_INTERVAL_MAX_MS", "1500")),
            heal_threshold=float(os.getenv("HEAL_THRESHOLD", "0.8")),
            heal_timeout_sec=int(os.getenv("HEAL_TIMEOUT_SEC", "60")),
            dom_snapshot_limit=int(os.getenv("DOM_SNAPSHOT_LIMIT", "10000")),
        )
