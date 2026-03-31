import re
import os
import sys
import json
import time
import atexit
import signal
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

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
MAX_WORKERS = 6  # 병렬 LLM 호출 수
_processing_lock = threading.Lock()  # 중복 실행 방지

# 서버 모드 상태
_server_mode = False
_server_url = ""   # 예: "http://localhost:8100"
_server_proc = None  # context_search --serve 프로세스


def _http_post(endpoint: str, payload: dict, timeout: int = 120) -> dict | None:
    """HTTP POST 요청. 서버 모드에서 인덱싱/검색에 사용."""
    import urllib.request
    import urllib.error
    url = f"{_server_url.rstrip('/')}{endpoint}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log(f"[경고] HTTP 요청 실패 ({endpoint}): {e}")
        return None


def _http_get(endpoint: str, timeout: int = 30) -> dict | None:
    """HTTP GET 요청. 서버 모드에서 상태 확인에 사용."""
    import urllib.request
    import urllib.error
    url = f"{_server_url.rstrip('/')}{endpoint}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log(f"[경고] HTTP 요청 실패 ({endpoint}): {e}")
        return None


# ─────────────────────────────────────────
# 프로세스 정리 (Windows)
# ─────────────────────────────────────────

def _setup_job_object():
    """
    Windows Job Object를 생성하여 현재 프로세스에 할당한다.
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE 플래그로
    watch.exe 종료 시 모든 자식/손자 프로세스(claude, MCP 서버)가 자동 정리된다.
    """
    if sys.platform != 'win32':
        return None
    try:
        import ctypes
        import ctypes.wintypes as wt

        kernel32 = ctypes.windll.kernel32

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ('PerProcessUserTimeLimit', ctypes.c_longlong),
                ('PerJobUserTimeLimit', ctypes.c_longlong),
                ('LimitFlags', wt.DWORD),
                ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', wt.DWORD),
                ('Affinity', ctypes.c_size_t),
                ('PriorityClass', wt.DWORD),
                ('SchedulingClass', wt.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(f'c{i}', ctypes.c_ulonglong) for i in range(6)]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ('IoInfo', IO_COUNTERS),
                ('ProcessMemoryLimit', ctypes.c_size_t),
                ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t),
                ('PeakJobMemoryUsed', ctypes.c_size_t),
            ]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        kernel32.SetInformationJobObject(
            job, 9,  # JobObjectExtendedLimitInformation
            ctypes.byref(info), ctypes.sizeof(info),
        )
        kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess())

        return job  # 핸들을 유지해야 GC되지 않음
    except Exception:
        return None


# MCP 프로세스명 목록 (Job Object 실패 시 폴백용)
_MCP_PROCESS_NAMES = [
    "context_search.exe", "log_analyzer.exe", "crash_analyzer.exe",
    "commandlet_runner.exe", "gemini_query.exe",
]


def _kill_orphan_mcps():
    """알려진 MCP 프로세스를 강제 종료한다 (Job Object 실패 시 폴백)."""
    # 서버 프로세스 정리
    global _server_proc
    if _server_proc is not None:
        try:
            _server_proc.terminate()
            _server_proc.wait(timeout=5)
        except Exception:
            try:
                _server_proc.kill()
            except Exception:
                pass
        _server_proc = None
    if sys.platform != 'win32':
        return
    for name in _MCP_PROCESS_NAMES:
        os.system(f'taskkill /F /IM {name} >nul 2>&1')


