import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import pandas as pd
import pytest
from jsonschema import validate
from jsonschema.exceptions import ValidationError

from deepeval import assert_test
from deepeval.metrics import (
    AnswerRelevancyMetric,
    ContextualRecallMetric,
    FaithfulnessMetric,
    GEval,
    ToxicityMetric,
)
from deepeval.models.gpt_model import GPTModel
from deepeval.test_case import LLMTestCase

try:
    from deepeval.metrics import ContextualPrecisionMetric
except ImportError:
    ContextualPrecisionMetric = None

from adapters.registry import AdapterRegistry

try:
    from langfuse import Langfuse
except Exception:
    Langfuse = None


TARGET_URL = os.environ.get("TARGET_URL")
TARGET_TYPE = os.environ.get("TARGET_TYPE", "http")
API_KEY = os.environ.get("API_KEY")
TARGET_AUTH_HEADER = os.environ.get("TARGET_AUTH_HEADER")

LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY")
LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "qwen3-coder:30b")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")

RUN_ID = os.environ.get("BUILD_TAG") or os.environ.get("BUILD_ID") or str(int(time.time()))
MODULE_ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = MODULE_ROOT / "configs"

DEFAULT_GOLDEN_PATHS = [
    MODULE_ROOT / "data" / "golden.csv",
    Path("/var/knowledges/eval/data/golden.csv"),
    Path("/var/jenkins_home/knowledges/eval/data/golden.csv"),
    Path("/app/data/golden.csv"),
]

TASK_COMPLETION_CRITERIA = """
Instruction:
You are a strict judge evaluating whether an AI agent has successfully completed a given task.
Analyze the user's 'input' (the task) and the agent's 'actual_output'.
The 'expected_output' field contains the success criteria for this task.
Score 1 if the agent's output clearly and unambiguously meets all success criteria.
Score 0 if the agent fails, provides an incomplete answer, or produces an error.
Your response must be a single float: 1.0 for success, 0.0 for failure.
"""

MULTI_TURN_CONSISTENCY_CRITERIA = """
Instruction:
You are a strict judge evaluating the conversational consistency of an AI assistant across multiple turns.
Analyze the 'input', which contains the full conversation transcript.
Score 1 if the assistant maintains context, remembers information from previous turns, and provides coherent, relevant responses throughout the conversation.
Score 0 if the assistant contradicts itself, forgets previous information, or gives responses that are out of context.
Your response must be a single float: 1.0 for perfect consistency, 0.0 for failure.
"""


def _resolve_existing_path(env_value: str, fallback_paths):
    """
    환경변수에 명시된 경로가 있으면 그것을 사용하고,
    없으면 러너가 일반적으로 배포되는 위치들을 순서대로 탐색해 첫 번째 존재 경로를 선택합니다.
    """
    if env_value:
        return Path(env_value).expanduser()
    for path in fallback_paths:
        if path.exists():
            return path
    return Path(fallback_paths[0])


GOLDEN_CSV_PATH = _resolve_existing_path(os.environ.get("GOLDEN_CSV_PATH"), DEFAULT_GOLDEN_PATHS)


langfuse = None
if Langfuse and LANGFUSE_PUBLIC_KEY:
    langfuse = Langfuse(
        public_key=LANGFUSE_PUBLIC_KEY,
        secret_key=LANGFUSE_SECRET_KEY,
        host=LANGFUSE_HOST,
    )


def _turn_sort_key(value):
    """
    turn_id를 정렬 가능한 값으로 바꿉니다.
    숫자는 숫자 순서대로, 문자열은 문자열 순서대로, 누락값은 가장 뒤로 보냅니다.
    """
    if value is None:
        return (1, 0)
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (0, str(value))


