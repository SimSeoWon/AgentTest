# AgentWatch

Unreal Engine 5 프로젝트 팀을 위한 **Git 변경 감지 + RAG 컨텍스트 자동 갱신 + 다중 에이전트 분석** 파이프라인 빌드 저장소.

**플랫폼**: Windows 전용

---

## 개요

이 저장소는 팀원들에게 배포할 실행 패키지(`AgentWatch.zip`)를 빌드하는 곳이다.
팀원은 zip을 UE5 프로젝트 루트에 압축 해제하고 `watch.exe`를 실행하기만 하면 된다.

```
Git 변경 감지 → git pull → 커밋별 개별 처리 (작성자 기록)
                         → stem 단위(.h/.cpp 쌍)로 컨텍스트+리뷰 동시 수행 (병렬 6)
                         → .claude/context/ MD + .claude/reviews/ 리포트 저장
                         → 벡터 인덱싱 → .claude/vector_db/ 갱신
                         → 에셋 검증 → .claude/reviews/ 저장
```

분석 엔진은 **Claude** (Sonnet/Opus/Haiku 선택) 또는 **Gemini** 중 선택 가능.
primary LLM 실패 시 자동으로 다른 LLM으로 폴백한다.

---

## 저장소 구조

```
AgentTest/
├── watcher/
│   ├── watch.py                # 메인 워처 (Git 감시, 설정, 프로세스 관리)
│   ├── common.py               # 공용 상수, 로깅, HTTP, LLM 호출
│   ├── setup.py                # 프로젝트 초기화/머지, CLAUDE.md 관리
│   ├── context.py              # 컨텍스트 MD 생성, 벡터 인덱싱, 코드 리뷰
│   ├── domain.py               # 도메인 자동 승급, 건강도 리포트
│   ├── review.py               # 에셋 검증 (DataValidation)
│   └── agent_templates.py      # 에이전트/MCP 템플릿 상수
├── mcp/
│   ├── context_search/
│   │   ├── server.py           # 태그 + 벡터 검색 MCP (3모드: 로컬/서버/클라이언트)
│   │   └── web_ui.py           # 웹 UI HTML
│   ├── log_analyzer/           # UE5 로그 분석 MCP
│   ├── crash_analyzer/         # UE5 크래시 분석 MCP
│   ├── commandlet_runner/      # UE5 커맨드렛 실행 MCP
│   └── gemini_query/           # Gemini CLI 위임 MCP
├── build.bat                   # 전체 빌드 (패치 버전 자동 증가)
├── bump_version.ps1            # 버전 증가 스크립트
├── INSTALL.md
├── CLAUDE.md
└── README.md
```

---

## 운영 모드

| 모드 | 대상 | config.json 설정 |
|------|------|------------------|
| **로컬** | 개별 PC | 기본값 (별도 설정 불필요) |
| **서버** | 공용 서버 1대 | `"server_mode": true, "server_port": 8100` |
| **클라이언트** | 팀원 PC | `"context_server_url": "http://<서버IP>:8100"` |

- 서버 모드: FastAPI HTTP 서버 + DoubleBufferedIndex(무중단 인덱싱) + 웹 UI (`http://서버IP:8100/`)
- 클라이언트: 서버에 검색 위임, 결과를 로컬 MD로 캐싱, 서버 미응답 시 캐시 태그 검색 폴백
- 서버 시작 시 방화벽 인바운드 규칙 자동 확인/추가

---

## 빌드

```bash
build.bat
```

- 빌드 시 패치 버전 자동 증가 (`1.1.2` → `1.1.3`)
- 총 6단계 빌드 후 `AgentWatch.zip` 자동 생성

```
dist/
├── watch.exe
└── .claude/
    └── mcp/
        ├── context_search.exe
        ├── log_analyzer.exe
        ├── crash_analyzer.exe
        ├── commandlet_runner.exe
        ├── gemini_query.exe
        └── onnx_model/          ← 임베딩 모델 (자동 번들)
```

---

## 배포 방법

1. `AgentWatch.zip`을 UE5 프로젝트 루트에 압축 해제
2. `watch.exe` 실행
3. 최초 실행 시 대화형 설정 → `config.json` 자동 생성

| 설정 항목 | 설명 | 기본값 |
|----------|------|--------|
| 감시 브랜치 | Git 브랜치명 | `main` |
| 폴링 간격 | 변경 확인 주기(초) | `60` |
| 자동 코드 리뷰 | 커밋 감지 시 자동 리뷰 | `y` |
| 에셋 검증 | `.uasset`/`.umap` 변경 시 DataValidation | `y` |
| Gemini 사용 | 분석 엔진을 Gemini로 전환 | `n` |
| Claude 모델 | sonnet/opus/haiku | `claude-sonnet-4-6` |
| 서버 모드 | HTTP 서버로 운영 | `n` |
| 파일 로그 | 날짜별 로그 파일 생성 | `y` |

### 배포 전제 조건 (팀원 PC)

| 항목 | 필수 여부 |
|------|----------|
| Claude CLI (`claude`) | **필수** |
| Git | **필수** |
| UE5 Source 폴더에 Git 초기화 | **필수** (`Source/.git` 존재) |
| Windows SDK (`cdb.exe`) | 선택 — `.dmp` 직접 분석 시 |
| Gemini CLI (`gemini`) | 선택 — Gemini 분석 엔진 사용 시 |

---

## 배포 후 생성되는 구조

