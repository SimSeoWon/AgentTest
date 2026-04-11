"""
컨텍스트 MD 생성/벡터 인덱싱 모듈.
변경 파일 분석, LLM 호출, 컨텍스트 MD 저장, 벡터 인덱스 갱신.
"""
import re
import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from agent_templates import PROMPT_TEMPLATES
import common


# ─────────────────────────────────────────
# 상수
# ─────────────────────────────────────────

HEADER_EXTENSIONS = {'.h', '.hpp', '.inl'}

# 디렉토리당 최대 프롬프트 크기 (클래스 여러 개를 한번에 분석하므로 넉넉하게)
GROUP_CONTENT_LIMIT = 8000


# ─────────────────────────────────────────
# 소스 파일 목록
# ─────────────────────────────────────────

def _list_all_source_files(repo_dir: Path) -> list[str]:
    """Git으로 추적 중인 모든 소스 파일 목록을 반환한다."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_dir, capture_output=True, text=True,
        encoding='utf-8', errors='replace'
    )
    if result.returncode != 0:
        return []
    return [f for f in result.stdout.strip().split('\n')
            if f and Path(f).suffix in common.TARGET_EXTENSIONS]


# ─────────────────────────────────────────
# 벡터 인덱싱 (context_search.exe에 위임)
# ─────────────────────────────────────────

def _get_context_search_cmd(base_dir: Path) -> list[str]:
    """context_search 실행 명령어를 반환한다. exe 없으면 Python 직접 실행."""
    exe = base_dir / ".claude" / "mcp" / "context_search.exe"
    if exe.exists():
        return [str(exe)]
    # 개발 모드: Python 스크립트 직접 실행
    script = Path(__file__).parent.parent / "mcp" / "context_search" / "server.py"
    if script.exists():
        return [sys.executable, str(script)]
    return []


def update_vector_index(context_dir: Path, base_dir: Path, changed_files: list[str] | None = None):
    """
    벡터 인덱싱을 수행한다.
    서버 모드: HTTP API로 요청, 로컬 모드: context_search.exe CLI 호출.
    changed_files 지정 시 해당 파일의 MD만 upsert, 미지정 시 전체 rebuild.
    """
    # ── 서버 모드: HTTP API 사용 ──
    if common._server_mode and common._server_url:
        if changed_files:
            md_set: set[str] = set()
            for f in changed_files:
                if Path(f).suffix not in common.TARGET_EXTENSIONS:
                    continue
                # 디렉토리 단위 MD (이전 버전 호환)
                dir_md = context_dir / (str(Path(f).parent) + ".md")
                if dir_md.exists():
                    md_set.add(str(dir_md.relative_to(context_dir)).replace("\\", "/"))
                # stem 단위 MD (현재 기본 방식)
                stem_md = context_dir / Path(f).with_suffix('.md')
                if stem_md.exists():
                    md_set.add(str(stem_md.relative_to(context_dir)).replace("\\", "/"))
            md_paths = sorted(md_set)
            if not md_paths:
                return
            result = common._http_post("/api/v1/index/upsert", {"files": md_paths})
        else:
            result = common._http_post("/api/v1/index/rebuild", {})

        if result:
            count = result.get("indexed_files") or result.get("upserted_files") or 0
            if count:
                common.log(f"벡터 인덱스 갱신: {count}개 문서")
            if result.get("error"):
                common.log(f"[경고] 벡터 인덱싱 오류: {result['error']}")
        return

    # ── 로컬 모드: subprocess CLI 호출 ──
    cmd_base = _get_context_search_cmd(base_dir)
    if not cmd_base:
        common.log("[경고] context_search를 찾을 수 없어 벡터 인덱싱 건너뜀")
        return

    if changed_files:
        md_set: set[str] = set()
        for f in changed_files:
            if Path(f).suffix not in common.TARGET_EXTENSIONS:
                continue
            # 디렉토리 단위 MD (소규모 그룹)
            dir_md = context_dir / (str(Path(f).parent) + ".md")
            if dir_md.exists():
                md_set.add(str(dir_md.relative_to(context_dir)).replace("\\", "/"))
            # stem 단위 MD (대규모 그룹에서 분할된 경우)
            stem_md = context_dir / Path(f).with_suffix('.md')
            if stem_md.exists():
                md_set.add(str(stem_md.relative_to(context_dir)).replace("\\", "/"))
        md_paths = sorted(md_set)
        if not md_paths:
            return
        cmd = cmd_base + ["--upsert", str(base_dir)] + md_paths
    else:
        cmd = cmd_base + ["--rebuild", str(base_dir)]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=120
        )
        if result.returncode == 0 and result.stdout.strip():
            import json as _json
            info = _json.loads(result.stdout.strip())
            count = info.get("indexed_files") or info.get("upserted_files") or 0
            if count:
                common.log(f"벡터 인덱스 갱신: {count}개 문서")
        elif result.returncode != 0:
            common.log(f"[경고] 벡터 인덱싱 실패: {result.stderr[:200]}")
    except subprocess.TimeoutExpired:
        common.log("[경고] 벡터 인덱싱 타임아웃 (120초)")
    except Exception as e:
        common.log(f"[경고] 벡터 인덱싱 오류: {e}")


def search_related_contexts(base_dir: Path, query: str, n_results: int = 3) -> list[dict]:
    """
    관련 컨텍스트를 검색한다.
    서버 모드: HTTP API, 로컬 모드: context_search.exe --search.
    반환: [{"file": ..., "content_preview": ..., "tags": [...], ...}, ...]
    """
    # ── 서버 모드: HTTP API 사용 ──
    if common._server_mode and common._server_url:
        result = common._http_post("/api/v1/search/combined", {
            "query": query, "n_results": n_results,
        }, timeout=30)
        if result:
            return result.get("results", [])
        return []

    # ── 로컬 모드: subprocess CLI 호출 ──
    cmd_base = _get_context_search_cmd(base_dir)
    if not cmd_base:
        return []

    cmd = cmd_base + ["--search", str(base_dir), query, str(n_results)]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout.strip())
            return data.get("results", [])
    except Exception:
        pass
    return []


# ─────────────────────────────────────────
# 컨텍스트 갱신
# ─────────────────────────────────────────

def _extract_comments_section(md_content: str) -> str:
    """MD 파일에서 ## 코멘트 섹션을 추출한다. 없으면 빈 문자열 반환."""
    match = re.search(r'(## 코멘트\s*\n.*)', md_content, re.DOTALL)
    return match.group(1).rstrip() if match else ""


