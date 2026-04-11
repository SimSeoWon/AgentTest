"""
context_search MCP 서버 (3모드: 로컬 / 서버 / 클라이언트)

- 로컬 모드: 기존 ChromaDB 직접 사용 (MCP stdio)
- 서버 모드 (--serve): FastAPI HTTP 서버, DoubleBufferedIndex로 무중단 인덱싱
- 클라이언트 모드: HTTP로 중앙 서버에 위임 (MCP stdio)

모드 결정:
  --serve 인수 → 서버 모드
  CONTEXT_SERVER_URL 환경변수 또는 config.json → 클라이언트 모드
  그 외 → 로컬 모드 (기존 동작)
"""
import re
import os
import sys
import json
import shutil
import threading
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
    if getattr(sys, 'frozen', False):
        bundled = Path(sys.executable).parent / "onnx_model"
    else:
        bundled = Path(__file__).parent / "onnx_model"
    if not bundled.exists() or not (bundled / "model.onnx").exists():
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    for f in bundled.iterdir():
        shutil.copy2(str(f), str(cache_dir / f.name))


if VECTOR_AVAILABLE:
    _ensure_onnx_model()


# ─────────────────────────────────────────
# 원격 모드 (HTTP 클라이언트)
# ─────────────────────────────────────────

_SERVER_URL = os.environ.get("CONTEXT_SERVER_URL", "")


def _load_server_url():
    """config.json에서 context_server_url을 읽어 _SERVER_URL에 설정한다."""
    global _SERVER_URL
    if _SERVER_URL:
        return
    # exe 위치 기준으로 config.json 탐색
    if getattr(sys, 'frozen', False):
        base = Path(sys.executable).parent.parent.parent  # .claude/mcp/ → 프로젝트 루트
    else:
        base = Path(__file__).parent.parent.parent
    config_path = base / "config.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            _SERVER_URL = config.get("context_server_url", "")
        except Exception:
            pass


# --serve 모드가 아닐 때만 서버 URL 로드
if not (len(sys.argv) > 1 and sys.argv[1] == "--serve"):
    _load_server_url()


def _is_remote_mode() -> bool:
    """원격 서버 모드인지 확인한다."""
    return bool(_SERVER_URL)


def _remote_post(endpoint: str, payload: dict) -> str:
    """중앙 서버에 POST 요청을 보내고 JSON 문자열을 반환한다."""
    import urllib.request
    import urllib.error
    url = f"{_SERVER_URL.rstrip('/')}{endpoint}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.URLError as e:
        return json.dumps({
            "error": f"컨텍스트 서버 연결 실패: {e}",
            "server_url": url,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"HTTP 요청 오류: {e}"}, ensure_ascii=False)


def _remote_get(endpoint: str) -> str:
    """중앙 서버에 GET 요청을 보내고 JSON 문자열을 반환한다."""
    import urllib.request
    import urllib.error
    url = f"{_SERVER_URL.rstrip('/')}{endpoint}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.URLError as e:
        return json.dumps({
            "error": f"컨텍스트 서버 연결 실패: {e}",
            "server_url": url,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"HTTP 요청 오류: {e}"}, ensure_ascii=False)


# ─────────────────────────────────────────
# 검색 로그
# ─────────────────────────────────────────

def _log_search(project_root: str, result_files: list[str]):
    """검색 결과 문서 ID를 로그에 기록한다 (도메인 자동 승급용)."""
    if not result_files or len(result_files) < 2:
        return
    from datetime import datetime
    log_path = Path(project_root) / ".claude" / "search_log.jsonl"
    try:
        entry = json.dumps({
            "ts": datetime.now().isoformat(),
            "results": result_files,
        }, ensure_ascii=False)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass


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
# 로컬 모드: 벡터 DB 헬퍼
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
# MCP 도구 (로컬 + 원격 분기)
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
    if _is_remote_mode():
        return _remote_post("/api/v1/search/tags", {
            "tags": tags, "match_all": match_all,
        })

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
    if _is_remote_mode():
        return _remote_get("/api/v1/tags")

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
# 벡터 기반 검색
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
    if _is_remote_mode():
        return _remote_post("/api/v1/search/vector", {
            "query": query, "n_results": n_results,
            "category_filter": category_filter,
        })

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
    if _is_remote_mode():
        return _remote_post("/api/v1/search/combined", {
            "query": query, "tags": tags, "n_results": n_results,
            "category_filter": category_filter,
        })

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

    # 검색 로그 기록 (도메인 자동 승급용)
    _log_search(project_root, [r["file"] for r in sorted_results])

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
    if _is_remote_mode():
        return _remote_post("/api/v1/index/rebuild", {})

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
    if _is_remote_mode():
        return _remote_get("/api/v1/index/status")

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


