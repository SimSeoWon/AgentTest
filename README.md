# AgentTest

Unreal Engine 5 프로젝트 팀을 위한 **Git 변경 감지 → RAG 컨텍스트 자동 갱신 → 다중 에이전트 분석** 파이프라인 빌드 저장소.

---

## 개요

이 저장소는 팀원들에게 배포할 실행 패키지(`AgentWatch.zip`)를 빌드하는 곳이다.
팀원은 zip을 UE5 프로젝트 루트에 압축 해제하고 `watch.exe`를 실행하기만 하면 된다.

```
Git 변경 감지 → git pull → 디렉토리(모듈) 단위로 컨텍스트+리뷰 한번에 (병렬 6)
                         → .claude/context/ MD + .claude/reviews/ 리포트 동시 저장
                         → 벡터 인덱싱 → .claude/vector_db/ 갱신
                         → 에셋 검증 → .claude/reviews/ 저장
                         → 리뷰 상담 (수동) → 개발자 코멘트 기록
```

분석 엔진은 **Claude** (Sonnet/Opus/Haiku 선택) 또는 **Gemini** 중 선택 가능하다.

---

## 저장소 구조

```
AgentTest/
├── watcher/
│   ├── watch.py                # 메인 워처 (PyInstaller 진입점)
│   └── agent_templates.py      # 에이전트·MCP 템플릿 상수
├── mcp/
│   ├── context_search/         # 태그 + 벡터 통합 컨텍스트 검색 MCP
│   ├── log_analyzer/           # UE5 로그 분석 MCP
│   ├── crash_analyzer/         # UE5 크래시 분석 MCP
│   ├── commandlet_runner/      # UE5 커맨드렛 실행 MCP
│   └── gemini_query/           # Gemini CLI 위임 MCP
├── build.bat                   # 전체 빌드 스크립트
├── INSTALL.md                  # 팀원용 설치 안내
├── CLAUDE.md
└── README.md
```

---

## 개발 환경 준비

```bash
pip install pyinstaller mcp google-generativeai
```

---

## 빌드

```bash
build.bat
```

총 6단계 빌드 후 `AgentWatch.zip`이 자동 생성된다:

```
dist/
├── watch.exe
└── .claude/
    └── mcp/
        ├── context_search.exe
        ├── log_analyzer.exe
        ├── crash_analyzer.exe
        ├── commandlet_runner.exe
        └── gemini_query.exe
```

---

## 배포 방법

1. `AgentWatch.zip`을 UE5 프로젝트 루트에 압축 해제
2. `watch.exe` 실행
3. 최초 실행 시 아래 항목 입력 → `config.json` 자동 생성

| 설정 항목 | 설명 | 기본값 |
|----------|------|--------|
| 감시 브랜치 | Git 브랜치명 | `main` |
| 폴링 간격 | 변경 확인 주기(초) | `60` |
| 자동 코드 리뷰 | 커밋 감지 시 자동 리뷰 | `y` |
| 에셋 검증 | `.uasset`/`.umap` 변경 시 DataValidation 실행 | `y` |
| Gemini 사용 | 분석 엔진을 Gemini CLI로 전환 (설치 시에만 질문) | `n` |
| Claude 모델 | 자동 파이프라인용 모델 (sonnet/opus/haiku) | `claude-sonnet-4-6` |

### 배포 전제 조건 (팀원 PC)

| 항목 | 필수 여부 |
|------|----------|
| Claude CLI (`claude`) | **필수** — 법인 라이센스로 로그인 상태 |
| Git | **필수** |
| UE5 Source 폴더에 Git 초기화 | **필수** (`Source/.git` 존재) |
| Windows SDK (`cdb.exe`) | 선택 — `.dmp` 직접 분석 시 필요 |
| Gemini CLI (`gemini`) | 선택 — Gemini 분석 엔진 사용 시 |

---

## 배포 후 생성되는 구조

