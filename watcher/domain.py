"""
도메인 자동 승급/건강도 모듈.
검색 로그 분석, 도메인 문서 생성, 아키텍처 개요, 건강도 리포트.
"""
import re
import json
from pathlib import Path
from datetime import datetime
from collections import Counter

from context import update_vector_index, _extract_comments_section
import common


# ─────────────────────────────────────────
# 상수
# ─────────────────────────────────────────

COOCCURRENCE_THRESHOLD = 5   # 동시 출현 최소 횟수
DOMAIN_MIN_DOCS = 2          # 도메인 최소 문서 수
DOMAIN_CHECK_INTERVAL = 10   # N번째 폴링마다 도메인 체크
HEALTH_CHECK_INTERVAL = 50   # N번째 폴링마다 건강도 리포트 생성


# ─────────────────────────────────────────
# 검색 패턴 분석
# ─────────────────────────────────────────

def _analyze_search_patterns(base_dir: Path) -> list[list[str]]:
    """
    검색 로그를 분석하여 자주 함께 검색되는 문서 클러스터를 식별한다.
    반환: [[doc1, doc2, ...], [doc3, doc4], ...] 형태의 클러스터 목록
    """
    log_path = base_dir / ".claude" / "search_log.jsonl"
    if not log_path.exists():
        return []

    # 1) 로그에서 결과 문서 목록 수집
    search_sessions: list[list[str]] = []
    try:
        for line in log_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            results = entry.get("results", [])
            # 도메인 문서 자체는 제외
            results = [r for r in results if common.DOMAIN_DIR_NAME + "/" not in r]
            if len(results) >= 2:
                search_sessions.append(results)
    except Exception:
        return []

    if not search_sessions:
        return []

    # 2) 문서 쌍의 동시 출현 빈도 카운트
    pair_counts: Counter = Counter()
    for session in search_sessions:
        docs = sorted(set(session))
        for i in range(len(docs)):
            for j in range(i + 1, len(docs)):
                pair_counts[frozenset([docs[i], docs[j]])] += 1

    # 3) 임계값 이상인 쌍으로 그래프 구축 + 연결 요소 추출
    strong_pairs = [pair for pair, cnt in pair_counts.items()
                    if cnt >= COOCCURRENCE_THRESHOLD]
    if not strong_pairs:
        return []

    # Union-Find로 클러스터링
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a: str, b: str):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for pair in strong_pairs:
        a, b = list(pair)
        parent.setdefault(a, a)
        parent.setdefault(b, b)
        union(a, b)

    # 클러스터 수집
    clusters: dict[str, list[str]] = {}
    for doc in parent:
        root = find(doc)
        clusters.setdefault(root, []).append(doc)

    return [docs for docs in clusters.values() if len(docs) >= DOMAIN_MIN_DOCS]


# ─────────────────────────────────────────
# 도메인 유틸
# ─────────────────────────────────────────

def _get_existing_domains(context_dir: Path) -> dict[str, set[str]]:
    """기존 도메인 문서와 그 소스 문서 목록을 반환한다."""
    domain_dir = context_dir / common.DOMAIN_DIR_NAME
    if not domain_dir.exists():
        return {}

    domains: dict[str, set[str]] = {}
    for md_file in domain_dir.glob("*.md"):
        try:
            content = md_file.read_text(encoding='utf-8')
            # source_documents 파싱
            sources: set[str] = set()
            in_sources = False
            for line in content.splitlines():
                if line.strip().startswith("source_documents:"):
                    in_sources = True
                    continue
                if in_sources:
                    if line.strip().startswith("- "):
                        sources.add(line.strip()[2:].strip())
                    elif line.strip() and not line.startswith(" "):
                        break
            domains[md_file.stem] = sources
        except Exception:
            continue
    return domains


