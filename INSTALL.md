# AgentWatch 설치 안내

## 사전 조건

아래 두 가지가 PC에 설치되어 있어야 합니다.

### 1. Git
- 설치 확인: cmd에서 `git --version`
- 설치: https://git-scm.com/download/win

### 2. Claude CLI
- 설치 확인: cmd에서 `claude --version`
- 설치: https://claude.ai/code
- 설치 후 로그인: `claude` 실행 → 법인 계정으로 로그인

---

## 설치

### 1단계 — 파일 배치

`AgentWatch.zip`을 받아 **UE5 프로젝트 루트**에 압축 해제한다.

```
[UE5 프로젝트 루트]\        ← .uproject 파일이 있는 폴더
  ├── watch.exe             ← 압축 해제된 파일
  ├── .claude\
  │   └── mcp\
  │       ├── context_search.exe
  │       ├── log_analyzer.exe
  │       └── crash_analyzer.exe
  ├── MyGame.uproject
  └── Source\
      └── .git\             ← Git 저장소가 여기 있어야 함
```

> Source 폴더 안에 `.git`이 없다면 담당자에게 문의하세요.

### 2단계 — 최초 실행

`watch.exe`를 더블클릭하거나 cmd에서 실행한다.

```
감시 브랜치 (기본값: main):         ← Enter (기본값 사용) 또는 브랜치명 입력
폴링 간격 초 (기본값: 60):          ← Enter
```

입력 후 자동으로 `.claude\` 폴더 구조가 생성되고 감시가 시작된다.

### 3단계 — 확인

아래 폴더가 생성되면 정상 설치된 것이다.

```
[UE5 프로젝트 루트]\
  └── .claude\
      ├── context\    ← Git 변경 감지 시 여기에 MD 파일이 자동 생성됨
      └── agents\     ← 에이전트 9개 폴더
```

---

## 실행 / 종료

| 동작 | 방법 |
|------|------|
| 시작 | `watch.exe` 실행 |
| 종료 | 콘솔 창에서 `Ctrl + C` |
| 백그라운드 실행 | 콘솔 창 최소화 후 그대로 유지 |

---

## 업데이트

새 버전의 `AgentWatch.zip`을 받으면:

1. `watch.exe`와 `mcp\` 폴더를 새 파일로 덮어쓰기
2. `watch.exe` 재실행

> 기존 `.claude\` 폴더 안의 커스텀 설정은 자동으로 유지됩니다.

---

## 문제 해결

**`claude` 명령을 찾을 수 없음**
→ Claude CLI 설치 후 PC를 재시작하세요.

**`.git 폴더를 찾을 수 없습니다` 오류**
→ `watch.exe`가 UE5 프로젝트 루트(`.uproject` 파일과 같은 폴더)에 있는지 확인하세요.

**컨텍스트 MD가 생성되지 않음**
→ `claude --version`으로 CLI 로그인 상태를 확인하세요.
