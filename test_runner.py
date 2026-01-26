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

# DeepEval: LLM 평가 프레임워크
from deepeval import assert_test
from deepeval.test_case import LLMTestCase
from deepeval.metrics import FaithfulnessMetric, AnswerRelevancyMetric, ContextualRecallMetric
from deepeval.models.gpt_model import GPTModel

# 커스텀 어댑터 레지스트리 (외부 AI 호출용)
from adapters.registry import AdapterRegistry

# Langfuse: 평가 결과 시각화 및 추적 (Observability)
try:
    from langfuse import Langfuse
except ImportError:
    Langfuse = None

# 평가지표 한글 병기 매핑 (Langfuse 및 리포트 표시용)
METRIC_DISPLAY_NAMES = {
    "PolicyViolation": "Policy Violation (보안 위반)",
    "FormatCompliance": "Format Compliance (형식 준수)",
    "TaskCompletion": "Task Completion (과업 완료율)",
    "AnswerRelevancyMetric": "Answer Relevancy (답변 적합성)",
    "FaithfulnessMetric": "Faithfulness (충실도)",
    "ContextualRecallMetric": "Contextual Recall (검색 재현율)",
    "Latency": "Latency (지연 시간)"
}

# =============================================================================
# [1] 환경 설정 및 초기화
# =============================================================================

# Jenkins 파라미터 또는 환경 변수로부터 평가 대상 정보를 가져옵니다.
TARGET_URL = os.environ.get("TARGET_URL")
TARGET_TYPE = os.environ.get("TARGET_TYPE", "http")  # 어댑터 타입 (기본: http)
API_KEY = os.environ.get("API_KEY")

# Langfuse 접속 정보
LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY")
LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST")

# Jenkins 빌드 식별자 (Trace ID 충돌 방지용)
RUN_ID = os.environ.get("BUILD_TAG") or os.environ.get("BUILD_ID") or str(int(time.time()))

# Langfuse 클라이언트 초기화
langfuse = None
if Langfuse and LANGFUSE_PUBLIC_KEY:
    langfuse = Langfuse(
        public_key=LANGFUSE_PUBLIC_KEY,
        secret_key=LANGFUSE_SECRET_KEY,
        host=LANGFUSE_HOST
    )

# =============================================================================
# [2] 유틸리티 및 헬퍼 함수
# =============================================================================

def load_dataset():
    """평가의 기준이 되는 Golden Dataset(CSV)을 로드합니다."""
    csv_path = "/app/data/golden.csv"
    if not os.path.exists(csv_path):
        return []
    # pandas를 이용해 데이터를 읽고 결측치는 None으로 처리합니다.
    df = pd.read_csv(csv_path)
    return df.where(pd.notnull(df), None).to_dict(orient="records")

def _json_get_path(obj, path: str):
    """JSON 객체 내에서 'json.a.b[0].c' 형태의 경로로 값을 찾아 반환합니다."""
    if obj is None or not path.startswith("json."):
        return None

    cur = obj
    tokens = path[5:].split(".")  # 'json.' 제외한 경로 토큰화
    for tok in tokens:
        # 배열 인덱스 처리 (예: data[0])
        m = re.match(r"^([a-zA-Z0-9_\-]+)\[(\d+)\]$", tok)
        if m:
            key, idx = m.group(1), int(m.group(2))
            if not isinstance(cur, dict) or key not in cur: return None
            cur = cur[key]
            if not isinstance(cur, list) or idx >= len(cur): return None
            cur = cur[idx]
        else:
            if not isinstance(cur, dict) or tok not in cur: return None
            cur = cur[tok]
    return cur

