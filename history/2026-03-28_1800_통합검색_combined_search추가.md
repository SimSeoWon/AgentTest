# 통합 검색 combined_search 툴 추가

## 작업 개요
벡터 검색(vector_search)과 태그 검색(search_context)을 항상 병행 수행하는 `combined_search` 통합 검색 툴을 추가.

## 배경
- `all-MiniLM-L6-v2` 모델이 영어 중심이라 한국어 의미 검색 정확도가 낮음
- 벡터 검색만으로는 놓치는 결과를 태그 검색이 보완, 그 반대도 마찬가지
- 사용자가 매번 두 툴을 따로 호출하는 것은 비효율적

## 동작 방식
1. 벡터 검색(의미 기반) 수행
2. 벡터 결과에서 태그 자동 추출 + 사용자 지정 태그 병합
3. 태그 검색(키워드 기반) 수행
4. 두 결과를 병합·중복 제거하여 반환 (both > vector > tag 순 정렬)

## 생성/수정 파일 목록
| 파일 | 변경 내용 |
|------|-----------|
| `mcp/context_search/server.py` | `combined_search()` MCP 툴 함수 추가 |
| `watcher/agent_templates.py` | SKILL_INDEX 검색 섹션 갱신, PROJECT_CLAUDE_MD_SECTION MCP 툴 목록에 `combined_search` 추가 |
| `CLAUDE.md` | MCP 서버 도구 목록, RAG 검색 섹션 갱신 |

## 주요 설계 결정
- 기존 `vector_search`, `search_context` 툴은 그대로 유지 (하위 호환)
- `combined_search`가 내부적으로 두 툴을 호출하는 래퍼 방식
- 태그 미지정 시 벡터 결과에서 자동 추출하여 태그 검색도 수행
