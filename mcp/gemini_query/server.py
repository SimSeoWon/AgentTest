"""
gemini_query MCP 서버
Gemini CLI에 단발 분석 요청을 위임한다.
Claude의 컨텍스트 윈도우를 아끼기 위해 대규모 파일 분석에 활용한다.

Gemini CLI가 설치되지 않은 환경에서는 안내 메시지를 반환하고 종료한다.
"""
import shutil
import subprocess
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("gemini-query")

_INSTALL_GUIDE = (
    "Gemini CLI가 설치되어 있지 않습니다.\n"
    "설치: https://ai.google.dev/gemini-api/docs/gemini-cli\n"
    "설치 후 로그인: gemini auth login"
)


def _gemini_available() -> bool:
    return shutil.which("gemini") is not None


@mcp.tool()
def gemini_analyze(prompt: str, timeout: int = 300) -> str:
    """
    Gemini CLI에 분석을 요청하고 결과를 반환한다.
    Claude의 토큰을 아끼기 위해 수십 개 이상의 파일을 한 번에 분석할 때 사용한다.

    Args:
        prompt: Gemini에게 요청할 분석 내용
        timeout: 최대 대기 시간(초) (기본값: 300)
    """
    if not _gemini_available():
        return _INSTALL_GUIDE

    try:
        result = subprocess.run(
            ["gemini", "-y", prompt],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=timeout,
        )
        output = result.stdout.strip()
        if not output and result.stderr:
            output = result.stderr.strip()
        return output or "(Gemini 응답 없음)"
    except subprocess.TimeoutExpired:
        return f"타임아웃 ({timeout}초) 초과 — 프롬프트를 더 짧게 줄여보세요."
    except Exception as e:
        return f"오류: {e}"


@mcp.tool()
def gemini_status() -> str:
    """
    Gemini CLI 설치 및 사용 가능 여부를 확인한다.
    """
    if not _gemini_available():
        return _INSTALL_GUIDE

    try:
        result = subprocess.run(
            ["gemini", "--version"],
            capture_output=True, text=True, encoding='utf-8', timeout=10,
        )
        version = (result.stdout or result.stderr).strip()
        return f"Gemini CLI 사용 가능\n버전: {version}"
    except Exception as e:
        return f"버전 확인 실패: {e}"


if __name__ == "__main__":
    mcp.run()
