# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 언어 설정

- **모든 응답은 한국어로** 작성할 것

---

## 프로젝트 개요

**AgentTest**는 Unreal Engine 프로젝트 팀에 배포할 실행 패키지를 빌드하는 개발 저장소다.

배포 대상 직원들은 `dist/` 폴더 전체를 Unreal 프로젝트 루트에 복사하고 `watch.exe`를 실행하면,
Git 변경 감지 → `.claude/` 구조 자동 생성 → Claude CLI로 RAG 컨텍스트 MD 자동 갱신이 동작한다.

### 이 저장소(AgentTest)의 역할
> `watch.exe`와 MCP 서버들을 **만드는** 곳. 직원들에게 배포되는 것은 `dist/` 폴더 전체다.

---

## 저장소 파일 구조 (현재 구현 기준)

```
AgentTest/                         ← 이 저장소
├── watcher/
│   ├── watch.py                   ← 메인 워처 스크립트 (PyInstaller 진입점)
│   └── agent_templates.py         ← 에이전트/MCP 템플릿 상수 모음 (watch.py가 import)
├── mcp/
│   ├── context_search/
│   │   └── server.py              ← 태그 + 벡터 검색 MCP (ChromaDB)
│   ├── log_analyzer/
│   │   └── server.py              ← UE5 로그 파일 분석 MCP
│   ├── crash_analyzer/
│   │   └── server.py              ← UE5 크래시 덤프/로그 분석 MCP
│   └── commandlet_runner/
│       └── server.py              ← UE5 커맨드렛 실행 MCP (DataValidation 등)
├── build.bat                      ← 전체 빌드 스크립트 (watch.exe + MCP 5종)
└── CLAUDE.md
```

### 빌드 산출물 (`.gitignore` 제외 — git 미추적)
```
dist/           ← PyInstaller 빌드 결과
build/          ← PyInstaller 임시 파일
*.spec          ← PyInstaller 스펙 파일
AgentWatch.zip  ← 배포 패키지 (build.bat 완료 시 자동 생성)
```

`build.bat` 완료 후 `AgentWatch.zip`이 자동 생성된다. 이 파일을 팀원에게 전달한다.
설치 방법은 `INSTALL.md` 참고.

---

## 배포 후 직원 PC에 생성되는 구조

직원이 `dist/` 전체를 Unreal 프로젝트 루트에 복사하고 `watch.exe`를 실행하면 자동 생성된다.

```
[Unreal 프로젝트 루트]/
├── Source/
│   └── .git/                      ← watch.exe가 자동 탐지하는 Git 저장소
├── .claude/
│   ├── settings.json
│   ├── mcp/                       ← MCP 실행 파일
│   │   ├── context_search.exe
│   │   ├── log_analyzer.exe
│   │   ├── crash_analyzer.exe
│   │   ├── commandlet_runner.exe
│   │   └── gemini_query.exe
│   ├── vector_db/                   ← ChromaDB 벡터 인덱스 (자동 생성)
│   ├── reviews/                   ← 커밋별 코드 리뷰 / 에셋 검증 리포트
│   ├── context/                   ← 변경된 소스 파일 → Claude가 생성한 MD
│   │   ├── AI/
│   │   ├── UI/
│   │   ├── 게임플로우/
│   │   ├── 네트워크/
│   │   ├── 데이터/
│   │   ├── 미션/
│   │   ├── 서비스시스템/
│   │   ├── 월드/
│   │   ├── 전투/
│   │   └── 캐릭터/
│   └── agents/
│       ├── SKILL_INDEX.md
│       ├── 01_소스분석/  →  role.md / prompt.md / settings.json
│       ├── 02_프로젝트분석/
│       ├── 03_코드규약/
│       ├── 04_코드작성/
│       ├── 05_코드검증/
│       ├── 06_빌드_통합/
│       ├── 07_코드매니저/
│       ├── 08_로그분석/
│       ├── 09_크래시분석/
│       ├── 10_에셋검증/
│       └── 11_리뷰상담/
├── config.json                    ← 브랜치명, 폴링 간격 저장 (최초 실행 시 생성)
├── .watch_state                   ← 마지막 확인 커밋 해시 (멱등성 보장)
└── watch.exe
```

---

## 핵심 컴포넌트 상세