```
[UE5 프로젝트 루트]/
├── watch.exe
├── config.json              ← 최초 실행 시 자동 생성 (스키마 자동 마이그레이션)
├── .watch_state             ← 마지막 커밋 해시 (멱등성 보장)
├── CLAUDE.md                ← AgentWatch 안내 구역 자동 삽입/갱신
└── .claude/
    ├── settings.json        ← MCP 5종 자동 등록 (기존 설정 머지)
    ├── mcp/                 ← MCP 실행 파일
    ├── vector_db/           ← ChromaDB 벡터 인덱스
    ├── logs/                ← 날짜별 로그 (7일 보관, 자동 로테이션)
    │   ├── watch_2026-04-11.log
    │   └── context_search_2026-04-11.log
    ├── context/             ← stem 단위 컨텍스트 MD (DamageSystem.h+.cpp → DamageSystem.md)
    │   └── _domains/        ← 도메인 문서 (자동 승급)
    ├── reviews/             ← 커밋별·작성자별 코드 리뷰 리포트
    └── agents/              ← 11개 에이전트 폴더
```

---

## 자동화 파이프라인

| 트리거 | 동작 |
|--------|------|
| 소스 변경 (`.cpp` `.h` `.cs` 등) | **커밋별** stem 단위로 컨텍스트 MD + 코드 리뷰 동시 수행 (병렬 6) |
| 에셋 변경 (`.uasset` `.umap`) | DataValidation 커맨드렛 → 에셋 검증 리포트 |
| 검색 패턴 축적 | 도메인 자동 승급 (자주 함께 검색되는 문서 클러스터 → 도메인 문서) |

### 핵심 설계 원칙

> **RAG는 진실의 원천이 아니라 네비게이션이다.**
> 에이전트는 RAG 검색 결과를 기반으로 관련 소스 파일을 찾은 뒤, **실제 코드를 직접 읽고 판단**한다.

### 에이전트 행동 원칙

1. **모든 판단은 소스 코드 기준** — RAG와 소스가 다르면 소스가 정답
2. **RAG는 참조만** — 검색 결과로 파일을 찾고, 반드시 `Read`로 직접 확인
3. **탐색 결과 캐시 저장** — `cache_context` 도구로 태그/카테고리/관련 클래스 저장
4. **캐시도 검증 대상** — 시간이 지나면 소스와 달라질 수 있으므로 재확인

### 리뷰 리포트

- **커밋별 개별 저장**: `reviews/2026-04-11_1630_홍길동_2912eccc.md`
- **작성자 기록**: Git 커밋 author 기준
- **코멘트 시스템**: `## 코멘트` 섹션으로 개발자 노트 기록, 다음 리뷰 시 참조

---

## RAG 검색

- **벡터 검색**: ChromaDB + `all-MiniLM-L6-v2` (ONNX, 384차원)
- **태그 검색**: frontmatter 태그 키워드 매칭
- **통합 검색** (`combined_search`): 벡터 + 태그 병합, n_results(기본 5개) 제한
- `related_classes` 포함 — 관련 파일 경로 즉시 파악
- **한국어 검색 팁**: 클래스명/함수명을 함께 포함하면 정확도 향상

---

## MCP 서버

| 서버 | 주요 툴 |
|------|---------|
| `context_search` | `combined_search`, `vector_search`, `search_context`, `list_tags`, `cache_context`, `impact_analysis`, `rebuild_index`, `index_status` |
| `log_analyzer` | `analyze_log`, `search_log` |
| `crash_analyzer` | `analyze_crash`, `analyze_crash_log` |
| `commandlet_runner` | `find_unreal_editor`, `run_data_validation`, `run_commandlet` |
| `gemini_query` | `gemini_analyze`, `gemini_status` |

---

## 로그 시스템

| 로그 파일 | 기록 주체 | 내용 |
|-----------|-----------|------|
| `watch_YYYY-MM-DD.log` | watch.exe | Git 감시, LLM 호출/폴백, 인덱싱, 도메인 승급 |
| `context_search_YYYY-MM-DD.log` | context_search.exe | 검색 요청/응답, 캐시 저장, 인덱싱, 폴백 |

- 경로: `.claude/logs/`
- 보관: 7일, 자동 로테이션
- `config.json`의 `enable_log: false`로 비활성화 가능

---

## 프로세스 안전장치

- **Windows Job Object** — watch.exe 종료 시 모든 자식 프로세스 자동 정리
- **포트 점유 감지** — 서버 시작 전 기존 프로세스 자동 정리
- **중복 실행 방지** — `_processing_lock`으로 이전 작업 진행 중 새 작업 차단
- **LLM 교차 폴백** — Claude 실패 시 Gemini, Gemini 실패 시 Claude (10초 대기)
- **config 자동 마이그레이션** — 누락 필드 자동 보충, 타입/범위 검증
- **Git pull 실패 안내** — merge conflict / 로컬 변경 구분 메시지

---

## 에이전트 추가 방법

1. `watcher/agent_templates.py`의 `AGENTS`, `ROLE_TEMPLATES`, `PROMPT_TEMPLATES`, `SETTINGS_TEMPLATES`, `SKILL_INDEX`에 항목 추가
2. `build.bat` 재실행
3. 팀원들에게 새 `AgentWatch.zip` 배포 → `watch.exe` 재실행 시 자동 머지

## MCP 추가 방법

1. `mcp/<name>/server.py` 작성 (FastMCP 사용)
2. `build.bat`에 PyInstaller 빌드 라인 추가
3. `agent_templates.py`의 `MCP_SERVERS`와 관련 에이전트 `SETTINGS_TEMPLATES`에 등록
