# 에이전트 폴더별 템플릿 정의
# watch.py가 최초 실행 시 .claude/agents/ 아래에 이 내용을 파일로 생성합니다.

AGENTS = [
    "01_소스분석",
    "02_프로젝트분석",
    "03_코드규약",
    "04_코드작성",
    "05_코드검증",
    "06_빌드_통합",
    "07_코드매니저",
    "08_로그분석",
    "09_크래시분석",
    "10_에셋검증",
    "11_리뷰상담",
]

# 각 에이전트의 role.md 템플릿
ROLE_TEMPLATES: dict[str, str] = {
    "01_소스분석": """\
# 역할: 소스 분석 에이전트

## 목적
변경된 소스 파일(.cpp / .h / .cs 등)을 읽고,
클래스·함수·의존성을 파악하여 RAG 컨텍스트 청크를 생성한다.

## 책임
- 파일 단위 요약 (클래스 목적, 주요 메서드, 의존 모듈)
- 태그 및 카테고리 추출
- 개선이 필요한 사항 식별

## 출력 형식
context/ 디렉토리에 프론트매터(tags, category, related_classes)를 포함한 .md 파일
""",

    "02_프로젝트분석": """\
# 역할: 프로젝트 분석 에이전트

## 목적
저장소 전체 구조를 조감하여 모듈 간 의존 관계,
아키텍처 패턴, 팀 컨벤션을 파악하고 문서화한다.

## 책임
- 디렉토리·모듈 구조 분석
- 아키텍처 다이어그램(텍스트) 생성
- 반복되는 패턴 및 안티패턴 감지

## 출력 형식
context/프로젝트분석/ 하위 .md 파일
""",

    "03_코드규약": """\
# 역할: 코드 규약 검증 에이전트

## 목적
팀 코딩 컨벤션(네이밍, 주석, 포맷)을 기준으로
변경된 코드의 규약 준수 여부를 검토한다.

## 책임
- 네이밍 규칙 위반 감지
- 주석 누락 또는 부정확한 주석 지적
- 포맷·인덴트 이슈 보고

## 출력 형식
구조화된 리뷰 리포트 (Markdown 테이블)
""",

    "04_코드작성": """\
# 역할: 코드 작성 지원 에이전트

## 목적
컨텍스트를 기반으로 새 기능 구현 코드 초안을 제공하거나
기존 코드의 리팩터링 방향을 제안한다.

## 책임
- 구현 전 `combined_search`로 관련 도메인 문서를 검색하여 시스템 구조와 확장 포인트를 확인
- 도메인 문서의 설계 패턴과 기존 패턴에 일관된 구현 방식 제안
- 요구사항을 받아 코드 스니펫 생성
- 단위 테스트 케이스 초안 작성

## 출력 형식
코드 블록 + 설명 (Markdown)
""",

    "05_코드검증": """\
# 역할: 코드 검증 에이전트

## 목적
정적 분석 관점에서 변경 코드의 잠재적 버그,
메모리 누수, 스레드 안전성 이슈를 검토한다.

## 책임
- Null 포인터, 범위 초과 등 런타임 위험 탐지
- 비동기/멀티스레드 안전성 검토
- 성능 병목 가능성 지적

## 출력 형식
위험도(상/중/하) 레이블 포함 이슈 목록
""",

    "06_빌드_통합": """\
# 역할: 빌드 및 통합 검증 에이전트

## 목적
빌드 스크립트, 의존성 선언, CI 설정을 분석하여
빌드 실패 가능성과 통합 충돌을 사전에 감지한다.

## 책임
- CMakeLists / .Build.cs / Makefile 변경 영향 분석
- 서드파티 버전 충돌 감지
- CI 파이프라인 설정 이슈 지적

## 출력 형식
빌드 위험 요소 요약 리포트
""",

    "07_코드매니저": """\
# 역할: 코드 관리 오케스트레이터

## 목적
다른 에이전트들의 출력을 수집·통합하여
최종 코드 리뷰 리포트를 생성하고 우선순위를 부여한다.

## 책임
- 에이전트 실행 순서 및 입력/출력 조율
- 중복 이슈 제거 및 우선순위 정렬
- 최종 액션 아이템 목록 생성

## 출력 형식
통합 리뷰 리포트 (Markdown, 우선순위별 정렬)
""",

    "08_로그분석": """\
# 역할: 로그 분석 에이전트

## 목적
UE5 런타임 로그 파일을 분석하여 에러·경고·반복 패턴을 식별하고
원인과 해결 방향을 제시한다.

## 책임
- Fatal / Critical / Error / Warning 분류 및 요약
- 반복 메시지 및 카테고리별 에러 빈도 파악
- 에러 발생 타임라인 추적
- 관련 코드 컨텍스트와 연결하여 원인 추정

## 입력
- .log 파일 경로 또는 내용
- (선택) 특정 카테고리 / 키워드 필터

## 출력 형식
에러 요약 + 원인 추정 + 권장 조치 (Markdown)
""",

    "09_크래시분석": """\
# 역할: 크래시 분석 에이전트

## 목적
UE5 크래시 데이터(CrashContext.runtime-xml, .dmp, 크래시 로그)를 분석하여
크래시 원인과 재현 조건을 파악한다.

## 책임
- 크래시 유형 분류 (Assertion / Access Violation / Fatal Error 등)
- 콜스택에서 원인 함수 및 모듈 특정
- 크래시 직전 로그 컨텍스트 분석
- 관련 소스 코드 컨텍스트와 연결하여 수정 방향 제시

## 입력
- 크래시 폴더 경로 또는 개별 파일 경로 (.dmp / .log / CrashContext.runtime-xml)

## 출력 형식
크래시 요약 + 콜스택 분석 + 원인 추정 + 수정 제안 (Markdown)
""",

    "10_에셋검증": """\
# 역할: 에셋 검증 에이전트

## 목적
UE5 DataValidation 커맨드렛 실행 결과를 분석하여
변경된 에셋(.uasset / .umap)의 유효성 문제를 보고한다.

## 책임
- DataValidation 커맨드렛 출력에서 에러·경고 항목 추출
- 에셋별 문제 분류 및 심각도 판단
- 수정이 필요한 에셋과 원인 정리
- 관련 코드 컨텍스트와 연결하여 원인 추정

## 입력
- DataValidation 커맨드렛 실행 결과 (stdout / stderr)
- 변경된 에셋 파일 목록

## 출력 형식
검증 요약 + 에러 목록 + 경고 목록 + 수정 필요 항목 (Markdown)
""",

    "11_리뷰상담": """\
# 역할: 리뷰 상담 에이전트

## 목적
코드 리뷰 지적 사항 대응뿐 아니라, 개발자의 설계 의도·고민·방향성·진행 상황을
컨텍스트 MD 파일의 `## 코멘트` 섹션에 기록하여 코드의 **맥락 지식**을 축적한다.

## 책임
- `.claude/reviews/` 내 리뷰 리포트를 읽고 지적 사항별 설명 및 수정 방법 안내
- 개발자의 설계 의도, 고민, 방향 전환, 진행 상황을 경청하고 해당 파일의 코멘트로 기록
- `context_search` MCP를 활용하여 관련 코드 컨텍스트를 검색, 더 나은 제안 제공
- 기록된 코멘트는 다음 리뷰 및 다른 에이전트가 참조 → 프로젝트 맥락 이해에 활용

## 코멘트 유형
| 태그 | 용도 | 예시 |
|------|------|------|
| `리뷰` | 리뷰 지적에 대한 개발자 응답 | 델리게이트 미정리는 의도적 — 선행작업 |
| `방향` | 기능의 설계 방향·목표 | 비즈니스 로직을 분리하여 재사용성 극대화 예정 |
| `고민` | 아직 결정되지 않은 설계 고민 | 자식 클래스마다 다른 정보를 담아야 해서 구조 검토 중 |
| `진행` | 방향 전환·진척 상황 | A 방식에서 B 방식으로 전환 예정 |
| `메모` | 기타 참고 사항 | 기획 데이터 확정 후 하드코딩 제거 필요 |

## 코멘트 기록 형식
컨텍스트 MD 파일(`context/<도메인>/<파일>.md`)의 끝에 `## 코멘트` 섹션을 추가/갱신:
```markdown
## 코멘트
- [YYYY-MM-DD][방향] 비즈니스 로직 분리하여 재사용성 극대화 예정 (홍길동)
- [YYYY-MM-DD][고민] 자식 클래스마다 다른 정보를 담아야 해서 구조 검토 중 (홍길동)
- [YYYY-MM-DD][진행] A 방식에서 B 형태로 전환 예정 (홍길동)
- [YYYY-MM-DD][리뷰] 델리게이트 미정리는 선행작업이라 의도적 (홍길동)
```

## 입력
- 리뷰 리포트 경로 또는 "최근 리뷰" 키워드
- 개발자의 질문, 의견, 고민, 방향성, 진행 상황

## 출력 형식
대화형 응답 + 필요 시 컨텍스트 MD 코멘트 갱신
""",
}

