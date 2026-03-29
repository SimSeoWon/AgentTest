"""
context_search MCP 서버
.claude/context/ 디렉토리의 MD 파일을 태그 기반 + 벡터 유사도 기반으로 검색한다.
"""
import re
import sys
import json
import shutil
from pathlib import Path
from mcp.server.fastmcp import FastMCP

# 벡터 검색 의존성 (없으면 태그 검색만 제공)
try:
    import chromadb
    VECTOR_AVAILABLE = True
except ImportError:
    VECTOR_AVAILABLE = False

mcp = FastMCP("context-search")


# ─────────────────────────────────────────
# ONNX 모델 캐시 보장
# ─────────────────────────────────────────

def _ensure_onnx_model():
    """번들된 ONNX 모델을 ChromaDB 캐시에 복사한다. 이미 있으면 건너뜀."""
    cache_dir = Path.home() / ".cache" / "chroma" / "onnx_models" / "all-MiniLM-L6-v2" / "onnx"
    if (cache_dir / "model.onnx").exists():
        return

    # 번들된 모델 경로: exe와 같은 폴더의 onnx_model/
    if getattr(sys, 'frozen', False):
        bundled = Path(sys.executable).parent / "onnx_model"
    else:
        bundled = Path(__file__).parent / "onnx_model"

    if not bundled.exists() or not (bundled / "model.onnx").exists():
        return  # 번들 없음 — ChromaDB가 자동 다운로드

    cache_dir.mkdir(parents=True, exist_ok=True)
    for f in bundled.iterdir():
        shutil.copy2(str(f), str(cache_dir / f.name))


if VECTOR_AVAILABLE:
    _ensure_onnx_model()


# ─────────────────────────────────────────
# 공통 파서
# ─────────────────────────────────────────

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


def _strip_comments_section(body: str) -> str:
    """## 코멘트 섹션을 제거한다 (벡터 임베딩용). 코멘트는 검색 품질에 영향을 주지 않도록 제외."""
    return re.sub(r'## 코멘트\s*\n.*', '', body, flags=re.DOTALL).strip()


# ─────────────────────────────────────────
# 벡터 DB 헬퍼
# ─────────────────────────────────────────

def _get_chroma_client(project_root: str):
    """ChromaDB PersistentClient를 반환한다."""
    db_path = Path(project_root) / ".claude" / "vector_db"
    db_path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(db_path))


def _get_collection(project_root: str):
    """context 컬렉션을 반환한다."""
    client = _get_chroma_client(project_root)
    return client.get_or_create_collection(
        name="context",
        metadata={"hnsw:space": "cosine"},
    )


# ─────────────────────────────────────────
# 태그 기반 검색 (기존)
# ─────────────────────────────────────────

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


# ─────────────────────────────────────────
# 벡터 기반 검색 (신규)
# ─────────────────────────────────────────

