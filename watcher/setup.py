"""
프로젝트 초기화/머지 모듈.
.claude/ 디렉토리 구조, 에이전트 템플릿, MCP 설정, CLAUDE.md 관리.
"""
import re
import sys
import json
from pathlib import Path

from agent_templates import (
    AGENTS, ROLE_TEMPLATES, PROMPT_TEMPLATES,
    SKILL_INDEX, DEFAULT_CONTEXT_DOMAINS, SETTINGS_TEMPLATES,
    MCP_SERVERS, PROJECT_CLAUDE_MD_SECTION,
    AGENTWATCH_MD_MARKER_START, AGENTWATCH_MD_MARKER_END,
)
import common


# ─────────────────────────────────────────
# Hook 스크립트
# ─────────────────────────────────────────

DOMAIN_HINT_SCRIPT = '''\
"""PostToolUse hook: 편집된 파일이 속한 도메인 정보를 Claude에게 전달한다."""
import sys, json, subprocess
from pathlib import Path

def main():
    data = json.load(sys.stdin)
    file_path = data.get("tool_input", {}).get("file_path", "")
    if not file_path:
        return

    cwd = data.get("cwd", ".")
    exe = Path(cwd) / ".claude" / "mcp" / "context_search.exe"
    if not exe.exists():
        return

    query = Path(file_path).stem
    try:
        result = subprocess.run(
            [str(exe), "--search", cwd, query, "3"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return

        info = json.loads(result.stdout.strip())
        results = info.get("results", [])
        domains = [r for r in results if "_domains/" in r.get("file", "")]
        if domains:
            d = domains[0]
            hint = f"[도메인] {d.get('file', '')}\\n{d.get('content_preview', '')[:300]}"
            print(hint, file=sys.stderr)
    except Exception:
        pass

if __name__ == "__main__":
    main()
'''


def _deploy_hook_scripts(claude_dir: Path):
    """hook 스크립트를 .claude/hooks/에 배포한다."""
    hooks_dir = claude_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    script_path = hooks_dir / "domain_hint.py"
    # 항상 최신으로 갱신
    script_path.write_text(DOMAIN_HINT_SCRIPT, encoding='utf-8')


# ─────────────────────────────────────────
# 에이전트 머지
# ─────────────────────────────────────────

