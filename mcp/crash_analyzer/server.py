"""
crash_analyzer MCP 서버
Unreal Engine 5 크래시 데이터를 분석한다.
- CrashContext.runtime-xml  : 크래시 메타데이터 + 스택 트레이스
- .dmp (미니덤프)           : cdb.exe(WinDbg) 또는 minidump 라이브러리로 분석
- 크래시 시점 로그          : 크래시 직전 로그 컨텍스트 추출
"""
import os
import re
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("crash-analyzer")


def _find_cdb() -> str | None:
    """
    cdb.exe(WinDbg) 경로를 탐색한다.
    1. PATH에 등록된 cdb.exe
    2. 환경변수 기반 Windows Kits 경로
    3. 일반적인 Windows SDK 설치 위치
    """
    # 1. PATH에서 탐색
    import shutil
    if shutil.which("cdb"):
        return shutil.which("cdb")

    # 2. 환경변수 기반 탐색
    program_files_candidates = [
        os.environ.get("ProgramFiles(x86)", ""),
        os.environ.get("ProgramFiles", ""),
        os.environ.get("ProgramW6432", ""),
    ]
    for pf in program_files_candidates:
        if not pf:
            continue
        cdb = Path(pf) / "Windows Kits" / "10" / "Debuggers" / "x64" / "cdb.exe"
        if cdb.exists():
            return str(cdb)

    return None


def _parse_crash_context(xml_path: Path) -> dict:
    """CrashContext.runtime-xml 파싱"""
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except Exception as e:
        return {"error": f"XML 파싱 실패: {e}"}

    result = {}

    # RuntimeProperties
    runtime = root.find("RuntimeProperties")
    if runtime is not None:
        for child in runtime:
            result[child.tag] = child.text

    # PlatformProperties
    platform = root.find("PlatformProperties")
    if platform is not None:
        result["Platform"] = {}
        for child in platform:
            result["Platform"][child.tag] = child.text

    return result


def _extract_callstack_from_log(log_content: str) -> list[str]:
    """로그에서 크래시 직전 콜스택 추출"""
    lines = log_content.splitlines()
    callstack = []
    in_callstack = False

    for line in lines:
        if any(kw in line for kw in ("Assertion failed", "Fatal error", "=== Critical error ===")):
            in_callstack = True
        if in_callstack:
            callstack.append(line)
        if in_callstack and len(callstack) > 80:
            break

    return callstack


def _analyze_dmp_with_cdb(dmp_path: Path, cdb_path: str) -> dict:
    """cdb.exe로 미니덤프를 분석한다."""
    try:
        result = subprocess.run(
            [cdb_path, "-z", str(dmp_path), "-c", "k 30; q"],
            capture_output=True, text=True, timeout=30
        )
        output = result.stdout + result.stderr
        return {"source": "cdb.exe", "output": output}
    except subprocess.TimeoutExpired:
        return {"error": "cdb.exe 분석 타임아웃 (30초)"}
    except Exception as e:
        return {"error": f"cdb.exe 실행 실패: {e}"}


@mcp.tool()
def analyze_crash(crash_path: str) -> str:
    """
    UE5 크래시 폴더 또는 개별 파일을 분석한다.
    크래시 폴더에는 CrashContext.runtime-xml, .dmp, .log 중 하나 이상이 있어야 한다.

    Args:
        crash_path: 크래시 폴더 경로 또는 .dmp / CrashContext.runtime-xml 파일 경로
    """
    path = Path(crash_path)
    result: dict = {"input": str(path)}

    # 폴더인 경우 내부 파일 탐색
    if path.is_dir():
        crash_dir = path
    elif path.is_file():
        crash_dir = path.parent
    else:
        return json.dumps({"error": f"경로를 찾을 수 없습니다: {crash_path}"}, ensure_ascii=False)

    # CrashContext.runtime-xml 분석
    xml_files = list(crash_dir.glob("CrashContext.runtime-xml"))
    if xml_files:
        result["crash_context"] = _parse_crash_context(xml_files[0])

    # 크래시 시점 로그 분석
    log_files = list(crash_dir.glob("*.log"))
    if log_files:
        log_content = log_files[0].read_text(encoding="utf-8", errors="replace")
        callstack = _extract_callstack_from_log(log_content)
        result["log_file"] = log_files[0].name
        result["crash_callstack_from_log"] = callstack if callstack else ["콜스택을 찾지 못했습니다."]

    # .dmp 분석 (cdb.exe 사용 가능 시)
    dmp_files = list(crash_dir.glob("*.dmp"))
    if dmp_files:
        result["dmp_file"] = dmp_files[0].name
        cdb = _find_cdb()
        if cdb:
            result["dmp_analysis"] = _analyze_dmp_with_cdb(dmp_files[0], cdb)
        else:
            result["dmp_analysis"] = {
                "warning": "cdb.exe를 찾을 수 없습니다. Windows SDK 설치 후 재시도하세요.",
                "hint": "PATH 또는 Windows SDK(ProgramFiles/Windows Kits/10/Debuggers/x64/cdb.exe) 설치 필요",
            }

    if len(result) == 1:
        result["error"] = "분석 가능한 파일이 없습니다 (CrashContext.runtime-xml / .dmp / .log)."

    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def analyze_crash_log(log_path: str, context_lines: int = 50) -> str:
    """
    크래시가 포함된 로그 파일에서 크래시 직전 컨텍스트와 콜스택을 추출한다.

    Args:
        log_path: 분석할 .log 파일 경로
        context_lines: 크래시 직전 몇 줄을 포함할지 (기본값: 50)
    """
    path = Path(log_path)
    if not path.exists():
        return json.dumps({"error": f"파일을 찾을 수 없습니다: {log_path}"}, ensure_ascii=False)

    content = path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()

    crash_keywords = ("Assertion failed", "Fatal error", "=== Critical error ===",
                      "Access violation", "Unhandled Exception")

    crash_line_idx = None
    for i, line in enumerate(lines):
        if any(kw in line for kw in crash_keywords):
            crash_line_idx = i
            break

    if crash_line_idx is None:
        return json.dumps({
            "file": path.name,
            "crash_detected": False,
            "message": "크래시 키워드를 찾지 못했습니다.",
        }, ensure_ascii=False, indent=2)

    start = max(0, crash_line_idx - context_lines)
    pre_context = lines[start:crash_line_idx]
    post_context = lines[crash_line_idx:crash_line_idx + 80]

    # 크래시 유형 추정
    crash_line = lines[crash_line_idx]
    crash_type = "Unknown"
    if "Assertion failed" in crash_line:
        crash_type = "Assertion Failure"
    elif "Access violation" in crash_line:
        crash_type = "Access Violation (Null Pointer / Invalid Memory)"
    elif "Fatal error" in crash_line:
        crash_type = "Fatal Error"
    elif "Unhandled Exception" in crash_line:
        crash_type = "Unhandled Exception"

    return json.dumps({
        "file": path.name,
        "crash_detected": True,
        "crash_type": crash_type,
        "crash_at_line": crash_line_idx + 1,
        "crash_line": crash_line,
        "pre_context": pre_context,
        "callstack": post_context,
    }, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run()
