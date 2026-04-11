"""
공용 함수/상수 모듈.
순환 import 방지를 위해 모든 모듈이 이 파일에서 공용 유틸을 import한다.
"""
import sys
import json
import shutil
import subprocess
from pathlib import Path
from datetime import datetime

# ─────────────────────────────────────────
# 상수
# ─────────────────────────────────────────

VERSION = "1.1.4"
CONFIG_FILE = "config.json"
STATE_FILE = ".watch_state"
TARGET_EXTENSIONS = {'.cpp', '.h', '.hpp', '.inl', '.cs', '.py'}
ASSET_EXTENSIONS = {'.uasset', '.umap'}
MAX_WORKERS = 6  # 병렬 LLM 호출 수
DOMAIN_DIR_NAME = "_domains"

# 타임아웃 상수
HTTP_TIMEOUT_SHORT = 30       # 상태 확인, 검색
HTTP_TIMEOUT_INDEXING = 120   # 인덱싱, 리빌드
SUBPROCESS_TIMEOUT = 120      # CLI 호출

# ─────────────────────────────────────────
# 모듈 레벨 변수 (main()에서 변경, 다른 모듈에서 common.xxx로 접근)
# ─────────────────────────────────────────

_server_mode = False
_server_url = ""   # 예: "http://localhost:8100"
_server_proc = None  # context_search --serve 프로세스

# 기본 모델 (config.json의 claude_model로 오버라이드 가능)
_claude_model: str = "claude-sonnet-4-6"

# 로그 설정
_log_enabled: bool = True
_log_dir: Path | None = None
_log_file = None  # 현재 열려 있는 로그 파일 핸들
_log_date: str = ""  # 현재 로그 파일의 날짜 (로테이션 체크용)
LOG_RETENTION_DAYS = 7  # 로그 보관 일수


# ─────────────────────────────────────────
# 로깅
# ─────────────────────────────────────────

def init_log(base_dir: Path, enabled: bool = True):
    """로그 시스템을 초기화한다. main()에서 호출."""
    global _log_enabled, _log_dir
    _log_enabled = enabled
    if not _log_enabled:
        return
    _log_dir = base_dir / ".claude" / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_old_logs()


def _get_log_file():
    """날짜별 로그 파일 핸들을 반환한다. 날짜가 바뀌면 새 파일로 로테이션."""
    global _log_file, _log_date
    if not _log_enabled or not _log_dir:
        return None
    today = datetime.now().strftime('%Y-%m-%d')
    if today != _log_date:
        if _log_file:
            _log_file.close()
        _log_date = today
        log_path = _log_dir / f"watch_{today}.log"
        _log_file = open(log_path, 'a', encoding='utf-8')
    return _log_file


def _cleanup_old_logs():
    """보관 기간이 지난 로그 파일을 삭제한다."""
    if not _log_dir or not _log_dir.exists():
        return
    from datetime import timedelta
    import re as _re
    cutoff = datetime.now() - timedelta(days=LOG_RETENTION_DAYS)
    for log_file in _log_dir.glob("*.log"):
        try:
            # 파일명에서 YYYY-MM-DD 패턴 추출
            m = _re.search(r'(\d{4}-\d{2}-\d{2})', log_file.stem)
            if m:
                file_date = datetime.strptime(m.group(1), '%Y-%m-%d')
                if file_date < cutoff:
                    log_file.unlink()
        except (ValueError, OSError):
            pass


def log(msg: str):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    f = _get_log_file()
    if f:
        f.write(line + "\n")
        f.flush()


# ─────────────────────────────────────────
# HTTP 유틸 (서버 모드)
# ─────────────────────────────────────────

def _http_post(endpoint: str, payload: dict, timeout: int = HTTP_TIMEOUT_INDEXING) -> dict | None:
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


def _http_get(endpoint: str, timeout: int = HTTP_TIMEOUT_SHORT) -> dict | None:
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
# LLM 호출
# ─────────────────────────────────────────

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
        log(f"[오류] Claude 호출 실패: {result.stderr[:200]}")
        return None
    return result.stdout


def _call_gemini(prompt: str) -> str | None:
    """Gemini CLI를 호출하고 결과 텍스트를 반환한다. 실패 시 None. (폴백은 _call_llm에서 처리)"""
    gemini_path = shutil.which("gemini")
    if not gemini_path:
        log("[경고] Gemini CLI를 찾을 수 없음")
        return None
    try:
        result = subprocess.run(
            [gemini_path, "-y"],
            input=prompt,
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            shell=True,
        )
    except OSError as e:
        log(f"[오류] Gemini 실행 실패: {e}")
        return None
    if result.returncode != 0:
        log(f"[오류] Gemini 호출 실패: {result.stderr[:200]}")
        return None
    return result.stdout


_fallback_count = 0  # 폴백 발생 횟수 (비용 추적용)

def _call_llm(prompt: str, use_gemini: bool = False) -> str | None:
    """use_gemini 플래그에 따라 Gemini 또는 Claude를 호출한다. 실패 시 대기 후 다른 LLM으로 폴백."""
    global _fallback_count
    if use_gemini:
        result = _call_gemini(prompt)
        if result is not None:
            return result
        _fallback_count += 1
        log(f"[폴백] Gemini 실패 → 10초 대기 후 Claude로 재시도 (누적 폴백: {_fallback_count}회)")
        import time; time.sleep(10)
        return _call_claude(prompt)
    else:
        result = _call_claude(prompt)
        if result is not None:
            return result
        _fallback_count += 1
        log(f"[폴백] Claude 실패 → 10초 대기 후 Gemini로 재시도 (누적 폴백: {_fallback_count}회)")
        import time; time.sleep(10)
        return _call_gemini(prompt)
