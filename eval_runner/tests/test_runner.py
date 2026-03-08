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
    if value is None:
        return (1, 0)
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (0, str(value))


def load_dataset():
    """
    `golden.csv`를 읽어 다중 턴 대화 단위로 그룹화하여 반환합니다.
    `conversation_id`가 없는 경우, 단일 턴 대화로 처리합니다.
    """

    if not GOLDEN_CSV_PATH.exists():
        raise FileNotFoundError(f"Evaluation dataset not found at {GOLDEN_CSV_PATH}")

    df = pd.read_csv(GOLDEN_CSV_PATH)
    df = df.where(pd.notnull(df), None)
    records = df.to_dict(orient="records")

    if "conversation_id" not in df.columns:
        return [[record] for record in records]

    grouped_conversations = {}
    grouped_order = []
    single_turn_conversations = []

    for record in records:
        conversation_id = record.get("conversation_id")
        if conversation_id:
            conversation_key = str(conversation_id)
            if conversation_key not in grouped_conversations:
                grouped_conversations[conversation_key] = []
                grouped_order.append(conversation_key)
            grouped_conversations[conversation_key].append(record)
        else:
            single_turn_conversations.append([record])

    conversations = []
    for conversation_key in grouped_order:
        turns = grouped_conversations[conversation_key]
        if "turn_id" in df.columns:
            turns = sorted(turns, key=lambda turn: _turn_sort_key(turn.get("turn_id")))
        conversations.append(turns)

    conversations.extend(single_turn_conversations)
    return conversations


def _safe_json_loads(raw_text: str):
    try:
        return json.loads(raw_text)
    except Exception:
        return None


def _safe_json_list(raw_text: str):
    parsed = _safe_json_loads(raw_text) if isinstance(raw_text, str) else raw_text
    return parsed if isinstance(parsed, list) else []


def _config_path(filename: str) -> Path:
    return CONFIG_ROOT / filename


def _build_judge_model():
    return GPTModel(model=JUDGE_MODEL, base_url=f"{OLLAMA_BASE_URL.rstrip('/')}/v1")


def _promptfoo_policy_check(raw_text: str):
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
        if tmp_input_path and os.path.exists(tmp_input_path):
            os.unlink(tmp_input_path)
        if tmp_output_path and os.path.exists(tmp_output_path):
            os.unlink(tmp_output_path)


def _schema_validate(raw_text: str):
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
    if not criteria:
        return "none"
    if any(token in criteria for token in ("status_code=", "raw~r/", "json.")):
        return "dsl"
    return "geval"


def _json_get_path(obj, path: str):
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
    if not criteria_str:
        return result.http_status == 200

    conditions = [condition.strip() for condition in criteria_str.split(" AND ")]
    parsed_json = _safe_json_loads(result.raw_response or "")

    for condition in conditions:
        if condition.startswith("status_code="):
            expected_code = condition.split("=", 1)[1].strip()
            if str(result.http_status) != expected_code:
                return False
            continue

        if condition.startswith("raw~r/"):
            pattern = condition[len("raw~r/") :]
            if pattern.endswith("/"):
                pattern = pattern[:-1]
            if not re.search(pattern, result.raw_response or ""):
                return False
            continue

        if "~r/" in condition and condition.startswith("json."):
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
    success_criteria = turn.get("success_criteria") or turn.get("expected_output")
    criteria_mode = _parse_success_criteria_mode(success_criteria)

    if criteria_mode == "dsl":
        score = 1.0 if _evaluate_rule_based_criteria(success_criteria, result) else 0.0
        reason = "Rule-based success_criteria evaluation"
    elif criteria_mode == "geval":
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
        span.score(name="TaskCompletion", value=score, comment=reason)

    if score < 0.5:
        raise AssertionError(f"TaskCompletion failed with score {score}. Reason: {reason}")


def _score_deepeval_metrics(turn, result, judge, span=None):
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
        for metric in getattr(test_case, "metrics", []):
            span.score(name=metric.__class__.__name__, value=metric.score, comment=metric.reason)


@pytest.mark.parametrize("conversation", load_dataset())
def test_evaluation(conversation):
    conv_id = conversation[0].get("conversation_id", conversation[0]["case_id"])
    parent_trace = None
    if langfuse:
        parent_trace = langfuse.trace(
            name=f"Conversation-{conv_id}",
            id=f"{RUN_ID}:{conv_id}",
            tags=[RUN_ID],
        )

    conversation_history = []
    full_conversation_passed = True

    for turn in conversation:
        case_id = turn["case_id"]
        input_text = turn["input"]

        span = None
        if parent_trace:
            span = parent_trace.span(name=f"Turn-{turn.get('turn_id', 1)}", input={"input": input_text})

        try:
            adapter = AdapterRegistry.get_instance(TARGET_TYPE, TARGET_URL, API_KEY, TARGET_AUTH_HEADER)
            result = adapter.invoke(input_text, history=conversation_history)

            update_payload = {"output": result.to_dict()}
            if result.usage:
                update_payload["usage"] = result.usage

            if span:
                span.update(**update_payload)
                span.score(name="Latency", value=result.latency_ms, comment="ms")

            if result.error:
                raise RuntimeError(f"Adapter Error: {result.error}")

            _promptfoo_policy_check(result.raw_response)
            if TARGET_TYPE == "http":
                _schema_validate(result.raw_response)

            judge = _build_judge_model()
            _score_task_completion(turn, result, judge, span)

            turn["actual_output"] = result.actual_output
            conversation_history.append(turn)

            _score_deepeval_metrics(turn, result, judge, span)
        except Exception as exc:
            full_conversation_passed = False
            pytest.fail(f"Turn failed for case_id {case_id}: {exc}")
        finally:
            if span:
                span.end()

    if len(conversation) > 1:
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
        pytest.fail("One or more turns in the conversation failed.")