@mcp.tool()
def impact_analysis(system_name: str, project_root: str = ".") -> str:
    """
    도메인 문서 기반으로 시스템 변경 시 영향받는 파일 목록과 확장 포인트를 분석한다.
    기능 추가/변경 계획 시 영향 범위를 사전에 파악하는 데 사용한다.

    Args:
        system_name: 분석할 시스템/도메인 이름 (예: "미션시스템", "전투")
        project_root: 프로젝트 루트 경로 (기본값: 현재 디렉토리)
    """
    context_dir = Path(project_root) / ".claude" / "context"
    domain_dir = context_dir / "_domains"
    if not domain_dir.exists():
        return json.dumps({
            "error": "도메인 문서가 없습니다. 검색 데이터가 충분히 쌓이면 자동 생성됩니다.",
        }, ensure_ascii=False)

    # 1) 도메인 문서 매칭
    matched_domain = None
    matched_content = ""
    for md_file in domain_dir.glob("*.md"):
        if md_file.name.startswith("_"):
            continue
        if system_name.lower() in md_file.stem.lower():
            matched_content = md_file.read_text(encoding="utf-8")
            matched_domain = md_file.stem
            break

    # 이름 매칭 실패 시 내용 검색
    if not matched_domain:
        for md_file in domain_dir.glob("*.md"):
            if md_file.name.startswith("_"):
                continue
            content = md_file.read_text(encoding="utf-8")
            if system_name.lower() in content.lower():
                matched_content = content
                matched_domain = md_file.stem
                break

    if not matched_domain:
        return json.dumps({
            "error": f"'{system_name}'과 일치하는 도메인을 찾을 수 없습니다.",
            "available_domains": [f.stem for f in domain_dir.glob("*.md") if not f.name.startswith("_")],
        }, ensure_ascii=False, indent=2)

    # 2) source_documents에서 1차 영향 파일 추출
    source_docs = re.findall(r'-\s+(\S+\.md)', matched_content)
    primary_files: list[dict] = []
    secondary_files: list[dict] = []

    for doc in source_docs:
        md_path = context_dir / doc
        if not md_path.exists():
            continue
        doc_content = md_path.read_text(encoding="utf-8")
        meta = _parse_frontmatter(doc_content)
        primary_files.append({
            "file": doc,
            "category": meta.get("category", ""),
            "tags": meta.get("tags", []),
        })

        # 3) related_classes로 2차 영향 파일 수집
        for cls_name, cls_path in meta.get("related_classes", {}).items():
            rel_md = str(Path(cls_path).with_suffix('.md'))
            if rel_md not in source_docs:
                secondary_files.append({
                    "file": rel_md,
                    "class": cls_name,
                    "referenced_by": doc,
                })

    # 4) 확장 포인트 추출
    ext_match = re.search(
        r'## 설계 패턴.*?\n(.*?)(?=\n## |\Z)',
        matched_content, re.DOTALL,
    )
    extension_points = ext_match.group(1).strip() if ext_match else ""

    return json.dumps({
        "domain": matched_domain,
        "primary_files": primary_files,
        "secondary_files": secondary_files,
        "extension_points": extension_points,
        "total_impact": len(primary_files) + len(secondary_files),
    }, ensure_ascii=False, indent=2)


def _upsert_files(project_root: str, md_relative_paths: list[str]) -> str:
    """지정된 MD 파일만 벡터 인덱스에 upsert한다. (CLI용)"""
    if _is_remote_mode():
        return _remote_post("/api/v1/index/upsert", {"files": md_relative_paths})

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


# ─────────────────────────────────────────
# 더블 버퍼링 인덱스 (서버 모드 전용)
# ─────────────────────────────────────────

