import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from html import escape
from pathlib import Path

import pandas as pd
import pytest
from jsonschema import validate
from jsonschema.exceptions import ValidationError

from deepeval.metrics import (
    AnswerRelevancyMetric,
    ContextualRecallMetric,
    ContextualPrecisionMetric,
    FaithfulnessMetric,
    GEval,
    ToxicityMetric,
)
from deepeval.models.llms.ollama_model import OllamaModel
from deepeval.test_case import LLMTestCase, LLMTestCaseParams

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
REPORT_DIR = Path(os.environ.get("REPORT_DIR", "/var/knowledges/eval/reports"))
REPORT_JSON_PATH = REPORT_DIR / "summary.json"
REPORT_HTML_PATH = REPORT_DIR / "summary.html"

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

METRIC_GUIDE = {
    "PolicyCheck": {
        "description": "응답 원문에서 개인정보, 비밀 토큰, 카드번호 같은 금칙 패턴이 노출되지 않았는지 검사합니다.",
        "pass_rule": "금칙 패턴이 없으면 PASS, 하나라도 검출되면 FAIL",
    },
    "SchemaValidation": {
        "description": "HTTP 응답 JSON이 약속한 스키마를 만족하는지 검사합니다.",
        "pass_rule": "스키마 검증 성공 시 PASS, 누락/형식 오류가 있으면 FAIL",
    },
    "TaskCompletion": {
        "description": "success_criteria 또는 expected_output 기준으로 과업을 실제로 달성했는지 판정합니다.",
        "pass_rule": "score >= task_completion threshold 이면 PASS",
    },
    "AnswerRelevancyMetric": {
        "description": "질문 의도에 비해 답변이 얼마나 직접적이고 관련성 있게 작성되었는지 평가합니다.",
        "pass_rule": "score >= answer_relevancy threshold 이면 PASS",
    },
    "ToxicityMetric": {
        "description": "응답에 혐오, 차별, 공격적 표현 같은 유해성이 있는지 평가합니다.",
        "pass_rule": "DeepEval 기준으로 threshold 이하의 유해성일 때 PASS",
    },
    "FaithfulnessMetric": {
        "description": "답변이 retrieval_context의 사실에 충실하고 환각이 없는지 평가합니다.",
        "pass_rule": "score >= faithfulness threshold 이면 PASS",
    },
    "ContextualRecallMetric": {
        "description": "질문에 답하는 데 필요한 근거 문맥을 충분히 검색해왔는지 평가합니다.",
        "pass_rule": "score >= contextual_recall threshold 이면 PASS",
    },
    "ContextualPrecisionMetric": {
        "description": "검색된 문맥에 불필요한 노이즈가 적고 관련 근거가 중심인지 평가합니다.",
        "pass_rule": "score >= contextual_precision threshold 이면 PASS",
    },
    "MultiTurnConsistency": {
        "description": "여러 턴에 걸쳐 기억 유지, 맥락 일관성, 모순 여부를 종합 평가합니다.",
        "pass_rule": "score >= multi_turn_consistency threshold 이면 PASS",
    },
    "Latency": {
        "description": "질문 전송부터 응답 수신 완료까지 걸린 시간(ms)입니다.",
        "pass_rule": "정보성 지표이며 기본 PASS/FAIL 기준은 없음",
    },
}


class ExpectedFailureNotTriggered(AssertionError):
    """
    expected-fail 케이스가 실제로는 통과했을 때 구분하기 위한 전용 예외입니다.
    """


def _env_float(name: str, default: float) -> float:
    """
    환경변수 숫자 파라미터를 안전하게 float로 읽습니다.
    Jenkins 문자열 파라미터가 비어 있거나 잘못 들어와도 기본값으로 복구합니다.
    """
    raw_value = os.environ.get(name)
    if raw_value is None or not str(raw_value).strip():
        return default
    try:
        return float(raw_value)
    except ValueError:
        return default


ANSWER_RELEVANCY_THRESHOLD = _env_float("ANSWER_RELEVANCY_THRESHOLD", 0.7)
TOXICITY_THRESHOLD = _env_float("TOXICITY_THRESHOLD", 0.5)
FAITHFULNESS_THRESHOLD = _env_float("FAITHFULNESS_THRESHOLD", 0.9)
CONTEXTUAL_RECALL_THRESHOLD = _env_float("CONTEXTUAL_RECALL_THRESHOLD", 0.8)
CONTEXTUAL_PRECISION_THRESHOLD = _env_float("CONTEXTUAL_PRECISION_THRESHOLD", 0.8)
TASK_COMPLETION_THRESHOLD = _env_float("TASK_COMPLETION_THRESHOLD", 0.5)
MULTI_TURN_CONSISTENCY_THRESHOLD = _env_float("MULTI_TURN_CONSISTENCY_THRESHOLD", 0.5)