def load_dataset():
    """
    `golden.csv`를 읽어 conversation 단위로 그룹화합니다.
    `conversation_id`가 있으면 멀티턴으로 묶고, 없으면 각 row를 단일 턴 대화 1개로 취급합니다.
    """
    if not GOLDEN_CSV_PATH.exists():
        raise FileNotFoundError(f"Evaluation dataset not found at {GOLDEN_CSV_PATH}")

    df = pd.read_csv(GOLDEN_CSV_PATH)
    df = df.where(pd.notnull(df), None)
    records = df.to_dict(orient="records")

    if "conversation_id" not in df.columns:
        # 레거시 단일 턴 포맷입니다.
        return [[record] for record in records]

    grouped_conversations = {}
    grouped_order = []
    single_turn_conversations = []

    for record in records:
        conversation_id = record.get("conversation_id")
        if conversation_id:
            # 같은 conversation_id를 가진 row들을 하나의 대화로 모읍니다.
            conversation_key = str(conversation_id)
            if conversation_key not in grouped_conversations:
                grouped_conversations[conversation_key] = []
                grouped_order.append(conversation_key)
            grouped_conversations[conversation_key].append(record)
        else:
            # conversation_id가 비어 있으면 독립 대화로 유지합니다.
            single_turn_conversations.append([record])

    conversations = []
    for conversation_key in grouped_order:
        turns = grouped_conversations[conversation_key]
        if "turn_id" in df.columns:
            # turn_id 기준 정렬로 사용자-에이전트 문맥 순서를 고정합니다.
            turns = sorted(turns, key=lambda turn: _turn_sort_key(turn.get("turn_id")))
        conversations.append(turns)

    conversations.extend(single_turn_conversations)
    return conversations


def _safe_json_loads(raw_text: str):
    """JSON 파싱 실패를 예외 대신 None으로 돌려주는 안전 래퍼입니다."""
    try:
        return json.loads(raw_text)
    except Exception:
        return None


def _safe_json_list(raw_text: str):
    """
    DeepEval의 context 입력은 list를 기대하므로,
    문자열 JSON 또는 이미 파싱된 값을 받아 최종적으로 list만 반환합니다.
    """
    parsed = _safe_json_loads(raw_text) if isinstance(raw_text, str) else raw_text
    return parsed if isinstance(parsed, list) else []


def _config_path(filename: str) -> Path:
    """평가 러너 모듈 위치를 기준으로 설정 파일 절대경로를 계산합니다."""
    return CONFIG_ROOT / filename


def _build_judge_model():
    """
    Ollama 호환 OpenAI 엔드포인트를 사용하는 심판 LLM 객체를 생성합니다.
    모든 DeepEval/GEval 호출이 동일한 모델 설정을 공유하도록 중앙화합니다.
    """
    return GPTModel(model=JUDGE_MODEL, base_url=f"{OLLAMA_BASE_URL.rstrip('/')}/v1")


def _promptfoo_policy_check(raw_text: str):
    """
    Promptfoo CLI를 사용해 응답 원문에 금칙 패턴이 있는지 검사합니다.
    파일 기반 입력을 쓰는 이유는 긴 응답도 안정적으로 처리하고 문서 지시와 동일한 흐름을 유지하기 위해서입니다.
    """
    config_path = _config_path("security.yaml")
    if not config_path.exists():
        return

    tmp_input_path = None
    tmp_output_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp_input:
            tmp_input.write(raw_text or "")
            tmp_input_path = tmp_input.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp_output:
            tmp_output_path = tmp_output.name

        # 결과 JSON을 파일로 남겨 CLI 출력 포맷 변화와 무관하게 실패 건수를 읽습니다.
        command = [
            "promptfoo",
            "eval",
            "-c",
            str(config_path),
            "--prompts",
            f"file://{tmp_input_path}",
            "--output",
            tmp_output_path,
            "--fail-on-error",
        ]
        process = subprocess.run(command, capture_output=True, text=True)
        if process.returncode not in (0, 100):
            raise RuntimeError(process.stderr or process.stdout or "Promptfoo failed")

        if tmp_output_path and os.path.exists(tmp_output_path):
            with open(tmp_output_path, "r", encoding="utf-8") as result_file:
                result_payload = json.load(result_file)
            failures = (((result_payload or {}).get("results") or {}).get("stats") or {}).get("failures", 0)
            if failures:
                raise RuntimeError(f"Promptfoo policy checks reported {failures} failure(s).")
    finally:
        # 매 테스트마다 생성되는 임시 파일이 누적되지 않도록 반드시 제거합니다.
        if tmp_input_path and os.path.exists(tmp_input_path):
            os.unlink(tmp_input_path)
        if tmp_output_path and os.path.exists(tmp_output_path):
            os.unlink(tmp_output_path)