class DoubleBufferedIndex:
    """Live/Work 이중 컬렉션 — 무중단 벡터 인덱싱.

    - 검색은 항상 Live 컬렉션에서 즉시 응답 (블로킹 없음)
    - 갱신은 Work 컬렉션에서 진행 후 완료 시 원자적 교체
    - 태그 캐시도 동시에 교체하여 일관성 보장
    - 하나의 ChromaDB에 context_a / context_b 두 컬렉션 사용
    """

    def __init__(self, claude_dir: Path):
        self._db_path = claude_dir / "vector_db"
        self._db_path.mkdir(parents=True, exist_ok=True)
        self._context_dir = claude_dir / "context"
        self._client = chromadb.PersistentClient(path=str(self._db_path))

        # 기존 단일 컬렉션('context') → 이중 컬렉션 마이그레이션
        self._migrate_old_collection()

        self._coll_a = self._client.get_or_create_collection(
            name="context_a", metadata={"hnsw:space": "cosine"},
        )
        self._coll_b = self._client.get_or_create_collection(
            name="context_b", metadata={"hnsw:space": "cosine"},
        )

        self._live_coll = self._coll_a
        self._work_coll = self._coll_b
        self._live_name = "a"

        self._swap_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._updating = False

        # 태그 캐시 (인메모리, 교체 시 원자적 스왑)
        self._live_tag_cache = self._build_tag_cache()

    def _migrate_old_collection(self):
        """기존 'context' 단일 컬렉션 → 'context_a'로 마이그레이션."""
        try:
            existing = {c.name for c in self._client.list_collections()}
        except Exception:
            return
        if "context" not in existing or "context_a" in existing:
            return
        old = self._client.get_collection("context")
        data = old.get(include=["documents", "metadatas", "embeddings"])
        new = self._client.create_collection(
            name="context_a", metadata={"hnsw:space": "cosine"},
        )
        if data["ids"]:
            BATCH = 5000
            for s in range(0, len(data["ids"]), BATCH):
                e = s + BATCH
                new.add(
                    ids=data["ids"][s:e], documents=data["documents"][s:e],
                    metadatas=data["metadatas"][s:e], embeddings=data["embeddings"][s:e],
                )
        self._client.delete_collection("context")

    def _build_tag_cache(self) -> dict:
        """context/ 디렉토리에서 태그 캐시를 빌드한다."""
        cache = {}
        if not self._context_dir.exists():
            return cache
        for md_file in sorted(self._context_dir.rglob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception:
                continue
            meta = _parse_frontmatter(content)
            if not meta:
                continue
            rel = str(md_file.relative_to(self._context_dir)).replace("\\", "/")
            cache[rel] = {
                "tags": meta.get("tags", []),
                "category": meta.get("category", ""),
                "related_classes": meta.get("related_classes", {}),
                "body": _extract_body(content),
            }
        return cache

    @property
    def live(self):
        """라이브 컬렉션 (검색용)."""
        return self._live_coll

    @property
    def tag_cache(self):
        """라이브 태그 캐시."""
        return self._live_tag_cache

    @property
    def is_updating(self):
        return self._updating

    def begin_update(self, fresh: bool = False):
        """갱신 시작. 워크 컬렉션을 반환한다.

        Args:
            fresh: True면 빈 컬렉션 (rebuild), False면 라이브 복사 후 upsert
        """
        self._write_lock.acquire()
        self._updating = True
        try:
            work_name = "context_b" if self._live_name == "a" else "context_a"

            # 워크 컬렉션 초기화
            try:
                self._client.delete_collection(work_name)
            except Exception:
                pass
            self._work_coll = self._client.create_collection(
                name=work_name, metadata={"hnsw:space": "cosine"},
            )

            if not fresh:
                # 라이브 → 워크 복사 (임베딩 포함, 재계산 불필요)
                data = self._live_coll.get(
                    include=["documents", "metadatas", "embeddings"],
                )
                if data["ids"]:
                    BATCH = 5000
                    for s in range(0, len(data["ids"]), BATCH):
                        e = s + BATCH
                        self._work_coll.add(
                            ids=data["ids"][s:e],
                            documents=data["documents"][s:e],
                            metadatas=data["metadatas"][s:e],
                            embeddings=data["embeddings"][s:e],
                        )

            return self._work_coll
        except Exception:
            self._updating = False
            self._write_lock.release()
            raise

    def commit_update(self):
        """워크 → 라이브 교체. 태그 캐시도 동시 갱신."""
        new_tag_cache = self._build_tag_cache()
        with self._swap_lock:
            self._live_coll, self._work_coll = self._work_coll, self._live_coll
            self._live_name = "b" if self._live_name == "a" else "a"
            self._live_tag_cache = new_tag_cache
        self._updating = False
        self._write_lock.release()

    def rollback_update(self):
        """갱신 실패 시 롤백. 라이브에 영향 없음."""
        self._updating = False
        self._write_lock.release()


# ─────────────────────────────────────────
# HTTP 서버 (--serve 모드)
# ─────────────────────────────────────────

def _run_http_server(project_root: str, port: int = 8100, host: str = "0.0.0.0"):
    """FastAPI HTTP 서버를 실행한다. DoubleBufferedIndex로 무중단 인덱싱 지원."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel
    import uvicorn

    claude_dir = Path(project_root) / ".claude"
    _index = DoubleBufferedIndex(claude_dir)

    app = FastAPI(title="AgentWatch Context Server")

    # ── 웹 UI ──

    from fastapi.responses import HTMLResponse

    @app.get("/", response_class=HTMLResponse)
    def web_ui():
        return _WEB_UI_HTML

    # ── 요청 모델 ──

    class CombinedSearchReq(BaseModel):
        query: str
        tags: list[str] | None = None
        n_results: int = 5
        category_filter: str = ""

    class VectorSearchReq(BaseModel):
        query: str
        n_results: int = 5
        category_filter: str = ""

    class TagSearchReq(BaseModel):
        tags: list[str]
        match_all: bool = False

    class UpsertReq(BaseModel):
        files: list[str]

    # ── 헬스 체크 ──

    @app.get("/api/v1/health")
    def health():
        return {
            "status": "ok",
            "project_root": project_root,
            "updating": _index.is_updating,
            "indexed_documents": _index.live.count(),
        }

    # ── 검색 엔드포인트 (Live 슬롯 — 항상 즉시 응답) ──

    @app.post("/api/v1/search/vector")
    def api_vector_search(req: VectorSearchReq):
        collection = _index.live
        count = collection.count()
        if count == 0:
            return {"query": req.query, "count": 0, "results": [],
                    "error": "벡터 인덱스가 비어 있습니다."}

        where_filter = None
        if req.category_filter.strip():
            where_filter = {"category": {"$contains": req.category_filter.strip()}}

        results = collection.query(
            query_texts=[req.query],
            n_results=min(req.n_results, count),
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )

        output = []
        for i in range(len(results["ids"][0])):
            dist = results["distances"][0][i]
            output.append({
                "file": results["ids"][0][i],
                "similarity": round(1.0 - dist, 4),
                "category": results["metadatas"][0][i].get("category", ""),
                "tags": [t for t in results["metadatas"][0][i].get("tags", "").split(",") if t],
                "content_preview": (results["documents"][0][i] or "")[:500],
            })
        return {"query": req.query, "count": len(output), "results": output}

    @app.post("/api/v1/search/tags")
    def api_tag_search(req: TagSearchReq):
        search_tags = {t.strip().lower() for t in req.tags}
        results = []
        for file, entry in _index.tag_cache.items():
            file_tags = {t.lower() for t in entry.get("tags", [])}
            matched = search_tags & file_tags
            if (req.match_all and matched == search_tags) or (not req.match_all and matched):
                results.append({
                    "file": file,
                    "tags": list(file_tags),
                    "category": entry.get("category", ""),
                    "related_classes": entry.get("related_classes", {}),
                    "matched_tags": list(matched),
                    "body": entry.get("body", ""),
                })
        return {"count": len(results), "results": results}

    @app.post("/api/v1/search/combined")
    def api_combined_search(req: CombinedSearchReq):
        merged = {}

        # 1) 벡터 검색 (Live 컬렉션)
        vector_results = []
        collection = _index.live
        if collection.count() > 0:
            try:
                where_filter = None
                if req.category_filter.strip():
                    where_filter = {"category": {"$contains": req.category_filter.strip()}}
                raw = collection.query(
                    query_texts=[req.query],
                    n_results=min(req.n_results, collection.count()),
                    where=where_filter,
                    include=["documents", "metadatas", "distances"],
                )
                for i in range(len(raw["ids"][0])):
                    dist = raw["distances"][0][i]
                    vector_results.append({
                        "file": raw["ids"][0][i],
                        "similarity": round(1.0 - dist, 4),
                        "category": raw["metadatas"][0][i].get("category", ""),
                        "tags": [t for t in raw["metadatas"][0][i].get("tags", "").split(",") if t],
                        "content_preview": (raw["documents"][0][i] or "")[:500],
                    })
            except Exception:
                pass

        for r in vector_results:
            merged[r["file"]] = {**r, "source": "vector"}

        # 2) 태그 수집
        collected_tags = set()
        if req.tags:
            collected_tags.update(t.strip().lower() for t in req.tags if t.strip())
        for r in vector_results:
            for t in r.get("tags", []):
                if t.strip():
                    collected_tags.add(t.strip().lower())

        # 3) 태그 검색 (Live 캐시)
        if collected_tags:
            for file, entry in _index.tag_cache.items():
                file_tags = {t.lower() for t in entry.get("tags", [])}
                matched = collected_tags & file_tags
                if matched:
                    if file in merged:
                        merged[file]["source"] = "both"
                        existing = set(merged[file].get("tags", []))
                        existing.update(entry.get("tags", []))
                        merged[file]["tags"] = list(existing)
                        if not merged[file].get("content_preview"):
                            merged[file]["content_preview"] = entry.get("body", "")[:500]
                    else:
                        merged[file] = {
                            "file": file, "similarity": 0,
                            "category": entry.get("category", ""),
                            "tags": entry.get("tags", []),
                            "matched_tags": list(matched),
                            "source": "tag",
                            "content_preview": entry.get("body", "")[:500],
                        }

        # 4) 정렬: both > vector > tag, similarity 내림차순
        source_order = {"both": 0, "vector": 1, "tag": 2}
        sorted_results = sorted(
            merged.values(),
            key=lambda x: (source_order.get(x["source"], 9), -x.get("similarity", 0)),
        )
        # 검색 로그 기록
        _log_search(project_root, [r["file"] for r in sorted_results])

        return {
            "query": req.query,
            "tags_used": sorted(collected_tags),
            "count": len(sorted_results),
            "results": sorted_results,
        }

    @app.get("/api/v1/tags")
    def api_list_tags():
        tag_counts = {}
        for entry in _index.tag_cache.values():
            for tag in entry.get("tags", []):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        sorted_tags = sorted(tag_counts.items(), key=lambda x: -x[1])
        return {"total_tags": len(sorted_tags), "tags": dict(sorted_tags)}

    @app.get("/api/v1/index/status")
    def api_index_status():
        count = _index.live.count()
        md_count = len(_index.tag_cache)
        return {
            "status": "정상",
            "indexed_documents": count,
            "context_md_files": md_count,
            "needs_rebuild": count != md_count,
            "updating": _index.is_updating,
            "vector_available": True,
        }

    # ── 인덱싱 엔드포인트 (Work 슬롯 → 교체) ──

    @app.post("/api/v1/index/upsert")
    def api_upsert(req: UpsertReq):
        context_dir = Path(project_root) / ".claude" / "context"
        try:
            work_coll = _index.begin_update(fresh=False)

            ids, documents, metadatas = [], [], []
            for rel_path in req.files:
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
                work_coll.upsert(ids=ids, documents=documents, metadatas=metadatas)

            _index.commit_update()
            return {"status": "완료", "upserted_files": len(ids)}
        except Exception as e:
            _index.rollback_update()
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.post("/api/v1/index/rebuild")
    def api_rebuild():
        context_dir = Path(project_root) / ".claude" / "context"
        if not context_dir.exists():
            return {"error": f"컨텍스트 디렉토리가 없습니다: {context_dir}"}

        try:
            work_coll = _index.begin_update(fresh=True)

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
                ids.append(str(md_file.relative_to(context_dir)).replace("\\", "/"))
                documents.append(body)
                metadatas.append({
                    "tags": ",".join(meta.get("tags", [])),
                    "category": meta.get("category", ""),
                })

            if ids:
                BATCH = 5000
                for s in range(0, len(ids), BATCH):
                    e = s + BATCH
                    work_coll.add(
                        ids=ids[s:e], documents=documents[s:e],
                        metadatas=metadatas[s:e],
                    )

            _index.commit_update()
            return {"status": "완료", "indexed_files": len(ids)}
        except Exception as e:
            _index.rollback_update()
            return JSONResponse(status_code=500, content={"error": str(e)})

    # ── 서버 시작 ──
    print(f"컨텍스트 HTTP 서버 시작: {host}:{port}")
    print(f"프로젝트 루트: {project_root}")
    uvicorn.run(app, host=host, port=port, log_level="info")


# ─────────────────────────────────────────
# 웹 UI HTML
# ─────────────────────────────────────────

_WEB_UI_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AgentWatch Context Server</title>
<style>
  :root { --bg: #0d1117; --surface: #161b22; --border: #30363d; --text: #e6edf3; --dim: #8b949e; --accent: #58a6ff; --green: #3fb950; --orange: #d29922; --red: #f85149; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; background: var(--bg); color: var(--text); line-height: 1.5; }
  .container { max-width: 1200px; margin: 0 auto; padding: 24px; }
  h1 { font-size: 1.5rem; margin-bottom: 8px; }
  h1 span { color: var(--accent); }
  .subtitle { color: var(--dim); margin-bottom: 24px; font-size: 0.9rem; }

  /* 상태 카드 */
  .status-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
  .card .label { color: var(--dim); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; }
  .card .value { font-size: 1.5rem; font-weight: 600; margin-top: 4px; }
  .card .value.ok { color: var(--green); }
  .card .value.warn { color: var(--orange); }

  /* 검색 */
  .search-box { display: flex; gap: 8px; margin-bottom: 16px; }
  .search-box input { flex: 1; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 10px 14px; color: var(--text); font-size: 0.95rem; outline: none; }
  .search-box input:focus { border-color: var(--accent); }
  .search-box button { background: var(--accent); color: #fff; border: none; border-radius: 6px; padding: 10px 20px; cursor: pointer; font-weight: 600; font-size: 0.95rem; white-space: nowrap; }
  .search-box button:hover { opacity: 0.9; }
  .search-opts { display: flex; gap: 16px; margin-bottom: 20px; align-items: center; flex-wrap: wrap; }
  .search-opts label { color: var(--dim); font-size: 0.85rem; }
  .search-opts input, .search-opts select { background: var(--surface); border: 1px solid var(--border); border-radius: 4px; padding: 4px 8px; color: var(--text); font-size: 0.85rem; }

  /* 탭 */
  .tabs { display: flex; gap: 0; margin-bottom: 20px; border-bottom: 1px solid var(--border); }
  .tab { padding: 8px 20px; cursor: pointer; color: var(--dim); border-bottom: 2px solid transparent; font-size: 0.9rem; }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--accent); border-bottom-color: var(--accent); }
  .tab-content { display: none; }
  .tab-content.active { display: block; }

  /* 결과 */
  .result { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-bottom: 12px; }
  .result-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
  .result-file { color: var(--accent); font-weight: 600; font-size: 0.95rem; }
  .result-score { color: var(--dim); font-size: 0.8rem; }
  .result-category { color: var(--orange); font-size: 0.8rem; margin-bottom: 6px; }
  .result-tags { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
  .tag { background: #1f6feb22; color: var(--accent); border: 1px solid #1f6feb44; border-radius: 12px; padding: 2px 10px; font-size: 0.75rem; cursor: pointer; }
  .tag:hover { background: #1f6feb44; }
  .result-body { color: var(--dim); font-size: 0.85rem; white-space: pre-wrap; max-height: 200px; overflow-y: auto; }

  /* 문서 목록 */
  .doc-list { display: grid; gap: 8px; }
  .doc-item { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 12px 16px; display: flex; justify-content: space-between; align-items: center; cursor: pointer; }
  .doc-item:hover { border-color: var(--accent); }
  .doc-name { color: var(--accent); font-size: 0.9rem; }
  .doc-tags { display: flex; gap: 4px; flex-wrap: wrap; }

  /* 태그 클라우드 */
  .tag-cloud { display: flex; flex-wrap: wrap; gap: 8px; }
  .tag-cloud .tag { font-size: 0.85rem; padding: 4px 12px; }
  .tag-count { color: var(--dim); font-size: 0.75rem; margin-left: 4px; }

  .empty { color: var(--dim); text-align: center; padding: 40px; }
  .loading { color: var(--dim); text-align: center; padding: 20px; }
</style>
</head>
<body>
<div class="container">
  <h1><span>AgentWatch</span> Context Server</h1>
  <p class="subtitle" id="projectRoot">Loading...</p>

  <div class="status-row" id="statusCards">
    <div class="card"><div class="label">Status</div><div class="value" id="sStatus">-</div></div>
    <div class="card"><div class="label">Documents</div><div class="value" id="sDocs">-</div></div>
    <div class="card"><div class="label">Tags</div><div class="value" id="sTags">-</div></div>
    <div class="card"><div class="label">Updating</div><div class="value" id="sUpdating">-</div></div>
  </div>

  <div class="tabs">
    <div class="tab active" data-tab="search">Search</div>
    <div class="tab" data-tab="docs">Documents</div>
    <div class="tab" data-tab="tags">Tags</div>
  </div>

  <!-- Search Tab -->
  <div class="tab-content active" id="tab-search">
    <div class="search-box">
      <input type="text" id="searchQuery" placeholder="Search query..." />
      <button onclick="doSearch()">Search</button>
    </div>
    <div class="search-opts">
      <label>Results: <input type="number" id="searchN" value="5" min="1" max="50" style="width:60px"></label>
      <label>Category: <input type="text" id="searchCat" placeholder="filter..." style="width:120px"></label>
      <label>Tags: <input type="text" id="searchTags" placeholder="comma separated" style="width:160px"></label>
    </div>
    <div id="searchResults"><div class="empty">Enter a query to search the context database.</div></div>
  </div>

  <!-- Documents Tab -->
  <div class="tab-content" id="tab-docs">
    <div class="search-box">
      <input type="text" id="docFilter" placeholder="Filter documents..." oninput="filterDocs()" />
    </div>
    <div id="docList"><div class="loading">Loading documents...</div></div>
  </div>

  <!-- Tags Tab -->
  <div class="tab-content" id="tab-tags">
    <div id="tagCloud"><div class="loading">Loading tags...</div></div>
  </div>
</div>

<script>
const API = '';

// ── Tabs ──
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
  });
});

// ── Status ──
async function loadStatus() {
  try {
    const [health, status, tags] = await Promise.all([
      fetch(API + '/api/v1/health').then(r => r.json()),
      fetch(API + '/api/v1/index/status').then(r => r.json()),
      fetch(API + '/api/v1/tags').then(r => r.json()),
    ]);
    document.getElementById('projectRoot').textContent = health.project_root || '';
    document.getElementById('sStatus').textContent = health.status || 'unknown';
    document.getElementById('sStatus').className = 'value' + (health.status === 'ok' ? ' ok' : ' warn');
    document.getElementById('sDocs').textContent = status.indexed_documents || 0;
    document.getElementById('sTags').textContent = tags.total_tags || 0;
    document.getElementById('sUpdating').textContent = health.updating ? 'Yes' : 'No';
    document.getElementById('sUpdating').className = 'value' + (health.updating ? ' warn' : ' ok');

    // tags cloud
    renderTagCloud(tags.tags || {});
  } catch(e) {
    document.getElementById('sStatus').textContent = 'Error';
    document.getElementById('sStatus').className = 'value warn';
  }
}

// ── Search ──
document.getElementById('searchQuery').addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });

async function doSearch() {
  const query = document.getElementById('searchQuery').value.trim();
  if (!query) return;
  const n = parseInt(document.getElementById('searchN').value) || 10;
  const cat = document.getElementById('searchCat').value.trim();
  const tagsRaw = document.getElementById('searchTags').value.trim();
  const tags = tagsRaw ? tagsRaw.split(',').map(t => t.trim()).filter(Boolean) : null;

  document.getElementById('searchResults').innerHTML = '<div class="loading">Searching...</div>';

  try {
    const body = { query, n_results: n };
    if (cat) body.category_filter = cat;
    if (tags) body.tags = tags;

    const resp = await fetch(API + '/api/v1/search/combined', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    });
    const data = await resp.json();
    renderResults(data);
  } catch(e) {
    document.getElementById('searchResults').innerHTML = '<div class="empty">Search failed: ' + e.message + '</div>';
  }
}

function renderResults(data) {
  const el = document.getElementById('searchResults');
  const results = data.results || [];
  if (!results.length) { el.innerHTML = '<div class="empty">No results found.</div>'; return; }

  el.innerHTML = results.map((r, i) => {
    const tags = (r.tags || []).map(t => '<span class="tag" onclick="searchByTag(\'' + t + '\')">' + t + '</span>').join('');
    const score = r.similarity != null && r.similarity > 0 ? (r.similarity * 100).toFixed(1) + '%' : (r.source || '');
    const preview = (r.content_preview || r.body || '').substring(0, 400);
    return '<div class="result">' +
      '<div class="result-header"><span class="result-file">' + (r.file || r.id || '?') + '</span><span class="result-score">' + score + '</span></div>' +
      (r.category ? '<div class="result-category">' + r.category + '</div>' : '') +
      (tags ? '<div class="result-tags">' + tags + '</div>' : '') +
      '<div class="result-body">' + escHtml(preview) + '</div>' +
    '</div>';
  }).join('');
}

function searchByTag(tag) {
  document.getElementById('searchTags').value = tag;
  const q = document.getElementById('searchQuery').value.trim();
  if (!q) document.getElementById('searchQuery').value = tag;
  doSearch();
}

// ── Documents ──
let allDocs = [];

async function loadDocs() {
  try {
    const tags = await fetch(API + '/api/v1/tags').then(r => r.json());
    // tag cache has document info, build doc list from combined search with broad query
    // Use index status + tags to build doc list
    const status = await fetch(API + '/api/v1/index/status').then(r => r.json());

    // Fetch all docs via vector search with a generic query
    const resp = await fetch(API + '/api/v1/search/vector', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ query: ' ', n_results: status.indexed_documents || 100 }),
    });
    const data = await resp.json();
    allDocs = (data.results || []).map(r => ({
      file: r.file || r.id || '?',
      tags: r.tags || [],
      category: r.category || '',
      preview: (r.content_preview || r.body || '').substring(0, 200),
    }));
    allDocs.sort((a, b) => a.file.localeCompare(b.file));
    renderDocs(allDocs);
  } catch(e) {
    document.getElementById('docList').innerHTML = '<div class="empty">Failed to load documents.</div>';
  }
}

function renderDocs(docs) {
  const el = document.getElementById('docList');
  if (!docs.length) { el.innerHTML = '<div class="empty">No documents found.</div>'; return; }
  el.innerHTML = docs.map(d => {
    const tags = d.tags.slice(0, 5).map(t => '<span class="tag" onclick="searchByTag(\'' + t + '\')">' + t + '</span>').join('');
    return '<div class="doc-item" onclick="searchDoc(\'' + escAttr(d.file) + '\')">' +
      '<span class="doc-name">' + escHtml(d.file) + '</span>' +
      '<span class="doc-tags">' + tags + '</span>' +
    '</div>';
  }).join('');
}

function filterDocs() {
  const q = document.getElementById('docFilter').value.toLowerCase();
  renderDocs(q ? allDocs.filter(d => d.file.toLowerCase().includes(q) || d.tags.some(t => t.toLowerCase().includes(q))) : allDocs);
}

function searchDoc(file) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  document.querySelector('[data-tab="search"]').classList.add('active');
  document.getElementById('tab-search').classList.add('active');
  document.getElementById('searchQuery').value = file.replace('.md', '');
  doSearch();
}

// ── Tags ──
function renderTagCloud(tags) {
  const el = document.getElementById('tagCloud');
  const entries = Object.entries(tags).sort((a, b) => b[1] - a[1]);
  if (!entries.length) { el.innerHTML = '<div class="empty">No tags found.</div>'; return; }
  el.innerHTML = '<div class="tag-cloud">' + entries.map(([tag, count]) =>
    '<span class="tag" onclick="searchByTag(\'' + tag + '\')">' + escHtml(tag) + '<span class="tag-count">' + count + '</span></span>'
  ).join('') + '</div>';
}

// ── Utils ──
function escHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function escAttr(s) { return s.replace(/'/g, "\\'").replace(/"/g, '&quot;'); }

// ── Init ──
loadStatus();
loadDocs();
</script>
</body>
</html>
"""


# ─────────────────────────────────────────
# CLI 엔트리포인트
# ─────────────────────────────────────────

if __name__ == "__main__":
    # CLI 모드: watch.py에서 subprocess로 호출
    #   --serve   <project_root> [port] [host]           HTTP 서버 모드
    #   --rebuild <project_root>                         전체 재구축
    #   --upsert  <project_root> <md1> <md2> ...         증분 갱신
    #   --status  <project_root>                         상태 확인
    #   --search  <project_root> <query> [n_results]     통합 검색
    # (인수 없음)                                         MCP 서버 모드
    if len(sys.argv) > 1 and sys.argv[1].startswith("--"):
        cmd = sys.argv[1]

        if cmd == "--serve":
            root = sys.argv[2] if len(sys.argv) > 2 else "."
            port = int(sys.argv[3]) if len(sys.argv) > 3 else 8100
            host = sys.argv[4] if len(sys.argv) > 4 else "0.0.0.0"
            _run_http_server(root, port, host)
        else:
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
