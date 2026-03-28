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
│   │   └── server.py              ← 태그 기반 컨텍스트 검색 MCP
│   ├── log_analyzer/
│   │   └── server.py              ← UE5 로그 파일 분석 MCP
│   ├── crash_analyzer/
│   │   └── server.py              ← UE5 크래시 덤프/로그 분석 MCP
│   └── commandlet_runner/
│       └── server.py              ← UE5 커맨드렛 실행 MCP (DataValidation 등)
├── build.bat                      ← 전체 빌드 스크립트 (watch.exe + MCP 4종)
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
│   │   └── commandlet_runner.exe
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
│       └── 10_에셋검증/
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

**초기화 및 머지 (`init_project_dirs` + `_merge_agents`)**

최초 실행과 재실행 모두 동일한 함수가 실행되며, 머지 방식으로 동작한다:

| 대상 | 최초 실행 | 재실행 (이미 존재) |
|------|----------|-----------------|
| `.claude/settings.json` | 생성 | 보존 (사용자 설정 유지) |
| `context/` 도메인 폴더 | 생성 | 없는 폴더만 추가 |
| `reviews/` 폴더 | 생성 | 유지 |
| 에이전트 폴더 | 생성 | 없는 폴더만 추가 |
| `role.md` / `prompt.md` / `settings.json` | 생성 | 보존 (커스텀 보호) |
| `SKILL_INDEX.md` | 생성 | **항상 덮어쓰기** (인덱스 최신 유지) |
| `config.json` | 생성 (auto_review 포함) | 보존 |

**감시 루프**
- `git fetch` → remote 해시와 local 해시 비교
- 변경 감지 시: `git pull` → `git diff --name-only` → 아래 세 작업 순차 실행
  1. **컨텍스트 갱신** (`update_context`) — `01_소스분석` 프롬프트로 MD 생성 → `context/`
  2. **코드 리뷰** (`run_code_review`) — `auto_review: true` 시에만 실행 → `reviews/`
  3. **에셋 검증** (`run_asset_validation`) — `auto_asset_validation: true` 시에만 실행 → `reviews/`
- 소스 대상 확장자: `.cpp`, `.h`, `.hpp`, `.inl`, `.cs`, `.py`
- 에셋 대상 확장자: `.uasset`, `.umap`
- `.watch_state` 파일로 마지막 커밋 해시 영속 저장 (멱등성 보장)

**코드 리뷰 흐름 (`run_code_review`)**
- 파일별: `03_코드규약` + `05_코드검증` 각각 Claude CLI 호출
- 전체 취합: `07_코드매니저` 프롬프트로 통합 리포트 생성
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
claude -p "[프롬프트]" --dangerously-skip-permissions
```
→ `01_소스분석`의 `PROMPT_TEMPLATES`를 사용하여 프롬프트 생성

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

### 3. `mcp/` — MCP 서버 4종

| 서버 | 빌드 산출물 | 제공 툴 | 사용 에이전트 |
|------|------------|---------|-------------|
| `context_search` | `mcp/context_search.exe` | `search_context`, `list_tags` | 02, 07 |
| `log_analyzer` | `mcp/log_analyzer.exe` | `analyze_log`, `search_log` | 08 |
| `crash_analyzer` | `mcp/crash_analyzer.exe` | `analyze_crash`, `analyze_crash_log` | 09 |
| `commandlet_runner` | `mcp/commandlet_runner.exe` | `find_unreal_editor`, `run_data_validation`, `run_commandlet` | 10 |

- `crash_analyzer`는 `cdb.exe`(Windows SDK) 유무를 자동 감지하여 `.dmp` 직접 분석 또는 XML/로그 폴백
- `commandlet_runner`는 `.uproject`의 `EngineAssociation` → `winreg` → `%ProgramFiles%` 순으로 엔진 탐색
- 모든 MCP는 `./.claude/mcp/<name>.exe` 경로로 각 에이전트의 `settings.json`에 등록됨

### 4. `build.bat`

총 5단계 빌드:
```
[1/5] watch.exe              ← --paths "watcher" 로 agent_templates.py 인식
[2/5] context_search.exe
[3/5] log_analyzer.exe
[4/5] crash_analyzer.exe
[5/5] commandlet_runner.exe  → 모두 dist/.claude/mcp/ 에 출력
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
```

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
