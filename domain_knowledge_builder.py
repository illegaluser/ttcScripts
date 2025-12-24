#!/usr/bin/env python3
import asyncio
import os
import sys
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode

# DSCORE-TTC 표준 경로
RESULT_DIR = "/var/knowledges/docs/result"

def log(msg: str) -> None:
    print(f"[Crawl-Universal] {msg}", flush=True)

def refine_any_tech_blog(html_content):
    """
    기술 블로그의 특성(코드 블록, 본문)을 살리되 
    메뉴, 사이드바, URL 링크 소음을 완벽히 제거한다.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 1. 일반적인 본문 컨테이너 후보들 탐색 (우아한형제들, 네이버, 위키 등 공용)
    content_area = None
    candidates = ['article', '.post-content', '.entry-content', '.content', 'main', '#content']
    for selector in candidates:
        content_area = soup.select_one(selector)
        if content_area: break
    
    if not content_area:
        content_area = soup.body # 최후의 수단

    # 2. 확실한 노이즈 요소 물리적 제거
    for unwanted in content_area.select('nav, footer, aside, .sidebar, .menu, .ads, script, style, header'):
        unwanted.decompose()

    # 3. 텍스트 추출 (마크다운 형식과 유사하게 추출)
    text = content_area.get_text(separator='\n')

    # 4. 강력한 정규식 정제 (이경석 님의 핵심 요청: URL 제거)
    # (1) [문구](http...) 형태에서 URL만 삭제하고 문구만 남김
    text = re.sub(r'\[([^\]]+)\]\(https?://\S+\)', r'\1', text)
    # (2) 텍스트에 노출된 일반 URL(http://...) 모두 삭제
    text = re.sub(r'https?://\S+', '', text)
    # (3) 위키백과 등에서 흔한 [편집], [1] 등의 기호 삭제
    text = re.sub(r'\[편집\]|\[\d+\]', '', text)
    # (4) 자잘한 특수기호나 중복 빈 줄 정리
    text = re.sub(r'\n\s*\n', '\n\n', text)
    
    return text.strip()

async def build_universal_knowledge(root_url):
    os.makedirs(RESULT_DIR, exist_ok=True)
    
    # 에러를 방지하기 위해 필터 옵션을 비우고 원본을 가져오는 데 집중
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    )

    async with AsyncWebCrawler() as crawler:
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
                    # 모든 사이트 공용 정제 로직 통과
                    clean_text = refine_any_tech_blog(res.html)
                    
                    if len(clean_text) < 300: continue # 너무 짧은 데이터는 무시

                    # 안전한 파일명 생성
                    safe_name = url.split("//")[-1].replace("/", "_").replace(".", "_")[:100]
                    out_path = Path(RESULT_DIR) / f"tech_{safe_name}.md"
                    
                    with open(out_path, "w", encoding="utf-8") as f:
                        f.write(f"# Source: {url}\n\n{clean_text}")
                    
                    log(f"[{i+1}/{len(urls_to_crawl)}] 클린 지식 확보: {url}")
                else:
                    log(f"[{i+1}/{len(urls_to_crawl)}] 건너뜀 (응답 없음)")
            except Exception as e:
                log(f"Error: {url} 처리 중 오류 - {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(1)
    asyncio.run(build_universal_knowledge(sys.argv[1]))