```
[UE5 프로젝트 루트]/
├── watch.exe
├── config.json              ← 최초 실행 시 자동 생성
├── .watch_state             ← 자동 생성
├── CLAUDE.md                ← AgentWatch 안내 구역 자동 삽입/갱신
└── .claude/
    ├── settings.json        ← MCP 5종 자동 등록 (기존 설정 머지)
    ├── mcp/                 ← MCP 실행 파일
    ├── vector_db/           ← ChromaDB 벡터 인덱스 (자동 생성)
    ├── context/             ← 디렉토리(모듈) 단위 컨텍스트 MD + 코멘트
    ├── reviews/             ← 코드 리뷰 · 에셋 검증 리포트
    └── agents/              ← 11개 에이전트 폴더
        ├── SKILL_INDEX.md
        ├── 01_소스분석/
        ├── 02_프로젝트분석/
        ├── 03_코드규약/
        ├── 04_코드작성/
        ├── 05_코드검증/
        ├── 06_빌드_통합/
        ├── 07_코드매니저/
        ├── 08_로그분석/
        ├── 09_크래시분석/
        ├── 10_에셋검증/
        └── 11_리뷰상담/
```

> 기존 `.claude/` 폴더가 있는 경우 머지 방식으로 동작한다.
> `role.md`, `prompt.md`, `settings.json` 등 커스텀 파일은 보존된다.

---

## 자동화 파이프라인

| 트리거 | 동작 |
|--------|------|
| `.cpp` `.h` `.cs` 등 소스 변경 | 디렉토리 단위로 묶어 컨텍스트 MD + 코드 리뷰를 1회 LLM 호출로 동시 수행 (병렬 6) |
| `.uasset` `.umap` 에셋 변경 | DataValidation 커맨드렛 실행 → 에셋 검증 리포트 |

### 성능 최적화

- **컨텍스트+리뷰 통합** — 1회 LLM 호출로 컨텍스트 MD 생성과 코드 리뷰를 동시 수행
- **디렉토리(모듈) 단위 그룹핑** — 같은 폴더의 .h/.cpp를 합쳐 하나의 단위로 처리
- **병렬 처리** — ThreadPoolExecutor 6워커 병렬 실행 (22개 파일 → 6모듈 → ~4분)
- **모델 선택** — Sonnet(기본, 빠름) / Opus(정확) / Haiku(최고속) 중 선택
- **stdin 프롬프트 전달** — Windows 명령줄 길이 제한(32KB) 회피

### 컨텍스트 코멘트 시스템

컨텍스트 MD 파일에 `## 코멘트` 섹션으로 개발자 노트를 기록할 수 있다.

```markdown
## 코멘트
- [2026-03-29][방향] 비즈니스 로직 분리하여 재사용성 극대화 예정 (홍길동)
- [2026-03-29][고민] 자식 클래스마다 다른 정보를 담아야 해서 구조 검토 중 (홍길동)
- [2026-03-29][리뷰] 델리게이트 미정리는 선행작업이라 의도적 (홍길동)
```

| 태그 | 용도 |
|------|------|
| `[리뷰]` | 리뷰 지적에 대한 응답 |
| `[방향]` | 설계 방향·목표 |
| `[고민]` | 미결 설계 고민 |
| `[진행]` | 방향 전환·진척 상황 |
| `[메모]` | 기타 참고 사항 |

- 코멘트는 벡터 임베딩에서 **제외** (검색 품질 유지)
- 코드 리뷰 시 에이전트가 **참조** (인지된 항목 반복 지적 방지 + 설계 맥락 반영)
- 컨텍스트 MD 재생성 시 코멘트 **자동 보존**
- `11_리뷰상담` 에이전트를 통해 대화형으로 기록

---

## RAG 검색 시스템

컨텍스트 MD 파일을 **벡터 유사도**(ChromaDB + `all-MiniLM-L6-v2`)와 **태그 키워드** 두 가지 방식으로 검색한다.

### 통합 검색 (`combined_search`)