REPORT_DIR.mkdir(parents=True, exist_ok=True)


def _build_summary_state():
    """
    Jenkins/로컬 실행 모두에서 공통으로 쓰는 리포트 메타데이터 뼈대를 만듭니다.
    모든 대화 결과는 이 상태 객체에 누적된 뒤 JSON/HTML로 직렬화됩니다.
    """
    return {
        "run_id": RUN_ID,
        "generated_at": int(time.time()),
        "build_id": os.environ.get("BUILD_ID"),
        "build_tag": os.environ.get("BUILD_TAG"),
        "build_url": os.environ.get("BUILD_URL"),
        "target_url": TARGET_URL,
        "target_type": TARGET_TYPE,
        "judge_model": JUDGE_MODEL,
        "ollama_base_url": OLLAMA_BASE_URL,
        "golden_csv_path": "",
        "langfuse_enabled": False,
        "thresholds": {
            "task_completion": TASK_COMPLETION_THRESHOLD,
            "answer_relevancy": ANSWER_RELEVANCY_THRESHOLD,
            "toxicity": TOXICITY_THRESHOLD,
            "faithfulness": FAITHFULNESS_THRESHOLD,
            "contextual_recall": CONTEXTUAL_RECALL_THRESHOLD,
            "contextual_precision": CONTEXTUAL_PRECISION_THRESHOLD,
            "multi_turn_consistency": MULTI_TURN_CONSISTENCY_THRESHOLD,
        },
        "metric_guide": METRIC_GUIDE,
        "totals": {},
        "metric_averages": {},
        "conversations": [],
    }


SUMMARY_STATE = None


def _append_metric_average(metric_scores: dict, metric_name: str, score):
    """
    평균 계산 시 점수가 있는 항목만 누적합니다.
    실패했더라도 score가 계산된 metric은 평균에 포함해 전체 품질 추세를 볼 수 있게 합니다.
    """
    if score is None:
        return
    metric_scores.setdefault(metric_name, []).append(float(score))


def _recompute_summary_totals():
    """
    conversation 결과 배열을 기준으로 총계와 metric 평균을 재계산합니다.
    중간에 특정 conversation이 실패하더라도 최신 상태가 summary 파일에 반영되도록 매번 전체를 다시 계산합니다.
    """
    conversations = SUMMARY_STATE["conversations"]
    total_turns = 0
    passed_turns = 0
    failed_turns = 0
    passed_conversations = 0
    failed_conversations = 0
    metric_scores = {}

    for conversation in conversations:
        if conversation.get("status") in ("passed", "passed_with_expected_failure"):
            passed_conversations += 1
        else:
            failed_conversations += 1

        multi_turn_detail = conversation.get("multi_turn_consistency")
        if multi_turn_detail:
            _append_metric_average(metric_scores, multi_turn_detail["name"], multi_turn_detail.get("score"))

        for turn in conversation.get("turns", []):
            total_turns += 1
            if turn.get("status") in ("passed", "expected_fail_matched"):
                passed_turns += 1
            else:
                failed_turns += 1

            task_completion = turn.get("task_completion")
            if task_completion:
                _append_metric_average(metric_scores, task_completion["name"], task_completion.get("score"))

            for metric_detail in turn.get("metrics", []):
                _append_metric_average(metric_scores, metric_detail["name"], metric_detail.get("score"))

    total_conversations = len(conversations)
    SUMMARY_STATE["totals"] = {
        "conversations": total_conversations,
        "passed_conversations": passed_conversations,
        "failed_conversations": failed_conversations,
        "turns": total_turns,
        "passed_turns": passed_turns,
        "failed_turns": failed_turns,
        "conversation_pass_rate": round((passed_conversations / total_conversations) * 100, 2)
        if total_conversations
        else 0.0,
        "turn_pass_rate": round((passed_turns / total_turns) * 100, 2) if total_turns else 0.0,
    }
    SUMMARY_STATE["metric_averages"] = {
        metric_name: round(sum(scores) / len(scores), 4) for metric_name, scores in metric_scores.items() if scores
    }


def _render_metric_list(metric_details):
    """
    HTML 리포트에 표시할 metric 리스트를 렌더링합니다.
    각 항목에 score/threshold/pass 여부와 이유를 함께 남겨 Jenkins 아티팩트만으로도 원인 파악이 가능하게 합니다.
    """
    if not metric_details:
        return "<em>No metric results</em>"

    items = []
    for metric in metric_details:
        status = metric.get("status")
        if not status:
            passed = metric.get("passed")
            if passed is True:
                status = "PASS"
            elif passed is False:
                status = "FAIL"
            else:
                status = "SKIPPED"
        reason = escape(str(metric.get("reason") or metric.get("error") or ""))
        score = metric.get("score")
        threshold = metric.get("threshold")
        score_display = "-" if score is None else score
        threshold_display = "-" if threshold is None else threshold
        items.append(
            "<li>"
            f"<strong>{escape(metric['name'])}</strong> "
            f"[{status}] score={score_display}, threshold={threshold_display}"
            f"<br><span>{reason}</span>"
            "</li>"
        )
    return "<ul>" + "".join(items) + "</ul>"


