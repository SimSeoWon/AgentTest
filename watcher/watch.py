import sys
import json
import time
import subprocess
from pathlib import Path
from datetime import datetime

from agent_templates import (
    AGENTS, ROLE_TEMPLATES, PROMPT_TEMPLATES,
    SKILL_INDEX, DEFAULT_CONTEXT_DOMAINS, SETTINGS_TEMPLATES
)

CONFIG_FILE = "config.json"
STATE_FILE = ".watch_state"


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
    # 1순위: Source 서브폴더 (Unreal 프로젝트 표준 구조)
    source_dir = base_dir / "Source"
    if (source_dir / ".git").exists():
        return source_dir

    # 2순위: exe가 있는 폴더 자체
    if (base_dir / ".git").exists():
        return base_dir

    # 3순위: 직접 하위 폴더 중 .git이 있는 첫 번째
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

    config = {
        "branch": branch,
        "poll_interval": poll_interval,
        "repo_dir": str(repo_dir),
    }

    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    print(f"\n설정 저장 완료: {config_path}\n")
    return config


def init_project_dirs(base_dir: Path) -> tuple[Path, Path]:
    """
    .claude/ 및 하위 디렉토리 초기화 (머지 방식).
    - 최초 실행: 전체 구조 생성
    - 재실행: 새 에이전트/도메인 추가, 기존 커스텀 파일 보존
    반환: (context_dir, agents_dir)
    """
    claude_dir = base_dir / ".claude"
    is_new = not claude_dir.exists()
    claude_dir.mkdir(exist_ok=True)

    # settings.json — 최초 1회만 생성 (사용자 설정 보존)
    settings_path = claude_dir / "settings.json"
    if not settings_path.exists():
        settings_path.write_text(
            json.dumps({"enabled": True}, ensure_ascii=False, indent=2),
            encoding='utf-8'
        )

    # context/ — 항상 실행: 새 도메인 폴더가 추가돼도 반영
    context_dir = claude_dir / "context"
    context_dir.mkdir(exist_ok=True)
    for domain in DEFAULT_CONTEXT_DOMAINS:
        (context_dir / domain).mkdir(exist_ok=True)

    # agents/ — 항상 실행: 새 에이전트가 추가돼도 반영
    agents_dir = claude_dir / "agents"
    agents_dir.mkdir(exist_ok=True)
    added = _merge_agents(agents_dir)

    if is_new:
        log(".claude/ 구조 초기화 완료")
    elif added:
        log(f".claude/ 머지 완료 — 새 에이전트 {len(added)}개 추가: {', '.join(added)}")

    return context_dir, agents_dir


def _merge_agents(agents_dir: Path) -> list[str]:
    """
    agents/ 하위를 현재 AGENTS 목록과 머지한다.
    - 없는 에이전트 폴더/파일 → 새로 생성
    - 이미 있는 role.md / prompt.md / settings.json → 보존 (커스텀 보호)
    - SKILL_INDEX.md → 항상 최신으로 덮어쓰기 (인덱스이므로)
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

    log(f"에이전트 폴더 {len(AGENTS)}개 초기화 완료")


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


def update_context(repo_dir: Path, context_dir: Path, changed_files: list[str]):
    target_extensions = {'.cpp', '.h', '.hpp', '.inl', '.cs', '.py'}

    for file_path in changed_files:
        full_path = repo_dir / file_path
        if not full_path.exists() or full_path.suffix not in target_extensions:
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

        result = subprocess.run(
            ["claude", "-p", prompt, "--dangerously-skip-permissions"],
            capture_output=True, text=True, encoding='utf-8'
        )

        if result.returncode != 0:
            print(f"[오류] Claude 호출 실패 ({file_path}): {result.stderr}")
            continue

        # 파일 경로를 그대로 context/ 아래에 미러링 (.md 확장자로)
        out_path = context_dir / Path(file_path).with_suffix('.md')
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(result.stdout, encoding='utf-8')
        print(f"  [갱신] {out_path.relative_to(context_dir.parent.parent)}")


def load_state(base_dir: Path) -> str | None:
    state_path = base_dir / STATE_FILE
    return state_path.read_text().strip() if state_path.exists() else None


def save_state(base_dir: Path, commit_hash: str):
    (base_dir / STATE_FILE).write_text(commit_hash)


def log(msg: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def main():
    base_dir = get_base_dir()

    print("=" * 50)
    print("  Git 컨텍스트 워처")
    print("=" * 50)

    # Git 저장소 자동 탐색
    try:
        repo_dir = find_git_repo(base_dir)
        log(f"Git 저장소 감지: {repo_dir}")
    except RuntimeError as e:
        print(f"\n[오류] {e}")
        input("\nEnter 키를 눌러 종료...")
        sys.exit(1)

    # 설정 로드 또는 최초 설정
    config = load_or_init_config(base_dir, repo_dir)
    branch = config["branch"]
    poll_interval = config["poll_interval"]

    # .claude/ 디렉토리 구조 초기화
    context_dir, agents_dir = init_project_dirs(base_dir)

    log(f"브랜치: {branch} | 폴링 간격: {poll_interval}초")
    log(f"컨텍스트 경로: {context_dir}")
    log(f"에이전트 경로: {agents_dir}")
    print()

    # 마지막 확인 커밋 해시 로드
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
                        log(f"변경된 파일 {len(changed_files)}개 — 컨텍스트 갱신 중...")

                        update_context(repo_dir, context_dir, changed_files)

                        last_hash = remote_hash
                        save_state(base_dir, last_hash)
                        log("컨텍스트 갱신 완료")
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