def _schema_validate(raw_text: str):
    """
    API 응답이 약속된 JSON 스키마를 만족하는지 검사합니다.
    UI 평가처럼 비JSON 응답이 자연스러운 경우는 상위 호출부에서 이 함수를 건너뜁니다.
    """
    schema_path = _config_path("schema.json")
    if not schema_path.exists():
        return

    with open(schema_path, "r", encoding="utf-8") as schema_file:
        schema = json.load(schema_file)

    try:
        parsed = json.loads(raw_text or "")
        validate(instance=parsed, schema=schema)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise RuntimeError(f"Format Compliance Failed (schema.json): {exc}") from exc


def _parse_success_criteria_mode(criteria: str) -> str:
    """
    success_criteria가 규칙 DSL인지, 자연어 GEval 기준인지, 비어 있는지를 판별합니다.
    기존 시험지 문법과 문서 예시를 동시에 지원하기 위한 분기 함수입니다.
    """
    if not criteria:
        return "none"
    if any(token in criteria for token in ("status_code=", "raw~r/", "json.")):
        return "dsl"
    return "geval"


def _json_get_path(obj, path: str):
    """
    json.foo.bar[0] 형태의 단순 경로 문법을 따라 값을 추출합니다.
    success_criteria의 `json.<path>~r/.../` 규칙을 처리하기 위한 최소 기능만 구현합니다.
    """
    current = obj
    for token in path.split("."):
        list_match = re.match(r"^([a-zA-Z0-9_\-]+)\[(\d+)\]$", token)
        if list_match:
            key, index = list_match.group(1), int(list_match.group(2))
            if not isinstance(current, dict) or key not in current:
                return None
            current = current[key]
            if not isinstance(current, list) or index >= len(current):
                return None
            current = current[index]
            continue

        if not isinstance(current, dict) or token not in current:
            return None
        current = current[token]
    return current


def _evaluate_rule_based_criteria(criteria_str: str, result) -> bool:
    """
    SUCCESS_CRITERIA_GUIDE.md의 규칙 기반 문법을 해석합니다.
    조건들은 모두 AND 관계로 처리하며, 하나라도 불일치하면 즉시 실패합니다.
    """
    if not criteria_str:
        return result.http_status == 200

    conditions = [condition.strip() for condition in criteria_str.split(" AND ")]
    parsed_json = _safe_json_loads(result.raw_response or "")

    for condition in conditions:
        if condition.startswith("status_code="):
            # HTTP 상태코드는 가장 직접적인 성공 신호입니다.
            expected_code = condition.split("=", 1)[1].strip()
            if str(result.http_status) != expected_code:
                return False
            continue

        if condition.startswith("raw~r/"):
            # raw_response 전체 문자열에 대한 정규식 매칭입니다.
            pattern = condition[len("raw~r/") :]
            if pattern.endswith("/"):
                pattern = pattern[:-1]
            if not re.search(pattern, result.raw_response or ""):
                return False
            continue

        if "~r/" in condition and condition.startswith("json."):
            # JSON 응답의 특정 경로 값을 꺼내 정규식으로 검증합니다.
            left, right = condition.split("~r/", 1)
            json_path = left.replace("json.", "", 1)
            pattern = right[:-1] if right.endswith("/") else right
            value = _json_get_path(parsed_json, json_path) if parsed_json is not None else None
            if value is None or not re.search(pattern, str(value)):
                return False
            continue

        return False

    return True