def _group_files(changed_files: list[str], repo_dir: Path) -> dict[str, list[str]]:
    """
    변경 파일을 stem(파일명) 단위로 묶는다.
    같은 디렉토리의 동일 stem (.h/.cpp 쌍)은 하나의 그룹으로 합쳐진다.
    변경되지 않은 파일의 MD를 덮어쓰지 않도록 항상 stem 단위로 분리한다.
    """
    groups: dict[str, list[str]] = {}
    for file_path in changed_files:
        full_path = repo_dir / file_path
        if not full_path.exists() or full_path.suffix not in common.TARGET_EXTENSIONS:
            continue
        stem_key = str(Path(file_path).parent / Path(file_path).stem)
        groups.setdefault(stem_key, []).append(file_path)
    return groups


def _find_matching_domain(context_dir: Path, file_paths: list[str]) -> str:
    """
    파일이 속한 도메인 문서를 찾아 핵심 섹션(시스템 개요 + 설계 패턴)을 반환한다.
    """
    domain_dir = context_dir / common.DOMAIN_DIR_NAME
    if not domain_dir.exists():
        return ""

    # file_paths를 MD 경로로 변환하여 매칭
    file_stems = set()
    for fp in file_paths:
        file_stems.add(str(Path(fp).with_suffix('.md')))
        file_stems.add(str(Path(fp).parent) + ".md")

    for md_file in domain_dir.glob("*.md"):
        if md_file.name.startswith("_"):
            continue
        try:
            content = md_file.read_text(encoding='utf-8')
        except Exception:
            continue

        # source_documents 파싱
        sources = set(re.findall(r'-\s+(\S+\.md)', content))
        if not sources:
            continue

        # 파일이 이 도메인에 속하는지 확인
        if not (file_stems & sources):
            continue

        # 시스템 개요 + 설계 패턴 섹션 추출 (1500자 이내)
        sections = []
        for header in ("## 시스템 개요", "## 클래스 간 관계", "## 설계 패턴"):
            match = re.search(
                rf'({header}\s*\n)(.*?)(?=\n## |\Z)',
                content, re.DOTALL,
            )
            if match:
                sections.append(match.group(1) + match.group(2).strip())

        if sections:
            domain_text = "\n\n".join(sections)
            return domain_text[:1500]

    return ""


