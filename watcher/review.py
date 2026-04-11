"""
에셋 검증 모듈.
Unreal Engine DataValidation 커맨드렛 실행 및 결과 분석.
"""
import json
import subprocess
from pathlib import Path
from datetime import datetime

import common


# ─────────────────────────────────────────
# Unreal Engine 탐색
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


# ─────────────────────────────────────────
# 에셋 검증
# ─────────────────────────────────────────

def run_asset_validation(
    base_dir: Path,
    reviews_dir: Path,
    changed_files: list[str],
    commit_hash: str,
    use_gemini: bool = False,
):
    """변경된 .uasset / .umap 파일에 대해 DataValidation 커맨드렛을 실행한다."""
    assets = [f for f in changed_files if Path(f).suffix in common.ASSET_EXTENSIONS]
    if not assets:
        return

    common.log(f"에셋 검증 시작 — {len(assets)}개 파일")

    editor, uproject = _find_unreal_editor(base_dir)
    if not editor:
        common.log("[경고] UnrealEditor-Cmd.exe를 찾을 수 없어 에셋 검증을 건너뜁니다.")
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
        common.log("[경고] 커맨드렛 타임아웃 (300초) — 에셋 검증 중단")
        return
    except Exception as e:
        common.log(f"[오류] 커맨드렛 실행 실패: {e}")
        return

    common.log("에셋 검증 결과 분석 중...")
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

    analysis = common._call_llm(prompt, use_gemini=use_gemini)

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
    common.log(f"에셋 검증 완료 → {report_path.relative_to(base_dir)}")