def _on_exit_signal(signum, frame):
    """SIGBREAK(콘솔 창 닫기) 시 정리 후 종료."""
    _kill_orphan_mcps()
    sys.exit(0)


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
        use_gemini = False

    print("\nClaude 모델 선택 (자동 파이프라인용):")
    print("  1. claude-sonnet-4-6  — 빠름, 대부분의 작업에 충분 (기본값)")
    print("  2. claude-opus-4-6    — 느리지만 정확, 복잡한 코드 분석에 유리")
    print("  3. claude-haiku-4-5   — 가장 빠름, 단순 작업용")
    model_choice = input("선택 [1/2/3]: ").strip()
    if model_choice == "2":
        claude_model = "claude-opus-4-6"
    elif model_choice == "3":
        claude_model = "claude-haiku-4-5"
    else:
        claude_model = "claude-sonnet-4-6"

    print("\n서버 모드 설정:")
    print("  공용 PC에서 RAG 서버를 운영하려면 y를 선택하세요.")
    server_mode_raw = input("서버 모드 활성화 (기본값: n) [y/n]: ").strip().lower()
    server_mode = server_mode_raw == 'y'

    server_port = 8100
    server_host = "0.0.0.0"
    context_server_url = ""
    if server_mode:
        port_raw = input("HTTP 서버 포트 (기본값: 8100): ").strip()
        server_port = int(port_raw) if port_raw.isdigit() else 8100
    else:
        url_raw = input("원격 RAG 서버 URL (없으면 Enter, 예: http://192.168.1.100:8100): ").strip()
        context_server_url = url_raw

    config = {
        "branch": branch,
        "poll_interval": poll_interval,
        "auto_review": auto_review,
        "auto_asset_validation": auto_asset_validation,
        "use_gemini": use_gemini,
        "claude_model": claude_model,
        "server_mode": server_mode,
        "server_host": server_host,
        "server_port": server_port,
        "context_server_url": context_server_url,
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
    MCP 서버를 두 곳에 머지한다:
      1. 프로젝트 루트 .mcp.json — Claude Code가 실제로 읽는 파일
      2. .claude/settings.json — 에이전트 레벨 참조용 (하위 호환)
    - 이미 등록된 서버는 보존 (사용자 커스텀 유지)
    - 누락된 서버만 추가
    - 항상 실행 (기존 파일 여부 무관)
    """
    base_dir = claude_dir.parent

    # 1. .mcp.json (프로젝트 루트) — Claude Code가 읽는 MCP 설정
    mcp_json_path = base_dir / ".mcp.json"
    mcp_json: dict = {}
    if mcp_json_path.exists():
        try:
            mcp_json = json.loads(mcp_json_path.read_text(encoding='utf-8'))
        except Exception:
            mcp_json = {}

    mcp_servers = mcp_json.setdefault("mcpServers", {})
    added_mcp = []
    for name, exe in MCP_SERVERS.items():
        if name not in mcp_servers:
            mcp_servers[name] = {"command": exe, "args": []}
            added_mcp.append(name)

    mcp_json_path.write_text(
        json.dumps(mcp_json, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )

    if added_mcp:
        log(f".mcp.json MCP 등록 추가: {', '.join(added_mcp)}")

    # 2. .claude/settings.json — 하위 호환 및 에이전트 참조용
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
# 벡터 인덱싱 (context_search.exe에 위임)
# ─────────────────────────────────────────

def _get_context_search_cmd(base_dir: Path) -> list[str]:
    """context_search 실행 명령어를 반환한다. exe 없으면 Python 직접 실행."""
    exe = base_dir / ".claude" / "mcp" / "context_search.exe"
    if exe.exists():
        return [str(exe)]
    # 개발 모드: Python 스크립트 직접 실행
    script = Path(__file__).parent.parent / "mcp" / "context_search" / "server.py"
    if script.exists():
        return [sys.executable, str(script)]
    return []


def update_vector_index(context_dir: Path, base_dir: Path, changed_files: list[str] | None = None):
    """
    벡터 인덱싱을 수행한다.
    서버 모드: HTTP API로 요청, 로컬 모드: context_search.exe CLI 호출.
    changed_files 지정 시 해당 파일의 MD만 upsert, 미지정 시 전체 rebuild.
    """
    # ── 서버 모드: HTTP API 사용 ──
    if _server_mode and _server_url:
        if changed_files:
            md_set: set[str] = set()
            for f in changed_files:
                if Path(f).suffix not in TARGET_EXTENSIONS:
                    continue
                dir_md = context_dir / (str(Path(f).parent) + ".md")
                if dir_md.exists():
                    md_set.add(str(dir_md.relative_to(context_dir)).replace("\\", "/"))
                file_md = context_dir / Path(f).with_suffix('.md')
                if file_md.exists():
                    md_set.add(str(file_md.relative_to(context_dir)).replace("\\", "/"))
            md_paths = sorted(md_set)
            if not md_paths:
                return
            result = _http_post("/api/v1/index/upsert", {"files": md_paths})
        else:
            result = _http_post("/api/v1/index/rebuild", {})

        if result:
            count = result.get("indexed_files") or result.get("upserted_files") or 0
            if count:
                log(f"벡터 인덱스 갱신: {count}개 문서")
            if result.get("error"):
                log(f"[경고] 벡터 인덱싱 오류: {result['error']}")
        return

    # ── 로컬 모드: subprocess CLI 호출 ──
    cmd_base = _get_context_search_cmd(base_dir)
    if not cmd_base:
        log("[경고] context_search를 찾을 수 없어 벡터 인덱싱 건너뜀")
        return

    if changed_files:
        # 변경된 소스 파일의 디렉토리 → 디렉토리 단위 MD 경로 수집 (중복 제거)
        md_set: set[str] = set()
        for f in changed_files:
            if Path(f).suffix not in TARGET_EXTENSIONS:
                continue
            # 디렉토리 단위 MD: Source/Module/SubDir.md
            dir_md = context_dir / (str(Path(f).parent) + ".md")
            if dir_md.exists():
                md_set.add(str(dir_md.relative_to(context_dir)).replace("\\", "/"))
            # 하위 호환: 기존 클래스 단위 MD도 체크
            file_md = context_dir / Path(f).with_suffix('.md')
            if file_md.exists():
                md_set.add(str(file_md.relative_to(context_dir)).replace("\\", "/"))
        md_paths = sorted(md_set)
        if not md_paths:
            return
        cmd = cmd_base + ["--upsert", str(base_dir)] + md_paths
    else:
        cmd = cmd_base + ["--rebuild", str(base_dir)]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=120
        )
        if result.returncode == 0 and result.stdout.strip():
            import json as _json
            info = _json.loads(result.stdout.strip())
            count = info.get("indexed_files") or info.get("upserted_files") or 0
            if count:
                log(f"벡터 인덱스 갱신: {count}개 문서")
        elif result.returncode != 0:
            log(f"[경고] 벡터 인덱싱 실패: {result.stderr[:200]}")
    except subprocess.TimeoutExpired:
        log("[경고] 벡터 인덱싱 타임아웃 (120초)")
    except Exception as e:
        log(f"[경고] 벡터 인덱싱 오류: {e}")


def search_related_contexts(base_dir: Path, query: str, n_results: int = 3) -> list[dict]:
    """
    관련 컨텍스트를 검색한다.
    서버 모드: HTTP API, 로컬 모드: context_search.exe --search.
    반환: [{"file": ..., "content_preview": ..., "tags": [...], ...}, ...]
    """
    # ── 서버 모드: HTTP API 사용 ──
    if _server_mode and _server_url:
        result = _http_post("/api/v1/search/combined", {
            "query": query, "n_results": n_results,
        }, timeout=30)
        if result:
            return result.get("results", [])
        return []

    # ── 로컬 모드: subprocess CLI 호출 ──
    cmd_base = _get_context_search_cmd(base_dir)
    if not cmd_base:
        return []

    cmd = cmd_base + ["--search", str(base_dir), query, str(n_results)]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout.strip())
            return data.get("results", [])
    except Exception:
        pass
    return []


# ─────────────────────────────────────────
# 컨텍스트 갱신
# ─────────────────────────────────────────

def _extract_comments_section(md_content: str) -> str:
    """MD 파일에서 ## 코멘트 섹션을 추출한다. 없으면 빈 문자열 반환."""
    match = re.search(r'(## 코멘트\s*\n.*)', md_content, re.DOTALL)
    return match.group(1).rstrip() if match else ""


HEADER_EXTENSIONS = {'.h', '.hpp', '.inl'}

# 디렉토리당 최대 프롬프트 크기 (클래스 여러 개를 한번에 분석하므로 넉넉하게)
GROUP_CONTENT_LIMIT = 8000


def _group_by_directory(changed_files: list[str], repo_dir: Path) -> dict[str, list[str]]:
    """
    변경 파일을 부모 디렉토리(모듈) 단위로 묶는다.
    키: 부모 디렉토리 상대경로 (예: Source/ModularStage/Mission/TaskSystem)
    값: 해당 디렉토리의 파일 경로 목록
    """
    groups: dict[str, list[str]] = {}
    for file_path in changed_files:
        full_path = repo_dir / file_path
        if not full_path.exists() or full_path.suffix not in TARGET_EXTENSIONS:
            continue
        dir_key = str(Path(file_path).parent)
        groups.setdefault(dir_key, []).append(file_path)
    return groups


def _build_related_context(base_dir: Path, file_path: str, context: str) -> str:
    """
    파일의 컨텍스트 요약을 검색 쿼리로 사용하여 관련 파일 컨텍스트를 수집한다.
    반환: 관련 컨텍스트 문자열 (없으면 빈 문자열)
    """
    query = Path(file_path).stem
    if context:
        body = re.sub(r'^---\s*\n.*?\n---\s*\n', '', context, count=1, flags=re.DOTALL).strip()
        query += " " + body[:200]

    results = search_related_contexts(base_dir, query, n_results=3)
    if not results:
        return ""

    file_stem = Path(file_path).with_suffix('.md').name
    related = []
    for r in results:
        if file_stem in r.get("file", ""):
            continue
        preview = r.get("content_preview", "")[:400]
        if preview:
            related.append(f"[{r.get('file', '?')}]\n{preview}")

    return "\n\n".join(related) if related else ""


def _process_directory_group(
    repo_dir: Path, context_dir: Path, base_dir: Path,
    dir_key: str, file_paths: list[str],
    auto_review: bool, use_gemini: bool,
) -> dict:
    """
    디렉토리(모듈) 단위로 컨텍스트 MD 생성 + 코드 리뷰를 한 번의 LLM 호출로 수행한다.
    반환: {"context_msg": ..., "review": ...} or partial
    """
    file_paths.sort(key=lambda f: (
        0 if Path(f).suffix in HEADER_EXTENSIONS else 1,
        Path(f).name,
    ))

    combined_content = ""
    included_paths = []
    for fp in file_paths:
        try:
            text = (repo_dir / fp).read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue
        included_paths.append(fp)
        combined_content += f"// ── {fp} ──\n{text}\n\n"

    if not included_paths:
        return {}

    # 기존 코멘트 보존
    out_path = context_dir / (dir_key + ".md")
    existing_comments = ""
    if out_path.exists():
        try:
            existing_comments = _extract_comments_section(
                out_path.read_text(encoding='utf-8')
            )
        except Exception:
            pass

    # 관련 컨텍스트 검색 (기존 벡터 인덱스 활용)
    existing_context = ""
    if out_path.exists():
        try:
            existing_context = out_path.read_text(encoding='utf-8')
        except Exception:
            pass

    related = _build_related_context(base_dir, included_paths[0], existing_context)
    related_notice = ""
    if related:
        related_notice = f"\n\n[관련 파일 컨텍스트 — 의존 관계와 연동 로직 참고]\n{related}\n"

    dev_comments = _extract_comments_section(existing_context) if existing_context else ""
    comments_notice = ""
    if dev_comments:
        comments_notice = (
            f"\n\n[개발자 코멘트 — 아래 항목은 이미 인지된 사항이므로 동일 지적을 생략하거나 "
            f"'인지됨'으로 표기해줘]\n{dev_comments}\n"
        )

    # ── 프롬프트 구성: 컨텍스트 MD + 코드 리뷰를 한번에 ──
    file_path_str = " + ".join(included_paths)
    code_block = combined_content[:GROUP_CONTENT_LIMIT]

    if auto_review:
        prompt = (
            f"아래 코드 파일들을 분석하여 두 가지 작업을 수행해줘.\n"
            f"반드시 === 구분자로 두 섹션을 나눠줘.\n\n"
            f"=== CONTEXT_MD ===\n"
            f"RAG 컨텍스트 MD를 생성해줘. 반드시 아래 형식을 지켜:\n"
            f"---\n"
            f"tags: [태그1, 태그2, ...]\n"
            f"category: 대분류/중분류/소분류\n"
            f"related_classes:\n"
            f"  - ClassName: file_path\n"
            f"---\n\n"
            f"## 요약\n(기능과 역할 요약)\n\n"
            f"## 개선 필요 사항\n(알려진 이슈, 기술 부채. 없으면 \"없음\")\n\n"
            f"주의: \"## 코멘트\" 섹션은 절대 생성하지 마.\n\n"
            f"=== CODE_REVIEW ===\n"
            f"같은 모듈의 파일들이므로 클래스 간 의존 관계, 인터페이스 일관성도 함께 확인해줘.\n\n"
            f"## 1. 코딩 컨벤션 검토\n"
            f"검토 기준: 클래스명 파스칼케이스, 함수명 파스칼케이스, "
            f"멤버변수 접두사(b/f/i), public 함수 주석 필수.\n"
            f"결과: | 파일 | 항목 | 위반 내용 | 라인 | 심각도 |\n\n"
            f"## 2. 버그·안전성 검토\n"
            f"Null 포인터, 메모리 누수, 멀티스레드 안전성, 배열 범위 초과, 미초기화 변수를 검토.\n"
            f"결과: | 파일 | 위험도 | 라인 | 설명 | 권장 수정 |"
            f"{comments_notice}"
            f"{related_notice}\n\n"
            f"파일 경로: {file_path_str}\n"
            f"```\n{code_block}\n```"
        )
    else:
        prompt = PROMPT_TEMPLATES["01_소스분석"].format(
            file_path=file_path_str,
            content=code_block,
        )

    dir_name = Path(dir_key).name
    file_list = ", ".join(Path(f).name for f in included_paths)
    print(f"  [분석] {dir_name}/ ({len(included_paths)}개: {file_list})")

    raw = _call_llm(prompt, use_gemini=use_gemini)
    if raw is None:
        return {}

    result = {}

    # ── 응답 파싱: CONTEXT_MD / CODE_REVIEW 분리 ──
    if auto_review and "=== CODE_REVIEW ===" in raw:
        parts = raw.split("=== CODE_REVIEW ===", 1)
        context_md = parts[0].replace("=== CONTEXT_MD ===", "").strip()
        review_text = parts[1].strip()
        result["review"] = {"file": dir_key, "review": review_text}
    elif auto_review and "=== CONTEXT_MD ===" in raw:
        # CODE_REVIEW 마커 없이 CONTEXT_MD만 있는 경우
        context_md = raw.replace("=== CONTEXT_MD ===", "").strip()
    else:
        context_md = raw.strip()

    # ── 컨텍스트 MD 저장 ──
    if existing_comments:
        context_md = re.sub(r'## 코멘트\s*\n.*', '', context_md, flags=re.DOTALL).rstrip()
        context_md = context_md + "\n\n" + existing_comments + "\n"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(context_md, encoding='utf-8')
    result["context_msg"] = f"  [분석] {out_path.relative_to(context_dir.parent.parent)} ({len(included_paths)}개 파일)"

    return result


def process_commit(
    repo_dir: Path, context_dir: Path, reviews_dir: Path,
    changed_files: list[str], commit_hash: str,
    auto_review: bool = True, use_gemini: bool = False,
):
    """
    컨텍스트 생성 + 코드 리뷰를 디렉토리(모듈) 단위로 한 번에 처리한다.
    LLM 1회 호출로 두 작업을 동시 수행하여 시간을 절반으로 줄인다.
    """
    base_dir = context_dir.parent.parent
    groups = _group_by_directory(changed_files, repo_dir)
    if not groups:
        log("소스 파일 없음 — 건너뜀")
        return

    total_files = sum(len(fps) for fps in groups.values())
    mode = "컨텍스트+리뷰" if auto_review else "컨텍스트"
    log(f"{mode} 시작 — {total_files}개 파일, {len(groups)}개 모듈 (병렬 {MAX_WORKERS})")

    all_reviews: list[dict] = []
    success, fail = 0, 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(
                _process_directory_group,
                repo_dir, context_dir, base_dir, dk, fps, auto_review, use_gemini,
            ): dk
            for dk, fps in groups.items()
        }
        for future in as_completed(futures):
            dk = futures[future]
            try:
                result = future.result()
                if result.get("context_msg"):
                    print(result["context_msg"])
                    success += 1
                else:
                    fail += 1
                if result.get("review"):
                    all_reviews.append(result["review"])
            except Exception as e:
                log(f"[경고] 처리 실패 ({dk}): {e}")
                fail += 1

    if fail:
        log(f"처리: {success}개 성공, {fail}개 실패")

    # 벡터 인덱스 갱신
    log("벡터 인덱스 갱신 중...")
    update_vector_index(context_dir, base_dir, changed_files)
    log("벡터 인덱스 갱신 완료")

    # 리뷰 리포트 저장
    if all_reviews:
        report = _build_review_report(all_reviews, commit_hash)
        timestamp = datetime.now().strftime('%Y-%m-%d_%H%M')
        report_path = reviews_dir / f"{timestamp}_{commit_hash[:8]}.md"
        report_path.write_text(report, encoding='utf-8')
        log(f"코드 리뷰 완료 → {report_path.relative_to(reviews_dir.parent.parent)}")


