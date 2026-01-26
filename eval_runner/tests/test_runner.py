import os
import json
import re
import time
import tempfile
import subprocess
import pytest
import pandas as pd
from jsonschema import validate
from jsonschema.exceptions import ValidationError

from deepeval import assert_test
from deepeval.test_case import LLMTestCase
from deepeval.metrics import FaithfulnessMetric, AnswerRelevancyMetric, ContextualRecallMetric
from deepeval.models.gpt_model import GPTModel

from adapters.registry import AdapterRegistry

try:
    from langfuse import Langfuse
except Exception:
    Langfuse = None

# =========================
# ENV
# =========================
TARGET_URL = os.environ.get("TARGET_URL")
TARGET_TYPE = os.environ.get("TARGET_TYPE", "http")  # adapter type
API_KEY = os.environ.get("API_KEY")

LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY")
LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST")

# Jenkins build 식별자(충돌 방지)
# 예: BUILD_TAG=jenkins-DSCORE-Universal-Eval-123
RUN_ID = os.environ.get("BUILD_TAG") or os.environ.get("BUILD_ID") or str(int(time.time()))

langfuse = None
if Langfuse and LANGFUSE_PUBLIC_KEY:
    langfuse = Langfuse(
        public_key=LANGFUSE_PUBLIC_KEY,
        secret_key=LANGFUSE_SECRET_KEY,
        host=LANGFUSE_HOST
    )

# =========================
# Dataset
# =========================
def load_dataset():
    csv_path = "/app/data/golden.csv"
    if not os.path.exists(csv_path):
        return []
    df = pd.read_csv(csv_path)
    return df.where(pd.notnull(df), None).to_dict(orient="records")

# =========================
# Helpers
# =========================
def _safe_json_loads(s: str):
    try:
        return json.loads(s)
    except Exception:
        return None

def _json_get_path(obj, path: str):
    """
    path 형식 예시:
      json.answer
      json.meta.issue_key
      json.data[0].id
    """
    if obj is None:
        return None

    if not path.startswith("json."):
        return None

    cur = obj
    tokens = path[5:].split(".")  # remove 'json.'
    for tok in tokens:
        # list index 처리: data[0]
        m = re.match(r"^([a-zA-Z0-9_\-]+)\[(\d+)\]$", tok)
        if m:
            key, idx = m.group(1), int(m.group(2))
            if not isinstance(cur, dict) or key not in cur:
                return None
            cur = cur[key]
            if not isinstance(cur, list) or idx >= len(cur):
                return None
            cur = cur[idx]
        else:
            if not isinstance(cur, dict) or tok not in cur:
                return None
            cur = cur[tok]
    return cur

def _evaluate_agent_criteria(criteria_str: str, result) -> bool:
    """
    success_criteria 지원 문법(v37.0):
      - "status_code=200"
      - "raw~r/<regex>/"                      : raw_response 전체에 정규식 매칭
      - "json.some.path~r/<regex>/"           : raw_response를 JSON 파싱 후 특정 path 값에 정규식 매칭
      - 조건 연결: " AND " (대문자 AND, 공백 포함)

    예시:
      status_code=200 AND raw~r/Success/
      status_code=200 AND json.meta.issue_key~r/^[A-Z]+-\\d+$/
    """
    if not criteria_str:
        # 조건 미기재 시: 호출 성공(HTTP 200)만으로 Pass 처리
        return result.http_status == 200

    conditions = [c.strip() for c in criteria_str.split(" AND ")]
    parsed = _safe_json_loads(result.raw_response)

    for cond in conditions:
        # status_code=200
        if "=" in cond and "~r/" not in cond:
            key, val = cond.split("=", 1)
            key = key.strip()
            val = val.strip()
            if key == "status_code":
                if str(result.http_status) != val:
                    return False
            else:
                # 확장 여지: key=value (현재는 status_code만 보장)
                return False
            continue

        # regex: raw~r/.../ or json.xxx~r/.../
        if "~r/" in cond:
            left, regex_part = cond.split("~r/", 1)
            regex = regex_part.rstrip("/")

            left = left.strip()
            if left == "raw":
                if not re.search(regex, result.raw_response or ""):
                    return False
                continue

            if left.startswith("json."):
                v = _json_get_path(parsed, left)
                if v is None:
                    return False
                if not re.search(regex, str(v)):
                    return False
                continue

            # 정의되지 않은 좌항
            return False

        # 알 수 없는 문법
        return False

    return True