에이전트는 **항상 `combined_search`를 우선 사용**한다. 이 툴은 내부적으로:

1. 벡터 검색(의미 기반) 수행
2. 벡터 결과에서 태그를 자동 추출 + 사용자 지정 태그 병합
3. 태그 검색(키워드 기반) 수행
4. 두 결과를 병합·중복 제거하여 반환

### 한국어 검색 정확도 주의사항

> **`all-MiniLM-L6-v2` 임베딩 모델은 영어 중심으로 학습되어, 한국어 자연어 쿼리의 의미 검색 정확도가 낮다.**
>
> - 순수 한국어 쿼리 → 유사도 0.4대, 관련 없는 문서가 상위에 노출될 수 있음
> - 영어 / C++ 클래스명 포함 쿼리 → 유사도 0.5~0.6, 정확도 양호
>
> **권장 사용법:**
> - 검색 시 **클래스명·함수명 등 코드 식별자를 함께 포함**하면 정확도가 크게 향상됨
>   - 예: `"UMissionTask_Spawn 스포너 컴포넌트 연결"` → 정확한 결과
>   - 예: `"몬스터 스폰 태스크"` (한국어만) → 부정확한 결과 가능
> - `combined_search`가 태그 검색을 자동 병행하므로 한국어만으로도 단독 벡터 검색보다 나은 결과를 얻을 수 있음

---

## MCP 서버

| 서버 | 주요 툴 | 사용 에이전트 |
|------|---------|-------------|
| `context_search` | `combined_search`, `search_context`, `list_tags`, `vector_search`, `rebuild_index`, `index_status` | 02, 03, 05, 07, 08, 09, 10, 11 |
| `log_analyzer` | `analyze_log`, `search_log` | 08 |
| `crash_analyzer` | `analyze_crash`, `analyze_crash_log` | 09 |
| `commandlet_runner` | `find_unreal_editor`, `run_data_validation`, `run_commandlet` | 10 |
| `gemini_query` | `gemini_analyze`, `gemini_status` | 02, 03, 05, 07, 08, 09, 10 |

### Gemini 분석 엔진

`gemini_query` MCP를 통해 서브 에이전트가 Gemini에 분석을 위임할 수 있다.

```
서브 에이전트 (Claude — 오케스트레이션)
  └── gemini_analyze("분석 요청") → Gemini CLI (대용량 처리)
```

- Gemini CLI 미설치 환경에서는 안내 메시지를 반환하며 정상 종료
- `config.json`의 `use_gemini: true` 설정 시 자동화 파이프라인도 Gemini로 전환

---

## 프로세스 안전장치

| 종료 방식 | Job Object | SIGBREAK | atexit |
|----------|-----------|---------|--------|
| Ctrl+C | - | - | O |
| CMD 창 X 버튼 | O | O | - |
| 작업관리자 강제종료 | O | - | - |

- **Windows Job Object** — watch.exe 종료 시 모든 자식/손자 프로세스(claude, MCP 서버) 자동 정리
- **중복 실행 방지** — `_processing_lock`으로 이전 작업 진행 중에는 새 작업 시작하지 않음
- **누적 커밋 처리** — 작업 중 추가 커밋이 쌓여도 diff 범위를 확장하여 누락 없이 처리

---

## 에이전트 추가 방법

1. `watcher/agent_templates.py`의 `AGENTS`, `ROLE_TEMPLATES`, `PROMPT_TEMPLATES`, `SETTINGS_TEMPLATES`, `SKILL_INDEX`에 항목 추가
2. `build.bat` 재실행
3. 팀원들에게 새 `AgentWatch.zip` 배포 → `watch.exe` 재실행 시 새 에이전트 자동 머지

## MCP 추가 방법

1. `mcp/<name>/server.py` 작성 (FastMCP 사용)
2. `build.bat`에 PyInstaller 빌드 라인 추가
3. `agent_templates.py`의 `MCP_SERVERS`와 관련 에이전트 `SETTINGS_TEMPLATES`에 등록