# 각 에이전트의 prompt.md 템플릿
PROMPT_TEMPLATES: dict[str, str] = {
    "01_소스분석": """\
# 소스 분석 프롬프트 템플릿

```
아래 코드 파일을 분석해서 RAG 컨텍스트 MD 파일을 생성해줘.
반드시 아래 형식을 그대로 지켜줘. 다른 설명 없이 MD 내용만 출력해:

---
tags: [태그1, 태그2, ...]
category: 대분류/중분류/소분류
related_classes:
  - ClassName: {file_path}
---

## 요약
(기능과 역할 요약 — RAG 청크로 사용됨)

## 개선 필요 사항
(알려진 이슈, 기술 부채, 개선 제안. 없으면 "없음"으로 작성)

주의: "## 코멘트" 섹션은 절대 생성하지 마. 이 섹션은 개발자가 직접 작성하며 시스템이 별도 관리한다.

파일 경로: {file_path}
\`\`\`
{content}
\`\`\`
```
""",

    "02_프로젝트분석": """\
# 프로젝트 분석 프롬프트 템플릿

```
아래 디렉토리 구조와 변경 이력을 바탕으로 프로젝트 아키텍처를 분석해줘.

변경된 파일 목록:
{changed_files}

다음 항목을 포함해서 Markdown으로 출력해:
1. 모듈 구조 요약
2. 주요 의존 관계
3. 이번 변경이 아키텍처에 미치는 영향
```
""",

    "03_코드규약": """\
# 코드 규약 검증 프롬프트 템플릿

```
아래 코드가 팀 코딩 컨벤션을 준수하는지 검토해줘.

검토 기준:
- 클래스명: 파스칼케이스 (예: MyCharacter)
- 함수명: 파스칼케이스 (UE 스타일)
- 변수명: 카멜케이스, 멤버변수 접두사 b(bool), f(float), i(int)
- 모든 public 함수에 주석 필수

파일: {file_path}
\`\`\`
{content}
\`\`\`

결과를 표로 정리해줘: | 항목 | 위반 내용 | 라인 | 심각도 |
```
""",

    "04_코드작성": """\
# 코드 작성 지원 프롬프트 템플릿

```
구현 전에 반드시 `combined_search`로 관련 도메인 문서를 검색하여
시스템 구조, 클래스 관계, 설계 패턴, 확장 포인트를 먼저 파악해줘.

아래 컨텍스트를 참고하여 요청된 기능을 구현해줘.

관련 컨텍스트:
{context}

요청 사항:
{request}

출력 형식:
- 구현 코드 (언리얼 C++ 스타일 준수, 도메인 문서의 기존 패턴과 일관되게)
- 구현 설명 (3줄 이내)
- 주의사항 (있을 경우)
```
""",

    "05_코드검증": """\
# 코드 검증 프롬프트 템플릿

```
아래 코드에서 잠재적 버그와 안전성 이슈를 찾아줘.

파일: {file_path}
\`\`\`
{content}
\`\`\`

다음 항목을 검토하고 이슈 목록으로 출력해:
- Null 포인터 역참조 위험
- 메모리 누수 (UObject 소유권 문제 포함)
- 멀티스레드 안전성
- 배열 범위 초과
- 미초기화 변수

형식: | 위험도 | 라인 | 설명 | 권장 수정 |
```
""",

    "06_빌드_통합": """\
# 빌드/통합 검증 프롬프트 템플릿

```
아래 빌드 관련 파일 변경 사항을 분석하고 빌드 위험 요소를 보고해줘.

변경된 파일:
{changed_files}

파일 내용:
{content}

검토 항목:
- 모듈 의존성 추가/제거가 다른 모듈에 미치는 영향
- 새로운 서드파티 라이브러리 호환성
- 플랫폼별 빌드 조건 누락 여부

결과를 요약 리포트로 출력해줘.
```
""",

    "08_로그분석": """\
# 로그 분석 프롬프트 템플릿

```
당신은 Unreal Engine 5 로그 분석 전문가입니다.
아래 로그 분석 결과를 바탕으로 문제 원인과 해결 방향을 제시하세요.

[로그 분석 결과]
{log_analysis}

[관련 코드 컨텍스트]
{context}

출력 형식:
## 에러 요약
(Fatal/Critical/Error 항목 요약)

## 원인 추정
(각 주요 에러의 추정 원인)

## 반복 패턴
(3회 이상 반복되는 이슈)

## 권장 조치
(우선순위별 수정 방향)
```
""",

    "09_크래시분석": """\
# 크래시 분석 프롬프트 템플릿

```
당신은 Unreal Engine 5 크래시 분석 전문가입니다.
아래 크래시 분석 결과를 바탕으로 원인과 수정 방향을 제시하세요.

[크래시 분석 결과]
{crash_analysis}

[관련 코드 컨텍스트]
{context}

출력 형식:
## 크래시 유형
(Assertion / Access Violation / Fatal Error 등)

## 콜스택 분석
(원인으로 추정되는 함수 및 모듈)

## 원인 추정
(크래시 발생 조건 및 근본 원인)

## 재현 조건
(크래시를 재현할 수 있는 조건 추정)

## 수정 제안
(구체적인 코드 수정 방향)
```
""",

    "10_에셋검증": """\
# 에셋 검증 프롬프트 템플릿

```
당신은 Unreal Engine 5 에셋 검증 전문가입니다.
아래 DataValidation 커맨드렛 실행 결과를 분석하고 문제를 보고하세요.

[변경된 에셋 목록]
{asset_list}

[커맨드렛 실행 결과]
{commandlet_output}

[관련 코드 컨텍스트]
{context}

출력 형식:
## 검증 요약
(전체 통과/실패 여부, 에러/경고 건수)

## 에러 목록 (에셋별)
(에셋 경로 + 에러 내용 + 원인 추정)

## 경고 목록
(에셋 경로 + 경고 내용)

## 수정 필요 항목
(우선순위별 수정 방향)
```
""",

    "07_코드매니저": """\
# 코드 매니저 (오케스트레이터) 프롬프트 템플릿

```
아래 에이전트 리포트들을 통합하여 최종 코드 리뷰 리포트를 작성해줘.

[소스 분석 결과]
{source_analysis}

[규약 검증 결과]
{convention_check}

[코드 검증 결과]
{code_validation}

[빌드 검증 결과]
{build_check}

최종 리포트 형식:
## 요약
## 즉시 수정 필요 (Critical)
## 권장 수정 (Warning)
## 참고 사항 (Info)
## 액션 아이템
```
""",

    "11_리뷰상담": """\
# 리뷰 상담 프롬프트 템플릿

```
당신은 UE5 프로젝트의 코드 리뷰 상담 및 설계 노트 기록 전문가입니다.
개발자의 질문에 답하고, 개발자의 의견·설계 의도·고민·방향성을 컨텍스트 파일에 기록합니다.

## 사용 가능한 작업

1. **리뷰 조회**: .claude/reviews/ 에서 리뷰 리포트를 읽고 설명
2. **수정 안내**: 지적 사항에 대한 구체적 수정 방법 제시 (combined_search로 관련 코드 검색)
3. **코멘트 기록**: 개발자의 의견을 해당 컨텍스트 MD 파일의 ## 코멘트 섹션에 기록

## 코멘트 유형 태그
개발자의 발언 내용에 따라 적절한 태그를 선택:
- `[리뷰]` — 리뷰 지적에 대한 응답 (의도적 구현, 향후 수정 예정 등)
- `[방향]` — 기능의 설계 방향, 목표, 아키텍처 결정
- `[고민]` — 아직 결정되지 않은 설계 고민, 트레이드오프
- `[진행]` — 방향 전환, 진척 상황, 마일스톤 변경
- `[메모]` — 기타 참고 사항 (기획 대기, 임시 구현 등)

## 코멘트 기록 규칙
- 대상 파일: .claude/context/<도메인>/<파일>.md
- 기존 ## 코멘트 섹션이 있으면 항목 추가, 없으면 섹션 새로 생성
- 형식: `- [YYYY-MM-DD][태그] <내용 요약> (작성자)`
- 개발자가 작성자명을 알려주면 사용, 아니면 생략 가능
- 기록된 코멘트는 다음 리뷰 시 03_코드규약, 05_코드검증이 참조함
- 해결된 코멘트는 개발자 요청 시 제거 가능

## 상담 시 행동 지침
- 개발자가 고민을 말하면 경청하고 관련 컨텍스트를 검색하여 의견 제시
- 방향성 변경이면 기존 코멘트 중 상충하는 항목이 있는지 확인하고 갱신 제안
- 단순 질문이면 코멘트 기록 없이 답변만 제공
- 기록이 필요한 내용이면 "이 내용을 코멘트로 기록할까요?" 확인 후 진행

## 리뷰 리포트 경로
- 코드 리뷰: .claude/reviews/YYYY-MM-DD_HHMM_<해시>.md
- 에셋 검증: .claude/reviews/YYYY-MM-DD_HHMM_<해시>_assets.md
- 최신 리포트를 찾으려면 .claude/reviews/ 를 날짜순으로 정렬
```
""",
}

