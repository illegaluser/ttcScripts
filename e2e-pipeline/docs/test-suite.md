# Zero-Touch QA 정상동작 검증 SRS 15개

Jenkins `Build with Parameters` 에 투입할 회귀 시나리오 셋. 각 항목의
`TARGET_URL` / `SRS_TEXT` 를 그대로 복붙해서 사용한다.

## 사용법

1. Jenkins → `DSCORE-ZeroTouch-QA-Docker` → **Build with Parameters**
2. `MODE = chat`
3. 아래 표의 **TARGET_URL**, **SRS_TEXT** 복붙
4. 빌드 → HTML 리포트 + `final_state.png` 확인

부담되면 **스모크 셋 5개** (#1 · #5 · #6 · #10 · #15) 먼저.

## 15개를 고르는 기준

1. 9대 DSL(`navigate·click·fill·press·select·check·hover·wait·verify`) 모두 최소 1회 등장
2. 자가 치유 각 단계(fallback / LocalHealer / Dify heal / 휴리스틱 B·E·H·J) 를 골고루 유발
3. 새 탭 전환, 비동기 로딩, negative test 등 엣지 케이스 포함
4. 봇 차단 심한 사이트(Yahoo·Google·Amazon) 회피 — 대신 Naver·DuckDuckGo·Wikipedia·GitHub·the-internet.herokuapp.com 활용

---

## 그룹 A. 검색엔진 패턴 (3개)

### 1. Naver 검색 (한글 UI)

```
TARGET_URL=https://www.naver.com
```

```
1. 검색창에 "ktds" 를 입력 후 엔터
2. 검색결과 목록이 정상 출력되는지 확인
3. 검색결과 중 첫 번째 링크 클릭
```

**기대**: 5 스텝 전부 PASS/HEALED. `#main_pack` 에서 J(검색결과 visible) 휴리스틱 매치 가능. 첫 결과 클릭 시 새 탭 전환.

---

### 2. DuckDuckGo 검색 (봇 친화 베이스라인)

```
TARGET_URL=https://duckduckgo.com
```

```
1. 검색창에 "Playwright automation" 입력 후 엔터
2. 검색결과에 "playwright.dev" 도메인이 포함되는지 확인
3. 검색결과 중 첫 번째 링크 클릭
```

**기대**: 봇 차단 없이 통과. jitter 없이도 성공해야 하는 기준선.

---

### 3. 한글 Wikipedia 본문 검증

```
TARGET_URL=https://ko.wikipedia.org
```

```
1. 검색창에 "대한민국" 입력 후 엔터
2. 본문에 "서울" 이라는 단어가 포함되는지 확인
3. 목차의 "역사" 섹션 링크 클릭
```

**기대**: verify 의 `inner_text` 텍스트 매칭 경로. H(검색창) 휴리스틱 유도 가능.

---

## 그룹 B. 폼/입력 컴포넌트 (4개)

### 4. the-internet Inputs — 숫자 입력

```
TARGET_URL=https://the-internet.herokuapp.com/inputs
```

```
1. 숫자 입력창에 "42" 입력
2. 입력된 값이 "42" 인지 확인
```

**기대**: 최단 2 스텝. `fill` + `verify(value)` 검증.

---

### 5. the-internet Checkboxes — check 액션

```
TARGET_URL=https://the-internet.herokuapp.com/checkboxes
```

```
1. 첫 번째 체크박스를 해제
2. 두 번째 체크박스를 체크
3. 두 번째 체크박스가 체크 상태인지 확인
```

**기대**: `check`/`uncheck` 분기(value=off) 첫 사용.

---

### 6. the-internet Dropdown — select 액션

```
TARGET_URL=https://the-internet.herokuapp.com/dropdown
```

```
1. 드롭다운에서 "Option 1" 선택
2. 선택된 옵션이 "Option 1" 인지 확인
```

**기대**: `select_option(label=...)` 분기 첫 사용.

---

### 7. httpbin 폼 submit — 다중 필드

```
TARGET_URL=https://httpbin.org/forms/post
```

```
1. Customer name 에 "ktds tester" 입력
2. Telephone 에 "010-1234-5678" 입력
3. Email 에 "test@example.com" 입력
4. Submit order 버튼 클릭
5. 응답 페이지에 "ktds tester" 문자열이 포함되는지 확인
```

**기대**: 멀티 `fill` + `click(submit)` + 페이지 전환 + 응답 본문 verify.

---

## 그룹 C. 인증/보안 (2개)

### 8. the-internet Login — 정상 로그인 (positive)

```
TARGET_URL=https://the-internet.herokuapp.com/login
```

```
1. Username 에 "tomsmith" 입력
2. Password 에 "SuperSecretPassword!" 입력
3. Login 버튼 클릭
4. 페이지에 "You logged into a secure area" 문자열 확인
```

**기대**: 전체 PASS. URL 이 `/secure` 로 변경.

---

### 9. the-internet Login — 잘못된 비밀번호 (negative)

```
TARGET_URL=https://the-internet.herokuapp.com/login
```

```
1. Username 에 "tomsmith" 입력
2. Password 에 "wrong_password" 입력
3. Login 버튼 클릭
4. 페이지에 "Your password is invalid" 문자열 확인
```

**기대**: 전체 PASS — 에러 메시지가 예상값이므로 verify 도 PASS. "의도된 실패" 패턴이 정직하게 녹색으로 끝나는지 검증.

---

## 그룹 D. 동적/비동기 (3개)

### 10. the-internet Dynamic Loading — wait 액션

```
TARGET_URL=https://the-internet.herokuapp.com/dynamic_loading/2
```

```
1. Start 버튼 클릭
2. 5초 대기
3. 페이지에 "Hello World!" 가 표시되는지 확인
```

**기대**: `wait` 액션 첫 등장. Planner 가 명시적 wait 를 DSL 로 뽑아내는지가 관건.

---

### 11. the-internet Disappearing Elements — 조건부 visible

```
TARGET_URL=https://the-internet.herokuapp.com/disappearing_elements
```

```
1. 페이지의 "Home" 링크가 보이는지 확인
2. "About" 링크가 보이는지 확인
```

**기대**: `verify` 의 `is_visible` 분기(값 없는 verify).

---

### 12. the-internet Hovers — hover 액션

```
TARGET_URL=https://the-internet.herokuapp.com/hovers
```

```
1. 첫 번째 사용자 프로필 이미지에 마우스를 가져간다
2. "name: user1" 문자열이 표시되는지 확인
```

**기대**: `hover` 액션 첫 등장. 호버 후 동적 텍스트 확인.

---

## 그룹 E. 페이지/탭 전환 (2개)

### 13. the-internet Multiple Windows — 새 탭 전환

```
TARGET_URL=https://the-internet.herokuapp.com/windows
```

```
1. "Click Here" 링크 클릭
2. 새로 열린 탭에 "New Window" 문자열이 표시되는지 확인
```

**기대**: 새 탭 감지 → `page` rebind → verify 가 새 탭 기준으로 실행. B-1 chrome-error 필터의 "유효한 새 탭" 반대 케이스.

---

### 14. GitHub 리포 탐색 — 멀티 네비게이션

```
TARGET_URL=https://github.com
```

```
1. 검색창에 "microsoft/playwright" 입력 후 엔터
2. 검색결과 중 "microsoft/playwright" 리포 링크 클릭
3. README 영역에 "Playwright" 문자열 확인
```

**기대**: 검색 → 클릭 → SPA 네비게이션 → 본문 verify.

---

## 그룹 F. 최소 검증 (1개)

### 15. example.com — 타이틀 확인

```
TARGET_URL=https://example.com
```

```
1. 페이지에 "Example Domain" 문자열이 표시되는지 확인
```

**기대**: `navigate` + `verify` 1 스텝. 실패 시 파이프라인 자체 문제.

---

## DSL / 치유 경로 커버리지 매트릭스

| # | navigate | fill | press | click | select | check | hover | wait | verify | 새탭 | 치유 유도 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | ✅ | ✅ | ✅ | ✅ |  |  |  |  | ✅ | 가능 | J/E |
| 2 | ✅ | ✅ | ✅ | ✅ |  |  |  |  | ✅ | 가능 | 베이스라인 |
| 3 | ✅ | ✅ | ✅ | ✅ |  |  |  | (자동) | ✅ |  | H |
| 4 | ✅ | ✅ |  |  |  |  |  |  | ✅ |  | - |
| 5 | ✅ |  |  |  |  | ✅ |  |  | ✅ |  | check |
| 6 | ✅ |  |  |  | ✅ |  |  |  | ✅ |  | select |
| 7 | ✅ | ✅ |  | ✅ |  |  |  |  | ✅ |  | submit |
| 8 | ✅ | ✅ |  | ✅ |  |  |  |  | ✅ |  | URL 변경 |
| 9 | ✅ | ✅ |  | ✅ |  |  |  |  | ✅ |  | negative |
| 10 | ✅ |  |  | ✅ |  |  |  | ✅ | ✅ |  | wait |
| 11 | ✅ |  |  |  |  |  |  |  | ✅ |  | is_visible |
| 12 | ✅ |  |  |  |  |  | ✅ |  | ✅ |  | hover |
| 13 | ✅ |  |  | ✅ |  |  |  |  | ✅ | ✅ | B-1 유효 새탭 |
| 14 | ✅ | ✅ | ✅ | ✅ |  |  |  |  | ✅ | 가능 | 멀티 nav |
| 15 | ✅ |  |  |  |  |  |  |  | ✅ |  | sanity |

**총계**: navigate 15/15, fill 8/15, press 4/15, click 8/15, select 1/15, check 1/15, hover 1/15, wait 1/15, verify 15/15.

9대 액션 전부 최소 1회 커버.

---

## 결과 기록 템플릿

빌드 후 결과를 아래 형식으로 기록하면 회귀 추적이 쉽다.

| # | 빌드 | PASS/FAIL | <think> 있음? | 치유 단계 사용 | 최종 도달 URL | 비고 |
|---|---|---|---|---|---|---|
| 1 | #20 | 5/5 | True | heal timeout→J, fallback | www.ktds.com | 운+B |
| 2 | -  | - | - | - | - | - |
| ... |  |  |  |  |  |  |

## 실패 시 원인 분류

- **LLM 추측 오류** — Planner/Healer 가 잘못된 target/value 생성. → 프롬프트 보강 검토.
- **사이트 변경** — DOM 구조가 바뀐 경우. → SRS 문구/사이트 교체.
- **봇 차단** — reCAPTCHA / chrome-error. → B-1 필터가 정직하게 FAIL 시키는지 확인 + 사이트 교체.
- **파이프라인 로직 버그** — executor/resolver/healer 결함. → 코드 수정.
