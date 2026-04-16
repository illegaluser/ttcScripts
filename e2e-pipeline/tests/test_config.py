"""config.py 유닛테스트.

Config 데이터클래스의 from_env() 팩토리 메서드와 불변성을 검증한다.
"""

import dataclasses

import pytest

from zero_touch_qa.config import Config


def test_from_env_defaults(monkeypatch):
    """환경변수가 없을 때 기본값으로 Config 가 생성된다."""
    for key in ("DIFY_BASE_URL", "DIFY_API_KEY", "ARTIFACTS_DIR",
                "VIEWPORT_WIDTH", "VIEWPORT_HEIGHT", "SLOW_MO",
                "HEAL_THRESHOLD", "DOM_SNAPSHOT_LIMIT"):
        monkeypatch.delenv(key, raising=False)

    cfg = Config.from_env()

    assert cfg.dify_base_url == "http://localhost/v1"
    assert cfg.dify_api_key == ""
    assert cfg.artifacts_dir == "artifacts"
    assert cfg.viewport == (1440, 900)
    assert cfg.slow_mo == 500
    assert cfg.heal_threshold == 0.8
    assert cfg.dom_snapshot_limit == 10000


def test_from_env_custom_values(monkeypatch):
    """모든 환경변수가 설정되면 해당 값을 정확히 읽는다."""
    monkeypatch.setenv("DIFY_BASE_URL", "http://custom:8080/v1")
    monkeypatch.setenv("DIFY_API_KEY", "my-secret-key")
    monkeypatch.setenv("ARTIFACTS_DIR", "/tmp/arts")
    monkeypatch.setenv("VIEWPORT_WIDTH", "1920")
    monkeypatch.setenv("VIEWPORT_HEIGHT", "1080")
    monkeypatch.setenv("SLOW_MO", "100")
    monkeypatch.setenv("HEAL_THRESHOLD", "0.6")
    monkeypatch.setenv("DOM_SNAPSHOT_LIMIT", "5000")

    cfg = Config.from_env()

    assert cfg.dify_base_url == "http://custom:8080/v1"
    assert cfg.dify_api_key == "my-secret-key"
    assert cfg.artifacts_dir == "/tmp/arts"
    assert cfg.viewport == (1920, 1080)
    assert cfg.slow_mo == 100
    assert cfg.heal_threshold == 0.6
    assert cfg.dom_snapshot_limit == 5000


def test_config_frozen():
    """frozen=True 이므로 속성 변경 시 FrozenInstanceError 가 발생한다."""
    cfg = Config(
        dify_base_url="http://x", dify_api_key="k",
        artifacts_dir="a", viewport=(800, 600),
        slow_mo=0, heal_threshold=0.5, dom_snapshot_limit=100,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.dify_api_key = "new-key"


def test_viewport_tuple_type():
    """viewport 필드가 tuple 타입인지 확인한다."""
    cfg = Config.from_env()
    assert isinstance(cfg.viewport, tuple)
    assert len(cfg.viewport) == 2
