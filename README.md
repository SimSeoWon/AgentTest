# AgentTest

Unreal Engine 5 프로젝트 팀을 위한 **Git 변경 감지 → RAG 컨텍스트 자동 갱신 → 다중 에이전트 분석** 파이프라인 빌드 저장소.

---

## 개요

이 저장소는 팀원들에게 배포할 실행 패키지(`dist/`)를 빌드하는 곳이다.
팀원은 `dist/` 폴더를 UE5 프로젝트 루트에 복사하고 `watch.exe`를 실행하기만 하면 된다.

```
Git 변경 감지 → git pull → 변경 파일 분석 (Claude CLI) → .claude/context/ MD 자동 갱신
```

---

## 저장소 구조

```
AgentTest/
├── watcher/
│   ├── watch.py                # 메인 워처 (PyInstaller 진입점)
│   └── agent_templates.py      # 에이전트·MCP 템플릿 상수
├── mcp/
│   ├── context_search/         # 태그 기반 컨텍스트 검색 MCP
│   ├── log_analyzer/           # UE5 로그 분석 MCP
│   └── crash_analyzer/         # UE5 크래시 분석 MCP
├── build.bat                   # 전체 빌드 스크립트
├── .gitignore
├── CLAUDE.md
└── README.md
```

---

## 개발 환경 준비

```bash
pip install pyinstaller mcp
```

---

## 빌드

```bash
build.bat
```

빌드 완료 후 `dist/` 폴더가 생성된다:

```
dist/
├── watch.exe
└── mcp/
    ├── context_search.exe
    ├── log_analyzer.exe
    └── crash_analyzer.exe
```

---

## 배포 방법

1. `dist/` 폴더 전체를 UE5 프로젝트 루트에 복사
2. `watch.exe` 실행
3. 최초 실행 시 브랜치명·폴링 간격 입력 → `config.json` 자동 생성

### 배포 전제 조건 (팀원 PC)

| 항목 | 필수 여부 |
|------|----------|
| Claude CLI (`claude` 명령) | **필수** — 법인 라이센스로 로그인 상태 |
| Git | **필수** |
| UE5 Source 폴더에 Git 초기화 | **필수** (`Source/.git` 존재) |
| Windows SDK (`cdb.exe`) | 선택 — `.dmp` 직접 분석 시 필요 |

---

## 배포 후 생성되는 구조

```
[UE5 프로젝트 루트]/
├── watch.exe
├── mcp/
├── config.json          ← 최초 실행 시 자동 생성
├── .watch_state         ← 자동 생성
└── .claude/
    ├── settings.json
    ├── context/         ← 10개 UE5 도메인 폴더 (자동 생성)
    └── agents/          ← 9개 에이전트 폴더 (자동 생성)
        ├── SKILL_INDEX.md
        ├── 01_소스분석/
        ├── 02_프로젝트분석/
        ├── 03_코드규약/
        ├── 04_코드작성/
        ├── 05_코드검증/
        ├── 06_빌드_통합/
        ├── 07_코드매니저/
        ├── 08_로그분석/
        └── 09_크래시분석/
```

> 재배포 시 기존 커스텀 설정(`role.md`, `prompt.md`, `settings.json`)은 보존된다.

---

## MCP 서버

| 서버 | 기능 |
|------|------|
| `context_search` | 태그로 `.claude/context/` MD 검색 |
| `log_analyzer` | UE5 `.log` 파일 에러·경고·패턴 분석 |
| `crash_analyzer` | `.dmp` / `CrashContext.runtime-xml` / 크래시 로그 분석 |

---

## 에이전트 추가 방법

1. `watcher/agent_templates.py`의 `AGENTS`, `ROLE_TEMPLATES`, `PROMPT_TEMPLATES`, `SETTINGS_TEMPLATES`에 항목 추가
2. `build.bat` 재실행으로 `dist/` 갱신
3. 팀원들에게 새 `dist/` 배포 → `watch.exe` 재실행 시 새 에이전트 자동 머지

## MCP 추가 방법

1. `mcp/<name>/server.py` 작성
2. `build.bat`에 PyInstaller 빌드 라인 추가
3. 관련 에이전트의 `SETTINGS_TEMPLATES`에 `mcpServers` 등록