# SKILL_INDEX.md 내용
SKILL_INDEX = """\
# SKILL INDEX

에이전트별 역할과 실행 순서를 정의합니다.

| 순서 | 에이전트 | 역할 | 입력 | 출력 |
|------|----------|------|------|------|
| 1 | 01_소스분석 | 변경 파일 → RAG 컨텍스트 MD 생성 | 소스 파일 경로 + 내용 | .claude/context/*.md |
| 2 | 02_프로젝트분석 | 전체 구조 변화 분석 | 변경 파일 목록 | 아키텍처 분석 MD |
| 3 | 03_코드규약 | 컨벤션 준수 여부 검토 | 소스 파일 내용 | 규약 위반 리포트 |
| 4 | 05_코드검증 | 버그·안전성 이슈 탐지 | 소스 파일 내용 | 이슈 목록 |
| 5 | 06_빌드_통합 | 빌드 위험 요소 분석 | 빌드 파일 내용 | 빌드 리포트 |
| 6 | 07_코드매니저 | 전체 통합 리포트 생성 | 위 결과 전체 | 최종 리뷰 리포트 |
| - | 08_로그분석 | UE5 로그 에러·경고 분석 | .log 파일 경로 | 에러 요약 + 권장 조치 |
| - | 09_크래시분석 | 크래시 원인 분석 | 크래시 폴더 / .dmp / .log | 크래시 요약 + 수정 제안 |
| - | 10_에셋검증 | DataValidation 커맨드렛 결과 분석 | 커맨드렛 출력 + 에셋 목록 | 에셋 검증 리포트 |
| - | 11_리뷰상담 | 리뷰 지적 사항 상담 + 코멘트 기록 | 리뷰 리포트 + 개발자 질문/의견 | 대화형 응답 + 컨텍스트 MD 코멘트 갱신 |

## 실행 방법
- **자동**: watch.py가 새 커밋 감지 시 01_소스분석 자동 실행
- **자동 (에셋)**: .uasset / .umap 변경 감지 시 DataValidation 자동 실행
- **자동 (벡터)**: 컨텍스트 생성 후 벡터 인덱스 자동 갱신
- **수동 (로그)**: 08_로그분석에 .log 파일 경로 전달
- **수동 (크래시)**: 09_크래시분석에 크래시 폴더 또는 파일 경로 전달
- **수동 (에셋)**: 10_에셋검증에 에셋 검증 요청
- **수동 (리뷰상담)**: 11_리뷰상담에 리뷰 관련 질문이나 의견 전달
- **전체 리뷰**: 07_코드매니저를 호출하여 전체 파이프라인 실행

## RAG 검색
- `combined_search(query, tags?)` — **벡터 + 태그 통합 검색** (항상 이 툴을 우선 사용)
  - 벡터 검색(의미 기반)과 태그 검색(키워드 기반)을 동시에 수행하여 결과를 병합
  - tags 미지정 시 벡터 결과에서 자동 추출하여 태그 검색도 수행
- `vector_search(query)` — 벡터 의미 검색만 단독 수행 (특수한 경우)
- `search_context(tags)` — 태그 검색만 단독 수행 (특수한 경우)
- `rebuild_index()` — 전체 벡터 인덱스 재구축
- `index_status()` — 인덱스 상태 확인
- watch.py가 컨텍스트 갱신 시 자동으로 벡터 인덱스도 갱신함

## 컨텍스트 코멘트 시스템
- 컨텍스트 MD 파일(`context/<도메인>/<파일>.md`)에 `## 코멘트` 섹션으로 개발자 노트 기록
- 코멘트 유형: `[리뷰]` 지적 응답, `[방향]` 설계 방향, `[고민]` 미결 고민, `[진행]` 상황 변경, `[메모]` 기타
- 코멘트는 벡터 임베딩에서 **제외** (검색 품질 유지)
- 코멘트는 코드 리뷰 시 에이전트가 **참조** (동일 지적 반복 방지 + 설계 맥락 이해)
- 컨텍스트 MD 갱신 시 코멘트 섹션은 자동 보존
- 11_리뷰상담 에이전트가 코멘트 기록 담당
"""