### 1. `watcher/watch.py`

**Git 저장소 자동 탐색 (`find_git_repo`)**
- 탐색 순서: `[루트]/Source/` → `[루트]/` → `[루트]` 직접 하위 폴더
- Unreal 표준 구조(`Source/.git`)를 1순위로 탐색
- 탐지 실패 시 오류 메시지 출력 후 종료

**초기화 및 머지 (`init_project_dirs` + `_merge_agents` + `_update_project_settings` + `_update_project_claude_md`)**

최초 실행과 재실행 모두 동일한 함수가 실행되며, 머지 방식으로 동작한다:

| 대상 | 최초 실행 | 재실행 (이미 존재) |
|------|----------|-----------------|
| `.mcp.json` (프로젝트 루트) mcpServers | 등록 | 누락된 서버만 추가, 기존 항목 보존 |
| `.claude/settings.json` mcpServers | 등록 | 누락된 서버만 추가 (하위 호환) |
| `CLAUDE.md` (프로젝트 루트) | 생성 | AgentWatch 마커 구역만 갱신, 기존 내용 보존 |
| `.claude/CLAUDE.md` | 건드리지 않음 | 존재 시 마커 구역 갱신 (팀 규칙 파일 대응) |
| `context/` 도메인 폴더 | 생성 | 없는 폴더만 추가 |
| `reviews/` 폴더 | 생성 | 유지 |
| 에이전트 폴더 | 생성 | 없는 폴더만 추가 |
| `role.md` / `prompt.md` / `settings.json` | 생성 | 보존 (커스텀 보호) |
| `SKILL_INDEX.md` | 생성 | **항상 덮어쓰기** (인덱스 최신 유지) |
| `config.json` | 생성 (auto_review 포함) | 보존 |

**기존 Claude 환경 대응 (`_update_project_settings` + `_update_project_claude_md`)**
- `.mcp.json`(프로젝트 루트)에 MCP 5종 머지 — Claude Code가 실제로 읽는 파일
- `.claude/settings.json`에도 동일하게 머지 — 에이전트 레벨 참조 및 하위 호환
- 이미 `CLAUDE.md`가 있어도 `<!-- AgentWatch:Start -->` ~ `<!-- AgentWatch:End -->` 마커 구역을 삽입/갱신
  - 마커가 없으면 파일 끝에 추가, 있으면 해당 구역만 최신 내용으로 교체
  - 마커 외부(사용자 작성 내용)는 절대 건드리지 않음

**감시 루프**
- `git fetch` → remote 해시와 local 해시 비교
- 변경 감지 시: `git pull` → `git diff --name-only` → `process_commit()` 실행
  1. **컨텍스트+리뷰 통합 처리** (`process_commit`) — 변경 파일을 **디렉토리(모듈) 단위**로 묶어 **1회의 LLM 호출**로 컨텍스트 MD 생성 + 코드 리뷰를 동시 수행 (병렬 6워커)
     - 같은 디렉토리의 `.h`/`.cpp` 파일을 합쳐서 하나의 MD로 생성 (예: `TaskSystem.md`)
     - `.h`를 먼저, `.cpp`를 뒤에 배치하여 인터페이스→구현 순서로 분석
     - 그룹당 최대 8000자 (`GROUP_CONTENT_LIMIT`)
     - `auto_review: false` 시 컨텍스트 MD만 생성
     - LLM 응답을 `=== CONTEXT_MD ===` / `=== CODE_REVIEW ===` 구분자로 파싱하여 분리 저장
  2. **벡터 인덱싱** (`update_vector_index`) — 생성된 MD를 ChromaDB에 upsert → `vector_db/`
  3. **에셋 검증** (`run_asset_validation`) — `auto_asset_validation: true` 시에만 실행 → `reviews/`
- stdin으로 프롬프트 전달 (Windows 명령줄 길이 제한 회피)
- 소스 대상 확장자: `.cpp`, `.h`, `.hpp`, `.inl`, `.cs`, `.py`
- 에셋 대상 확장자: `.uasset`, `.umap`
- `.watch_state` 파일로 마지막 커밋 해시 영속 저장 (멱등성 보장)

