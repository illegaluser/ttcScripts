#!/usr/bin/env python3
import asyncio
import os
import sys
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode, BrowserConfig

# 목적:
# - Jenkins 파이프라인에서 웹/기술 블로그 URL을 받아 HTML을 가져온다.
# - 본문만 남기고 메뉴/광고/사이드바/링크 같은 소음을 제거한 텍스트를 뽑는다.
# - 정제된 텍스트를 Markdown 파일로 저장해서 Dify 지식베이스 업로드 전에 “미리 정리된 웹 지식”을 확보한다.
# - 같은 로직을 여러 도메인에 적용하기 위해, CSS 셀렉터나 정규식은 최대한 범용적으로 구성한다.
# - 지나치게 짧은 결과나 잡음만 남은 경우는 저장하지 않아, 지식 품질을 유지한다.
# 원칙:
# - 공유 경로(RESULT_DIR)를 사용해 doc_processor.py 업로드 파이프라인과 동일한 출력 위치를 유지한다.
# - 메뉴/광고/링크 등 소음을 제거하고, 300자 미만 짧은 결과는 저장하지 않아 품질을 담보한다.
# - 내부 링크 수집은 동일 도메인으로 제한해 불필요한 외부 크롤을 피한다.
# 기대결과: 정제된 웹 텍스트 MD들이 생성되어, Dify 지식베이스에 추가할 수 있는 웹 지식이 확보된다.

# DSCORE-TTC 표준 경로
RESULT_DIR = "/var/knowledges/docs/result"

def log(msg: str) -> None:
    """
    크롤링 진행 상황을 한 줄씩 남긴다.
    - Jenkins 콘솔 로그에서 단계별 진행률을 눈으로 확인할 수 있다.
    - flush=True로 즉시 출력해 긴 작업에서도 로그가 지연되지 않도록 한다.
    """
    print(f"[Crawl-Universal] {msg}", flush=True)