# ─────────────────────────────────────────
# settings.json 템플릿 (에이전트별 허용 툴 및 MCP 설정)
# ─────────────────────────────────────────
SETTINGS_TEMPLATES: dict[str, dict] = {

    "01_소스분석": {
        "description": "변경된 소스 파일을 읽어 RAG 컨텍스트 MD를 생성한다.",
        "allowedTools": ["Read", "Glob"],
        "scope": "repo",
    },

    "02_프로젝트분석": {
        "description": "저장소 전체 구조를 탐색하여 아키텍처를 분석한다.",
        "allowedTools": ["Read", "Glob", "Grep"],
        "scope": "repo",
        "mcpServers": {
            "context-search": {
                "command": "./.claude/mcp/context_search.exe",
                "args": []
            },
            "gemini-query": {
                "command": "./.claude/mcp/gemini_query.exe",
                "args": []
            }
        },
    },

    "03_코드규약": {
        "description": "코딩 컨벤션 준수 여부를 검토한다. 파일 읽기 전용.",
        "allowedTools": ["Read", "Grep"],
        "scope": "repo",
        "mcpServers": {
            "context-search": {
                "command": "./.claude/mcp/context_search.exe",
                "args": []
            },
            "gemini-query": {
                "command": "./.claude/mcp/gemini_query.exe",
                "args": []
            }
        },
    },

    "04_코드작성": {
        "description": "컨텍스트 기반으로 코드 초안을 작성하거나 리팩터링을 제안한다. 도메인 문서를 참조하여 기존 패턴과 일관된 코드를 생성한다.",
        "allowedTools": ["Read", "Write", "Edit", "Glob"],
        "scope": "repo",
        "mcpServers": ["context-search"],
    },

    "05_코드검증": {
        "description": "잠재적 버그, 메모리 누수, 스레드 안전성 이슈를 탐지한다. 읽기 전용.",
        "allowedTools": ["Read", "Grep", "Glob"],
        "scope": "repo",
        "mcpServers": {
            "context-search": {
                "command": "./.claude/mcp/context_search.exe",
                "args": []
            },
            "gemini-query": {
                "command": "./.claude/mcp/gemini_query.exe",
                "args": []
            }
        },
    },

    "06_빌드_통합": {
        "description": "빌드 스크립트 및 의존성 변경이 빌드에 미치는 영향을 분석한다.",
        "allowedTools": ["Read", "Glob", "Bash"],
        "scope": "repo",
        "targetExtensions": [".Build.cs", ".Target.cs", "CMakeLists.txt", ".bat", ".sh"],
        "mcpServers": {},
    },

    "07_코드매니저": {
        "description": "전체 에이전트를 조율하고 통합 리포트를 생성하는 오케스트레이터.",
        "allowedTools": ["Read", "Write", "Glob", "Bash"],
        "scope": "project",
        "mcpServers": {
            "context-search": {
                "command": "./.claude/mcp/context_search.exe",
                "args": []
            },
            "gemini-query": {
                "command": "./.claude/mcp/gemini_query.exe",
                "args": []
            }
        },
    },

    "08_로그분석": {
        "description": "UE5 로그 파일을 분석하여 에러·경고·패턴을 요약한다.",
        "allowedTools": ["Read"],
        "scope": "project",
        "mcpServers": {
            "log-analyzer": {
                "command": "./.claude/mcp/log_analyzer.exe",
                "args": []
            },
            "context-search": {
                "command": "./.claude/mcp/context_search.exe",
                "args": []
            },
            "gemini-query": {
                "command": "./.claude/mcp/gemini_query.exe",
                "args": []
            }
        },
    },

    "09_크래시분석": {
        "description": "UE5 크래시 데이터(dmp/log/xml)를 분석하여 원인과 수정 방향을 제시한다.",
        "allowedTools": ["Read"],
        "scope": "project",
        "mcpServers": {
            "crash-analyzer": {
                "command": "./.claude/mcp/crash_analyzer.exe",
                "args": []
            },
            "context-search": {
                "command": "./.claude/mcp/context_search.exe",
                "args": []
            },
            "gemini-query": {
                "command": "./.claude/mcp/gemini_query.exe",
                "args": []
            }
        },
    },

    "10_에셋검증": {
        "description": "UE5 DataValidation 커맨드렛 결과를 분석하여 에셋 유효성 문제를 보고한다.",
        "allowedTools": ["Read"],
        "scope": "project",
        "mcpServers": {
            "commandlet-runner": {
                "command": "./.claude/mcp/commandlet_runner.exe",
                "args": []
            },
            "context-search": {
                "command": "./.claude/mcp/context_search.exe",
                "args": []
            },
            "gemini-query": {
                "command": "./.claude/mcp/gemini_query.exe",
                "args": []
            }
        },
    },

    "11_리뷰상담": {
        "description": "코드 리뷰 지적 사항에 대해 개발자와 상담하고, 의견을 컨텍스트 MD에 코멘트로 기록한다.",
        "allowedTools": ["Read", "Write", "Edit", "Glob", "Grep"],
        "scope": "project",
        "mcpServers": {
            "context-search": {
                "command": "./.claude/mcp/context_search.exe",
                "args": []
            },
            "gemini-query": {
                "command": "./.claude/mcp/gemini_query.exe",
                "args": []
            }
        },
    },
}