def _cleanup_stale_domains(context_dir: Path):
    """소스 문서가 50% 이상 사라진 stale 도메인을 삭제한다."""
    existing = _get_existing_domains(context_dir)
    domain_dir = context_dir / common.DOMAIN_DIR_NAME
    if not domain_dir.exists():
        return

    for domain_name, sources in existing.items():
        if not sources:
            continue
        alive = sum(1 for s in sources if (context_dir / s).exists())
        if alive < len(sources) * 0.5:
            md_path = domain_dir / f"{domain_name}.md"
            md_path.unlink(missing_ok=True)
            common.log(f"stale 도메인 삭제: {domain_name} (소스 {alive}/{len(sources)} 존재)")


def _generate_architecture_overview(context_dir: Path, use_gemini: bool = False):
    """
    도메인 문서가 3개 이상이면 전체를 종합한 아키텍처 개요를 생성한다.
    _domains/_overview.md에 저장한다.
    """
    domain_dir = context_dir / common.DOMAIN_DIR_NAME
    if not domain_dir.exists():
        return

    domain_files = [f for f in domain_dir.glob("*.md") if not f.name.startswith("_")]
    if len(domain_files) < 3:
        return

    # 각 도메인의 시스템 개요를 수집
    summaries = []
    for md_file in sorted(domain_files):
        try:
            content = md_file.read_text(encoding='utf-8')
            overview_m = re.search(r'## 시스템 개요\s*\n(.*?)(?=\n## |\Z)', content, re.DOTALL)
            if overview_m:
                summaries.append(f"### {md_file.stem}\n{overview_m.group(1).strip()}")
        except Exception:
            continue

    if len(summaries) < 3:
        return

    prompt = (
        f"아래는 프로젝트의 주요 시스템(도메인) 목록이다.\n"
        f"이 도메인들을 종합하여 '프로젝트 아키텍처 개요'를 생성해줘.\n\n"
        f"형식:\n"
        f"---\n"
        f"tags: [아키텍처, 개요]\n"
        f"category: 도메인/아키텍처개요\n"
        f"type: overview\n"
        f"---\n\n"
        f"## 프로젝트 구조 요약\n"
        f"(전체 시스템의 큰 그림을 3~5문장으로)\n\n"
        f"## 시스템 간 관계\n"
        f"(도메인들이 어떻게 연결되는지, 데이터가 어떻게 흐르는지)\n\n"
        f"## 핵심 진입점\n"
        f"(새로운 팀원이 가장 먼저 봐야 할 시스템과 파일)\n\n"
        f"주의: '## 코멘트' 섹션은 생성하지 마.\n\n"
        f"=== 도메인 목록 ===\n\n"
        + "\n\n".join(summaries)
    )

    raw = common._call_llm(prompt, use_gemini=use_gemini)
    if not raw:
        return

    overview_path = domain_dir / "_overview.md"
    # 기존 코멘트 보존
    overview_content = raw.strip()
    if overview_path.exists():
        existing_comments = _extract_comments_section(
            overview_path.read_text(encoding='utf-8')
        )
        if existing_comments:
            overview_content = re.sub(
                r'## 코멘트\s*\n.*', '', overview_content, flags=re.DOTALL
            ).rstrip()
            overview_content = overview_content + "\n\n" + existing_comments + "\n"
    overview_path.write_text(overview_content, encoding='utf-8')
    common.log(f"아키텍처 개요 갱신: {len(domain_files)}개 도메인 종합")


# ─────────────────────────────────────────
# 도메인 승급
# ─────────────────────────────────────────