def _build_related_context(base_dir: Path, file_path: str, context: str) -> str:
    """
    파일의 컨텍스트 요약을 검색 쿼리로 사용하여 관련 파일 컨텍스트를 수집한다.
    반환: 관련 컨텍스트 문자열 (없으면 빈 문자열)
    """
    query = Path(file_path).stem
    if context:
        body = re.sub(r'^---\s*\n.*?\n---\s*\n', '', context, count=1, flags=re.DOTALL).strip()
        query += " " + body[:200]

    results = search_related_contexts(base_dir, query, n_results=3)
    if not results:
        return ""

    file_stem = Path(file_path).with_suffix('.md').name
    related = []
    for r in results:
        if file_stem in r.get("file", ""):
            continue
        preview = r.get("content_preview", "")[:400]
        if preview:
            related.append(f"[{r.get('file', '?')}]\n{preview}")

    return "\n\n".join(related) if related else ""


def _process_directory_group(
    repo_dir: Path, context_dir: Path, base_dir: Path,
    dir_key: str, file_paths: list[str],
    auto_review: bool, use_gemini: bool,
) -> dict:
    """
    디렉토리(모듈) 단위로 컨텍스트 MD 생성 + 코드 리뷰를 한 번의 LLM 호출로 수행한다.
    반환: {"context_msg": ..., "review": ...} or partial
    """
    file_paths.sort(key=lambda f: (
        0 if Path(f).suffix in HEADER_EXTENSIONS else 1,
        Path(f).name,
    ))

    combined_content = ""
    included_paths = []
    for fp in file_paths:
        try:
            text = (repo_dir / fp).read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue
        included_paths.append(fp)
        combined_content += f"// ── {fp} ──\n{text}\n\n"

    if not included_paths:
        return {}

    # 기존 코멘트 보존
    out_path = context_dir / (dir_key + ".md")
    existing_comments = ""
    if out_path.exists():
        try:
            existing_comments = _extract_comments_section(
                out_path.read_text(encoding='utf-8')
            )
        except Exception:
            pass

    # 관련 컨텍스트 검색 (기존 벡터 인덱스 활용)
    existing_context = ""
    if out_path.exists():
        try:
            existing_context = out_path.read_text(encoding='utf-8')
        except Exception:
            pass

    # 도메인 컨텍스트 검색 (시스템 수준 맥락)
    domain_ctx = _find_matching_domain(context_dir, included_paths)
    domain_notice = ""
    if domain_ctx:
        domain_notice = f"\n\n[도메인 컨텍스트 — 이 파일이 속한 시스템의 전체 구조]\n{domain_ctx}\n"

    related = _build_related_context(base_dir, included_paths[0], existing_context)
    related_notice = ""
    if related:
        related_notice = f"\n\n[관련 파일 컨텍스트 — 의존 관계와 연동 로직 참고]\n{related}\n"

    dev_comments = _extract_comments_section(existing_context) if existing_context else ""
    comments_notice = ""
    if dev_comments:
        comments_notice = (
            f"\n\n[개발자 코멘트 — 아래 항목은 이미 인지된 사항이므로 동일 지적을 생략하거나 "
            f"'인지됨'으로 표기해줘]\n{dev_comments}\n"
        )

    # ── 프롬프트 구성: 컨텍스트 MD + 코드 리뷰를 한번에 ──
    file_path_str = " + ".join(included_paths)
    code_block = combined_content[:GROUP_CONTENT_LIMIT]

    if auto_review:
        prompt = (
            f"아래 코드 파일들을 분석하여 두 가지 작업을 수행해줘.\n"
            f"반드시 === 구분자로 두 섹션을 나눠줘.\n\n"
            f"=== CONTEXT_MD ===\n"
            f"RAG 컨텍스트 MD를 생성해줘. 반드시 아래 형식을 지켜:\n"
            f"---\n"
            f"tags: [태그1, 태그2, ...]\n"
            f"category: 대분류/중분류/소분류\n"
            f"related_classes:\n"
            f"  - ClassName: file_path\n"
            f"---\n\n"
            f"## 요약\n(기능과 역할 요약)\n\n"
            f"## 개선 필요 사항\n(알려진 이슈, 기술 부채. 없으면 \"없음\")\n\n"
            f"주의: \"## 코멘트\" 섹션은 절대 생성하지 마.\n\n"
            f"=== CODE_REVIEW ===\n"
            f"같은 모듈의 파일들이므로 클래스 간 의존 관계, 인터페이스 일관성도 함께 확인해줘.\n\n"
            f"## 1. 코딩 컨벤션 검토\n"
            f"검토 기준: 클래스명 파스칼케이스, 함수명 파스칼케이스, "
            f"멤버변수 접두사(b/f/i), public 함수 주석 필수.\n"
            f"결과: | 파일 | 항목 | 위반 내용 | 라인 | 심각도 |\n\n"
            f"## 2. 버그·안전성 검토\n"
            f"Null 포인터, 메모리 누수, 멀티스레드 안전성, 배열 범위 초과, 미초기화 변수를 검토.\n"
            f"결과: | 파일 | 위험도 | 라인 | 설명 | 권장 수정 |"
            f"{domain_notice}"
            f"{comments_notice}"
            f"{related_notice}\n\n"
            f"파일 경로: {file_path_str}\n"
            f"```\n{code_block}\n```"
        )
    else:
        prompt = PROMPT_TEMPLATES["01_소스분석"].format(
            file_path=file_path_str,
            content=code_block,
        )

    group_name = Path(dir_key).name
    file_list = ", ".join(Path(f).name for f in included_paths)
    print(f"  [분석] {group_name} ({len(included_paths)}개: {file_list})")

    raw = common._call_llm(prompt, use_gemini=use_gemini)
    if raw is None:
        return {}

    result = {}

    # ── 응답 파싱: CONTEXT_MD / CODE_REVIEW 분리 ──
    if auto_review and "=== CODE_REVIEW ===" in raw:
        parts = raw.split("=== CODE_REVIEW ===", 1)
        context_md = parts[0].replace("=== CONTEXT_MD ===", "").strip()
        review_text = parts[1].strip()
        result["review"] = {"file": dir_key, "review": review_text}
    elif auto_review and "=== CONTEXT_MD ===" in raw:
        # CODE_REVIEW 마커 없이 CONTEXT_MD만 있는 경우
        context_md = raw.replace("=== CONTEXT_MD ===", "").strip()
    else:
        context_md = raw.strip()

    # ── 컨텍스트 MD 저장 ──
    if existing_comments:
        context_md = re.sub(r'## 코멘트\s*\n.*', '', context_md, flags=re.DOTALL).rstrip()
        context_md = context_md + "\n\n" + existing_comments + "\n"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(context_md, encoding='utf-8')
    result["context_msg"] = f"  [분석] {out_path.relative_to(context_dir.parent.parent)} ({len(included_paths)}개 파일)"

    return result