def refine_any_tech_blog(html_content):
    """
    HTML에서 본문 텍스트만 추출하고 광고, 메뉴 등 노이즈를 제거하는 핵심 정제 함수입니다.
    
    1. 본문 후보 영역 탐색: article, main 등 본문일 확률이 높은 태그를 우선 탐색합니다.
    2. 노이즈 제거: nav, footer, sidebar 등 본문과 상관없는 UI 요소를 물리적으로 삭제(decompose)합니다.
    3. 텍스트 추출: 줄바꿈을 유지하며 순수 텍스트만 뽑아냅니다.
    4. 정규식 정제: 텍스트 내에 포함된 URL, 위키 편집 기호 등을 삭제하여 지식의 순도를 높입니다.
    
      * [문구](http...)에서 링크만 제거하고 문구는 살린다.
      * 노출된 http://... 문자열은 모두 비운다.
      * 위키에서 흔한 [편집], [1] 같은 표기는 없애 깔끔하게 만든다.
      * 연속된 빈 줄을 하나로 줄여 가독성을 높인다.
    - 결과: 코드 블록/본문은 최대한 보존하고, 클릭용 링크나 광고성 문구는 없어진 텍스트 문자열이 반환된다.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    log(f"Refining HTML content (Raw size: {len(html_content)} bytes)")
    
    # [Step 1] 본문 후보 영역 탐색 - 텍스트 길이가 가장 긴 영역을 본문으로 간주합니다.
    content_area = None
    max_text_len = 0
    candidates = ['article', 'main', '.post-content', '.entry-content', '.content', '#content', 'section', '.post-body', '.prose']
    
    for selector in candidates:
        elements = soup.select(selector)
        for el in elements:
            # 단순 텍스트 길이를 비교하여 가장 본문다운 영역을 찾음
            current_len = len(el.get_text(strip=True))
            if current_len > max_text_len:
                max_text_len = current_len
                content_area = el
    
    if content_area:
        log(f"Selected best content area (Length: {max_text_len} chars)")
    else:
        log("Warning: No specific content area found, falling back to <body>")
        content_area = soup.body

    if not content_area:
        return ""

    # [Step 2] 확실한 노이즈 요소 제거 - 본문 내부에 섞인 네비게이션이나 광고 요소를 삭제합니다.
    unwanted_selectors = 'nav, footer, aside, .sidebar, .menu, .ads, script, style, header, .nav, .footer, .header, .bottom, .related, .comments'
    removed_count = 0
    for unwanted in content_area.select(unwanted_selectors):
        unwanted.decompose()
        removed_count += 1
    log(f"Removed {removed_count} noise elements.")

    # [Step 3] 텍스트 추출 - 개행 문자를 구분자로 사용하여 문단 구조를 유지합니다.
    text = content_area.get_text(separator='\n')
    log(f"Extracted text length: {len(text)} chars")

    # 4. 강력한 정규식 정제 (이경석 님의 핵심 요청: URL 제거)
    # (1) [문구](http...) 형태에서 URL만 삭제하고 문구만 남김
    text = re.sub(r'\[([^\]]+)\]\(https?://\S+\)', r'\1', text)
    # (2) 텍스트에 노출된 일반 URL(http://...) 모두 삭제
    text = re.sub(r'https?://\S+', '', text)
    # (3) 위키백과 등에서 흔한 [편집], [1] 등의 기호 삭제
    text = re.sub(r'\[편집\]|\[\d+\]', '', text)
    # (4) 자잘한 특수기호나 중복 빈 줄 정리
    text = re.sub(r'\n\s*\n', '\n\n', text)
    
    log(f"Final cleaned text length: {len(text.strip())} chars")
    return text.strip()

async def build_universal_knowledge(root_url):
    """
    주어진 URL부터 페이지(및 필요한 경우 하위 내부 링크)를 수집해 정제한 후 MD로 저장한다.
    - 실행 전: RESULT_DIR를 만들어 두어 파일 저장 실패를 막는다.
    - 1) 1차 요청으로 대상 페이지를 열고 성공 여부를 확인한다.
         * 실패하면 바로 종료해 불필요한 반복을 막는다.
    - 2) 크롤 대상 URL 목록을 만든다.
         * 일반적으로 입력받은 URL 하나만 넣는다.
         * 블로그 메인처럼 보이는 경우(루트로 끝나거나 우아한형제들 블로그 도메인) 내부 링크를 추가로 모은다.
           - 링크를 루트와 같은 도메인으로 제한해 외부 사이트로 퍼지지 않게 한다.
           - path 길이가 1보다 커야(“/”가 아닌 경우) 실제 글로 본다.
    - 3) 각 URL을 비동기로 가져와 refine_any_tech_blog로 정제한다.
         * HTML이 없거나 실패하면 건너뛴다.
         * 결과 텍스트가 300자 미만이면 품질이 낮다고 보고 저장하지 않는다.
    - 4) URL을 안전한 파일명으로 바꿔 tech_<URL>.md 형태로 저장한다.
         * 슬래시/점은 언더스코어로 치환하고, 너무 긴 이름은 100자로 자른다.
         * 파일 상단에 Source URL을 남겨 추적 가능하게 한다.
    - 5) 처리 결과를 로그로 남겨 진행률을 확인한다.
    """
    os.makedirs(RESULT_DIR, exist_ok=True)
    
    # Docker/Jenkins 환경 최적화 브라우저 설정 (핵심: no-sandbox, disable-dev-shm-usage)
    browser_config = BrowserConfig(
        headless=True,
        verbose=True,
        extra_args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
    )

    # 에러를 방지하기 위해 필터 옵션을 비우고 원본을 가져오는 데 집중
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        stream=False,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    )

    async with AsyncWebCrawler(config=browser_config) as crawler:
        log(f"Phase 1: 타겟 분석 시작 - {root_url}")
        result = await crawler.arun(url=root_url, config=run_config)
        
        if not result.success:
            log(f"Error: {result.error_message}")
            return

        # 단일 페이지 스크래핑인지 도메인 전체인지 판단 (URL에 게시글 ID가 있으면 단일 페이지로 처리)
        # 여기서는 입력받은 URL을 최우선으로 수집
        urls_to_crawl = [root_url]
        
        # 만약 메인 페이지라면 하위 링크를 수집 (우아한형제들 블로그 홈일 경우)
        if root_url.endswith('/') or 'techblog.woowahan.com' in root_url:
            base_domain = urlparse(root_url).netloc
            internal_urls = {
                urljoin(root_url, link['href']) 
                for link in result.links.get("internal", [])
                if base_domain in urljoin(root_url, link['href']) and 
                   len(urlparse(urljoin(root_url, link['href'])).path) > 1 # 루트 제외
            }
            urls_to_crawl = list(set(urls_to_crawl) | internal_urls)

        log(f"Phase 2: 총 {len(urls_to_crawl)}개 페이지 정밀 정제 시작...")

        for i, url in enumerate(urls_to_crawl):
            try:
                res = await crawler.arun(url=url, config=run_config)
                if res.success and res.html:
                    # 모든 사이트 공용 정제 로직 통과: 본문만 남기고 소음을 없앤다.
                    clean_text = refine_any_tech_blog(res.html)
                    
                    # 너무 짧은 데이터는 무시해 품질을 관리한다.
                    if len(clean_text) < 300:
                        log(f"[{i+1}/{len(urls_to_crawl)}] 무시됨: 텍스트가 너무 짧음 ({len(clean_text)}자)")
                        continue

                    # 안전한 파일명 생성
                    safe_name = url.split("//")[-1].replace("/", "_").replace(".", "_")[:100]
                    out_path = Path(RESULT_DIR) / f"tech_{safe_name}.md"
                    
                    with open(out_path, "w", encoding="utf-8") as f:
                        # 출처 URL을 파일 상단에 남겨, 추후 역추적이 가능하도록 한다.
                        f.write(f"# Source: {url}\n\n{clean_text}")
                    
                    log(f"[{i+1}/{len(urls_to_crawl)}] 클린 지식 확보 성공: {out_path}")
                else:
                    log(f"[{i+1}/{len(urls_to_crawl)}] 건너뜀 (응답 없음)")
            except Exception as e:
                log(f"Error: {url} 처리 중 오류 - {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(1)
    asyncio.run(build_universal_knowledge(sys.argv[1]))