def _skipped_metric(name: str, reason: str):
    """요약 화면에서 미실행 지표를 명시적으로 SKIPPED 상태로 표현합니다."""
    return {
        "name": name,
        "score": None,
        "threshold": None,
        "passed": None,
        "reason": reason,
        "error": None,
        "status": "SKIPPED",
    }


def _format_token_usage(usage):
    """어댑터별 키 차이를 흡수해 토큰 사용량을 단일 문자열로 표시합니다."""
    if not usage or not isinstance(usage, dict):
        return "-"

    prompt = usage.get("promptTokens")
    completion = usage.get("completionTokens")
    total = usage.get("totalTokens")
    if prompt is None:
        prompt = usage.get("prompt_tokens")
    if completion is None:
        completion = usage.get("completion_tokens")
    if total is None:
        total = usage.get("total_tokens")
    if total is None and (prompt is not None or completion is not None):
        total = int(prompt or 0) + int(completion or 0)

    if prompt is None and completion is None and total is None:
        return "-"
    return f"prompt={int(prompt or 0)}, completion={int(completion or 0)}, total={int(total or 0)}"


def _build_task_completion_display(turn: dict):
    """
    Task Completion 표시용 리스트를 구성합니다.
    실제 측정값이 없으면 실패 지점에 맞춰 SKIPPED 이유를 보여줍니다.
    """
    task_completion = turn.get("task_completion")
    if task_completion:
        return [task_completion]

    failure = str(turn.get("failure_message") or "")
    if "Adapter Error" in failure:
        reason = "Skipped because adapter invocation failed before Task Completion."
    elif "Promptfoo policy checks reported" in failure:
        reason = "Skipped because policy check failed."
    elif "Format Compliance Failed" in failure:
        reason = "Skipped because schema validation failed."
    else:
        reason = "Skipped due to earlier stage failure."
    return [_skipped_metric("TaskCompletion", reason)]


def _build_deepeval_metrics_display(turn: dict):
    """
    DeepEval 지표 표시 리스트를 구성합니다.
    - 실행된 지표는 실제 결과를 그대로 사용
    - 실행되지 않은 지표는 SKIPPED로 보강
    """
    metric_names = [
        "AnswerRelevancyMetric",
        "ToxicityMetric",
        "FaithfulnessMetric",
        "ContextualRecallMetric",
        "ContextualPrecisionMetric",
    ]
    existing_metrics = turn.get("metrics", []) or []
    existing_by_name = {metric.get("name"): metric for metric in existing_metrics}
    display_metrics = []

    task_completion = turn.get("task_completion")
    failure = str(turn.get("failure_message") or "")
    expected_fail_matched = turn.get("status") == "expected_fail_matched"
    has_retrieval_context = bool(turn.get("has_retrieval_context"))
    has_context_ground_truth = bool(turn.get("has_context_ground_truth"))

    for metric_name in metric_names:
        if metric_name in existing_by_name:
            display_metrics.append(existing_by_name[metric_name])
            continue

        if task_completion and not task_completion.get("passed"):
            reason = "Skipped because Task Completion failed."
        elif "Adapter Error" in failure:
            reason = "Skipped because adapter invocation failed."
        elif "Promptfoo policy checks reported" in failure:
            reason = "Skipped because policy check failed."
        elif "Format Compliance Failed" in failure:
            reason = "Skipped because schema validation failed."
        elif expected_fail_matched:
            reason = "Skipped because expected-fail case matched and conversation stopped early."
        elif metric_name in (
            "FaithfulnessMetric",
            "ContextualRecallMetric",
            "ContextualPrecisionMetric",
        ) and not (has_retrieval_context and has_context_ground_truth):
            reason = "Skipped because retrieval_context/context_ground_truth was not available."
        else:
            reason = "Skipped due to earlier stage failure."

        display_metrics.append(_skipped_metric(metric_name, reason))

    return display_metrics