def _evaluate_agent_criteria(criteria_str: str, result) -> bool:
    """
    Agent 유형의 성공 조건(success_criteria)을 검증합니다.
    지원 문법: status_code=200 AND raw~r/pattern/ AND json.path~r/pattern/
    """
    if not criteria_str:
        # 조건이 없으면 HTTP 200 OK만 확인합니다.
        return result.http_status == 200

    conditions = [c.strip() for c in criteria_str.split(" AND ")]
    parsed_json = None
    try:
        parsed_json = json.loads(result.raw_response)
    except:
        pass

    for cond in conditions:
        # Case 1: HTTP 상태 코드 확인 (예: status_code=200)
        if "=" in cond and "~r/" not in cond:
            key, val = cond.split("=", 1)
            if key.strip() == "status_code" and str(result.http_status) != val.strip():
                return False
        
        # Case 2: 정규식 매칭 (예: raw~r/Success/ 또는 json.meta.id~r/^\d+$/)
        elif "~r/" in cond:
            left, regex_part = cond.split("~r/", 1)
            regex = regex_part.rstrip("/")
            left = left.strip()
            
            if left == "raw":
                if not re.search(regex, result.raw_response or ""): return False
            elif left.startswith("json."):
                val = _json_get_path(parsed_json, left)
                if val is None or not re.search(regex, str(val)): return False
            else:
                return False
    return True

