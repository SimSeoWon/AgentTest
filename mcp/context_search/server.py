"""
context_search MCP 서버
.claude/context/ 디렉토리의 MD 파일을 태그 기반으로 검색한다.
"""
import re
import json
from pathlib import Path
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("context-search")


def _parse_frontmatter(content: str) -> dict:
    """MD 파일의 YAML 프론트매터에서 tags, category를 파싱한다."""
    match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
    if not match:
        return {}

    frontmatter = match.group(1)
    result = {}

    # tags: [태그1, 태그2, ...]
    tags_match = re.search(r'tags:\s*\[([^\]]*)\]', frontmatter)
    if tags_match:
        raw = tags_match.group(1)
        result["tags"] = [t.strip().strip('"').strip("'") for t in raw.split(',') if t.strip()]

    # category: 대분류/중분류/소분류
    category_match = re.search(r'category:\s*(.+)', frontmatter)
    if category_match:
        result["category"] = category_match.group(1).strip()

    # related_classes
    classes = re.findall(r'-\s+(\w+):\s+(.+)', frontmatter)
    if classes:
        result["related_classes"] = {cls: path.strip() for cls, path in classes}

    return frontmatter and result or {}


def _extract_body(content: str) -> str:
    """프론트매터를 제외한 본문만 반환한다."""
    return re.sub(r'^---\s*\n.*?\n---\s*\n', '', content, count=1, flags=re.DOTALL).strip()


@mcp.tool()
def search_context(tags: list[str], project_root: str = ".", match_all: bool = False) -> str:
    """
    태그로 .claude/context/ MD 파일을 검색한다.

    Args:
        tags: 검색할 태그 목록
        project_root: 프로젝트 루트 경로 (기본값: 현재 디렉토리)
        match_all: True면 모든 태그가 일치하는 파일만 반환, False면 하나라도 일치하면 반환
    """
    context_dir = Path(project_root) / ".claude" / "context"
    if not context_dir.exists():
        return json.dumps({"error": f"컨텍스트 디렉토리를 찾을 수 없습니다: {context_dir}"}, ensure_ascii=False)

    search_tags = {t.strip().lower() for t in tags}
    results = []

    for md_file in sorted(context_dir.rglob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue

        meta = _parse_frontmatter(content)
        file_tags = {t.lower() for t in meta.get("tags", [])}

        matched = search_tags & file_tags
        if (match_all and matched == search_tags) or (not match_all and matched):
            results.append({
                "file": str(md_file.relative_to(context_dir)),
                "tags": list(file_tags),
                "category": meta.get("category", ""),
                "related_classes": meta.get("related_classes", {}),
                "matched_tags": list(matched),
                "body": _extract_body(content),
            })

    return json.dumps(
        {"count": len(results), "results": results},
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def list_tags(project_root: str = ".") -> str:
    """
    .claude/context/ 에 존재하는 모든 태그 목록과 등장 횟수를 반환한다.

    Args:
        project_root: 프로젝트 루트 경로 (기본값: 현재 디렉토리)
    """
    context_dir = Path(project_root) / ".claude" / "context"
    if not context_dir.exists():
        return json.dumps({"error": f"컨텍스트 디렉토리를 찾을 수 없습니다: {context_dir}"}, ensure_ascii=False)

    tag_counts: dict[str, int] = {}

    for md_file in context_dir.rglob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue

        meta = _parse_frontmatter(content)
        for tag in meta.get("tags", []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    sorted_tags = sorted(tag_counts.items(), key=lambda x: -x[1])
    return json.dumps(
        {"total_tags": len(sorted_tags), "tags": dict(sorted_tags)},
        ensure_ascii=False,
        indent=2,
    )


if __name__ == "__main__":
    mcp.run()
