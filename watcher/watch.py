"""
메인 워처 스크립트. Git 감시 루프와 프로세스 관리만 담당한다.
실제 로직은 common, setup, context, domain, review 모듈로 분리됨.
"""
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
import threading

import common
from setup import init_project_dirs
from context import (
    initial_context_build, update_vector_index,
    process_commit, _get_context_search_cmd,
)
from domain import (
    promote_domains, generate_health_report,
    DOMAIN_CHECK_INTERVAL, HEALTH_CHECK_INTERVAL,
)
from review import run_asset_validation


_processing_lock = threading.Lock()  # 중복 실행 방지


# ─────────────────────────────────────────
# 프로세스 정리 (Windows)
# ─────────────────────────────────────────

def _setup_job_object():
    """
    Windows Job Object를 생성하여 현재 프로세스에 할당한다.
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE 플래그로
    watch.exe 종료 시 모든 자식/손자 프로세스(claude, MCP 서버)가 자동 정리된다.
    """
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


# MCP 프로세스명 목록 (Job Object 실패 시 폴백용) — agent_templates에서 자동 추출
from agent_templates import MCP_SERVERS
_MCP_PROCESS_NAMES = [Path(p).name for p in MCP_SERVERS.values()]


def _kill_orphan_mcps():
    """알려진 MCP 프로세스를 강제 종료한다 (Job Object 실패 시 폴백)."""
    # 서버 프로세스 정리
    if common._server_proc is not None:
        try:
            common._server_proc.terminate()
            common._server_proc.wait(timeout=5)
        except Exception:
            try:
                common._server_proc.kill()
            except Exception:
                pass
        common._server_proc = None
    for name in _MCP_PROCESS_NAMES:
        os.system(f'taskkill /F /IM {name} >nul 2>&1')


def _on_exit_signal(signum, frame):
    """SIGBREAK(콘솔 창 닫기) 시 정리 후 종료."""
    _kill_orphan_mcps()
    sys.exit(0)


# ─────────────────────────────────────────
# 기본 경로
# ─────────────────────────────────────────

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


# ─────────────────────────────────────────
# 방화벽
# ─────────────────────────────────────────
# 포트 점유 정리
# ─────────────────────────────────────────

def _ensure_port_free(port: int):
    """포트가 점유 중이면 해당 프로세스를 정리한다."""
    result = subprocess.run(
        ["netstat", "-ano"],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
    )
    for line in result.stdout.splitlines():
        if f":{port}" in line and "LISTENING" in line:
            parts = line.split()
            pid = parts[-1]
            if pid.isdigit() and int(pid) != os.getpid():
                common.log(f"포트 {port} 점유 프로세스 정리 (PID {pid})")
                os.system(f'taskkill /F /PID {pid} >nul 2>&1')


# ─────────────────────────────────────────
# 방화벽
# ─────────────────────────────────────────

def _firewall_rule_name(port: int) -> str:
    return f"AgentWatch Context Server (TCP {port})"


def _check_firewall_exists(port: int) -> bool:
    """Windows 방화벽에 해당 포트의 인바운드 규칙이 있는지 확인한다."""
    rule_name = _firewall_rule_name(port)
    check = subprocess.run(
        ["netsh", "advfirewall", "firewall", "show", "rule", f"name={rule_name}"],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
    )
    return check.returncode == 0 and rule_name in check.stdout


def _setup_firewall(port: int):
    """Windows 방화벽 인바운드 규칙을 추가한다."""
    rule_name = _firewall_rule_name(port)
    print(f"  방화벽 규칙 추가 중... (관리자 권한 필요)")
    result = subprocess.run(
        ["netsh", "advfirewall", "firewall", "add", "rule",
         f"name={rule_name}", "dir=in", "action=allow",
         "protocol=TCP", f"localport={port}"],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
    )
    if result.returncode == 0:
        print(f"  방화벽 인바운드 규칙 추가 완료 (포트 {port})")
    else:
        print(f"  [경고] 방화벽 설정 실패 — 관리자 권한으로 watch.exe를 실행해주세요.")
        print(f"  수동 설정: netsh advfirewall firewall add rule name=\"{rule_name}\" dir=in action=allow protocol=TCP localport={port}")


# ─────────────────────────────────────────
# 설정
# ─────────────────────────────────────────

CONFIG_VERSION = 2  # config 스키마 버전

_CONFIG_DEFAULTS = {
    "auto_review": True,
    "auto_asset_validation": True,
    "use_gemini": False,
    "claude_model": "claude-sonnet-4-6",
    "server_mode": False,
    "server_host": "0.0.0.0",
    "server_port": 8100,
    "context_server_url": "",
    "enable_log": True,
}


