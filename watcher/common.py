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

CONFIG_FILE = "config.json"
STATE_FILE = ".watch_state"
TARGET_EXTENSIONS = {'.cpp', '.h', '.hpp', '.inl', '.cs', '.py'}
ASSET_EXTENSIONS = {'.uasset', '.umap'}
MAX_WORKERS = 6  # 병렬 LLM 호출 수
DOMAIN_DIR_NAME = "_domains"

# ─────────────────────────────────────────
# 모듈 레벨 변수 (main()에서 변경, 다른 모듈에서 common.xxx로 접근)
# ─────────────────────────────────────────

_server_mode = False
_server_url = ""   # 예: "http://localhost:8100"
_server_proc = None  # context_search --serve 프로세스

# 기본 모델 (config.json의 claude_model로 오버라이드 가능)
_claude_model: str = "claude-sonnet-4-6"


# ─────────────────────────────────────────
# 로깅
# ─────────────────────────────────────────

def log(msg: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


# ─────────────────────────────────────────
# HTTP 유틸 (서버 모드)
# ─────────────────────────────────────────

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
        print(f"[오류] Claude 호출 실패: {result.stderr[:200]}")
        return None
    return result.stdout


def _call_gemini(prompt: str) -> str | None:
    """Gemini CLI를 호출하고 결과 텍스트를 반환한다. 실패 시 None. (폴백은 _call_llm에서 처리)"""
    if not shutil.which("gemini"):
        log("[경고] Gemini CLI를 찾을 수 없음")
        return None
    result = subprocess.run(
        ["gemini", "-y", prompt],
        capture_output=True, text=True, encoding='utf-8', errors='replace'
    )
    if result.returncode != 0:
        print(f"[오류] Gemini 호출 실패: {result.stderr[:200]}")
        return None
    return result.stdout


def _call_llm(prompt: str, use_gemini: bool = False) -> str | None:
    """use_gemini 플래그에 따라 Gemini 또는 Claude를 호출한다. 실패 시 다른 LLM으로 폴백."""
    if use_gemini:
        result = _call_gemini(prompt)
        if result is not None:
            return result
        log("[폴백] Gemini 실패 → Claude로 재시도")
        return _call_claude(prompt)
    else:
        result = _call_claude(prompt)
        if result is not None:
            return result
        log("[폴백] Claude 실패 → Gemini로 재시도")
        return _call_gemini(prompt)
