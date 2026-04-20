# TTC — Offline All-in-One Images

이 폴더는 폐쇄망 반출용 **독립 All-in-One 이미지 2종**을 담습니다. 각 하위 폴더는 자체 완결되어 있어 **폴더만 압축해서 다른 워크스테이션으로 옮겨도 바로 빌드 및 구동이 가능**합니다.

## 구성

| 이미지 | 폴더 | 대상 파이프라인 | 특징 |
|--------|------|----------------|------|
| **Playwright Zero-Touch QA** | [playwright-allinone/](playwright-allinone/) | E2E 테스트 자동 생성·실행·치유 | 호스트 Ollama + 호스트 Jenkins agent 하이브리드, Playwright headed Chromium |
| **Code & AI Quality** | [code-AI-quality-allinone/](code-AI-quality-allinone/) | 코드 사전학습 / 정적분석 / 이슈등록 / AI 평가 | docker compose 로 GitLab 까지 스택 구성, Dify/Sonar/Jenkins 전체 자동 프로비저닝 |

두 이미지는 서로 **포트·볼륨·Docker 네트워크가 완전히 분리**되어 같은 호스트에서 동시에 기동 가능합니다. 기존 루트 `docker-compose.yaml` / `e2e-pipeline/docker-compose.yaml` 스택과도 공존합니다.

## 빌드 진입점

각 이미지는 자체 완결 폴더입니다. 해당 폴더 안에서 실행하면 됩니다.

```bash
# Playwright 이미지 (호스트 Ollama + 호스트 agent 하이브리드)
cd e2e-pipeline/offline/playwright-allinone
bash build.sh

# Code & AI Quality 이미지 (docker compose 스택: allinone + gitlab)
cd e2e-pipeline/offline/code-AI-quality-allinone
bash scripts/download-plugins.sh          # 최초 1회 — Jenkins + Dify 플러그인 (온라인)
bash scripts/build-wsl2.sh                # 또는 scripts/build-mac.sh
```

세부 가이드는 각 폴더의 `README.md` 를 참고.

두 이미지는 이제 **상호 의존이 없습니다**. 각각 독립적으로 빌드·배포 가능합니다.
