import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    dify_base_url: str
    dify_api_key: str
    artifacts_dir: str
    viewport: tuple[int, int]
    slow_mo: int
    heal_threshold: float
    dom_snapshot_limit: int
    # [변경] dify_timeout 필드 추가
    # 사유: 원본은 requests.post(timeout=120) 하드코딩으로 로컬 LLM(qwen3:8b 등)의
    #       느린 추론 속도(수 분)에 의해 ReadTimeout 이 반복 발생했다.
    #       Config 를 통해 환경변수로 주입받아 시나리오 생성·치유 모두 동일한 타임아웃을
    #       적용하고, Jenkins 파이프라인에서도 DIFY_TIMEOUT 으로 제어할 수 있게 했다.
    dify_timeout: int

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            dify_base_url=os.getenv("DIFY_BASE_URL", "http://localhost/v1"),
            dify_api_key=os.getenv("DIFY_API_KEY", ""),
            artifacts_dir=os.getenv("ARTIFACTS_DIR", "artifacts"),
            viewport=(
                int(os.getenv("VIEWPORT_WIDTH", "1440")),
                int(os.getenv("VIEWPORT_HEIGHT", "900")),
            ),
            slow_mo=int(os.getenv("SLOW_MO", "500")),
            heal_threshold=float(os.getenv("HEAL_THRESHOLD", "0.8")),
            dom_snapshot_limit=int(os.getenv("DOM_SNAPSHOT_LIMIT", "10000")),
            # [변경] 기본값 1800초(30분) — 로컬 LLM 추론 + Nginx proxy_read_timeout 과 일치시킴
            # 원본: DifyClient._call() 에 timeout=120 하드코딩(변경 불가)
            dify_timeout=int(os.getenv("DIFY_TIMEOUT", "1800")),
        )