# 프로젝트 레벨 settings.json에 등록할 MCP 서버 목록
MCP_SERVERS: dict[str, str] = {
    "context-search":    "./.claude/mcp/context_search.exe",
    "log-analyzer":      "./.claude/mcp/log_analyzer.exe",
    "crash-analyzer":    "./.claude/mcp/crash_analyzer.exe",
    "commandlet-runner": "./.claude/mcp/commandlet_runner.exe",
    "gemini-query":      "./.claude/mcp/gemini_query.exe",
}

# CLAUDE.md 에 삽입/갱신하는 AgentWatch 관리 구역
# <!-- AgentWatch:Start --> ~ <!-- AgentWatch:End --> 사이 내용을 항상 최신으로 유지한다.
AGENTWATCH_MD_MARKER_START = "<!-- AgentWatch:Start -->"
AGENTWATCH_MD_MARKER_END   = "<!-- AgentWatch:End -->"

PROJECT_CLAUDE_MD_SECTION = """\
<!-- AgentWatch:Start -->
## AgentWatch — 자동화 컨텍스트 시스템

Git 커밋 감지 → RAG 컨텍스트 자동 갱신 → 다중 에이전트 분석이 자동으로 동작합니다.

### 컨텍스트 파일 위치 및 형식
- `.claude/context/<도메인>/` — 변경 소스 파일을 Claude가 요약한 MD 파일
- 프론트매터 스키마:
  ```yaml
  ---
  tags: [태그1, 태그2]
  category: 대분류/중분류
  related_classes:
    - ClassName: path/to/file.ext
  ---
  ```
- 코드 분석 시 이 파일들을 컨텍스트로 먼저 로드하세요.

### 리뷰 리포트 위치
- `.claude/reviews/YYYY-MM-DD_HHMM_<커밋해시>.md` — 커밋별 코드 리뷰
- `.claude/reviews/YYYY-MM-DD_HHMM_<커밋해시>_assets.md` — 에셋 검증 결과

### 컨텍스트 코멘트
- 컨텍스트 MD 파일 끝에 `## 코멘트` 섹션으로 개발자 노트를 기록할 수 있음
- 코멘트 유형: `[리뷰]` 지적 응답, `[방향]` 설계 방향, `[고민]` 미결 고민, `[진행]` 상황 변경, `[메모]` 기타
- 코멘트는 벡터 임베딩에서 제외되어 검색 품질에 영향 없음
- 코드 리뷰 시 에이전트가 코멘트를 참조하여 설계 맥락 이해 및 인지된 항목 반복 지적 방지
- 11_리뷰상담 에이전트를 통해 리뷰 상담, 설계 고민, 방향성 기록 가능

### 에이전트 목록
`.claude/agents/SKILL_INDEX.md` 참고

### 등록된 MCP 서버
| 서버 | 실행 파일 | 주요 툴 |
|------|-----------|---------|
| `context-search` | `.claude/mcp/context_search.exe` | `combined_search`, `search_context`, `list_tags`, `vector_search`, `rebuild_index`, `index_status` |
| `log-analyzer` | `.claude/mcp/log_analyzer.exe` | `analyze_log`, `search_log` |
| `crash-analyzer` | `.claude/mcp/crash_analyzer.exe` | `analyze_crash`, `analyze_crash_log` |
| `commandlet-runner` | `.claude/mcp/commandlet_runner.exe` | `find_unreal_editor`, `run_data_validation`, `run_commandlet` |
| `gemini-query` | `.claude/mcp/gemini_query.exe` | `gemini_analyze`, `gemini_status` (Gemini CLI 미설치 시 안내 반환) |
<!-- AgentWatch:End -->"""

# 기본 컨텍스트 도메인 폴더 목록
DEFAULT_CONTEXT_DOMAINS = [
    "AI",
    "UI",
    "게임플로우",
    "네트워크",
    "데이터",
    "미션",
    "서비스시스템",
    "월드",
    "전투",
    "캐릭터",
]