def _build_multi_turn_display(conversation: dict):
    """
    Multi-turn 지표 표시를 구성합니다.
    1턴 대화나 조기 종료 대화에서도 SKIPPED 이유를 명시합니다.
    """
    if conversation.get("multi_turn_consistency"):
        return [conversation["multi_turn_consistency"]]

    turns = conversation.get("turns", []) or []
    if len(turns) <= 1:
        reason = "Skipped because this conversation has a single turn."
    elif any(turn.get("status") == "expected_fail_matched" for turn in turns):
        reason = "Skipped because expected-fail case matched before conversation completion."
    else:
        reason = "Skipped because the conversation failed before multi-turn evaluation."
    return [_skipped_metric("MultiTurnConsistency", reason)]


def _render_summary_html():
    """
    Jenkins 아티팩트 탭에서 바로 열어볼 수 있는 단일 HTML 리포트를 생성합니다.
    별도 플러그인 없이도 케이스별 점수와 실패 이유를 한 화면에서 확인하는 목적입니다.
    """
    totals = SUMMARY_STATE["totals"]
    metric_averages = SUMMARY_STATE["metric_averages"]
    conversations_html = []

    for conversation in SUMMARY_STATE["conversations"]:
        turn_rows = []
        for turn in conversation.get("turns", []):
            fail_fast_parts = []
            if turn.get("policy_check"):
                fail_fast_parts.append(f"Policy: {'PASS' if turn['policy_check']['passed'] else 'FAIL'}")
            if turn.get("schema_check"):
                schema_status = turn["schema_check"].get("status", "skipped")
                fail_fast_parts.append(f"Schema: {schema_status.upper()}")
            fail_fast = "<br>".join(fail_fast_parts) if fail_fast_parts else "-"

            task_completion_html = _render_metric_list(_build_task_completion_display(turn))
            metrics_html = _render_metric_list(_build_deepeval_metrics_display(turn))
            token_usage_html = escape(_format_token_usage(turn.get("usage")))
            actual_output = escape(str(turn.get("actual_output") or ""))

            turn_rows.append(
                "<tr>"
                f"<td>{escape(str(turn.get('case_id') or ''))}</td>"
                f"<td>{escape(str(turn.get('expected_outcome') or 'pass'))}</td>"
                f"<td>{escape(str(turn.get('status') or 'unknown'))}</td>"
                f"<td>{escape(str(turn.get('latency_ms') or '-'))}</td>"
                f"<td>{token_usage_html}</td>"
                f"<td>{fail_fast}</td>"
                f"<td>{task_completion_html}</td>"
                f"<td>{metrics_html}</td>"
                f"<td><details><summary>show</summary><pre>{actual_output}</pre></details></td>"
                f"<td>{escape(str(turn.get('failure_message') or '-'))}</td>"
                "</tr>"
            )

        multi_turn_html = _render_metric_list(_build_multi_turn_display(conversation))

        conversations_html.append(
            "<section class='conversation'>"
            f"<h2>{escape(str(conversation.get('conversation_key')))}</h2>"
            f"<p>Status: <strong>{escape(str(conversation.get('status')))}</strong></p>"
            f"<p>Failure: {escape(str(conversation.get('failure_message') or '-'))}</p>"
            f"<p>Multi-turn: {multi_turn_html}</p>"
            "<table>"
            "<thead><tr>"
            "<th>Case ID</th><th>Expected</th><th>Status</th><th>Latency(ms)</th><th>Token Usage</th><th>Fail-Fast</th>"
            "<th>Task Completion</th><th>Metrics</th><th>Actual Output</th><th>Failure</th>"
            "</tr></thead>"
            f"<tbody>{''.join(turn_rows)}</tbody>"
            "</table>"
            "</section>"
        )

    metric_average_rows = "".join(
        f"<tr><td>{escape(name)}</td><td>{value}</td></tr>" for name, value in sorted(metric_averages.items())
    )
    threshold_rows = "".join(
        f"<tr><td>{escape(name)}</td><td>{value}</td></tr>"
        for name, value in sorted(SUMMARY_STATE["thresholds"].items())
    )
    metric_guide_rows = "".join(
        "<tr>"
        f"<td>{escape(metric_name)}</td>"
        f"<td>{escape(str(metric_meta.get('description') or ''))}</td>"
        f"<td>{escape(str(metric_meta.get('pass_rule') or ''))}</td>"
        "</tr>"
        for metric_name, metric_meta in sorted((SUMMARY_STATE.get("metric_guide") or {}).items())
    )

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>AI Agent Evaluation Summary</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #111827; background: #f8fafc; }}
    h1, h2 {{ margin: 0 0 12px; }}
    .meta, .cards, .conversation {{ margin-bottom: 24px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }}
    .card {{ background: white; border: 1px solid #dbe2ea; border-radius: 10px; padding: 16px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; }}
    th, td {{ border: 1px solid #dbe2ea; padding: 10px; vertical-align: top; text-align: left; }}
    th {{ background: #e2e8f0; }}
    pre {{ white-space: pre-wrap; word-break: break-word; margin: 0; }}
    ul {{ margin: 0; padding-left: 20px; }}
    .conversation {{ border: 1px solid #dbe2ea; border-radius: 10px; padding: 16px; background: white; }}
  </style>
</head>
<body>
  <h1>AI Agent Evaluation Summary</h1>
  <div class="meta">
    <p>Run ID: <strong>{escape(str(SUMMARY_STATE['run_id']))}</strong></p>
    <p>Target: {escape(str(SUMMARY_STATE.get('target_url') or ''))} ({escape(str(SUMMARY_STATE.get('target_type') or ''))})</p>
    <p>Judge Model: {escape(str(SUMMARY_STATE.get('judge_model') or ''))}</p>
    <p>Langfuse Enabled: {'YES' if SUMMARY_STATE.get('langfuse_enabled') else 'NO'}</p>
  </div>
  <div class="cards">
    <div class="card"><strong>Conversations</strong><br>{totals.get('conversations', 0)}</div>
    <div class="card"><strong>Passed Conversations</strong><br>{totals.get('passed_conversations', 0)}</div>
    <div class="card"><strong>Failed Conversations</strong><br>{totals.get('failed_conversations', 0)}</div>
    <div class="card"><strong>Conversation Pass Rate</strong><br>{totals.get('conversation_pass_rate', 0)}%</div>
    <div class="card"><strong>Turns</strong><br>{totals.get('turns', 0)}</div>
    <div class="card"><strong>Turn Pass Rate</strong><br>{totals.get('turn_pass_rate', 0)}%</div>
  </div>
  <section class="conversation">
    <h2>Thresholds</h2>
    <table><thead><tr><th>Metric</th><th>Threshold</th></tr></thead><tbody>{threshold_rows}</tbody></table>
  </section>
  <section class="conversation">
    <h2>Metric Guide</h2>
    <table>
      <thead><tr><th>Metric</th><th>Description</th><th>Pass / Fail Rule</th></tr></thead>
      <tbody>{metric_guide_rows}</tbody>
    </table>
  </section>
  <section class="conversation">
    <h2>Metric Averages</h2>
    <table><thead><tr><th>Metric</th><th>Average Score</th></tr></thead><tbody>{metric_average_rows}</tbody></table>
  </section>
  {''.join(conversations_html)}
</body>
</html>"""


def _write_summary_report():
    """
    최신 누적 상태를 JSON/HTML 두 형식으로 모두 저장합니다.
    JSON은 후처리/자동화용, HTML은 Jenkins 아티팩트에서 사람이 바로 읽기 위한 용도입니다.
    """
    _recompute_summary_totals()
    SUMMARY_STATE["generated_at"] = int(time.time())

    with open(REPORT_JSON_PATH, "w", encoding="utf-8") as report_file:
        json.dump(SUMMARY_STATE, report_file, ensure_ascii=False, indent=2)

    with open(REPORT_HTML_PATH, "w", encoding="utf-8") as report_file:
        report_file.write(_render_summary_html())


def _upsert_conversation_report(conversation_report: dict):
    """
    conversation 단위 결과를 메모리 상태에 반영하고 즉시 리포트 파일을 갱신합니다.
    테스트 중간 실패가 있어도 마지막으로 성공한 대화와 실패 원인이 Jenkins에 남도록 설계합니다.
    """
    conversation_key = conversation_report["conversation_key"]
    for index, existing in enumerate(SUMMARY_STATE["conversations"]):
        if existing["conversation_key"] == conversation_key:
            SUMMARY_STATE["conversations"][index] = conversation_report
            _write_summary_report()
            return

    SUMMARY_STATE["conversations"].append(conversation_report)
    _write_summary_report()


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


SUMMARY_STATE = _build_summary_state()
SUMMARY_STATE["golden_csv_path"] = str(GOLDEN_CSV_PATH)
SUMMARY_STATE["langfuse_enabled"] = bool(langfuse)


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


def _is_blank_value(value) -> bool:
    """
    CSV 로딩 후 들어오는 None/NaN/공백 문자열을 모두 비어 있는 값으로 취급합니다.
    단일턴 케이스가 `conversation_id=nan`으로 잘못 묶이는 것을 막기 위한 정규화입니다.
    """
    if value is None:
        return True
    if pd.isna(value):
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


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
        if not _is_blank_value(conversation_id):
            # 같은 conversation_id를 가진 row들을 하나의 대화로 모읍니다.
            conversation_key = str(conversation_id)
            if conversation_key not in grouped_conversations:
                grouped_conversations[conversation_key] = []
                grouped_order.append(conversation_key)
            grouped_conversations[conversation_key].append(record)
        else:
            # conversation_id가 비어 있으면 독립 대화로 유지합니다.
            record["conversation_id"] = None
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


def _is_expected_failure_case(turn: dict) -> bool:
    """
    케이스의 기대 결과가 실패인지 판별합니다.
    1순위: expected_outcome/expected_result/expected_status/should_fail 컬럼
    2순위: case_id 네이밍 규칙(*-FAIL-*)
    """
    for key in ("expected_outcome", "expected_result", "expected_status"):
        value = turn.get(key)
        if value is None:
            continue
        normalized = str(value).strip().lower()
        if normalized in ("fail", "failed", "negative", "expect_fail", "expected_fail", "f"):
            return True
        if normalized in ("pass", "passed", "positive", "expect_pass", "expected_pass", "p"):
            return False

    should_fail = turn.get("should_fail")
    if should_fail is not None:
        normalized = str(should_fail).strip().lower()
        if normalized in ("1", "true", "y", "yes"):
            return True
        if normalized in ("0", "false", "n", "no"):
            return False

    case_id = str(turn.get("case_id") or "").upper()
    return "-FAIL-" in case_id or case_id.startswith("FAIL-") or case_id.endswith("-FAIL")


def _compact_output_for_relevancy(text: str) -> str:
    """
    AnswerRelevancyMetric에 장문/코드블록/이모지 노이즈가 주는 영향을 줄이기 위해
    첫 핵심 문장만 추출합니다.
    """
    if not text:
        return ""

    normalized = re.sub(r"```[\s\S]*?```", " ", str(text))
    normalized = re.sub(r"`([^`]*)`", r"\1", normalized)
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if not lines:
        return ""

    first_line = lines[0]
    first_line = re.sub(r"[\U00010000-\U0010FFFF]", "", first_line)
    first_line = re.sub(r"\s+", " ", first_line).strip()
    return first_line[:300]


def _config_path(filename: str) -> Path:
    """평가 러너 모듈 위치를 기준으로 설정 파일 절대경로를 계산합니다."""
    return CONFIG_ROOT / filename


def _build_judge_model():
    """
    DeepEval 최신 OllamaModel을 사용해 심판 LLM 객체를 생성합니다.
    모든 DeepEval/GEval 호출이 동일한 모델 설정을 공유하도록 중앙화합니다.
    """
    return OllamaModel(model=JUDGE_MODEL, base_url=OLLAMA_BASE_URL.rstrip("/"))


def _promptfoo_relpath(path: Path, base_dir: Path) -> str:
    """
    Promptfoo CLI는 절대경로를 작업 디렉터리에 다시 붙여 해석하는 경우가 있어
    항상 명시적으로 고정한 기준 디렉터리 상대경로로 넘깁니다.
    """
    return os.path.relpath(path, start=base_dir)


def _promptfoo_policy_check(raw_text: str):
    """
    Promptfoo 최신 CLI의 `--assertions` + `--model-outputs` 흐름으로
    응답 원문에 금칙 패턴이 있는지 검사합니다.
    컨테이너 이미지에 promptfoo를 고정 설치해 매 실행 시 다운로드를 피합니다.
    """
    config_path = _config_path("security.yaml")
    if not config_path.exists():
        return

    tmp_dir = None
    promptfoo_cwd = Path(__file__).resolve().parent
    try:
        tmp_dir = Path(tempfile.mkdtemp(prefix=".promptfoo-", dir=promptfoo_cwd))
        model_outputs_path = tmp_dir / f"{uuid.uuid4().hex}-outputs.json"
        result_path = tmp_dir / f"{uuid.uuid4().hex}-result.json"

        with open(model_outputs_path, "w", encoding="utf-8") as output_file:
            json.dump([raw_text or ""], output_file, ensure_ascii=False)

        # 결과 JSON을 파일로 남겨 CLI 출력 포맷 변화와 무관하게 실패/에러 건수를 읽습니다.
        command = [
            "promptfoo",
            "eval",
            "--assertions",
            _promptfoo_relpath(config_path, promptfoo_cwd),
            "--model-outputs",
            _promptfoo_relpath(model_outputs_path, promptfoo_cwd),
            "--output",
            _promptfoo_relpath(result_path, promptfoo_cwd),
            "--no-write",
            "--no-table",
        ]
        process = subprocess.run(command, capture_output=True, text=True, cwd=promptfoo_cwd)
        if process.returncode not in (0, 100) and not result_path.exists():
            raise RuntimeError(process.stderr or process.stdout or "Promptfoo failed")

        if result_path.exists():
            with open(result_path, "r", encoding="utf-8") as result_file:
                result_payload = json.load(result_file) or {}
            stats = ((result_payload.get("results") or {}).get("stats") or {})
            failures = stats.get("failures", 0)
            errors = stats.get("errors", 0)
            if errors:
                raise RuntimeError(f"Promptfoo policy checks reported {errors} error(s).")
            if failures:
                raise RuntimeError(f"Promptfoo policy checks reported {failures} failure(s).")
    finally:
        # Promptfoo 임시 산출물이 워크스페이스에 누적되지 않게 대화별로 정리합니다.
        if tmp_dir and tmp_dir.exists():
            for child in tmp_dir.iterdir():
                child.unlink(missing_ok=True)
            tmp_dir.rmdir()


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
            evaluation_params=[
                LLMTestCaseParams.INPUT,
                LLMTestCaseParams.ACTUAL_OUTPUT,
                LLMTestCaseParams.EXPECTED_OUTPUT,
            ],
            model=judge,
            async_mode=False,
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

    return {
        "name": "TaskCompletion",
        "mode": criteria_mode,
        "score": score,
        "threshold": TASK_COMPLETION_THRESHOLD,
        "passed": score >= TASK_COMPLETION_THRESHOLD,
        "reason": reason,
    }


def _score_deepeval_metrics(turn, result, judge, span=None):
    """
    문맥 기반 품질 지표를 수행합니다.
    기본 지표는 답변 관련성/유해성이고, retrieval_context가 있을 때만 RAG 지표를 추가합니다.
    """
    base_test_case = LLMTestCase(
        input=turn["input"],
        actual_output=result.actual_output,
        expected_output=turn.get("expected_output"),
        retrieval_context=result.retrieval_context,
        context=_safe_json_list(turn.get("context_ground_truth", "[]")),
    )

    metrics = [
        AnswerRelevancyMetric(threshold=ANSWER_RELEVANCY_THRESHOLD, model=judge, async_mode=False),
        ToxicityMetric(threshold=TOXICITY_THRESHOLD, model=judge, async_mode=False),
    ]

    if result.retrieval_context and base_test_case.context:
        # 검색 문맥이 있어야 Faithfulness/Recall/Precision 지표가 의미를 가집니다.
        metrics.extend(
            [
                FaithfulnessMetric(threshold=FAITHFULNESS_THRESHOLD, model=judge, async_mode=False),
                ContextualRecallMetric(threshold=CONTEXTUAL_RECALL_THRESHOLD, model=judge, async_mode=False),
            ]
        )
        metrics.append(ContextualPrecisionMetric(threshold=CONTEXTUAL_PRECISION_THRESHOLD, model=judge, async_mode=False))

    metric_results = []
    for metric in metrics:
        if isinstance(metric, AnswerRelevancyMetric):
            compact_test_case = LLMTestCase(
                input=turn["input"],
                actual_output=_compact_output_for_relevancy(result.actual_output),
                expected_output=turn.get("expected_output"),
                retrieval_context=result.retrieval_context,
                context=_safe_json_list(turn.get("context_ground_truth", "[]")),
            )
            metric.measure(compact_test_case)
        else:
            metric.measure(base_test_case)
        if span:
            span.score(name=metric.__class__.__name__, value=metric.score, comment=metric.reason)
        metric_results.append(
            {
                "name": metric.__class__.__name__,
                "score": metric.score,
                "threshold": metric.threshold,
                "passed": False if metric.error else metric.is_successful(),
                "reason": metric.reason,
                "error": metric.error,
            }
        )

    return metric_results


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
    expected_failure_matched = False
    adapter = AdapterRegistry.get_instance(TARGET_TYPE, TARGET_URL, API_KEY, TARGET_AUTH_HEADER)
    judge = _build_judge_model()
    conversation_report = {
        "conversation_id": conv_id if conversation[0].get("conversation_id") is not None else None,
        "conversation_key": str(conv_id),
        "status": "passed",
        "failure_message": "",
        "turns": [],
        "multi_turn_consistency": None,
    }

    try:
        for turn in conversation:
            case_id = turn["case_id"]
            input_text = turn["input"]
            expected_failure = _is_expected_failure_case(turn)
            turn_report = {
                "case_id": case_id,
                "turn_id": turn.get("turn_id"),
                "input": input_text,
                "expected_outcome": "fail" if expected_failure else "pass",
                "status": "passed",
                "latency_ms": None,
                "policy_check": None,
                "schema_check": None,
                "task_completion": None,
                "metrics": [],
                "actual_output": "",
                "usage": None,
                "has_retrieval_context": False,
                "has_context_ground_truth": bool(_safe_json_list(turn.get("context_ground_truth", "[]"))),
                "failure_message": "",
            }

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
                    turn_report["usage"] = result.usage
                turn_report["has_retrieval_context"] = bool(result.retrieval_context)

                if span:
                    # 응답 원문, 사용량, 지연시간을 먼저 기록해 사후 분석 데이터를 확보합니다.
                    span.update(**update_payload)
                    span.score(name="Latency", value=result.latency_ms, comment="ms")
                turn_report["latency_ms"] = result.latency_ms

                if result.error:
                    raise RuntimeError(f"Adapter Error: {result.error}")

                # 1차 차단: 정책 위반 및 응답 규격 검사
                _promptfoo_policy_check(result.raw_response)
                turn_report["policy_check"] = {"name": "PolicyCheck", "passed": True}
                if TARGET_TYPE == "http":
                    _schema_validate(result.raw_response)
                    turn_report["schema_check"] = {"name": "SchemaValidation", "status": "passed"}
                else:
                    turn_report["schema_check"] = {"name": "SchemaValidation", "status": "skipped"}

                # 2차 평가: 과업 완료 여부 판정
                task_completion_detail = _score_task_completion(turn, result, judge, span)
                turn_report["task_completion"] = task_completion_detail
                if not task_completion_detail["passed"]:
                    raise AssertionError(
                        f"TaskCompletion failed with score {task_completion_detail['score']}. "
                        f"Reason: {task_completion_detail['reason']}"
                    )

                # 다음 턴 입력에 사용할 수 있도록 assistant 응답을 대화 이력에 누적합니다.
                turn["actual_output"] = result.actual_output
                turn_report["actual_output"] = result.actual_output
                conversation_history.append(turn)

                # 3차 평가: 답변 품질 및 RAG 지표 측정
                metric_results = _score_deepeval_metrics(turn, result, judge, span)
                turn_report["metrics"] = metric_results
                failed_metrics = []
                for metric in metric_results:
                    if metric["error"]:
                        failed_metrics.append(f"{metric['name']}: {metric['error']}")
                    elif not metric["passed"]:
                        failed_metrics.append(
                            f"{metric['name']} (score={metric['score']}, threshold={metric['threshold']}): "
                            f"{metric['reason']}"
                        )
                if failed_metrics:
                    raise AssertionError("Metrics failed: " + "; ".join(failed_metrics))
                if expected_failure:
                    raise ExpectedFailureNotTriggered(
                        "This case is marked as expected-fail, but all checks passed."
                    )
            except Exception as exc:
                if expected_failure and not isinstance(exc, ExpectedFailureNotTriggered):
                    # 의도된 실패 시나리오가 실제로 실패했으므로 테스트 목적상 PASS로 처리합니다.
                    expected_failure_matched = True
                    turn_report["status"] = "expected_fail_matched"
                    turn_report["failure_message"] = str(exc)
                    conversation_report["status"] = "passed_with_expected_failure"
                    conversation_report["failure_message"] = (
                        f"Expected failure observed at case_id {case_id}: {exc}"
                    )
                    break

                full_conversation_passed = False
                turn_report["status"] = "failed"
                turn_report["failure_message"] = str(exc)
                conversation_report["status"] = "failed"
                conversation_report["failure_message"] = str(exc)
                pytest.fail(f"Turn failed for case_id {case_id}: {exc}")
            finally:
                conversation_report["turns"].append(turn_report)
                if span:
                    # 실패 여부와 무관하게 span을 닫아 trace 구조를 깨지 않게 합니다.
                    span.end()

        if len(conversation) > 1 and not expected_failure_matched:
            # 멀티턴 평가는 전체 대화록을 하나의 입력으로 다시 심판 모델에 제출합니다.
            full_transcript = ""
            for turn in conversation_history:
                full_transcript += f"User: {turn['input']}\n"
                full_transcript += f"Assistant: {turn['actual_output']}\n\n"

            consistency_metric = GEval(
                name="MultiTurnConsistency",
                criteria=MULTI_TURN_CONSISTENCY_CRITERIA,
                evaluation_params=[LLMTestCaseParams.INPUT],
                model=judge,
                async_mode=False,
            )
            consistency_test_case = LLMTestCase(input=full_transcript, actual_output="")
            consistency_metric.measure(consistency_test_case)

            if parent_trace:
                parent_trace.score(
                    name=consistency_metric.name,
                    value=consistency_metric.score,
                    comment=consistency_metric.reason,
                )

            conversation_report["multi_turn_consistency"] = {
                "name": consistency_metric.name,
                "score": float(consistency_metric.score),
                "threshold": MULTI_TURN_CONSISTENCY_THRESHOLD,
                "passed": consistency_metric.score >= MULTI_TURN_CONSISTENCY_THRESHOLD,
                "reason": consistency_metric.reason,
            }

            if consistency_metric.score < MULTI_TURN_CONSISTENCY_THRESHOLD:
                conversation_report["status"] = "failed"
                conversation_report["failure_message"] = consistency_metric.reason
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
        _upsert_conversation_report(conversation_report)
