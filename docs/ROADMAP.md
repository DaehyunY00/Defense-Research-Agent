# Defense Research Platform Roadmap

이 문서는 현재 Sprint가 아니라 프로젝트의 장기 제품·엔지니어링 방향을 설명한다.
구현 순서와 개발자가 바로 수행할 작업은 [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md),
현재 기술 설계는 [ARCHITECTURE.md](./ARCHITECTURE.md), 품질 판정 기준은
[EVALUATION.md](./EVALUATION.md)를 참조한다.

시각적 요약은
[Defense Research Platform Roadmap PDF](../output/pdf/defense-research-platform-roadmap.pdf)에
있다.

## 비전

현재 Defense Research Agent를 다음 공통 기반과 응용 프로그램을 가진
**Defense Research Platform**으로 발전시킨다.

```text
Defense Research Platform

├── ingestion
├── document intelligence
├── knowledge
├── retrieval
├── embeddings
├── model runtime
├── rag
├── evaluation
│
├── applications
│   ├── research_copilot
│   ├── learning_companion
│   ├── topic_discovery
│   ├── report_builder
│   └── research_lab
│
├── api
└── ui
```

Topic Discovery와 Research Lab은 교체 대상이 아니라 플랫폼의 핵심 애플리케이션으로
유지한다. 새 애플리케이션은 가능한 한 같은 문서·검색·모델·평가 기반을 공유한다.

## 상태와 우선순위 원칙

- **Baseline**: 현재 구현돼 있고 후속 Epic이 의존하는 기반
- **Next**: 다음 개발 사이클에서 먼저 완성할 영역
- **Later**: 선행 기반과 평가 기준이 갖춰진 뒤 진행할 영역
- **Explore**: 제품·도메인 검증 후 투자 여부를 결정할 영역

우선순위는 다음 흐름을 따른다.

```text
검증 가능한 현재 시스템
    -> 신뢰할 수 있는 문서와 페이지 근거
    -> 측정 가능한 retrieval
    -> citation-grounded applications
    -> runtime과 UI 확장
    -> advanced research intelligence
```

## Epic 0 — Existing Research Agent Foundation

**상태: Baseline**

현재 후속 개발이 보존해야 하는 기반이다.

- 읽기 전용 publication normalization과 `artifacts/` 파생 산출물 정책
- `ResearchPublicationRepository`와 `PublicationSearchAlgorithm`
- 로컬 lexical search와 결정적 필터·정렬
- external issue provider abstraction, fixture provider와 공식 출처 adapter
- Topic Discovery 후보 생성과 Pydantic 구조화 출력
- 네 독립 평가기와 결정적 점수·감점·다양성 랭킹
- 사람 승인 중단·재개와 append-only review history
- 7개 역할 Research Lab orchestration과 도구 allow-list
- provider-neutral `ModelGateway`, `FakeModelGateway`, Claude adapter
- 격리 코드 검증과 제한된 공개 데이터 분석 경계
- 선택적 GCP API, worker, 상태·산출물 배포 구성

이 Epic은 미래 기능 목록이 아니다. 이후 변경의 회귀 baseline이다.

## Epic 1 — Document Intelligence

**상태: Next**

목표는 PDF를 단순 파일이 아니라 품질과 provenance가 명확한 연구 근거로 바꾸는 것이다.

주요 결과:

- document parser interface와 parser/version metadata
- PDF 본문·페이지 추출 adapter
- OCR fallback boundary와 채택 품질 기준
- 제목·저자·발행일·초록·키워드 등 metadata extraction
- 저추출·손상·중복·orphan을 분리하는 quality gate
- page-aware / section-aware chunking
- publication, page, section, checksum과 version을 보존하는 `PublicationChunk`
- 재추출 실패와 수동 검토 대기열

현재 `PublicationChunk` 도메인 모델과 기존 JSON page text는 부분 기반으로 존재하지만,
PDF 추출·OCR·청킹을 연결한 production pipeline은 아직 없다.

완료 신호:

- 검색 가능한 모든 청크가 원본 publication과 실제 페이지로 역추적된다.
- 품질 미달 문서가 조용히 기본 인덱스에 들어가지 않는다.
- parser/chunking 버전 변경 시 파생 산출물을 재현할 수 있다.

## Epic 2 — Retrieval Engine

**상태: Next**

목표는 lexical baseline을 유지하면서 검색 품질을 측정 가능하게 개선하는 것이다.

주요 결과:

- `EmbeddingProvider` abstraction과 결정적 `FakeEmbeddingProvider`
- `VectorIndex` / `VectorSearchAlgorithm` abstraction
- BM25 또는 현재 lexical baseline의 명시적 개선
- vector search
- lexical/vector 원점수를 별도로 보존하는 hybrid search
- Reciprocal Rank Fusion 또는 버전이 명시된 다른 fusion 전략
- `Reranker` abstraction과 선택적 구현
- query, latency, candidate count, fusion/rerank trace observability
- versioned golden dataset 기반 retrieval benchmark

중요 원칙:

- 기존 lexical search를 제거하지 않는다.
- lexical은 오프라인 결정성, 약어, 무기체계명, 정책 식별자와 정확 용어 검색의 baseline이다.
- 검색 방식 변경은 Recall@K, MRR, latency와 자원 사용량을 함께 비교한다.