def promote_domains(
    base_dir: Path, context_dir: Path,
    use_gemini: bool = False,
):
    """
    검색 로그를 분석하여 자주 함께 검색되는 문서 클러스터를
    도메인 문서로 승급시킨다.
    """
    # stale 도메인 정리
    _cleanup_stale_domains(context_dir)

    clusters = _analyze_search_patterns(base_dir)
    if not clusters:
        return

    existing = _get_existing_domains(context_dir)
    domain_dir = context_dir / common.DOMAIN_DIR_NAME
    domain_dir.mkdir(parents=True, exist_ok=True)

    # 이미 도메인으로 만들어진 클러스터인지 확인 (양방향 체크)
    new_clusters = []
    for cluster in clusters:
        cluster_set = set(cluster)
        already = False
        for domain_sources in existing.values():
            overlap = len(cluster_set & domain_sources)
            # 클러스터의 80% 이상이 기존 도메인에 포함되거나,
            # 기존 도메인의 80% 이상이 클러스터에 포함되면 중복
            if (domain_sources and overlap >= len(domain_sources) * 0.8) or \
               (cluster_set and overlap >= len(cluster_set) * 0.8):
                already = True
                break
        if not already:
            new_clusters.append(cluster)

    if not new_clusters:
        return

    common.log(f"도메인 승급 후보: {len(new_clusters)}개 클러스터")

    for cluster in new_clusters:
        # 클러스터 내 문서들의 컨텍스트 MD 읽기
        doc_contents = []
        for doc_file in sorted(cluster):
            md_path = context_dir / doc_file
            if md_path.exists():
                try:
                    content = md_path.read_text(encoding='utf-8')
                    doc_contents.append(f"[{doc_file}]\n{content}")
                except Exception:
                    continue

        if len(doc_contents) < DOMAIN_MIN_DOCS:
            continue

        # LLM으로 도메인 문서 생성
        source_list = "\n".join(f"  - {d}" for d in sorted(cluster))
        prompt = (
            f"아래는 자주 함께 검색되는 관련 코드 컨텍스트들이다.\n"
            f"이 문서들을 종합하여 하나의 '도메인 문서'를 생성해줘.\n\n"
            f"반드시 아래 형식을 지켜줘:\n"
            f"---\n"
            f"tags: [태그1, 태그2, ...]\n"
            f"category: 도메인/<도메인명>\n"
            f"type: domain\n"
            f"source_documents:\n{source_list}\n"
            f"---\n\n"
            f"## 시스템 개요\n"
            f"(이 기능/시스템이 하는 일, 주요 컴포넌트 역할)\n\n"
            f"## 클래스 간 관계\n"
            f"(상호작용, 의존 관계, 호출 흐름)\n\n"
            f"## 데이터 흐름\n"
            f"(주요 데이터가 어떻게 이동하고 변환되는지)\n\n"
            f"## 설계 패턴 및 확장 포인트\n"
            f"(사용된 패턴, 수정 시 주의할 지점)\n\n"
            f"주의:\n"
            f"- 도메인명은 한글로, 이 기능/시스템을 대표하는 이름으로 지어줘\n"
            f"- '## 코멘트' 섹션은 생성하지 마\n\n"
            f"=== 소스 문서들 ===\n\n"
            + "\n\n---\n\n".join(doc_contents)
        )

        raw = common._call_llm(prompt, use_gemini=use_gemini)
        if not raw:
            continue

        # 도메인명 추출 (category에서)
        cat_match = re.search(r'category:\s*도메인/(.+)', raw)
        domain_name = cat_match.group(1).strip() if cat_match else f"도메인_{len(existing) + 1}"
        # 파일명에 사용할 수 없는 문자 제거
        safe_name = re.sub(r'[\\/:*?"<>|]', '_', domain_name)

        out_path = domain_dir / f"{safe_name}.md"
        # 기존 코멘트 보존
        domain_content = raw.strip()
        if out_path.exists():
            existing_comments = _extract_comments_section(
                out_path.read_text(encoding='utf-8')
            )
            if existing_comments:
                domain_content = re.sub(
                    r'## 코멘트\s*\n.*', '', domain_content, flags=re.DOTALL
                ).rstrip()
                domain_content = domain_content + "\n\n" + existing_comments + "\n"
        out_path.write_text(domain_content, encoding='utf-8')
        common.log(f"도메인 승급: {safe_name} ({len(cluster)}개 문서 종합)")

    # 도메인 3개 이상이면 아키텍처 맵 자동 생성
    _generate_architecture_overview(context_dir, use_gemini)

    # 승급 완료 — 승급에 사용된 문서가 포함된 로그 항목만 제거
    promoted_docs = set()
    for cluster in new_clusters:
        promoted_docs.update(cluster)
    log_path = base_dir / ".claude" / "search_log.jsonl"
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
        kept = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            results = set(entry.get("results", []))
            if not results & promoted_docs:
                kept.append(line)
        removed = len(lines) - len(kept)
        log_path.write_text("\n".join(kept) + "\n" if kept else "", encoding="utf-8")
        common.log(f"검색 로그 정리: {removed}건 제거, {len(kept)}건 보존")
    except Exception:
        pass

    # 도메인 문서 포함하여 벡터 인덱스 재구축
    common.log("도메인 반영 — 벡터 인덱스 갱신 중...")
    update_vector_index(context_dir, base_dir)
    common.log("벡터 인덱스 갱신 완료")