def process_commit(
    repo_dir: Path, context_dir: Path, reviews_dir: Path,
    changed_files: list[str], commit_hash: str,
    auto_review: bool = True, use_gemini: bool = False,
):
    """
    컨텍스트 생성 + 코드 리뷰를 디렉토리(모듈) 단위로 한 번에 처리한다.
    LLM 1회 호출로 두 작업을 동시 수행하여 시간을 절반으로 줄인다.
    """
    base_dir = context_dir.parent.parent
    groups = _group_files(changed_files, repo_dir)
    if not groups:
        common.log("소스 파일 없음 — 건너뜀")
        return

    total_files = sum(len(fps) for fps in groups.values())
    mode = "컨텍스트+리뷰" if auto_review else "컨텍스트"
    common.log(f"{mode} 시작 — {total_files}개 파일, {len(groups)}개 모듈 (병렬 {common.MAX_WORKERS})")

    all_reviews: list[dict] = []
    success, fail = 0, 0

    with ThreadPoolExecutor(max_workers=common.MAX_WORKERS) as executor:
        futures = {
            executor.submit(
                _process_directory_group,
                repo_dir, context_dir, base_dir, dk, fps, auto_review, use_gemini,
            ): dk
            for dk, fps in groups.items()
        }
        for future in as_completed(futures):
            dk = futures[future]
            try:
                result = future.result()
                if result.get("context_msg"):
                    print(result["context_msg"])
                    success += 1
                else:
                    fail += 1
                if result.get("review"):
                    all_reviews.append(result["review"])
            except Exception as e:
                common.log(f"[경고] 처리 실패 ({dk}): {e}")
                fail += 1

    if fail:
        common.log(f"처리: {success}개 성공, {fail}개 실패")

    # 벡터 인덱스 갱신
    common.log("벡터 인덱스 갱신 중...")
    update_vector_index(context_dir, base_dir, changed_files)
    common.log("벡터 인덱스 갱신 완료")

    # 리뷰 리포트 저장
    if all_reviews:
        report = _build_review_report(all_reviews, commit_hash)
        timestamp = datetime.now().strftime('%Y-%m-%d_%H%M')
        report_path = reviews_dir / f"{timestamp}_{commit_hash[:8]}.md"
        report_path.write_text(report, encoding='utf-8')
        common.log(f"코드 리뷰 완료 → {report_path.relative_to(reviews_dir.parent.parent)}")