@mcp.tool()
def vector_search(query: str, project_root: str = ".", n_results: int = 5, category_filter: str = "") -> str:
    """
    의미 기반으로 .claude/context/ MD 파일을 검색한다.
    ChromaDB의 all-MiniLM-L6-v2 임베딩으로 코사인 유사도 검색을 수행한다.

    Args:
        query: 검색할 자연어 질문 (예: "전투 시스템의 데미지 계산")
        project_root: 프로젝트 루트 경로 (기본값: 현재 디렉토리)
        n_results: 반환할 결과 수 (기본값: 5)
        category_filter: 카테고리 필터 (예: "전투"). 빈 문자열이면 전체 검색
    """
    if not VECTOR_AVAILABLE:
        return json.dumps({"error": "chromadb가 설치되어 있지 않습니다. pip install chromadb"}, ensure_ascii=False)

    try:
        collection = _get_collection(project_root)
    except Exception as e:
        return json.dumps({"error": f"벡터 DB 접근 실패: {e}"}, ensure_ascii=False)

    count = collection.count()
    if count == 0:
        return json.dumps({
            "error": "벡터 인덱스가 비어 있습니다. rebuild_index를 먼저 실행하세요.",
            "hint": "rebuild_index(project_root) 호출로 인덱스를 구축할 수 있습니다.",
        }, ensure_ascii=False)

    where_filter = None
    if category_filter.strip():
        where_filter = {"category": {"$contains": category_filter.strip()}}

    results = collection.query(
        query_texts=[query],
        n_results=min(n_results, count),
        where=where_filter,
        include=["documents", "metadatas", "distances"],
    )

    output = []
    for i in range(len(results["ids"][0])):
        dist = results["distances"][0][i]
        similarity = round(1.0 - dist, 4)  # cosine distance → similarity
        output.append({
            "file": results["ids"][0][i],
            "similarity": similarity,
            "category": results["metadatas"][0][i].get("category", ""),
            "tags": [t for t in results["metadatas"][0][i].get("tags", "").split(",") if t],
            "content_preview": (results["documents"][0][i] or "")[:500],
        })

    return json.dumps(
        {"query": query, "count": len(output), "results": output},
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def combined_search(query: str, project_root: str = ".", tags: list[str] | None = None, n_results: int = 5, category_filter: str = "") -> str:
    """
    벡터 검색(의미 기반)과 태그 검색(키워드 기반)을 동시에 수행하여 결과를 병합한다.
    두 검색 방식의 장점을 결합하여 더 정확한 결과를 제공한다.

    Args:
        query: 검색할 자연어 질문 (예: "UMissionTask_Spawn 스포너 컴포넌트 연결")
        project_root: 프로젝트 루트 경로 (기본값: 현재 디렉토리)
        tags: 추가 태그 목록 (미지정 시 벡터 검색 결과에서 자동 추출)
        n_results: 벡터 검색 반환 수 (기본값: 5)
        category_filter: 카테고리 필터 (빈 문자열이면 전체 검색)
    """
    merged: dict[str, dict] = {}  # file -> result

    # ── 1) 벡터 검색 ──
    vector_results = []
    if VECTOR_AVAILABLE:
        try:
            raw = json.loads(vector_search(query, project_root, n_results, category_filter))
            vector_results = raw.get("results", [])
        except Exception:
            pass

    for r in vector_results:
        f = r["file"]
        merged[f] = {
            "file": f,
            "similarity": r.get("similarity", 0),
            "category": r.get("category", ""),
            "tags": r.get("tags", []),
            "source": "vector",
            "content_preview": r.get("content_preview", ""),
        }

    # ── 2) 태그 수집: 명시적 tags + 벡터 결과에서 추출 ──
    collected_tags = set()
    if tags:
        collected_tags.update(t.strip().lower() for t in tags if t.strip())
    # 벡터 상위 결과에서 태그 자동 추출
    for r in vector_results:
        for t in r.get("tags", []):
            if t.strip():
                collected_tags.add(t.strip().lower())

    # ── 3) 태그 검색 ──
    tag_results = []
    if collected_tags:
        try:
            raw = json.loads(search_context(list(collected_tags), project_root, match_all=False))
            tag_results = raw.get("results", [])
        except Exception:
            pass

    for r in tag_results:
        f = r["file"]
        if f in merged:
            # 이미 벡터 검색에 있으면 source를 both로 갱신, 태그 보강
            merged[f]["source"] = "both"
            existing_tags = set(merged[f].get("tags", []))
            existing_tags.update(r.get("tags", []))
            merged[f]["tags"] = list(existing_tags)
            if not merged[f].get("content_preview"):
                merged[f]["content_preview"] = r.get("body", "")[:500]
        else:
            merged[f] = {
                "file": f,
                "similarity": 0,
                "category": r.get("category", ""),
                "tags": r.get("tags", []),
                "matched_tags": r.get("matched_tags", []),
                "source": "tag",
                "content_preview": r.get("body", "")[:500],
            }

    # ── 4) 정렬: both > vector > tag, similarity 내림차순 ──
    source_order = {"both": 0, "vector": 1, "tag": 2}
    sorted_results = sorted(
        merged.values(),
        key=lambda x: (source_order.get(x["source"], 9), -x.get("similarity", 0)),
    )

    return json.dumps({
        "query": query,
        "tags_used": sorted(collected_tags),
        "count": len(sorted_results),
        "results": sorted_results,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def rebuild_index(project_root: str = ".") -> str:
    """
    .claude/context/ 의 모든 MD 파일을 벡터 인덱스로 재구축한다.
    기존 인덱스를 삭제하고 처음부터 다시 생성한다.

    Args:
        project_root: 프로젝트 루트 경로 (기본값: 현재 디렉토리)
    """
    if not VECTOR_AVAILABLE:
        return json.dumps({"error": "chromadb가 설치되어 있지 않습니다. pip install chromadb"}, ensure_ascii=False)

    context_dir = Path(project_root) / ".claude" / "context"
    if not context_dir.exists():
        return json.dumps({"error": f"컨텍스트 디렉토리가 없습니다: {context_dir}"}, ensure_ascii=False)

    try:
        client = _get_chroma_client(project_root)
        # 기존 컬렉션 삭제 후 재생성
        try:
            client.delete_collection("context")
        except Exception:
            pass

        collection = client.get_or_create_collection(
            name="context",
            metadata={"hnsw:space": "cosine"},
        )
    except Exception as e:
        return json.dumps({"error": f"벡터 DB 초기화 실패: {e}"}, ensure_ascii=False)

    ids, documents, metadatas = [], [], []

    for md_file in sorted(context_dir.rglob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue

        meta = _parse_frontmatter(content)
        body = _strip_comments_section(_extract_body(content))
        if not body.strip():
            continue

        doc_id = str(md_file.relative_to(context_dir)).replace("\\", "/")
        ids.append(doc_id)
        documents.append(body)
        metadatas.append({
            "tags": ",".join(meta.get("tags", [])),
            "category": meta.get("category", ""),
        })

    if not ids:
        return json.dumps({"status": "완료", "indexed_files": 0, "message": "인덱싱할 MD 파일이 없습니다."}, ensure_ascii=False)

    # ChromaDB는 내부적으로 배치 처리 (최대 5461개씩)
    BATCH = 5000
    for start in range(0, len(ids), BATCH):
        end = start + BATCH
        collection.add(
            ids=ids[start:end],
            documents=documents[start:end],
            metadatas=metadatas[start:end],
        )

    return json.dumps({
        "status": "완료",
        "indexed_files": len(ids),
        "db_path": str(Path(project_root) / ".claude" / "vector_db"),
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def index_status(project_root: str = ".") -> str:
    """
    벡터 인덱스 상태를 확인한다.

    Args:
        project_root: 프로젝트 루트 경로 (기본값: 현재 디렉토리)
    """
    if not VECTOR_AVAILABLE:
        return json.dumps({"status": "chromadb 미설치", "vector_available": False}, ensure_ascii=False)

    db_path = Path(project_root) / ".claude" / "vector_db"
    if not db_path.exists():
        return json.dumps({"status": "인덱스 없음", "indexed_documents": 0, "vector_available": True}, ensure_ascii=False)

    try:
        collection = _get_collection(project_root)
        count = collection.count()

        # context/ 내 MD 파일 수와 비교
        context_dir = Path(project_root) / ".claude" / "context"
        md_count = len(list(context_dir.rglob("*.md"))) if context_dir.exists() else 0

        return json.dumps({
            "status": "정상",
            "indexed_documents": count,
            "context_md_files": md_count,
            "needs_rebuild": count != md_count,
            "db_path": str(db_path),
            "vector_available": True,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"status": "오류", "error": str(e), "vector_available": True}, ensure_ascii=False)


def _upsert_files(project_root: str, md_relative_paths: list[str]) -> str:
    """지정된 MD 파일만 벡터 인덱스에 upsert한다. (CLI용)"""
    if not VECTOR_AVAILABLE:
        return json.dumps({"error": "chromadb 미설치"}, ensure_ascii=False)

    context_dir = Path(project_root) / ".claude" / "context"
    try:
        collection = _get_collection(project_root)
    except Exception as e:
        return json.dumps({"error": f"벡터 DB 접근 실패: {e}"}, ensure_ascii=False)

    ids, documents, metadatas = [], [], []
    for rel_path in md_relative_paths:
        md_file = context_dir / rel_path
        if not md_file.exists():
            continue
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue
        meta = _parse_frontmatter(content)
        body = _strip_comments_section(_extract_body(content))
        if not body.strip():
            continue
        ids.append(rel_path.replace("\\", "/"))
        documents.append(body)
        metadatas.append({
            "tags": ",".join(meta.get("tags", [])),
            "category": meta.get("category", ""),
        })

    if ids:
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)

    return json.dumps({"status": "완료", "upserted_files": len(ids)}, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    import sys

    # CLI 모드: watch.py에서 subprocess로 호출
    #   --rebuild <project_root>                         전체 재구축
    #   --upsert  <project_root> <md1> <md2> ...         증분 갱신
    #   --status  <project_root>                         상태 확인
    #   --search  <project_root> <query> [n_results]     통합 검색
    # (인수 없음)                                         MCP 서버 모드
    if len(sys.argv) > 1 and sys.argv[1].startswith("--"):
        cmd = sys.argv[1]
        root = sys.argv[2] if len(sys.argv) > 2 else "."
        if cmd == "--rebuild":
            print(rebuild_index(root))
        elif cmd == "--upsert":
            files = sys.argv[3:]
            print(_upsert_files(root, files))
        elif cmd == "--status":
            print(index_status(root))
        elif cmd == "--search":
            query = sys.argv[3] if len(sys.argv) > 3 else ""
            n = int(sys.argv[4]) if len(sys.argv) > 4 else 5
            if query:
                print(combined_search(query, root, n_results=n))
            else:
                print(json.dumps({"error": "검색어가 필요합니다: --search <root> <query> [n]"}, ensure_ascii=False))
        else:
            print(json.dumps({"error": f"알 수 없는 명령: {cmd}"}, ensure_ascii=False))
    else:
        mcp.run()