def _promptfoo_policy_check(raw_text: str):
    """
    Promptfoo는 결정론적(Pass/Fail) 보안 패턴 차단 역할로 사용한다.
    - configs/security.yaml 내 assert 규칙 기준.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
        tmp.write(raw_text or "")
        tmp_path = tmp.name

    cmd = [
        "promptfoo",
        "eval",
        "-c",
        "/app/configs/security.yaml",
        "--prompts",
        f"file://{tmp_path}",
        "-o",
        "json",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "Promptfoo failed")

def _schema_validate(raw_text: str):
    """
    schema.json 기반 Format Compliance.
    raw_text는 JSON 파싱 가능해야 하며, 필수 필드(answer)를 포함해야 한다.
    """
    schema_path = "/app/configs/schema.json"
    if not os.path.exists(schema_path):
        return

    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    try:
        parsed = json.loads(raw_text or "")
        validate(instance=parsed, schema=schema)
    except (json.JSONDecodeError, ValidationError) as e:
        raise RuntimeError(f"Format Compliance Failed (schema.json): {e}")

# =========================
# Tests
# =========================
@pytest.mark.parametrize("case", load_dataset())
def test_evaluation(case):
    case_id = case["case_id"]
    target_category = case["target_type"]  # rag / agent / chat
    input_text = case["input"]

    trace_id = f"{RUN_ID}:{case_id}"
    trace = None
    if langfuse:
        trace = langfuse.trace(name=f"Eval-{case_id}", id=trace_id, input=input_text)

    # 1) Adapter 호출
    adapter = AdapterRegistry.get_instance(TARGET_TYPE, TARGET_URL, API_KEY)
    result = adapter.invoke(input_text)

    if trace:
        trace.update(output=result.to_dict())
        trace.score(name="Latency", value=result.latency_ms, comment="ms")

    if result.error:
        pytest.fail(f"Adapter Error: {result.error}")

    # 2) Fail-Fast: Promptfoo (Policy Violation)
    try:
        _promptfoo_policy_check(result.raw_response)
    except Exception as e:
        pytest.fail(f"Policy Violation (Promptfoo) Failed: {e}")

    # 3) Fail-Fast: Format Compliance (schema.json)
    try:
        _schema_validate(result.raw_response)
    except Exception as e:
        pytest.fail(str(e))

    # 4) Agent: Task Completion
    if target_category == "agent":
        passed = _evaluate_agent_criteria(case.get("success_criteria"), result)
        if trace:
            trace.score(name="TaskCompletion", value=1 if passed else 0)
        assert passed, f"Agent Task Failed. Status: {result.http_status}, Criteria: {case.get('success_criteria')}"
        return

    # 5) DeepEval: Judge 기반 지표 평가
    judge = GPTModel(
        model="qwen3-coder:30b",
        base_url=f"{os.environ.get('OLLAMA_BASE_URL')}/v1"
    )

    test_case = LLMTestCase(
        input=input_text,
        actual_output=result.actual_output,
        expected_output=case.get("expected_output"),
        retrieval_context=result.retrieval_context,
        context=json.loads(case.get("context_ground_truth", "[]") or "[]"),
    )

    metrics = [AnswerRelevancyMetric(threshold=0.8, model=judge)]

    if target_category == "rag":
        metrics.append(FaithfulnessMetric(threshold=0.9, model=judge))
        metrics.append(ContextualRecallMetric(threshold=0.8, model=judge))

    # Langfuse score 기록(측정값/사유)
    for m in metrics:
        m.measure(test_case)
        if trace:
            trace.score(name=m.__class__.__name__, value=m.score, comment=m.reason)

    assert_test(test_case, metrics)