def load_or_init_config(base_dir: Path, repo_dir: Path) -> dict:
    config_path = base_dir / common.CONFIG_FILE

    if config_path.exists():
        with open(config_path, encoding='utf-8') as f:
            config = json.load(f)
        if config.get("branch") and config.get("poll_interval"):
            # 타입/범위 검증
            try:
                config["poll_interval"] = max(10, int(config["poll_interval"]))
            except (ValueError, TypeError):
                config["poll_interval"] = 60
            if config.get("server_port"):
                try:
                    config["server_port"] = int(config["server_port"])
                except (ValueError, TypeError):
                    config["server_port"] = 8100
            # 스키마 마이그레이션: 누락 필드 보충
            updated = False
            for key, default in _CONFIG_DEFAULTS.items():
                if key not in config:
                    config[key] = default
                    updated = True
            if config.get("_version", 0) < CONFIG_VERSION:
                config["_version"] = CONFIG_VERSION
                updated = True
            if updated:
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(config, f, ensure_ascii=False, indent=2)
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
        # 방화벽 인바운드 규칙 설정
        fw_raw = input(f"방화벽 인바운드 포트 {server_port} 설정을 확인해보시겠습니까? (y/n): ").strip().lower()
        if fw_raw == 'y':
            _setup_firewall(server_port)
    else:
        url_raw = input("원격 RAG 서버 URL (없으면 Enter, 예: http://192.168.1.100:8100): ").strip()
        if url_raw and not url_raw.startswith("http://") and not url_raw.startswith("https://"):
            url_raw = "http://" + url_raw
            print(f"  → 프로토콜 자동 추가: {url_raw}")
        context_server_url = url_raw

    enable_log_raw = input("파일 로그 활성화 (기본값: y) [y/n]: ").strip().lower()
    enable_log = enable_log_raw != 'n'

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
        "enable_log": enable_log,
        "repo_dir": str(repo_dir),
    }

    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    print(f"\n설정 저장 완료: {config_path}\n")
    return config


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
        cwd=repo_dir, capture_output=True, text=True,
        encoding='utf-8', errors='replace',
    )
    if result.returncode == 0:
        return True

    stderr = result.stderr or ""
    # 상태별 안내 메시지
    if "CONFLICT" in stderr or "CONFLICT" in (result.stdout or ""):
        common.log("[오류] git pull 실패: merge conflict 발생")
        common.log("  → 수동으로 충돌을 해결한 뒤 watch.exe를 재시작하세요.")
        common.log("  → git status 로 충돌 파일 확인 가능")
    elif "local changes" in stderr.lower() or "uncommitted" in stderr.lower():
        common.log("[오류] git pull 실패: 로컬 변경사항이 있습니다")
        common.log("  → git stash 또는 git commit 후 재시작하세요.")
    else:
        common.log(f"[오류] git pull 실패:\n{stderr[:300]}")
    return False


def get_changed_files(repo_dir: Path, old_hash: str, new_hash: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", old_hash, new_hash],
        cwd=repo_dir, capture_output=True, text=True
    )
    return [f for f in result.stdout.strip().split('\n') if f]


# ─────────────────────────────────────────
# 상태 저장
# ─────────────────────────────────────────

def load_state(base_dir: Path) -> str | None:
    state_path = base_dir / common.STATE_FILE
    return state_path.read_text(encoding='utf-8').strip() if state_path.exists() else None


def save_state(base_dir: Path, commit_hash: str):
    (base_dir / common.STATE_FILE).write_text(commit_hash, encoding='utf-8')


# ─────────────────────────────────────────
# 메인
# ─────────────────────────────────────────

