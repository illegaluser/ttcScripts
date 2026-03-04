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
from openai import OpenAI

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
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "qwen3-coder:30b")

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
GOLDEN_CSV_PATH = os.environ.get("GOLDEN_CSV_PATH", "/app/data/golden.csv")

def load_dataset():
    """
    `golden.csv`를 읽어 다중 턴 대화 단위로 그룹화하여 반환합니다.
    `conversation_id`가 없는 경우, 단일 턴 대화로 처리합니다.
    """
    if not os.path.exists(GOLDEN_CSV_PATH):
        raise FileNotFoundError(f"Evaluation dataset not found at {GOLDEN_CSV_PATH}")
    
    df = pd.read_csv(GOLDEN_CSV_PATH).where(pd.notnull(df), None)
    
    # 다중 턴 대화 지원
    if "conversation_id" in df.columns and "turn_id" in df.columns:
        conversations = []
        for _, group in df.groupby("conversation_id"):
            # turn_id 순서대로 정렬하여 대화 흐름을 보장
            sorted_group = group.sort_values(by="turn_id").to_dict(orient="records")
            conversations.append(sorted_group)
        return conversations
    else:
        # 단일 턴 시험 (레거시)
        return [ [record] for record in df.to_dict(orient="records") ]

# =========================
# Helpers
# =========================
def _safe_json_loads(s: str):
    try:
        return json.loads(s)
    except Exception:
        return None

def _json_get_path(obj, path: str):
    if obj is None or not path.startswith("json."):
        return None
    cur = obj
    tokens = path[5:].split(".")
    for tok in tokens:
        m = re.match(r"^([a-zA-Z0-9_\-]+)\[(\d+)\]$", tok)
        if m:
            key, idx = m.group(1), int(m.group(2))
            if not isinstance(cur, dict) or key not in cur or not isinstance(cur[key], list) or idx >= len(cur[key]):
                return None
            cur = cur[key][idx]
        else:
            if not isinstance(cur, dict) or tok not in cur:
                return None
            cur = cur[tok]
    return cur

def _evaluate_agent_criteria(criteria_str: str, result) -> bool:
    if not criteria_str:
        return result.http_status == 200
    conditions = [c.strip() for c in criteria_str.split(" AND ")]
    parsed = _safe_json_loads(result.raw_response)
    for cond in conditions:
        if "=" in cond and "~r/" not in cond:
            key, val = cond.split("=", 1)
            if key.strip() == "status_code" and str(result.http_status) != val.strip():
                return False
        elif "~r/" in cond:
            left, regex_part = cond.split("~r/", 1)
            regex = regex_part.rstrip("/")
            left = left.strip()
            target_text = ""
            if left == "raw":
                target_text = result.raw_response or ""
            elif left.startswith("json."):
                v = _json_get_path(parsed, left)
                target_text = str(v) if v is not None else ""
            if not re.search(regex, target_text):
                return False
    return True

def _promptfoo_policy_check(raw_text: str):
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
            tmp.write(raw_text or "")
            tmp_path = tmp.name
        
        cmd = ["promptfoo", "eval", "-c", "/app/configs/security.yaml", "--prompts", f"file://{tmp_path}", "-o", "json"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr or proc.stdout or "Promptfoo failed")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

def _schema_validate(raw_text: str):
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

def _evaluate_multi_turn(conversation_history, judge):
    """다중 턴 대화 전체의 일관성을 심판 LLM으로 평가합니다."""
    if not langfuse: return

    full_transcript = ""
    for turn in conversation_history:
        full_transcript += f"User: {turn['input']}\n"
        full_transcript += f"Assistant: {turn['actual_output']}\n\n"

    prompt = f"""The following is a conversation with an AI assistant. Your task is to evaluate the assistant's performance for overall conversational consistency. Consider if the assistant correctly remembers and references information from previous turns. Rate the consistency on a scale of 0 to 1, where 1 is perfectly consistent. Provide a brief reason for your score. Respond in JSON format with keys "score" and "reason".

<Conversation>
{full_transcript}
</Conversation>
"""
    try:
        client = OpenAI(base_url=f"{os.environ.get('OLLAMA_BASE_URL')}/v1", api_key="ollama")
        res = client.chat.completions.create(
            model=judge.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"}
        )
        result = json.loads(res.choices[0].message.content)
        return float(result.get("score", 0)), result.get("reason", "")
    except Exception as e:
        return 0, f"Failed to evaluate multi-turn consistency: {e}"

# =========================
# Tests
# =========================
@pytest.mark.parametrize("conversation", load_dataset())
def test_evaluation(conversation):
    conv_id = conversation[0].get("conversation_id", conversation[0]["case_id"])
    parent_trace = None
    if langfuse:
        parent_trace = langfuse.trace(name=f"Conversation-{conv_id}", id=f"{RUN_ID}:{conv_id}")

    conversation_history = []
    full_conversation_passed = True

    for turn in conversation:
        case_id = turn["case_id"]
        target_category = turn["target_type"]
        input_text = turn["input"]

        span = None
        if parent_trace:
            span = parent_trace.span(name=f"Turn-{turn.get('turn_id', 1)}", input={"input": input_text})

        try:
            adapter = AdapterRegistry.get_instance(TARGET_TYPE, TARGET_URL, API_KEY)
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
            _schema_validate(result.raw_response)

            turn["actual_output"] = result.actual_output # 대화 기록에 실제 출력 추가
            conversation_history.append(turn)

            if target_category == "agent":
                passed = _evaluate_agent_criteria(turn.get("success_criteria"), result)
                if span: span.score(name="TaskCompletion", value=1 if passed else 0)
                assert passed, "Agent Task Failed"
                continue

            judge = GPTModel(model=JUDGE_MODEL, base_url=f"{os.environ.get('OLLAMA_BASE_URL')}/v1")
            test_case = LLMTestCase(
                input=input_text,
                actual_output=result.actual_output,
                expected_output=turn.get("expected_output"),
                retrieval_context=result.retrieval_context,
                context=json.loads(turn.get("context_ground_truth", "[]") or "[]"),
            )
            metrics = [AnswerRelevancyMetric(threshold=0.8, model=judge)]
            if target_category == "rag":
                metrics.extend([
                    FaithfulnessMetric(threshold=0.9, model=judge),
                    ContextualRecallMetric(threshold=0.8, model=judge)
                ])
            
            for m in metrics:
                m.measure(test_case)
                if span:
                    span.score(name=m.__class__.__name__, value=m.score, comment=m.reason)
            assert_test(test_case, metrics)

        except Exception as e:
            full_conversation_passed = False
            pytest.fail(f"Turn failed for case_id {case_id}: {e}")
        finally:
            if span: span.end()

    if len(conversation) > 1:
        judge = GPTModel(model=JUDGE_MODEL, base_url=f"{os.environ.get('OLLAMA_BASE_URL')}/v1")
        score, reason = _evaluate_multi_turn(conversation_history, judge)
        if parent_trace:
            parent_trace.score(name="MultiTurnConsistency", value=score, comment=reason)
    
    if not full_conversation_passed:
        pytest.fail("One or more turns in the conversation failed.")