def _score_task_completion(turn, result, judge, span=None):
    """
    과업 완료 여부를 채점합니다.
    - DSL이면 결정론적 규칙 검사
    - 자연어면 GEval 심판 채점
    - 조건이 없으면 HTTP 성공 여부로 최소 판정
    """
    success_criteria = turn.get("success_criteria") or turn.get("expected_output")
    criteria_mode = _parse_success_criteria_mode(success_criteria)

    if criteria_mode == "dsl":
        score = 1.0 if _evaluate_rule_based_criteria(success_criteria, result) else 0.0
        reason = "Rule-based success_criteria evaluation"
    elif criteria_mode == "geval":
        # 문서가 권장하는 자연어 success_criteria는 GEval 심판이 판정합니다.
        task_completion_metric = GEval(
            name="TaskCompletion",
            criteria=TASK_COMPLETION_CRITERIA,
            evaluation_params=["input", "actual_output", "expected_output"],
            model=judge,
        )
        completion_test_case = LLMTestCase(
            input=turn["input"],
            actual_output=result.actual_output,
            expected_output=success_criteria,
        )
        task_completion_metric.measure(completion_test_case)
        score = float(task_completion_metric.score)
        reason = task_completion_metric.reason
    else:
        score = 1.0 if result.http_status < 400 else 0.0
        reason = "No success_criteria provided; falling back to HTTP success."

    if span:
        # 관제 시스템에서 턴별 과업 완료 점수를 바로 볼 수 있게 span에도 기록합니다.
        span.score(name="TaskCompletion", value=score, comment=reason)

    if score < 0.5:
        raise AssertionError(f"TaskCompletion failed with score {score}. Reason: {reason}")


def _score_deepeval_metrics(turn, result, judge, span=None):
    """
    문맥 기반 품질 지표를 수행합니다.
    기본 지표는 답변 관련성/유해성이고, retrieval_context가 있을 때만 RAG 지표를 추가합니다.
    """
    test_case = LLMTestCase(
        input=turn["input"],
        actual_output=result.actual_output,
        expected_output=turn.get("expected_output"),
        retrieval_context=result.retrieval_context,
        context=_safe_json_list(turn.get("context_ground_truth", "[]")),
    )

    metrics = [
        AnswerRelevancyMetric(threshold=0.8, model=judge),
        ToxicityMetric(threshold=0.5, model=judge),
    ]

    if result.retrieval_context:
        # 검색 문맥이 있어야 Faithfulness/Recall/Precision 지표가 의미를 가집니다.
        metrics.extend(
            [
                FaithfulnessMetric(threshold=0.9, model=judge),
                ContextualRecallMetric(threshold=0.8, model=judge),
            ]
        )
        if ContextualPrecisionMetric is not None:
            metrics.append(ContextualPrecisionMetric(threshold=0.8, model=judge))

    assert_test(test_case, metrics)

    if span:
        # 각 지표의 점수와 사유를 Langfuse에 개별 기록합니다.
        for metric in getattr(test_case, "metrics", []):
            span.score(name=metric.__class__.__name__, value=metric.score, comment=metric.reason)