**코드 리뷰 흐름 (컨텍스트 생성과 통합)**
- 컨텍스트 MD 생성과 코드 리뷰를 **1회의 LLM 호출**로 동시 수행
- **관련 컨텍스트 자동 검색**: 모듈별로 `context_search.exe --search`를 호출하여 관련 파일 컨텍스트 3개를 수집, 프롬프트에 주입
- **개발자 코멘트 참조**: 컨텍스트 MD의 `## 코멘트` 섹션이 있으면 "인지된 항목"으로 프롬프트에 주입하여 반복 지적 방지
- 통합 리포트: 모듈별 리뷰 결과를 직접 취합 (추가 LLM 호출 없음)
- 저장: `.claude/reviews/YYYY-MM-DD_HHMM_<커밋해시>.md`
- `config.json`의 `auto_review: false` 로 비활성화 가능

**에셋 검증 흐름 (`run_asset_validation`)**
- `.uasset` / `.umap` 파일이 변경 목록에 있을 때만 실행
- `.uproject`의 `EngineAssociation` → 레지스트리(`winreg`) → `%ProgramFiles%/Epic Games` 순으로 `UnrealEditor-Cmd.exe` 탐색
- `UnrealEditor-Cmd.exe <uproject> -run=DataValidation -log -unattended -nullrhi` 실행
- 커맨드렛 출력을 Claude CLI로 분석 → `reviews/YYYY-MM-DD_HHMM_<커밋해시>_assets.md` 저장
- `config.json`의 `auto_asset_validation: false` 로 비활성화 가능

**Claude CLI 호출 방식**
```
claude -p --dangerously-skip-permissions --model <claude_model>
```
→ stdin으로 프롬프트 전달 (Windows 명령줄 길이 제한 회피)
→ `config.json`의 `claude_model`로 모델 지정 (기본값: `claude-sonnet-4-6`)

### 2. `watcher/agent_templates.py`

`watch.py`가 import하는 상수 모음. PyInstaller 번들 시 자동 포함됨.

| 상수 | 내용 |
|------|------|
| `AGENTS` | 에이전트 폴더명 목록 (01~10) |
| `ROLE_TEMPLATES` | 각 에이전트의 `role.md` 내용 |
| `PROMPT_TEMPLATES` | 각 에이전트의 `prompt.md` 내용 |
| `SETTINGS_TEMPLATES` | 각 에이전트의 `settings.json` 내용 (allowedTools, mcpServers) |
| `SKILL_INDEX` | `SKILL_INDEX.md` 내용 |
| `DEFAULT_CONTEXT_DOMAINS` | `context/` 하위 도메인 폴더 목록 |

### 5. 리뷰 상담 시스템 (11_리뷰상담)

**기능**: 코드 리뷰 지적 사항에 대해 개발자와 대화형 상담, 의견을 컨텍스트 MD에 코멘트로 기록

**코멘트 흐름**:
```
개발자 → 11_리뷰상담 에이전트에 질문/의견/고민/방향 전달
  ├─ 리뷰 리포트 읽기 → 지적 사항 설명
  ├─ context_search MCP로 관련 코드 검색 → 수정 방법 안내
  └─ 개발자 노트를 context/<도메인>/<파일>.md의 ## 코멘트 섹션에 기록
      → 다음 리뷰 시 03_코드규약, 05_코드검증이 코멘트를 참조
      → 이미 인지된 항목은 반복 지적 생략 + 설계 맥락 반영
```

**코멘트 유형**:
| 태그 | 용도 | 예시 |
|------|------|------|
| `[리뷰]` | 리뷰 지적에 대한 응답 | 델리게이트 미정리는 선행작업이라 의도적 |
| `[방향]` | 설계 방향·목표 | 비즈니스 로직 분리하여 재사용성 극대화 예정 |
| `[고민]` | 미결 설계 고민 | 자식 클래스마다 다른 정보 담아야 해서 구조 검토 중 |
| `[진행]` | 방향 전환·진척 상황 | A 방식에서 B 형태로 전환 예정 |
| `[메모]` | 기타 참고 사항 | 기획 데이터 확정 후 하드코딩 제거 필요 |

**코멘트 보존 규칙**:
- `update_context()` 시 기존 `## 코멘트` 섹션을 추출하여 새 MD 끝에 재부착
- 벡터 임베딩 시 `## 코멘트` 섹션은 자동 제외 (`_strip_comments_section()`)
- 태그 검색 결과에는 코멘트 포함 (에이전트가 참조 가능)