## Epic 3 — Research Copilot

**상태: Later**

목표는 이미 선택된 연구 주제에 대해 근거를 탐색하고 검증 가능한 답변을 작성하도록
연구자를 지원하는 것이다.

기능 후보:

- corpus 기반 질문과 evidence retrieval
- citation-grounded answer generation
- publication/page/section citation과 source inspection
- 문서 간 비교와 상충 근거 표시
- follow-up question과 연구 프로젝트 context
- 근거 부족 시 명시적 abstention
- 답변에서 notebook 또는 Topic Discovery로의 연결

Research Copilot은 retrieval benchmark와 page-aware evidence가 준비된 뒤 시작한다.

## Epic 4 — Learning Companion

**상태: Later**

Learning Companion은 별도 문서 저장소를 만드는 시스템이 아니라 기존 Document
Intelligence, Retrieval, Model Runtime 위의 애플리케이션이다.

기능 후보:

- 초급·중급·전문가 수준 설명
- 핵심 개념과 관련 개념 탐색
- 출처 기반 learning path와 읽을거리 추천
- quiz generation과 knowledge check
- research notebook
- 학습 질문에서 Topic Discovery로 연결

애플리케이션 역할은 다음처럼 구분한다.

```text
Learning Companion -> 분야를 학습한다
Research Copilot   -> 이미 선택된 주제를 연구한다
Topic Discovery    -> 무엇을 연구할지 찾는다
Research Lab       -> 복잡한 연구 과업을 멀티에이전트로 수행한다
```

## Epic 5 — Model Runtime Platform

**상태: Later**

현재 `FakeModelGateway`와 Anthropic adapter를 기반으로 application-facing interface를
안정적으로 유지하면서 실행 방식을 확장한다.

목표 provider와 모드:

```text
LOCAL   -> local model only
HYBRID  -> local default with selected API escalation
CLOUD   -> managed API providers
BYOK    -> user-provided credentials and policy
```

- Fake model
- Anthropic
- OpenAI-compatible provider
- local model
- vLLM / Ollama 계열 endpoint
- 다른 provider adapter 확장 가능성
- BYOK credential isolation

중요 원칙:

- 비밀정보는 로그, 상태, 프롬프트 snapshot이나 산출물에 남기지 않는다.
- provider-specific SDK와 오류 처리는 adapter 내부에 격리한다.
- 모든 구조화 출력은 Pydantic 검증을 통과해야 한다.
- application-facing `ModelGateway`는 최대한 안정적으로 유지한다.
- provider 선택은 품질, latency, cost, memory, privacy 평가로 결정한다.

## Epic 6 — Product UI

**상태: Later**

목표는 연구자가 검색·학습·연구·검토를 하나의 흐름으로 수행할 수 있는 경험을 만드는
것이다.

제품 영역 후보:

- Search
- Learn
- Research
- Topics
- Notebook
- Reports

UI는 다음 경계를 명확히 보여줘야 한다.

- 원본 출처와 page citation
- 모델 생성 내용과 결정적 계산 결과
- 근거 부족, 실패와 미검증 상태
- 사람 검토 대기와 승인 이력
- 외부 콘텐츠의 untrusted 상태

프론트엔드 프레임워크는 별도 제품·운영 결정을 거치기 전에는 확정하지 않는다.

## Epic 7 — Advanced Research Intelligence

**상태: Explore / 후순위**

후보 기능:

- knowledge graph
- entity extraction
- relation extraction
- event extraction
- timeline
- report builder
- trend analysis

이 Epic은 Document Intelligence, Retrieval, RAG와 평가 체계보다 우선하지 않는다.
그래프나 timeline이 실제 연구 성과를 개선하는지 검증할 데이터와 use case가 있을 때
구체적인 저장소·스키마·UI 결정을 내린다.

## 단계별 제품 그림

| 단계 | 사용자 가치 | 핵심 기반 | 통과해야 할 gate |
|---|---|---|---|
| Foundation | 연구주제 후보와 멀티에이전트 연구 흐름 | 현재 Agent, lexical, review | 오프라인 E2E와 회귀 검증 |
| Evidence | 페이지까지 추적되는 신뢰 가능한 문서 | Document Intelligence | extraction·page mapping 품질 |
| Retrieval | lexical/vector/hybrid 비교 가능한 검색 | Embedding, vector, reranker | golden dataset retrieval benchmark |
| Assistance | 근거 기반 연구·학습 지원 | RAG, citation, notebook | groundedness와 citation 평가 |
| Platform | 다양한 runtime과 사용자 경험 | Model Runtime, API, UI | privacy·cost·latency·운영성 |
| Intelligence | 관계·사건·보고서 고도화 | Knowledge graph, timeline | 검증된 use case와 incremental value |

## 로드맵 운영 규칙

- Epic 상태는 실제 코드, 테스트와 운영 검증을 근거로 갱신한다.
- 미구현 기능을 완료로 표시하지 않는다.
- 신규 agent 역할보다 데이터·검색·평가 기반을 먼저 강화한다.
- 품질 수치가 없으면 개선을 주장하지 않고 측정 계획부터 만든다.
- 제품·국방 도메인 trade-off는 사람이 결정한다.
- Architecture Decision은 [DECISIONS.md](./DECISIONS.md)에 기록한다.