@pytest.mark.parametrize("conversation", load_dataset())
def test_evaluation(conversation):
    """
    하나의 conversation을 끝까지 평가하는 메인 테스트입니다.
    문서의 흐름대로 어댑터 호출 -> Fail-Fast 검사 -> 과업 완료 -> 심층 평가 -> 멀티턴 평가를 수행합니다.
    """
    conv_id = conversation[0].get("conversation_id", conversation[0]["case_id"])
    parent_trace = None
    if langfuse:
        # conversation 단위 상위 trace를 만들고 모든 턴 span을 그 아래에 연결합니다.
        parent_trace = langfuse.trace(
            name=f"Conversation-{conv_id}",
            id=f"{RUN_ID}:{conv_id}",
            tags=[RUN_ID],
        )

    conversation_history = []
    full_conversation_passed = True
    adapter = AdapterRegistry.get_instance(TARGET_TYPE, TARGET_URL, API_KEY, TARGET_AUTH_HEADER)

    try:
        for turn in conversation:
            case_id = turn["case_id"]
            input_text = turn["input"]

            span = None
            if parent_trace:
                # 각 턴을 별도 span으로 남겨 어느 지점에서 실패했는지 추적 가능하게 합니다.
                span = parent_trace.span(name=f"Turn-{turn.get('turn_id', 1)}", input={"input": input_text})

            try:
                # 같은 conversation 안에서는 동일 어댑터 인스턴스를 재사용합니다.
                # 특히 ui_chat은 같은 브라우저 세션이 유지되어야 실제 멀티턴 검증이 됩니다.
                result = adapter.invoke(input_text, history=conversation_history)

                update_payload = {"output": result.to_dict()}
                if result.usage:
                    update_payload["usage"] = result.usage

                if span:
                    # 응답 원문, 사용량, 지연시간을 먼저 기록해 사후 분석 데이터를 확보합니다.
                    span.update(**update_payload)
                    span.score(name="Latency", value=result.latency_ms, comment="ms")

                if result.error:
                    raise RuntimeError(f"Adapter Error: {result.error}")

                # 1차 차단: 정책 위반 및 응답 규격 검사
                _promptfoo_policy_check(result.raw_response)
                if TARGET_TYPE == "http":
                    _schema_validate(result.raw_response)

                # 2차 평가: 과업 완료 여부 판정
                judge = _build_judge_model()
                _score_task_completion(turn, result, judge, span)

                # 다음 턴 입력에 사용할 수 있도록 assistant 응답을 대화 이력에 누적합니다.
                turn["actual_output"] = result.actual_output
                conversation_history.append(turn)

                # 3차 평가: 답변 품질 및 RAG 지표 측정
                _score_deepeval_metrics(turn, result, judge, span)
            except Exception as exc:
                full_conversation_passed = False
                pytest.fail(f"Turn failed for case_id {case_id}: {exc}")
            finally:
                if span:
                    # 실패 여부와 무관하게 span을 닫아 trace 구조를 깨지 않게 합니다.
                    span.end()

        if len(conversation) > 1:
            # 멀티턴 평가는 전체 대화록을 하나의 입력으로 다시 심판 모델에 제출합니다.
            full_transcript = ""
            for turn in conversation_history:
                full_transcript += f"User: {turn['input']}\n"
                full_transcript += f"Assistant: {turn['actual_output']}\n\n"

            judge = _build_judge_model()
            consistency_metric = GEval(
                name="MultiTurnConsistency",
                criteria=MULTI_TURN_CONSISTENCY_CRITERIA,
                evaluation_params=["input"],
                model=judge,
            )
            consistency_test_case = LLMTestCase(input=full_transcript, actual_output="")
            consistency_metric.measure(consistency_test_case)

            if parent_trace:
                parent_trace.score(
                    name=consistency_metric.name,
                    value=consistency_metric.score,
                    comment=consistency_metric.reason,
                )

            if consistency_metric.score < 0.5:
                pytest.fail(
                    f"MultiTurnConsistency failed for conversation {conv_id} with score "
                    f"{consistency_metric.score}. Reason: {consistency_metric.reason}"
                )

        if not full_conversation_passed:
            # 개별 턴 실패를 conversation 수준 실패로 다시 명시합니다.
            pytest.fail("One or more turns in the conversation failed.")
    finally:
        # conversation 단위 자원은 여기서 정리합니다.
        adapter.close()