### 3. `mcp/` — MCP 서버 4종

| 서버 | 빌드 산출물 | 제공 툴 | 사용 에이전트 |
|------|------------|---------|-------------|
| `context_search` | `mcp/context_search.exe` | `combined_search`, `search_context`, `list_tags`, `vector_search`, `rebuild_index`, `index_status` | 02, 03, 05, 07, 08, 09, 10 |
| `log_analyzer` | `mcp/log_analyzer.exe` | `analyze_log`, `search_log` | 08 |
| `crash_analyzer` | `mcp/crash_analyzer.exe` | `analyze_crash`, `analyze_crash_log` | 09 |
| `commandlet_runner` | `mcp/commandlet_runner.exe` | `find_unreal_editor`, `run_data_validation`, `run_commandlet` | 10 |
| `gemini_query` | `mcp/gemini_query.exe` | `gemini_analyze`, `gemini_status` | 02, 07 |

- `crash_analyzer`는 `cdb.exe`(Windows SDK) 유무를 자동 감지하여 `.dmp` 직접 분석 또는 XML/로그 폴백
- `commandlet_runner`는 `.uproject`의 `EngineAssociation` → `winreg` → `%ProgramFiles%` 순으로 엔진 탐색
- `gemini_query`는 `gemini` CLI 미설치 환경에서 안내 메시지 반환 (크래시 없음)
- 모든 MCP는 `./.claude/mcp/<name>.exe` 경로로 각 에이전트의 `settings.json`에 등록됨

### 4. `build.bat`

총 6단계 빌드:
```
[1/6] watch.exe              ← --paths "watcher" 로 agent_templates.py 인식
[2/6] context_search.exe
[3/6] log_analyzer.exe
[4/6] crash_analyzer.exe
[5/6] commandlet_runner.exe
[6/6] gemini_query.exe       → 모두 dist/.claude/mcp/ 에 출력
```

---

## 컨텍스트 MD 파일 스키마

`watch.py`가 Claude CLI를 통해 생성하는 MD 파일 형식:

```markdown
---
tags: [태그1, 태그2, ...]
category: 대분류/중분류/소분류
related_classes:
  - ClassName: path/to/file.ext
---

## 요약
(기능과 역할 요약 — RAG 청크로 사용됨)

## 개선 필요 사항
(알려진 이슈, 기술 부채, 개선 제안)

## 코멘트
(선택 — 11_리뷰상담 에이전트가 개발자 노트를 기록. 벡터 임베딩에서 제외됨)
- [YYYY-MM-DD][리뷰] <지적 사항 응답> (작성자)
- [YYYY-MM-DD][방향] <설계 방향·목표> (작성자)
- [YYYY-MM-DD][고민] <미결 설계 고민> (작성자)
- [YYYY-MM-DD][진행] <방향 전환·진척 상황> (작성자)
- [YYYY-MM-DD][메모] <기타 참고 사항> (작성자)
```

---

## RAG 검색

### 통합 검색 (`combined_search`)
에이전트는 **항상 `combined_search`를 우선 사용**한다. 이 툴은 내부적으로:
1. 벡터 검색(의미 기반) 수행
2. 벡터 결과에서 태그를 자동 추출 + 사용자 지정 태그 병합
3. 태그 검색(키워드 기반) 수행
4. 두 결과를 병합·중복 제거하여 반환 (both > vector > tag 순 정렬)

### 아키텍처
```
watch.exe → context MD 생성 → context_search.exe --upsert 호출 → ChromaDB 저장
                                context_search.exe (MCP 모드) ← 에이전트가 combined_search 호출
```
- chromadb는 **context_search.exe에만** 번들됨 (watch.exe는 가벼움 유지)
- watch.py는 subprocess로 context_search.exe를 CLI 모드로 호출

### 구성 요소
| 구성요소 | 기술 | 비고 |
|----------|------|------|
| 임베딩 모델 | `all-MiniLM-L6-v2` (ONNX) | ChromaDB 내장, 384차원 |
| 벡터 DB | ChromaDB PersistentClient | `.claude/vector_db/` 에 파일 기반 저장 |
| 인덱싱 | `watch.py` → `context_search.exe --upsert` | subprocess 위임 |
| 검색 | `context_search` MCP → `combined_search()` | 벡터+태그 통합 검색 |

