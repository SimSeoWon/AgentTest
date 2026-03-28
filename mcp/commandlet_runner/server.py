"""
commandlet_runner MCP 서버
Unreal Engine 5 커맨드렛을 실행하고 결과를 반환한다.

탐색 순서:
  1. .uproject의 EngineAssociation → 레지스트리
  2. 레지스트리 실패 시 %ProgramFiles%/Epic Games/UE_<version> 폴더
"""
import os
import json
import subprocess
from pathlib import Path
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("commandlet-runner")


# ─────────────────────────────────────────
# 엔진 경로 탐색
# ─────────────────────────────────────────

def _find_uproject(project_root: str) -> Path | None:
    for p in Path(project_root).glob("*.uproject"):
        return p
    return None


def _get_engine_association(uproject_path: Path) -> str | None:
    try:
        with open(uproject_path, encoding='utf-8') as f:
            return json.load(f).get("EngineAssociation")
    except Exception:
        return None


def _find_editor_from_registry(engine_version: str) -> str | None:
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for arch in (winreg.KEY_READ, winreg.KEY_READ | winreg.KEY_WOW64_32KEY):
                try:
                    key = winreg.OpenKey(
                        hive,
                        f"SOFTWARE\\EpicGames\\Unreal Engine\\{engine_version}",
                        access=arch
                    )
                    install_dir, _ = winreg.QueryValueEx(key, "InstalledDirectory")
                    for exe in ("UnrealEditor-Cmd.exe", "UE4Editor-Cmd.exe"):
                        candidate = Path(install_dir) / "Engine" / "Binaries" / "Win64" / exe
                        if candidate.exists():
                            return str(candidate)
                except Exception:
                    continue
    except ImportError:
        pass
    return None


def _find_editor_from_env(engine_version: str) -> str | None:
    pf = os.environ.get("ProgramFiles", "C:\\Program Files")
    epic_dir = Path(pf) / "Epic Games"
    for ue_dir in epic_dir.glob(f"UE_{engine_version}*"):
        for exe in ("UnrealEditor-Cmd.exe", "UE4Editor-Cmd.exe"):
            candidate = ue_dir / "Engine" / "Binaries" / "Win64" / exe
            if candidate.exists():
                return str(candidate)
    return None


def _resolve_editor(project_root: str) -> tuple[str | None, str | None, str | None]:
    """(editor_path, uproject_path, engine_version) 반환"""
    uproject = _find_uproject(project_root)
    if not uproject:
        return None, None, None

    version = _get_engine_association(uproject)
    if not version:
        return None, str(uproject), None

    editor = _find_editor_from_registry(version) or _find_editor_from_env(version)
    return editor, str(uproject), version


# ─────────────────────────────────────────
# MCP 툴
# ─────────────────────────────────────────

@mcp.tool()
def find_unreal_editor(project_root: str = ".") -> str:
    """
    .uproject의 EngineAssociation을 기반으로 UnrealEditor-Cmd.exe 경로를 탐색한다.

    Args:
        project_root: .uproject 파일이 있는 디렉토리 (기본값: 현재 디렉토리)
    """
    editor, uproject, version = _resolve_editor(project_root)
    return json.dumps({
        "found": editor is not None,
        "editor_path": editor,
        "uproject_path": uproject,
        "engine_version": version,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def run_data_validation(project_root: str = ".", timeout: int = 300) -> str:
    """
    DataValidation 커맨드렛을 실행하여 에셋 유효성을 검사한다.

    Args:
        project_root: 프로젝트 루트 경로 (기본값: 현재 디렉토리)
        timeout: 최대 대기 시간(초) (기본값: 300)
    """
    editor, uproject, version = _resolve_editor(project_root)
    if not editor:
        return json.dumps({
            "error": f"UnrealEditor-Cmd.exe를 찾을 수 없습니다. (엔진 버전: {version})",
            "hint": "Epic Games Launcher에서 해당 엔진 버전이 설치되어 있는지 확인하세요.",
        }, ensure_ascii=False)

    try:
        result = subprocess.run(
            [editor, uproject,
             "-run=DataValidation", "-log", "-unattended", "-nullrhi"],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=timeout
        )
        return json.dumps({
            "returncode": result.returncode,
            "engine_version": version,
            "stdout": result.stdout[-8000:],
            "stderr": result.stderr[-2000:],
        }, ensure_ascii=False, indent=2)
    except subprocess.TimeoutExpired:
        return json.dumps({"error": f"타임아웃 ({timeout}초) 초과"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@mcp.tool()
def run_commandlet(
    commandlet: str,
    project_root: str = ".",
    extra_args: list[str] | None = None,
    timeout: int = 300,
) -> str:
    """
    임의의 UE5 커맨드렛을 실행한다.

    Args:
        commandlet: 커맨드렛 이름 (예: "DataValidation", "ResavePackages", "CheckForError")
        project_root: 프로젝트 루트 경로 (기본값: 현재 디렉토리)
        extra_args: 추가 인자 목록
        timeout: 최대 대기 시간(초) (기본값: 300)
    """
    editor, uproject, version = _resolve_editor(project_root)
    if not editor:
        return json.dumps({
            "error": f"UnrealEditor-Cmd.exe를 찾을 수 없습니다. (엔진 버전: {version})"
        }, ensure_ascii=False)

    cmd = [editor, uproject, f"-run={commandlet}", "-log", "-unattended", "-nullrhi"]
    if extra_args:
        cmd.extend(extra_args)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=timeout
        )
        return json.dumps({
            "commandlet": commandlet,
            "returncode": result.returncode,
            "stdout": result.stdout[-8000:],
            "stderr": result.stderr[-2000:],
        }, ensure_ascii=False, indent=2)
    except subprocess.TimeoutExpired:
        return json.dumps({"error": f"타임아웃 ({timeout}초) 초과"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