def _promptfoo_policy_check(raw_text: str):
    """Promptfoo를 호출하여 보안 위반(Policy Violation)을 결정론적으로 체크합니다."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
        tmp.write(raw_text or "")
        tmp_path = tmp.name

    # security.yaml에 정의된 정규식 규칙을 기반으로 검사합니다.
    cmd = ["promptfoo", "eval", "-c", "/app/configs/security.yaml", "--prompts", f"file://{tmp_path}", "-o", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"보안 위반 탐지 (Promptfoo): {proc.stderr or proc.stdout}")

def _schema_validate(raw_text: str):
    """응답이 약속된 JSON 구조(schema.json)를 지키는지 검증합니다."""
    schema_path = "/app/configs/schema.json"
    if not os.path.exists(schema_path):
        return

    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    try:
        parsed = json.loads(raw_text or "")
        validate(instance=parsed, schema=schema)
    except (json.JSONDecodeError, ValidationError) as e:
        raise RuntimeError(f"형식 미준수 (Format Compliance): {e}")

# =============================================================================
# [3] 메인 테스트 로직 (pytest)
# =============================================================================

@pytest.mark.parametrize("case", load_dataset())
def test_evaluation(case):
    """
    Golden Dataset의 각 케이스에 대해 평가 파이프라인을 실행합니다.
    """
    case_id = case["case_id"]
    target_category = case["target_type"]  # rag / agent / chat
    input_text = case["input"]

    # Langfuse 트레이스 시작 (빌드 ID와 케이스 ID 조합으로 고유성 확보)
    trace_id = f"{RUN_ID}:{case_id}"
    trace = None
    if langfuse:
        trace = langfuse.trace(name=f"Eval-{case_id}", id=trace_id, input=input_text)

    # ---------------------------------------------------------
    # STEP 1: 외부 AI 호출 (Adapter)
    # ---------------------------------------------------------
    # [동작] TARGET_TYPE(예: http)에 따라 사전에 정의된 어댑터를 동적으로 로드합니다.
    # [목적] 벤더마다 다른 API 규격을 CDM(Canonical Data Model)이라는 표준 형식으로 통일하여 
    #       이후의 평가 로직이 동일한 잣대로 동작하게 합니다.
    adapter = AdapterRegistry.get_instance(TARGET_TYPE, TARGET_URL, API_KEY)
    result = adapter.invoke(input_text)

    # 호출 결과 및 지연 시간 기록
    if trace:
        trace.update(output=result.to_dict())
        metric_name = METRIC_DISPLAY_NAMES.get("Latency", "Latency")
        trace.score(name=metric_name, value=result.latency_ms, comment="ms")

    # 연결 자체에 실패한 경우 테스트를 중단합니다.
    if result.error:
        pytest.fail(f"어댑터 호출 실패: {result.error}")

    # ---------------------------------------------------------
    # STEP 2: Fail-Fast (보안 및 형식 검증)
    # ---------------------------------------------------------
    
    # 2-1. 보안 위반 체크 (Policy Violation)
    # 개인정보나 기밀이 유출되었는지 정규식으로 빠르게 확인합니다.
    try:
        _promptfoo_policy_check(result.raw_response)
    except Exception as e:
        if trace: trace.score(name="PolicyViolation", value=0, comment=str(e))
        pytest.fail(str(e))

    # 2-2. 형식 준수 체크 (Format Compliance)
    # 응답이 시스템 연동에 적합한 JSON 구조인지 확인합니다.
    try:
        _schema_validate(result.raw_response)
    except Exception as e:
        if trace: trace.score(name="FormatCompliance", value=0, comment=str(e))
        pytest.fail(str(e))

    # ---------------------------------------------------------
    # STEP 3: 과업 성공 검증 (Agent 전용)
    # ---------------------------------------------------------
    if target_category == "agent":
        # [지표 3: Task Completion (과업 완료율)]
        # 원리: golden.csv의 success_criteria에 정의된 논리 조건(상태코드, 정규식 등)을 검사합니다.
        # 이유: 에이전트가 실제로 비즈니스 로직을 성공적으로 수행했는지 확인하기 위함입니다.
        passed = _evaluate_agent_criteria(case.get("success_criteria"), result)
        if trace:
            metric_name = METRIC_DISPLAY_NAMES.get("TaskCompletion", "TaskCompletion")
            trace.score(name=metric_name, value=1 if passed else 0)
        assert passed, f"에이전트 과업 실패. 기준: {case.get('success_criteria')}"
        return  # 에이전트는 여기서 평가 종료

    # ---------------------------------------------------------
    # STEP 4: 심층 품질 평가 (DeepEval + Ollama Judge)
    # ---------------------------------------------------------
    
    # 로컬 Ollama 서버의 qwen3-coder 모델을 심판(Judge)으로 설정합니다.
    judge = GPTModel(
        model="qwen3-coder:30b",
        base_url=f"{os.environ.get('OLLAMA_BASE_URL')}/v1"
    )

    # DeepEval 테스트 케이스 구성
    test_case = LLMTestCase(
        input=input_text,
        actual_output=result.actual_output,
        expected_output=case.get("expected_output"),
        retrieval_context=result.retrieval_context,
        # RAG의 경우 정답 근거 문서를 함께 제공하여 Recall을 측정합니다.
        context=json.loads(case.get("context_ground_truth", "[]") or "[]"),
    )

    # [지표 4: Answer Relevancy (답변 적합성)]
    # 원리: 답변으로부터 질문을 역추론하여 원본 질문과의 의미적 유사도를 측정합니다.
    # 이유: 질문의 의도에 맞는 핵심적인 답변을 내놓는지 확인하여 동문서답을 방지합니다.
    metrics = [AnswerRelevancyMetric(threshold=0.8, model=judge)]

    # RAG 유형일 경우 추가 지표 측정
    if target_category == "rag":
        # [지표 5: Faithfulness (충실도)]
        # 원리: 답변의 모든 문장이 검색된 문서(Context)에 기반하는지 대조하여 환각을 탐지합니다.
        metrics.append(FaithfulnessMetric(threshold=0.9, model=judge))
        
        # [지표 6: Contextual Recall (검색 재현율)]
        # 원리: 운영자가 작성한 정답의 핵심 사실이 검색된 문서에 포함되어 있는지 확인합니다.
        metrics.append(ContextualRecallMetric(threshold=0.8, model=judge))

    # 각 지표 측정 및 Langfuse 기록
    for m in metrics:
        m.measure(test_case)
        if trace:
            metric_name = METRIC_DISPLAY_NAMES.get(m.__class__.__name__, m.__class__.__name__)
            # 점수와 함께 Judge가 내린 판단 근거(reason)를 기록합니다.
            trace.score(
                name=metric_name, 
                value=m.score, 
                comment=getattr(m, 'reason', '')
            )

    # 최종 PASS/FAIL 판정 (임계값 미달 시 에러 발생)
    assert_test(test_case, metrics)

if __name__ == "__main__":
    # 로컬 테스트용 실행 코드
    pytest.main([__file__, "-v", "-n", "1"])