### context_search.exe CLI 모드
```
context_search.exe --rebuild <project_root>                   전체 재구축
context_search.exe --upsert  <project_root> <md1> <md2> ...  증분 갱신
context_search.exe --status  <project_root>                   상태 확인
context_search.exe --search  <project_root> <query> [n]       통합 검색 (자동 리뷰용)
context_search.exe                                            MCP 서버 모드 (기본)
```

### 인덱싱 흐름
1. **최초 실행**: `vector_db/` 없으면 `--rebuild`로 전체 인덱싱
2. **커밋 감지**: 변경된 파일의 MD만 `--upsert`로 증분 갱신
3. **수동 재구축**: `rebuild_index()` MCP 툴 또는 `--rebuild` CLI

### 검색 흐름
```
유저 질문 → combined_search(query, tags?)
           ├→ vector_search → 임베딩 → 코사인 유사도 상위 k개
           └→ search_context → 벡터 결과 태그 + 사용자 태그로 키워드 검색
           → 결과 병합·중복 제거 → 반환
```
- `category_filter` 파라미터로 도메인 필터링 가능
- `tags` 파라미터로 명시적 태그 추가 가능 (미지정 시 벡터 결과에서 자동 추출)

### 의존성
- `chromadb` — context_search.exe에만 번들 (`onnxruntime` + `tokenizers` 포함)
- watch.exe에는 chromadb 미포함 (기존과 동일한 경량 크기 유지)

---

## 핵심 설계 결정

| 결정 사항 | 이유 |
|-----------|------|
| `dist/` 폴더 단위 배포 | watch.exe + MCP exe를 함께 전달, 설치 불필요 |
| `Source/.git` 자동 탐색 | Unreal 프로젝트는 Source 하위에 Git 관리 |
| 에이전트 템플릿을 코드로 내장 | 별도 파일 배포 불필요, exe에 번들됨 |
| 에이전트별 `settings.json` | MCP 연결과 허용 툴을 에이전트 단위로 격리 |
| `.watch_state` 파일로 상태 저장 | 재시작해도 중복 처리 없음 (멱등성) |
| Claude CLI 외부 호출 | 직원 PC의 Claude 계정/설정 재사용 가능 |
| ChromaDB + ONNX 임베딩 | PyTorch 불필요(~2GB 절약), 로컬 완결, 오프라인 동작 |
| 증분 벡터 인덱싱 | 변경된 파일만 upsert — 전체 재인덱싱 불필요 |

---

## 작업 히스토리 관리

- 모든 작업 완료 후 반드시 두 가지를 수행할 것:
  1. **CLAUDE.md** — 변경사항 반영
  2. **`history/` 폴더** — 작업 기록 파일 생성
- 히스토리 파일명 형식: `YYYY-MM-DD_HHMM_<작업내용요약>.md`
- 기록 내용: 작업 개요, 생성/수정 파일 목록, 주요 설계 결정

---

## 개발 환경 및 규칙

- 플랫폼: Windows 11, 기본 셸은 `bash` (Git Bash / WSL)
- 에이전트 추가 시: `agent_templates.py`의 `AGENTS`, `ROLE_TEMPLATES`, `PROMPT_TEMPLATES`, `SETTINGS_TEMPLATES`, `SKILL_INDEX`에 동시 추가
- MCP 추가 시: `mcp/<name>/server.py` 생성 → `build.bat`에 빌드 라인 추가 → 관련 에이전트 `SETTINGS_TEMPLATES`에 등록
- 컨텍스트 도메인 추가 시: `agent_templates.py`의 `DEFAULT_CONTEXT_DOMAINS` 리스트에 추가
- 소스 수정 후 반드시 `build.bat` 재실행하여 `dist/` 갱신

---

## 배포 전제 조건 (직원 PC)

1. **Claude CLI 설치** — `claude` 명령이 PATH에 있어야 함
2. **Git 설치** — `git` 명령이 PATH에 있어야 함
3. **Unreal Source 폴더에 Git 초기화** — `Source/.git` 존재해야 함
4. **Windows SDK** (선택) — `.dmp` 직접 분석 시 `cdb.exe` 필요. 없으면 XML/로그로 폴백
