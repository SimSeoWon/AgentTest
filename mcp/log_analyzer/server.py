"""
log_analyzer MCP 서버
Unreal Engine 5 로그 파일(.log)을 파싱하여 에러·경고·패턴을 분석한다.
"""
import re
import json
from pathlib import Path
from collections import defaultdict
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("log-analyzer")

# UE5 로그 라인 패턴: [2024.01.15-10.30.45:123][  0]LogCategory: Severity: Message
_LOG_PATTERN = re.compile(
    r'^\[(\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d+)\]\[\s*(\d+)\]'
    r'(\w+):\s*(?:(Warning|Error|Fatal|Critical|Display|Verbose|Log):\s*)?(.*)$'
)

_SEVERITY_ORDER = {"Fatal": 0, "Critical": 1, "Error": 2, "Warning": 3, "Display": 4}


def _parse_log(content: str) -> list[dict]:
    entries = []
    for line in content.splitlines():
        m = _LOG_PATTERN.match(line.rstrip())
        if m:
            timestamp, frame, category, severity, message = m.groups()
            entries.append({
                "timestamp": timestamp,
                "frame": int(frame),
                "category": category,
                "severity": severity or "Log",
                "message": message,
            })
    return entries


@mcp.tool()
def analyze_log(log_path: str, max_issues: int = 50) -> str:
    """
    UE5 로그 파일을 분석하여 에러·경고·패턴 요약을 반환한다.

    Args:
        log_path: 분석할 .log 파일 경로
        max_issues: 반환할 최대 이슈 수 (기본값: 50)
    """
    path = Path(log_path)
    if not path.exists():
        return json.dumps({"error": f"파일을 찾을 수 없습니다: {log_path}"}, ensure_ascii=False)

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    entries = _parse_log(content)
    total_lines = len(content.splitlines())
    parsed_count = len(entries)

    # 심각도별 분류
    by_severity: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        if e["severity"] in _SEVERITY_ORDER:
            by_severity[e["severity"]].append(e)

    # 카테고리별 에러 빈도
    category_errors: dict[str, int] = defaultdict(int)
    for e in by_severity.get("Error", []) + by_severity.get("Fatal", []) + by_severity.get("Critical", []):
        category_errors[e["category"]] += 1

    # 반복 메시지 감지 (동일 메시지 3회 이상)
    message_counts: dict[str, int] = defaultdict(int)
    for e in entries:
        message_counts[e["message"]] += 1
    repeated = {msg: cnt for msg, cnt in message_counts.items() if cnt >= 3}

    # 주요 이슈 목록 (Fatal > Critical > Error > Warning 순)
    issues = []
    for severity in ("Fatal", "Critical", "Error", "Warning"):
        for e in by_severity.get(severity, []):
            issues.append(e)
            if len(issues) >= max_issues:
                break
        if len(issues) >= max_issues:
            break

    result = {
        "file": str(path.name),
        "total_lines": total_lines,
        "parsed_entries": parsed_count,
        "summary": {
            "fatal":    len(by_severity.get("Fatal", [])),
            "critical": len(by_severity.get("Critical", [])),
            "error":    len(by_severity.get("Error", [])),
            "warning":  len(by_severity.get("Warning", [])),
        },
        "top_error_categories": dict(
            sorted(category_errors.items(), key=lambda x: -x[1])[:10]
        ),
        "repeated_messages": dict(
            sorted(repeated.items(), key=lambda x: -x[1])[:10]
        ),
        "issues": issues,
    }

    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def search_log(log_path: str, keyword: str, severity_filter: str = "") -> str:
    """
    로그 파일에서 키워드로 특정 메시지를 검색한다.

    Args:
        log_path: 분석할 .log 파일 경로
        keyword: 검색할 키워드 (대소문자 무시)
        severity_filter: 필터링할 심각도 (Error, Warning, Fatal 등. 비워두면 전체)
    """
    path = Path(log_path)
    if not path.exists():
        return json.dumps({"error": f"파일을 찾을 수 없습니다: {log_path}"}, ensure_ascii=False)

    content = path.read_text(encoding="utf-8", errors="replace")
    entries = _parse_log(content)

    keyword_lower = keyword.lower()
    results = [
        e for e in entries
        if keyword_lower in e["message"].lower()
        and (not severity_filter or e["severity"] == severity_filter)
    ]

    return json.dumps(
        {"keyword": keyword, "count": len(results), "results": results[:100]},
        ensure_ascii=False, indent=2
    )


if __name__ == "__main__":
    mcp.run()