def main():
    # 프로세스 정리 설정: Job Object (1차) + atexit/signal (폴백)
    _job = _setup_job_object()
    atexit.register(_kill_orphan_mcps)
    signal.signal(signal.SIGBREAK, _on_exit_signal)

    base_dir = get_base_dir()

    print("=" * 50)
    print("  Git 컨텍스트 워처")
    print("=" * 50)

    try:
        repo_dir = find_git_repo(base_dir)
        common.log(f"Git 저장소 감지: {repo_dir}")
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

    # Claude 모델 설정 (기본값: sonnet -- 속도와 품질의 균형)
    claude_model = config.get("claude_model", "claude-sonnet-4-6")
    common.set_claude_model(claude_model)

    # ── 파일 로그 설정 ──
    common.init_log(base_dir, config.get("enable_log", True))

    # ── 서버 모드 설정 ──
    common._server_mode = config.get("server_mode", False)
    server_port = config.get("server_port", 8100)
    server_host = config.get("server_host", "0.0.0.0")

    if common._server_mode:
        # 방화벽 규칙 확인
        if not _check_firewall_exists(server_port):
            fw_raw = input(f"방화벽 인바운드 포트 {server_port} 설정을 확인해보시겠습니까? (y/n): ").strip().lower()
            if fw_raw == 'y':
                _setup_firewall(server_port)
        # 이전 실행 잔여 프로세스 포트 정리
        _ensure_port_free(server_port)
        common._server_url = f"http://localhost:{server_port}"
        # context_search HTTP 서버를 백그라운드 프로세스로 시작
        cmd = _get_context_search_cmd(base_dir)
        if cmd:
            serve_cmd = cmd + ["--serve", str(base_dir), str(server_port), server_host]
            common._server_proc = subprocess.Popen(
                serve_cmd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                encoding='utf-8', errors='replace',
            )
            # 서버 시작 대기 (최대 15초)
            import urllib.request
            import urllib.error
            for _ in range(30):
                try:
                    with urllib.request.urlopen(f"{common._server_url}/api/v1/health", timeout=2):
                        break
                except Exception:
                    time.sleep(0.5)
            else:
                common.log("[경고] HTTP 서버 시작 대기 타임아웃")
            common.log(f"컨텍스트 HTTP 서버: {server_host}:{server_port}")
        else:
            common.log("[경고] context_search를 찾을 수 없어 서버 모드 비활성화")
            common._server_mode = False
            common._server_url = ""

    context_dir, agents_dir, reviews_dir = init_project_dirs(base_dir)

    llm_label = "Gemini" if use_gemini else f"Claude ({claude_model.split('-')[1]})"
    if common._server_mode:
        vector_label = f"서버 모드 (:{server_port})"
    else:
        vector_ok = bool(_get_context_search_cmd(base_dir))
        vector_label = "ON (로컬)" if vector_ok else "OFF (context_search 없음)"
    common.log(
        f"브랜치: {branch} | 폴링 간격: {poll_interval}초 | "
        f"자동 리뷰: {'ON' if auto_review else 'OFF'} | "
        f"에셋 검증: {'ON' if auto_asset_validation else 'OFF'} | "
        f"분석 엔진: {llm_label} | "
        f"벡터 RAG: {vector_label}"
    )
    common.log(f"컨텍스트: {context_dir}")
    common.log(f"리뷰 저장: {reviews_dir}")
    print()

    # 초기 컨텍스트 생성 (최초 실행 시 전체 소스 분석, 중단 재실행 시 누락분 보충)
    initial_context_build(repo_dir, context_dir, base_dir, use_gemini=use_gemini)

    last_hash = load_state(base_dir) or get_local_hash(repo_dir)
    save_state(base_dir, last_hash)

    # 컨텍스트 MD는 있지만 벡터 DB가 없는 경우 대비 (DB 삭제 등)
    if common._server_mode:
        status = common._http_get("/api/v1/index/status")
        if status and status.get("indexed_documents", 0) == 0:
            common.log("벡터 인덱스 초기 구축 중...")
            update_vector_index(context_dir, base_dir)
    else:
        vector_db_path = base_dir / ".claude" / "vector_db"
        if not vector_db_path.exists():
            common.log("벡터 인덱스 초기 구축 중...")
            update_vector_index(context_dir, base_dir)

    common.log("감시 시작... (종료: Ctrl+C)")

    poll_count = 0
    while True:
        poll_count += 1
        try:
            # 주기적 도메인 승급 체크
            if poll_count % DOMAIN_CHECK_INTERVAL == 0:
                try:
                    promote_domains(base_dir, context_dir, use_gemini=use_gemini)
                except Exception as e:
                    common.log(f"[경고] 도메인 체크 오류: {e}")

            # 주기적 건강도 리포트 생성
            if poll_count % HEALTH_CHECK_INTERVAL == 0:
                try:
                    generate_health_report(base_dir, context_dir, reviews_dir)
                except Exception as e:
                    common.log(f"[경고] 건강도 리포트 오류: {e}")

            if not git_fetch(repo_dir):
                common.log("[경고] git fetch 실패, 재시도 대기 중...")
            else:
                remote_hash = get_remote_hash(repo_dir, branch)

                if remote_hash and remote_hash != last_hash:
                    # 중복 실행 방지: 이전 작업이 아직 진행 중이면 건너뜀
                    if not _processing_lock.acquire(blocking=False):
                        common.log(f"[대기] 이전 작업 진행 중 — 다음 폴링에서 처리 ({remote_hash[:8]})")
                    else:
                        try:
                            common.log(f"새 커밋 감지: {last_hash[:8]} → {remote_hash[:8]}")

                            if not git_pull(repo_dir, branch):
                                common.log(f"  다음 폴링에서 재시도합니다. ({poll_interval}초 후)")
                            else:
                                # pull 후 실제 최신 해시 재확인 (작업 중 추가 커밋 대응)
                                actual_hash = get_local_hash(repo_dir)
                                if actual_hash != remote_hash:
                                    common.log(f"추가 커밋 포함: {remote_hash[:8]} → {actual_hash[:8]}")

                                changed_files = get_changed_files(repo_dir, last_hash, actual_hash)
                                common.log(f"변경된 파일 {len(changed_files)}개")

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
            common.log("종료")
            sys.exit(0)
        except Exception as e:
            common.log(f"[오류] {e}")

        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
