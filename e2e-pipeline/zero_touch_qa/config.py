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
        )
