"""
DSCORE Zero-Touch QA v4.0 — CLI 엔트리포인트.

사용법:
  python3 -m zero_touch_qa --mode chat
  python3 -m zero_touch_qa --mode doc --file upload.pdf
  python3 -m zero_touch_qa --mode convert --file recorded.py
  python3 -m zero_touch_qa --mode execute --scenario scenario.json
"""

import argparse
import json
import logging
import os
import sys

from . import __version__
from .config import Config
from .converter import convert_playwright_to_dsl
from .dify_client import DifyClient, DifyConnectionError
from .executor import QAExecutor
from .report import build_html_report, save_run_log, save_scenario
from .regression_generator import generate_regression_test

log = logging.getLogger("zero_touch_qa")


def main():
    parser = argparse.ArgumentParser(
        description=f"DSCORE Zero-Touch QA v{__version__}"
    )
    parser.add_argument(
        "--mode",
        choices=["chat", "doc", "convert", "execute"],
        required=True,
        help="chat: 자연어, doc: 기획서 업로드, convert: Playwright 녹화 변환, execute: 기존 시나리오 재실행",
    )
    parser.add_argument("--file", default=None, help="기획서 또는 Playwright .py 파일 경로")
    parser.add_argument("--scenario", default=None, help="기존 scenario.json 경로 (execute 모드)")
    parser.add_argument("--target-url", default=None, help="테스트 시작 URL")
    parser.add_argument("--srs-text", default=None, help="자연어 요구사항 (chat 모드)")
    parser.add_argument("--headed", action="store_true", default=True, help="실제 브라우저 표시 (기본값)")
    parser.add_argument("--headless", action="store_true", help="헤드리스 모드")
    parser.add_argument("-v", "--verbose", action="store_true", help="상세 로그 출력")
    args = parser.parse_args()

    # 로깅 설정
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    config = Config.from_env()
    headed = not args.headless

    # 환경변수 폴백 (Jenkins에서 env로 전달하는 경우)
    target_url = args.target_url or os.getenv("TARGET_URL", "")
    srs_text = args.srs_text or os.getenv("SRS_TEXT", "")

    try:
        scenario = _prepare_scenario(args, config, target_url, srs_text)
    except DifyConnectionError as e:
        log.error("Dify 연결 실패: %s", e)
        _generate_error_report(config.artifacts_dir, str(e))
        sys.exit(1)
    except FileNotFoundError as e:
        log.error("%s", e)
        sys.exit(1)

    if not scenario:
        log.error("시나리오가 비어 있습니다.")
        sys.exit(1)

    # 원본 시나리오 저장
    save_scenario(scenario, config.artifacts_dir)

    # 실행
    log.info("시나리오 실행 시작 (%d스텝, headed=%s)", len(scenario), headed)
    executor = QAExecutor(config)
    results = executor.execute(scenario, headed=headed)

    # 산출물 생성
    save_run_log(results, config.artifacts_dir)
    save_scenario(scenario, config.artifacts_dir, suffix=".healed")
    build_html_report(results, config.artifacts_dir, version=__version__)
    generate_regression_test(scenario, results, config.artifacts_dir)

    # 결과 요약
    passed = sum(1 for r in results if r.status in ("PASS", "HEALED"))
    failed = sum(1 for r in results if r.status == "FAIL")
    log.info("실행 완료 — PASS: %d, FAIL: %d", passed, failed)

    if failed > 0:
        sys.exit(1)


def _prepare_scenario(
    args, config: Config, target_url: str, srs_text: str
) -> list[dict]:
    """모드에 따라 시나리오를 준비한다."""
    if args.mode == "execute":
        if not args.scenario:
            raise FileNotFoundError("execute 모드에는 --scenario 인자가 필요합니다.")
        with open(args.scenario, "r", encoding="utf-8") as f:
            scenario = json.load(f)
        log.info("[Scenario] %s 로드 (%d스텝)", args.scenario, len(scenario))
        return scenario

    if args.mode == "convert":
        if not args.file:
            raise FileNotFoundError("convert 모드에는 --file 인자가 필요합니다.")
        return convert_playwright_to_dsl(args.file, config.artifacts_dir)

    # chat / doc 모드: Dify 호출
    dify = DifyClient(config)
    file_id = None

    if args.mode == "doc":
        if not args.file:
            log.warning("[Doc] --file 인자가 없습니다. SRS_TEXT로 대체합니다.")
        else:
            file_id = dify.upload_file(args.file)

    scenario = dify.generate_scenario(
        run_mode=args.mode,
        srs_text=srs_text,
        target_url=target_url,
        file_id=file_id,
    )
    log.info("[Dify] 시나리오 수신 (%d스텝)", len(scenario))
    return scenario


def _generate_error_report(artifacts_dir: str, error_msg: str):
    """Dify 연결 실패 시 최소한의 에러 리포트를 생성한다."""
    os.makedirs(artifacts_dir, exist_ok=True)
    html = f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="utf-8"><title>Zero-Touch QA Error</title></head>
<body style="font-family: sans-serif; margin: 40px; color: #991b1b;">
  <h1>Zero-Touch QA 실행 실패</h1>
  <p style="background: #fee2e2; padding: 16px; border-radius: 8px;">
    <strong>Dify 연결 실패:</strong> {error_msg}
  </p>
  <p>Dify 서비스 상태를 확인하십시오.</p>
</body>
</html>"""
    path = os.path.join(artifacts_dir, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("[Error Report] %s 생성", path)


if __name__ == "__main__":
    main()