def _build_review_report(file_reviews: list[dict], commit_hash: str) -> str:
    """모듈별 리뷰 결과를 직접 취합하여 통합 리포트를 생성한다. (LLM 호출 없음)"""
    file_reviews.sort(key=lambda r: r["file"])

    modules_section = "\n\n---\n\n".join(
        f"### {Path(r['file']).name}/\n{r['review']}"
        for r in file_reviews
    )

    return (
        f"# 코드 리뷰 리포트\n\n"
        f"커밋: `{commit_hash}`  \n"
        f"생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  \n"
        f"검토 모듈: {len(file_reviews)}개\n\n"
        f"---\n\n"
        f"{modules_section}"
    )


def initial_context_build(
    repo_dir: Path, context_dir: Path, base_dir: Path,
    use_gemini: bool = False,
):
    """
    기존 소스 파일 중 컨텍스트 MD가 없는 모듈을 일괄 생성한다.
    최초 설치 시 전체 RAG를 초기화하며, 중단 후 재실행 시 누락분만 보충한다.
    """
    all_files = _list_all_source_files(repo_dir)
    if not all_files:
        common.log("소스 파일 없음 — 초기 컨텍스트 생성 건너뜀")
        return

    groups = _group_files(all_files, repo_dir)

    # 이미 MD가 있는 그룹 제외 (중단 후 재실행 시 누락분만 처리)
    missing: dict[str, list[str]] = {}
    for stem_key, files in groups.items():
        md_path = context_dir / (stem_key + ".md")
        if not md_path.exists():
            missing[stem_key] = files

    if not missing:
        return

    total_modules = len(missing)
    total_files = sum(len(f) for f in missing.values())
    common.log(f"초기 컨텍스트 생성 시작 — {total_modules}개 모듈, {total_files}개 파일 (병렬 {common.MAX_WORKERS})")

    completed = 0
    success = 0
    fail = 0

    with ThreadPoolExecutor(max_workers=common.MAX_WORKERS) as executor:
        futures = {
            executor.submit(
                _process_directory_group,
                repo_dir, context_dir, base_dir, dk, fps,
                False,  # auto_review=False (컨텍스트만 생성)
                use_gemini,
            ): dk
            for dk, fps in missing.items()
        }
        for future in as_completed(futures):
            dk = futures[future]
            completed += 1
            try:
                result = future.result()
                if result.get("context_msg"):
                    success += 1
                else:
                    fail += 1
            except Exception as e:
                common.log(f"[경고] 초기화 실패 ({dk}): {e}")
                fail += 1
            if completed % 10 == 0 or completed == total_modules:
                common.log(f"초기화 진행: {completed}/{total_modules} (성공: {success}, 실패: {fail})")

    common.log(f"초기 컨텍스트 생성 완료 — 성공: {success}, 실패: {fail}")

    # 벡터 인덱스 전체 구축
    common.log("벡터 인덱스 전체 구축 중...")
    update_vector_index(context_dir, base_dir)  # changed_files=None -> rebuild
    common.log("벡터 인덱스 구축 완료")