# ─────────────────────────────────────────
# 건강도 리포트
# ─────────────────────────────────────────

def generate_health_report(base_dir: Path, context_dir: Path, reviews_dir: Path):
    """
    도메인별 기술 부채, 리뷰 지적 빈도, 미해결 고민을 집계하여
    프로젝트 건강도 리포트를 생성한다. LLM 호출 없이 파일 파싱만으로 동작.
    """
    existing_domains = _get_existing_domains(context_dir)
    if not existing_domains:
        return

    domain_stats: dict[str, dict] = {}

    for domain_name, sources in existing_domains.items():
        stats = {"issues": 0, "comments": 0, "concerns": 0, "files": len(sources)}

        # 소스 문서에서 개선 필요 사항 / 코멘트 집계
        for src in sources:
            md_path = context_dir / src
            if not md_path.exists():
                continue
            try:
                content = md_path.read_text(encoding='utf-8')
            except Exception:
                continue

            # "## 개선 필요 사항" 항목 수 카운트
            improve_m = re.search(r'## 개선 필요 사항\s*\n(.*?)(?=\n## |\Z)', content, re.DOTALL)
            if improve_m:
                text = improve_m.group(1).strip()
                if text and text != "없음":
                    stats["issues"] += text.count("\n") + 1

            # 코멘트 집계
            comments = _extract_comments_section(content)
            if comments:
                stats["comments"] += comments.count("\n") + 1
                stats["concerns"] += comments.count("[고민]")

        domain_stats[domain_name] = stats

    # 리뷰 리포트에서 도메인별 지적 빈도 (최근 20개 리뷰)
    review_files = sorted(reviews_dir.glob("*.md"), reverse=True)[:20]
    domain_review_counts: dict[str, int] = {d: 0 for d in existing_domains}
    for rf in review_files:
        try:
            review_content = rf.read_text(encoding='utf-8')
            for domain_name, sources in existing_domains.items():
                for src in sources:
                    stem = Path(src).stem
                    if stem in review_content:
                        domain_review_counts[domain_name] += 1
                        break
        except Exception:
            continue

    # 리포트 생성
    lines = [
        "# 프로젝트 건강도 리포트\n",
        f"생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
        "\n## 도메인별 현황\n",
        "| 도메인 | 소스 파일 | 개선 사항 | 코멘트 | 미결 고민 | 리뷰 지적 |",
        "|--------|----------|----------|--------|----------|----------|",
    ]
    for domain_name, stats in sorted(domain_stats.items()):
        review_count = domain_review_counts.get(domain_name, 0)
        lines.append(
            f"| {domain_name} | {stats['files']} | {stats['issues']} | "
            f"{stats['comments']} | {stats['concerns']} | {review_count} |"
        )

    # 주의 필요 도메인
    high_debt = [d for d, s in domain_stats.items() if s["issues"] >= 5]
    high_concerns = [d for d, s in domain_stats.items() if s["concerns"] >= 3]
    if high_debt or high_concerns:
        lines.append("\n## 주의 필요\n")
        if high_debt:
            lines.append(f"- **기술 부채 높음**: {', '.join(high_debt)}")
        if high_concerns:
            lines.append(f"- **미결 고민 많음**: {', '.join(high_concerns)}")

    report_path = reviews_dir / "_health_report.md"
    report_path.write_text("\n".join(lines), encoding='utf-8')
    common.log(f"건강도 리포트 갱신 → {report_path.relative_to(base_dir)}")
