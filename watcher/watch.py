import re
import sys
import json
import time
import shutil
import subprocess
from pathlib import Path
from datetime import datetime

from agent_templates import (
    AGENTS, ROLE_TEMPLATES, PROMPT_TEMPLATES,
    SKILL_INDEX, DEFAULT_CONTEXT_DOMAINS, SETTINGS_TEMPLATES,
    MCP_SERVERS, PROJECT_CLAUDE_MD_SECTION,
    AGENTWATCH_MD_MARKER_START, AGENTWATCH_MD_MARKER_END,
)

CONFIG_FILE = "config.json"
STATE_FILE = ".watch_state"
TARGET_EXTENSIONS = {'.cpp', '.h', '.hpp', '.inl', '.cs', '.py'}
ASSET_EXTENSIONS = {'.uasset', '.umap'}


def get_base_dir() -> Path:
    """exe 실행 시 exe 위치, py 직접 실행 시 프로젝트 루트 반환"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent  # watcher/watch.py -> 프로젝트 루트


def find_git_repo(base_dir: Path) -> Path:
    """
    .git 위치를 자동 탐색한다.
    탐색 순서:
      1. base_dir/Source/
      2. base_dir 자체
      3. base_dir의 직접 하위 폴더들
    """
    source_dir = base_dir / "Source"
    if (source_dir / ".git").exists():
        return source_dir

    if (base_dir / ".git").exists():
        return base_dir

    for child in sorted(base_dir.iterdir()):
        if child.is_dir() and (child / ".git").exists():
            return child

    raise RuntimeError(
        f".git 폴더를 찾을 수 없습니다.\n"
        f"탐색 위치: {base_dir}\n"
        f"  - {base_dir / 'Source'} (없음)\n"
        f"  - {base_dir} (없음)\n"
        f"Unreal Source 폴더에 git 저장소가 초기화되어 있는지 확인하세요."
    )


def load_or_init_config(base_dir: Path, repo_dir: Path) -> dict:
    config_path = base_dir / CONFIG_FILE

    if config_path.exists():
        with open(config_path, encoding='utf-8') as f:
            config = json.load(f)
        if config.get("branch") and config.get("poll_interval"):
            return config

    print("=" * 50)
    print("  최초 설정")
    print("=" * 50)
    print(f"  감시 저장소: {repo_dir}")
    print()

    branch = input("감시 브랜치 (기본값: main): ").strip() or "main"

    interval_raw = input("폴링 간격 초 (기본값: 60): ").strip()
    poll_interval = int(interval_raw) if interval_raw.isdigit() else 60

    auto_review_raw = input("변경 감지 시 코드 리뷰 자동 실행 (기본값: y) [y/n]: ").strip().lower()
    auto_review = auto_review_raw != 'n'

    auto_asset_raw = input("에셋 변경 시 커맨드렛 검증 자동 실행 (기본값: y) [y/n]: ").strip().lower()
    auto_asset_validation = auto_asset_raw != 'n'

    gemini_available = shutil.which("gemini") is not None
    if gemini_available:
        use_gemini_raw = input("분석에 Gemini CLI 사용 (기본값: n) [y/n]: ").strip().lower()
        use_gemini = use_gemini_raw == 'y'
    else:
        print("Gemini CLI가 감지되지 않아 Claude를 사용합니다.")
        use_gemini = False

    config = {
        "branch": branch,
        "poll_interval": poll_interval,
        "auto_review": auto_review,
        "auto_asset_validation": auto_asset_validation,
        "use_gemini": use_gemini,
        "repo_dir": str(repo_dir),
    }

    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    print(f"\n설정 저장 완료: {config_path}\n")
    return config


def init_project_dirs(base_dir: Path) -> tuple[Path, Path, Path]:
    """
    .claude/ 및 하위 디렉토리 초기화 (머지 방식).
    반환: (context_dir, agents_dir, reviews_dir)
    """
    claude_dir = base_dir / ".claude"
    is_new = not claude_dir.exists()
    claude_dir.mkdir(exist_ok=True)

    # context/ — 항상 실행: 새 도메인 폴더가 추가돼도 반영
    context_dir = claude_dir / "context"
    context_dir.mkdir(exist_ok=True)
    for domain in DEFAULT_CONTEXT_DOMAINS:
        (context_dir / domain).mkdir(exist_ok=True)

    # reviews/ — 코드 리뷰 리포트 저장
    reviews_dir = claude_dir / "reviews"
    reviews_dir.mkdir(exist_ok=True)

    # agents/ — 항상 실행: 새 에이전트가 추가돼도 반영
    agents_dir = claude_dir / "agents"
    agents_dir.mkdir(exist_ok=True)
    added = _merge_agents(agents_dir)

    # MCP 서버 및 CLAUDE.md — 항상 머지 (기존 Claude 환경 대응)
    _update_project_settings(claude_dir)
    _update_project_claude_md(base_dir)

    if is_new:
        log(".claude/ 구조 초기화 완료")
    elif added:
        log(f".claude/ 머지 완료 — 새 에이전트 {len(added)}개 추가: {', '.join(added)}")

    return context_dir, agents_dir, reviews_dir


def _merge_agents(agents_dir: Path) -> list[str]:
    """
    agents/ 하위를 현재 AGENTS 목록과 머지한다.
    - 없는 에이전트 폴더/파일 → 새로 생성
    - 이미 있는 role.md / prompt.md / settings.json → 보존 (커스텀 보호)
    - SKILL_INDEX.md → 항상 최신으로 덮어쓰기
    반환: 새로 추가된 에이전트 이름 목록
    """
    added = []

    for agent_name in AGENTS:
        agent_dir = agents_dir / agent_name
        is_new_agent = not agent_dir.exists()
        agent_dir.mkdir(exist_ok=True)

        if not (agent_dir / "role.md").exists():
            (agent_dir / "role.md").write_text(
                ROLE_TEMPLATES.get(agent_name, f"# {agent_name}\n\n역할을 정의하세요.\n"),
                encoding='utf-8'
            )

        if not (agent_dir / "prompt.md").exists():
            (agent_dir / "prompt.md").write_text(
                PROMPT_TEMPLATES.get(agent_name, "# 프롬프트 템플릿\n\n프롬프트를 작성하세요.\n"),
                encoding='utf-8'
            )

        if not (agent_dir / "settings.json").exists():
            (agent_dir / "settings.json").write_text(
                json.dumps(SETTINGS_TEMPLATES.get(agent_name, {}), ensure_ascii=False, indent=2),
                encoding='utf-8'
            )

        if is_new_agent:
            added.append(agent_name)

    # SKILL_INDEX.md — 항상 최신 상태로 갱신
    (agents_dir / "SKILL_INDEX.md").write_text(SKILL_INDEX, encoding='utf-8')

    return added


def _update_project_settings(claude_dir: Path):
    """
    .claude/settings.json에 MCP 서버를 머지한다.
    - 이미 등록된 서버는 보존 (사용자 커스텀 유지)
    - 누락된 서버만 추가
    - 항상 실행 (기존 파일 여부 무관)
    """
    settings_path = claude_dir / "settings.json"
    settings: dict = {}

    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding='utf-8'))
        except Exception:
            settings = {}

    mcp = settings.setdefault("mcpServers", {})
    added = []
    for name, exe in MCP_SERVERS.items():
        if name not in mcp:
            mcp[name] = {"command": exe, "args": []}
            added.append(name)

    settings_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )

    if added:
        log(f"settings.json MCP 등록 추가: {', '.join(added)}")


def _merge_claude_md_section(md_path: Path):
    """
    지정한 CLAUDE.md 파일에 AgentWatch 관리 구역을 삽입/갱신한다.
    - 마커 구역 있으면 최신 내용으로 교체
    - 없으면 파일 끝에 추가
    - 파일 자체가 없으면 새로 생성
    """
    if md_path.exists():
        content = md_path.read_text(encoding='utf-8')
        if AGENTWATCH_MD_MARKER_START in content:
            content = re.sub(
                re.escape(AGENTWATCH_MD_MARKER_START)
                + r".*?"
                + re.escape(AGENTWATCH_MD_MARKER_END),
                PROJECT_CLAUDE_MD_SECTION,
                content,
                flags=re.DOTALL,
            )
            return content, "갱신"
        else:
            content = content.rstrip() + "\n\n" + PROJECT_CLAUDE_MD_SECTION + "\n"
            return content, "추가"
    else:
        return PROJECT_CLAUDE_MD_SECTION + "\n", "생성"


def _update_project_claude_md(base_dir: Path):
    """
    CLAUDE.md에 AgentWatch 관리 구역을 삽입/갱신한다.
    대상:
      1. 프로젝트 루트 CLAUDE.md (항상)
      2. .claude/CLAUDE.md (존재하는 경우에만 — 팀이 여기서 규칙을 관리하는 경우 대응)
    항상 실행 (기존 파일 여부 무관)
    """
    # 1. 루트 CLAUDE.md
    root_md = base_dir / "CLAUDE.md"
    content, action = _merge_claude_md_section(root_md)
    root_md.write_text(content, encoding='utf-8')
    log(f"CLAUDE.md AgentWatch 구역 {action}")

    # 2. .claude/CLAUDE.md (있을 때만)
    dot_claude_md = base_dir / ".claude" / "CLAUDE.md"
    if dot_claude_md.exists():
        content, action = _merge_claude_md_section(dot_claude_md)
        dot_claude_md.write_text(content, encoding='utf-8')
        log(f".claude/CLAUDE.md AgentWatch 구역 {action}")


# ─────────────────────────────────────────
# Git 유틸
# ─────────────────────────────────────────

def git_fetch(repo_dir: Path) -> bool:
    result = subprocess.run(
        ["git", "fetch"],
        cwd=repo_dir, capture_output=True, text=True
    )
    return result.returncode == 0


def get_remote_hash(repo_dir: Path, branch: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", f"origin/{branch}"],
        cwd=repo_dir, capture_output=True, text=True
    )
    return result.stdout.strip()


def get_local_hash(repo_dir: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_dir, capture_output=True, text=True
    )
    return result.stdout.strip()


def git_pull(repo_dir: Path, branch: str) -> bool:
    result = subprocess.run(
        ["git", "pull", "origin", branch],
        cwd=repo_dir, capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"[오류] git pull 실패:\n{result.stderr}")
    return result.returncode == 0


def get_changed_files(repo_dir: Path, old_hash: str, new_hash: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", old_hash, new_hash],
        cwd=repo_dir, capture_output=True, text=True
    )
    return [f for f in result.stdout.strip().split('\n') if f]


# ─────────────────────────────────────────
# 컨텍스트 갱신
# ─────────────────────────────────────────

def update_context(repo_dir: Path, context_dir: Path, changed_files: list[str]):
    for file_path in changed_files:
        full_path = repo_dir / file_path
        if not full_path.exists() or full_path.suffix not in TARGET_EXTENSIONS:
            continue

        try:
            content = full_path.read_text(encoding='utf-8', errors='ignore')
        except Exception as e:
            print(f"[경고] 파일 읽기 실패 ({file_path}): {e}")
            continue

        prompt = PROMPT_TEMPLATES["01_소스분석"].format(
            file_path=file_path,
            content=content[:4000],
        )

        result = _call_claude(prompt)
        if result is None:
            continue

        out_path = context_dir / Path(file_path).with_suffix('.md')
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(result, encoding='utf-8')
        print(f"  [컨텍스트] {out_path.relative_to(context_dir.parent.parent)}")


# ─────────────────────────────────────────
# 코드 리뷰
# ─────────────────────────────────────────

def run_code_review(
    repo_dir: Path,
    context_dir: Path,
    reviews_dir: Path,
    changed_files: list[str],
    commit_hash: str,
    use_gemini: bool = False,
):
    """변경된 파일에 대해 코드 리뷰를 실행하고 .claude/reviews/ 에 저장한다."""
    reviewable = [
        f for f in changed_files
        if (repo_dir / f).exists() and Path(f).suffix in TARGET_EXTENSIONS
    ]

    if not reviewable:
        log("리뷰 대상 파일 없음 — 건너뜀")
        return

    log(f"코드 리뷰 시작 — {len(reviewable)}개 파일")

    file_reviews: list[dict] = []

    for file_path in reviewable:
        full_path = repo_dir / file_path
        try:
            content = full_path.read_text(encoding='utf-8', errors='ignore')[:4000]
        except Exception:
            continue

        # 해당 파일의 컨텍스트 MD 로드 (없으면 빈 문자열)
        context_md = context_dir / Path(file_path).with_suffix('.md')
        context = context_md.read_text(encoding='utf-8') if context_md.exists() else ""

        print(f"  [리뷰] {file_path}")

        convention = _call_llm(
            f"아래 코드가 UE5 팀 코딩 컨벤션을 준수하는지 검토해줘.\n"
            f"검토 기준: 클래스명 파스칼케이스, 함수명 파스칼케이스, "
            f"멤버변수 접두사(b/f/i), public 함수 주석 필수.\n"
            f"결과를 표로 정리해줘: | 항목 | 위반 내용 | 라인 | 심각도 |\n\n"
            f"파일: {file_path}\n```\n{content}\n```",
            use_gemini=use_gemini,
        )

        validation = _call_llm(
            f"아래 코드에서 잠재적 버그와 안전성 이슈를 찾아줘.\n"
            f"Null 포인터, 메모리 누수, 멀티스레드 안전성, 배열 범위 초과, 미초기화 변수를 검토해줘.\n"
            f"형식: | 위험도 | 라인 | 설명 | 권장 수정 |\n\n"
            f"관련 컨텍스트:\n{context[:1000]}\n\n"
            f"파일: {file_path}\n```\n{content}\n```",
            use_gemini=use_gemini,
        )

        file_reviews.append({
            "file": file_path,
            "convention": convention or "분석 실패",
            "validation": validation or "분석 실패",
        })

    if not file_reviews:
        return

    # 07_코드매니저로 통합 리포트 생성
    log("통합 리포트 생성 중...")
    report = _build_review_report(file_reviews, commit_hash, use_gemini=use_gemini)

    timestamp = datetime.now().strftime('%Y-%m-%d_%H%M')
    report_path = reviews_dir / f"{timestamp}_{commit_hash[:8]}.md"
    report_path.write_text(report, encoding='utf-8')
    log(f"코드 리뷰 완료 → {report_path.relative_to(reviews_dir.parent.parent)}")


def _build_review_report(file_reviews: list[dict], commit_hash: str, use_gemini: bool = False) -> str:
    """07_코드매니저 프롬프트로 통합 리포트를 생성한다."""
    files_summary = "\n\n".join(
        f"### {r['file']}\n"
        f"**규약 검토**\n{r['convention']}\n\n"
        f"**코드 검증**\n{r['validation']}"
        for r in file_reviews
    )

    prompt = (
        f"아래 에이전트 리포트들을 통합하여 최종 코드 리뷰 리포트를 작성해줘.\n\n"
        f"커밋: {commit_hash}\n"
        f"검토 파일 수: {len(file_reviews)}개\n\n"
        f"{files_summary}\n\n"
        f"최종 리포트 형식:\n"
        f"## 요약\n"
        f"## 즉시 수정 필요 (Critical)\n"
        f"## 권장 수정 (Warning)\n"
        f"## 참고 사항 (Info)\n"
        f"## 액션 아이템"
    )

    result = _call_llm(prompt, use_gemini=use_gemini)
    if result:
        return f"# 코드 리뷰 리포트\n\n커밋: `{commit_hash}`  \n생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n{result}"

    # Claude 호출 실패 시 원본 취합본 반환
    return (
        f"# 코드 리뷰 리포트\n\n"
        f"커밋: `{commit_hash}`  \n"
        f"생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"{files_summary}"
    )


def _call_claude(prompt: str) -> str | None:
    """Claude CLI를 호출하고 결과 텍스트를 반환한다. 실패 시 None."""
    result = subprocess.run(
        ["claude", "-p", prompt, "--dangerously-skip-permissions"],
        capture_output=True, text=True, encoding='utf-8'
    )
    if result.returncode != 0:
        print(f"[오류] Claude 호출 실패: {result.stderr[:200]}")
        return None
    return result.stdout


def _call_gemini(prompt: str) -> str | None:
    """Gemini CLI를 호출하고 결과 텍스트를 반환한다. 실패 시 None."""
    if not shutil.which("gemini"):
        print("[경고] Gemini CLI를 찾을 수 없어 Claude로 대체합니다.")
        return _call_claude(prompt)
    result = subprocess.run(
        ["gemini", "-y", prompt],
        capture_output=True, text=True, encoding='utf-8', errors='replace'
    )
    if result.returncode != 0:
        print(f"[오류] Gemini 호출 실패: {result.stderr[:200]}")
        return None
    return result.stdout


def _call_llm(prompt: str, use_gemini: bool = False) -> str | None:
    """use_gemini 플래그에 따라 Gemini 또는 Claude를 호출한다."""
    if use_gemini:
        return _call_gemini(prompt)
    return _call_claude(prompt)


# ─────────────────────────────────────────
# 에셋 검증 (커맨드렛)
# ─────────────────────────────────────────

def _find_uproject(base_dir: Path) -> Path | None:
    for p in base_dir.glob("*.uproject"):
        return p
    return None


def _find_unreal_editor(base_dir: Path) -> tuple[str | None, str | None]:
    """(editor_path, uproject_path) 반환. 탐색 실패 시 None."""
    uproject = _find_uproject(base_dir)
    if not uproject:
        return None, None

    try:
        with open(uproject, encoding='utf-8') as f:
            version = json.load(f).get("EngineAssociation", "")
    except Exception:
        return None, str(uproject)

    # 1. 레지스트리 탐색
    editor = None
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for arch in (winreg.KEY_READ, winreg.KEY_READ | winreg.KEY_WOW64_32KEY):
                try:
                    key = winreg.OpenKey(
                        hive,
                        f"SOFTWARE\\EpicGames\\Unreal Engine\\{version}",
                        access=arch
                    )
                    install_dir, _ = winreg.QueryValueEx(key, "InstalledDirectory")
                    for exe in ("UnrealEditor-Cmd.exe", "UE4Editor-Cmd.exe"):
                        candidate = Path(install_dir) / "Engine" / "Binaries" / "Win64" / exe
                        if candidate.exists():
                            editor = str(candidate)
                            break
                except Exception:
                    continue
            if editor:
                break
    except ImportError:
        pass

    # 2. 환경변수 탐색
    if not editor:
        import os
        pf = os.environ.get("ProgramFiles", "C:\\Program Files")
        for ue_dir in Path(pf, "Epic Games").glob(f"UE_{version}*"):
            for exe in ("UnrealEditor-Cmd.exe", "UE4Editor-Cmd.exe"):
                candidate = ue_dir / "Engine" / "Binaries" / "Win64" / exe
                if candidate.exists():
                    editor = str(candidate)
                    break

    return editor, str(uproject)


def run_asset_validation(
    base_dir: Path,
    reviews_dir: Path,
    changed_files: list[str],
    commit_hash: str,
    use_gemini: bool = False,
):
    """변경된 .uasset / .umap 파일에 대해 DataValidation 커맨드렛을 실행한다."""
    assets = [f for f in changed_files if Path(f).suffix in ASSET_EXTENSIONS]
    if not assets:
        return

    log(f"에셋 검증 시작 — {len(assets)}개 파일")

    editor, uproject = _find_unreal_editor(base_dir)
    if not editor:
        log("[경고] UnrealEditor-Cmd.exe를 찾을 수 없어 에셋 검증을 건너뜁니다.")
        return

    try:
        result = subprocess.run(
            [editor, uproject,
             "-run=DataValidation", "-log", "-unattended", "-nullrhi"],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=300
        )
        raw_output = result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        log("[경고] 커맨드렛 타임아웃 (300초) — 에셋 검증 중단")
        return
    except Exception as e:
        log(f"[오류] 커맨드렛 실행 실패: {e}")
        return

    log("에셋 검증 결과 분석 중...")
    prompt = (
        f"아래는 UE5 DataValidation 커맨드렛 실행 결과입니다.\n"
        f"변경된 에셋 목록:\n" +
        "\n".join(f"  - {a}" for a in assets) +
        f"\n\n커맨드렛 출력 (마지막 6000자):\n```\n{raw_output[-6000:]}\n```\n\n"
        f"다음 형식으로 분석해줘:\n"
        f"## 검증 요약\n"
        f"## 에러 목록 (에셋별)\n"
        f"## 경고 목록\n"
        f"## 수정 필요 항목"
    )

    analysis = _call_llm(prompt, use_gemini=use_gemini)

    timestamp = datetime.now().strftime('%Y-%m-%d_%H%M')
    report_path = reviews_dir / f"{timestamp}_{commit_hash[:8]}_assets.md"
    report_path.write_text(
        f"# 에셋 검증 리포트\n\n"
        f"커밋: `{commit_hash}`  \n"
        f"생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"## 검증 대상 에셋\n" +
        "\n".join(f"- `{a}`" for a in assets) +
        f"\n\n{analysis or '(분석 결과 없음)'}",
        encoding='utf-8'
    )
    log(f"에셋 검증 완료 → {report_path.relative_to(base_dir)}")


# ─────────────────────────────────────────
# 상태 저장
# ─────────────────────────────────────────

def load_state(base_dir: Path) -> str | None:
    state_path = base_dir / STATE_FILE
    return state_path.read_text().strip() if state_path.exists() else None


def save_state(base_dir: Path, commit_hash: str):
    (base_dir / STATE_FILE).write_text(commit_hash)


def log(msg: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


# ─────────────────────────────────────────
# 메인
# ─────────────────────────────────────────

def main():
    base_dir = get_base_dir()

    print("=" * 50)
    print("  Git 컨텍스트 워처")
    print("=" * 50)

    try:
        repo_dir = find_git_repo(base_dir)
        log(f"Git 저장소 감지: {repo_dir}")
    except RuntimeError as e:
        print(f"\n[오류] {e}")
        input("\nEnter 키를 눌러 종료...")
        sys.exit(1)

    config = load_or_init_config(base_dir, repo_dir)
    branch = config["branch"]
    poll_interval = config["poll_interval"]
    auto_review = config.get("auto_review", True)
    auto_asset_validation = config.get("auto_asset_validation", True)
    use_gemini = config.get("use_gemini", False) and bool(shutil.which("gemini"))

    context_dir, agents_dir, reviews_dir = init_project_dirs(base_dir)

    llm_label = "Gemini" if use_gemini else "Claude"
    log(
        f"브랜치: {branch} | 폴링 간격: {poll_interval}초 | "
        f"자동 리뷰: {'ON' if auto_review else 'OFF'} | "
        f"에셋 검증: {'ON' if auto_asset_validation else 'OFF'} | "
        f"분석 엔진: {llm_label}"
    )
    log(f"컨텍스트: {context_dir}")
    log(f"리뷰 저장: {reviews_dir}")
    print()

    last_hash = load_state(base_dir) or get_local_hash(repo_dir)
    save_state(base_dir, last_hash)

    log("감시 시작... (종료: Ctrl+C)")

    while True:
        try:
            if not git_fetch(repo_dir):
                log("[경고] git fetch 실패, 재시도 대기 중...")
            else:
                remote_hash = get_remote_hash(repo_dir, branch)

                if remote_hash and remote_hash != last_hash:
                    log(f"새 커밋 감지: {last_hash[:8]} → {remote_hash[:8]}")

                    if git_pull(repo_dir, branch):
                        changed_files = get_changed_files(repo_dir, last_hash, remote_hash)
                        log(f"변경된 파일 {len(changed_files)}개")

                        log("컨텍스트 갱신 중...")
                        update_context(repo_dir, context_dir, changed_files)
                        log("컨텍스트 갱신 완료")

                        if auto_review:
                            run_code_review(repo_dir, context_dir, reviews_dir, changed_files, remote_hash, use_gemini=use_gemini)

                        if auto_asset_validation:
                            run_asset_validation(base_dir, reviews_dir, changed_files, remote_hash, use_gemini=use_gemini)

                        last_hash = remote_hash
                        save_state(base_dir, last_hash)
                else:
                    print(
                        f"\r[{datetime.now().strftime('%H:%M:%S')}] 대기 중... "
                        f"(마지막 커밋: {last_hash[:8]})",
                        end='', flush=True
                    )

        except KeyboardInterrupt:
            print()
            log("종료")
            sys.exit(0)
        except Exception as e:
            log(f"[오류] {e}")

        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