def _build_review_report(file_reviews: list[dict], commit_hash: str) -> str:
    """모듈별 리뷰 결과를 직접 취합하여 통합 리포트를 생성한다. (LLM 호출 없음)"""
    file_reviews.sort(key=lambda r: r["file"])

    modules_section = "\n\n---\n\n".join(
        f"### {Path(r['file']).name}/\n{r['review']}"
        for r in file_reviews
    )

    return (
        f"# 코드 리뷰 리포트\n\n"
        f"커밋: `{commit_hash}`  \n"
        f"생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  \n"
        f"검토 모듈: {len(file_reviews)}개\n\n"
        f"---\n\n"
        f"{modules_section}"
    )


# 기본 모델 (config.json의 claude_model로 오버라이드 가능)
_claude_model: str = "claude-sonnet-4-6"


def set_claude_model(model: str):
    global _claude_model
    _claude_model = model


def _call_claude(prompt: str) -> str | None:
    """Claude CLI를 호출하고 결과 텍스트를 반환한다. stdin으로 프롬프트 전달 (Windows 명령줄 길이 제한 회피)."""
    cmd = ["claude", "-p", "--dangerously-skip-permissions"]
    if _claude_model:
        cmd.extend(["--model", _claude_model])
    result = subprocess.run(
        cmd, input=prompt,
        capture_output=True, text=True, encoding='utf-8',
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
    # 프로세스 정리 설정: Job Object (1차) + atexit/signal (폴백)
    _job = _setup_job_object()
    atexit.register(_kill_orphan_mcps)
    if sys.platform == 'win32':
        signal.signal(signal.SIGBREAK, _on_exit_signal)

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

    # Claude 모델 설정 (기본값: sonnet — 속도와 품질의 균형)
    claude_model = config.get("claude_model", "claude-sonnet-4-6")
    set_claude_model(claude_model)

    # ── 서버 모드 설정 ──
    global _server_mode, _server_url, _server_proc
    _server_mode = config.get("server_mode", False)
    server_port = config.get("server_port", 8100)
    server_host = config.get("server_host", "0.0.0.0")

    if _server_mode:
        _server_url = f"http://localhost:{server_port}"
        # context_search HTTP 서버를 백그라운드 프로세스로 시작
        cmd = _get_context_search_cmd(base_dir)
        if cmd:
            serve_cmd = cmd + ["--serve", str(base_dir), str(server_port), server_host]
            _server_proc = subprocess.Popen(
                serve_cmd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                encoding='utf-8', errors='replace',
            )
            # 서버 시작 대기 (최대 15초)
            import urllib.request
            import urllib.error
            for _ in range(30):
                try:
                    with urllib.request.urlopen(f"{_server_url}/api/v1/health", timeout=2):
                        break
                except Exception:
                    time.sleep(0.5)
            else:
                log("[경고] HTTP 서버 시작 대기 타임아웃")
            log(f"컨텍스트 HTTP 서버: {server_host}:{server_port}")
        else:
            log("[경고] context_search를 찾을 수 없어 서버 모드 비활성화")
            _server_mode = False
            _server_url = ""

    context_dir, agents_dir, reviews_dir = init_project_dirs(base_dir)

    llm_label = "Gemini" if use_gemini else f"Claude ({claude_model.split('-')[1]})"
    if _server_mode:
        vector_label = f"서버 모드 (:{server_port})"
    else:
        vector_ok = bool(_get_context_search_cmd(base_dir))
        vector_label = "ON (로컬)" if vector_ok else "OFF (context_search 없음)"
    log(
        f"브랜치: {branch} | 폴링 간격: {poll_interval}초 | "
        f"자동 리뷰: {'ON' if auto_review else 'OFF'} | "
        f"에셋 검증: {'ON' if auto_asset_validation else 'OFF'} | "
        f"분석 엔진: {llm_label} | "
        f"벡터 RAG: {vector_label}"
    )
    log(f"컨텍스트: {context_dir}")
    log(f"리뷰 저장: {reviews_dir}")
    print()

    last_hash = load_state(base_dir) or get_local_hash(repo_dir)
    save_state(base_dir, last_hash)

    # 최초 실행 또는 인덱스 없을 때 전체 벡터 인덱싱
    if _server_mode:
        # 서버 모드: HTTP API로 상태 확인 후 필요시 rebuild
        status = _http_get("/api/v1/index/status")
        if status and status.get("indexed_documents", 0) == 0:
            log("벡터 인덱스 초기 구축 중...")
            update_vector_index(context_dir, base_dir)
    else:
        vector_db_path = base_dir / ".claude" / "vector_db"
        if not vector_db_path.exists():
            log("벡터 인덱스 초기 구축 중...")
            update_vector_index(context_dir, base_dir)

    log("감시 시작... (종료: Ctrl+C)")

    while True:
        try:
            if not git_fetch(repo_dir):
                log("[경고] git fetch 실패, 재시도 대기 중...")
            else:
                remote_hash = get_remote_hash(repo_dir, branch)

                if remote_hash and remote_hash != last_hash:
                    # 중복 실행 방지: 이전 작업이 아직 진행 중이면 건너뜀
                    if not _processing_lock.acquire(blocking=False):
                        log(f"[대기] 이전 작업 진행 중 — 다음 폴링에서 처리 ({remote_hash[:8]})")
                    else:
                        try:
                            log(f"새 커밋 감지: {last_hash[:8]} → {remote_hash[:8]}")

                            if git_pull(repo_dir, branch):
                                # pull 후 실제 최신 해시 재확인 (작업 중 추가 커밋 대응)
                                actual_hash = get_local_hash(repo_dir)
                                if actual_hash != remote_hash:
                                    log(f"추가 커밋 포함: {remote_hash[:8]} → {actual_hash[:8]}")

                                changed_files = get_changed_files(repo_dir, last_hash, actual_hash)
                                log(f"변경된 파일 {len(changed_files)}개")

                                process_commit(
                                    repo_dir, context_dir, reviews_dir,
                                    changed_files, actual_hash,
                                    auto_review=auto_review, use_gemini=use_gemini,
                                )

                                if auto_asset_validation:
                                    run_asset_validation(base_dir, reviews_dir, changed_files, actual_hash, use_gemini=use_gemini)

                                last_hash = actual_hash
                                save_state(base_dir, last_hash)
                        finally:
                            _processing_lock.release()
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