def _merge_agents(agents_dir: Path) -> list[str]:
    """
    agents/ 하위를 현재 AGENTS 목록과 머지한다.
    - 없는 에이전트 폴더/파일 -> 새로 생성
    - 이미 있는 role.md / prompt.md / settings.json -> 보존 (커스텀 보호)
    - SKILL_INDEX.md -> 항상 최신으로 덮어쓰기
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

    # SKILL_INDEX.md -- 항상 최신 상태로 갱신
    (agents_dir / "SKILL_INDEX.md").write_text(SKILL_INDEX, encoding='utf-8')

    return added


# ─────────────────────────────────────────
# 프로젝트 설정 머지
# ─────────────────────────────────────────

def _update_project_settings(claude_dir: Path):
    """
    MCP 서버를 두 곳에 머지한다:
      1. 프로젝트 루트 .mcp.json -- Claude Code가 실제로 읽는 파일
      2. .claude/settings.json -- 에이전트 레벨 참조용 (하위 호환)
    - 이미 등록된 서버는 보존 (사용자 커스텀 유지)
    - 누락된 서버만 추가
    - 항상 실행 (기존 파일 여부 무관)
    """
    base_dir = claude_dir.parent

    # 1. .mcp.json (프로젝트 루트) -- Claude Code가 읽는 MCP 설정
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
        common.log(f".mcp.json MCP 등록 추가: {', '.join(added_mcp)}")

    # 2. .claude/settings.json -- 하위 호환 및 에이전트 참조용
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

    # hooks 머지 (누락된 이벤트만 추가, 기존 보존)
    hooks = settings.setdefault("hooks", {})
    hooks_added = False
    if "PostToolUse" not in hooks:
        hooks["PostToolUse"] = [
            {
                "matcher": "Edit|Write",
                "hooks": [
                    {
                        "type": "command",
                        "command": sys.executable + " " + str(claude_dir / "hooks" / "domain_hint.py"),
                        "timeout": 10,
                    }
                ]
            }
        ]
        hooks_added = True

    settings_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )

    if added:
        common.log(f"settings.json MCP 등록 추가: {', '.join(added)}")
    if hooks_added:
        common.log("settings.json hooks 등록 추가: PostToolUse (도메인 힌트)")

    # hook 스크립트 배포
    _deploy_hook_scripts(claude_dir)


# ─────────────────────────────────────────
# CLAUDE.md 관리
# ─────────────────────────────────────────

def _build_domain_map_section(context_dir: Path) -> str:
    """
    context/_domains/ 의 도메인 문서를 스캔하여 도메인 맵 섹션을 동적 생성한다.
    도메인이 없으면 빈 문자열을 반환한다.
    """
    domain_dir = context_dir / common.DOMAIN_DIR_NAME
    if not domain_dir.exists():
        return ""

    entries = []
    for md_file in sorted(domain_dir.glob("*.md")):
        if md_file.name.startswith("_"):
            continue
        try:
            content = md_file.read_text(encoding='utf-8')
        except Exception:
            continue

        # 프론트매터 파싱
        fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
        if not fm_match:
            continue
        fm = fm_match.group(1)

        tags_m = re.search(r'tags:\s*\[([^\]]*)\]', fm)
        tags = tags_m.group(1).strip() if tags_m else ""
        cat_m = re.search(r'category:\s*(.+)', fm)
        category = cat_m.group(1).strip() if cat_m else ""
        sources = re.findall(r'-\s+(\S+\.md)', fm)

        # 시스템 개요 첫 문장 추출
        overview_m = re.search(r'## 시스템 개요\s*\n(.+)', content)
        summary = overview_m.group(1).strip()[:80] if overview_m else ""

        entries.append(f"| {md_file.stem} | {summary} | {tags} | {len(sources)}개 |")

    if not entries:
        return ""

    section = (
        "\n### 도메인 맵 (자동 생성)\n\n"
        "| 도메인 | 개요 | 태그 | 소스 |\n"
        "|--------|------|------|------|\n"
        + "\n".join(entries)
        + "\n\n"
        "**기능 추가/변경 요청 시 워크플로우:**\n"
        "1. `combined_search`로 관련 도메인 문서를 먼저 검색\n"
        "2. 도메인의 '설계 패턴 및 확장 포인트'를 확인\n"
        "3. `source_documents` 목록의 실제 코드를 읽고 작업 시작\n"
    )
    return section


def _merge_claude_md_section(md_path: Path, dynamic_section: str = ""):
    """
    지정한 CLAUDE.md 파일에 AgentWatch 관리 구역을 삽입/갱신한다.
    - 마커 구역 있으면 최신 내용으로 교체
    - 없으면 파일 끝에 추가
    - 파일 자체가 없으면 새로 생성
    dynamic_section: 도메인 맵 등 런타임에 생성되는 추가 섹션
    """
    full_section = PROJECT_CLAUDE_MD_SECTION.replace(
        AGENTWATCH_MD_MARKER_END,
        dynamic_section + AGENTWATCH_MD_MARKER_END,
    )
    if md_path.exists():
        content = md_path.read_text(encoding='utf-8')
        if AGENTWATCH_MD_MARKER_START in content:
            content = re.sub(
                re.escape(AGENTWATCH_MD_MARKER_START)
                + r".*?"
                + re.escape(AGENTWATCH_MD_MARKER_END),
                full_section,
                content,
                flags=re.DOTALL,
            )
            return content, "갱신"
        else:
            content = content.rstrip() + "\n\n" + full_section + "\n"
            return content, "추가"
    else:
        return full_section + "\n", "생성"


def _update_project_claude_md(base_dir: Path):
    """
    CLAUDE.md에 AgentWatch 관리 구역을 삽입/갱신한다.
    도메인 문서가 존재하면 도메인 맵 섹션도 동적으로 포함한다.
    """
    context_dir = base_dir / ".claude" / "context"
    domain_section = _build_domain_map_section(context_dir)

    # 1. 루트 CLAUDE.md
    root_md = base_dir / "CLAUDE.md"
    content, action = _merge_claude_md_section(root_md, domain_section)
    root_md.write_text(content, encoding='utf-8')
    common.log(f"CLAUDE.md AgentWatch 구역 {action}")

    # 2. .claude/CLAUDE.md (있을 때만)
    dot_claude_md = base_dir / ".claude" / "CLAUDE.md"
    if dot_claude_md.exists():
        content, action = _merge_claude_md_section(dot_claude_md, domain_section)
        dot_claude_md.write_text(content, encoding='utf-8')
        common.log(f".claude/CLAUDE.md AgentWatch 구역 {action}")


# ─────────────────────────────────────────
# 메인 초기화 함수
# ─────────────────────────────────────────

def init_project_dirs(base_dir: Path) -> tuple[Path, Path, Path]:
    """
    .claude/ 및 하위 디렉토리 초기화 (머지 방식).
    반환: (context_dir, agents_dir, reviews_dir)
    """
    claude_dir = base_dir / ".claude"
    is_new = not claude_dir.exists()
    claude_dir.mkdir(exist_ok=True)

    # context/ -- 항상 실행: 새 도메인 폴더가 추가돼도 반영
    context_dir = claude_dir / "context"
    context_dir.mkdir(exist_ok=True)
    for domain in DEFAULT_CONTEXT_DOMAINS:
        (context_dir / domain).mkdir(exist_ok=True)

    # reviews/ -- 코드 리뷰 리포트 저장
    reviews_dir = claude_dir / "reviews"
    reviews_dir.mkdir(exist_ok=True)

    # agents/ -- 항상 실행: 새 에이전트가 추가돼도 반영
    agents_dir = claude_dir / "agents"
    agents_dir.mkdir(exist_ok=True)
    added = _merge_agents(agents_dir)

    # MCP 서버 및 CLAUDE.md -- 항상 머지 (기존 Claude 환경 대응)
    _update_project_settings(claude_dir)
    _update_project_claude_md(base_dir)

    if is_new:
        common.log(".claude/ 구조 초기화 완료")
    elif added:
        common.log(f".claude/ 머지 완료 -- 새 에이전트 {len(added)}개 추가: {', '.join(added)}")

    return context_dir, agents_dir, reviews_dir
