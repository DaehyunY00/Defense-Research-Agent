# Architecture Decision Log

이 문서는 프로젝트의 주요 아키텍처 결정과 trade-off를 간단한 ADR 형식으로 기록한다.
현재 구조 설명은 [ARCHITECTURE.md](./ARCHITECTURE.md), 장기 방향은
[ROADMAP.md](./ROADMAP.md), 실행 backlog는
[IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md)를 참조한다.

ADR의 `Status`는 결정의 채택 상태이며 기능 구현 완료 여부와 다를 수 있다. 구현 상태가
부분적인 경우 별도로 명시한다.

## ADR-001 — 원본 corpus는 읽기 전용으로 유지

**Status: Accepted**

### Context

원본 PDF와 JSON은 연구 근거와 데이터 품질 분석의 기준이다. 파생 처리 과정이 원본을
변경하면 재현성, 감사 가능성과 오류 복구가 훼손된다.

### Decision

`data/`는 immutable input으로 취급한다. 생성·수정·삭제·이동·이름 변경을 금지하며
정규화 데이터, 인덱스, 실행 로그와 평가 결과는 `artifacts/`에 기록한다. 데이터 처리
전후에 해시 또는 동등한 불변 검사를 수행한다.

### Consequences

- 파생 산출물은 언제든 재생성 가능해야 한다.
- 원본 오류를 수정하는 대신 정정·계보 metadata를 별도로 보존한다.
- 저장공간이 추가로 필요하지만 감사와 재현성이 향상된다.

## ADR-002 — Provider abstraction을 유지

**Status: Accepted**

### Context

모델, 검색과 외부 이슈 provider는 비용, 가용성, 개인정보 경계와 API가 달라질 수 있다.
애플리케이션 로직이 특정 SDK에 직접 결합하면 테스트와 교체가 어려워진다.

### Decision

애플리케이션은 `ModelGateway`, `PublicationSearchAlgorithm`,
`ResearchPublicationRepository`, `ExternalIssueSearchProvider` 같은 안정적인 interface에
의존한다. provider-specific 코드, retry와 오류 변환은 adapter 내부에 격리한다.

### Consequences

- fake 구현으로 네트워크 없는 결정적 테스트가 가능하다.
- adapter와 application domain 사이에 명시적 변환 코드가 필요하다.
- 모든 provider 기능을 최소공통분모로 축소하지 않고 선택 기능은 capability로 표현한다.

## ADR-003 — Lexical retrieval을 baseline으로 유지

**Status: Accepted**

### Context

Vector retrieval은 의미적 유사도에 강점이 있지만 exact term, 약어, 무기체계명과 정책
식별자에서 lexical 검색이 더 안정적일 수 있다. 현재 lexical 구현은 외부 서비스 없이
결정적으로 실행된다.

### Decision

Vector와 hybrid retrieval을 추가하더라도 기존 lexical retrieval을 제거하지 않는다.
lexical은 오프라인 baseline과 fallback으로 유지하고 동일 golden dataset에서 다른
검색기와 비교한다.

### Consequences

- 검색 개선을 정량 비교할 기준이 유지된다.
- 여러 인덱스와 score trace를 관리해야 한다.
- fusion 결과에서도 lexical/vector 원점수를 별도로 보존한다.

## ADR-004 — Page-aware evidence를 보존

**Status: Accepted**

**Implementation: Partial** - `PublicationChunk` 도메인 모델과 기존 JSON page text는
있지만 PDF 추출·페이지/섹션 청킹·검색 파이프라인은 아직 완성되지 않았다.

### Context

Publication ID만으로는 연구자가 주장 근거를 빠르게 검증할 수 없고 RAG citation
correctness도 측정하기 어렵다.

### Decision

향후 모든 chunk retrieval은 publication, page range와 가능한 경우 section provenance를
보존한다. 청크에는 checksum과 parser/chunking version을 연결해 원문과 파생 과정으로
역추적할 수 있게 한다.

### Consequences

- 청킹 자유도가 줄고 metadata와 인덱스 크기가 증가한다.
- 인용 검증, source inspection과 page-level evaluation이 가능해진다.
- 페이지 매핑을 잃는 extraction·chunking 구현은 채택하지 않는다.

## ADR-005 — Optimization 전에 evaluation을 구축

**Status: Proposed**

### Context

Embedding, chunking, fusion, reranking과 모델 변경은 demo 결과만으로 품질 개선을 판단하기
어렵다. 현재 retrieval golden dataset은 아직 없다.

### Decision

Retrieval과 model 변경은 versioned evaluation dataset, corpus snapshot과 experiment log를
기준으로 baseline과 비교한다. 품질, latency, memory와 cost를 함께 기록한다.

### Consequences

- 초기 기능 개발 전에 데이터셋 작성과 benchmark harness 비용이 든다.
- 근거 없는 최적화와 회귀 위험을 줄인다.
- dataset label과 trade-off 해석은 사람이 책임진다.

## ADR-006 — Learning Companion은 application layer에 둔다

**Status: Proposed**

### Context

학습 기능은 설명 수준, 퀴즈와 learning path가 필요하지만 연구 검색과 같은 문서·근거
기반을 사용한다. 별도 지식 저장소를 만들면 중복과 일관성 문제가 생긴다.

### Decision

Learning Companion은 별도의 knowledge silo가 아니라 기존 ingestion, document
intelligence, retrieval, model runtime과 evaluation 위의 application으로 구현한다.

### Consequences

- Search, Research Copilot과 citation·notebook 기반을 공유한다.
- 학습자 수준과 knowledge check에는 application-specific schema와 평가가 추가된다.
- 기존 retrieval 기반이 준비되기 전에는 구현을 시작하지 않는다.

## ADR-007 — Multi-runtime model support

**Status: Proposed**

### Context

국방 연구 환경은 품질뿐 아니라 비용, GPU, 개인정보와 데이터 반출 경계에 따라 다른
실행 방식이 필요하다. 현재 구현은 Fake와 Anthropic adapter를 제공한다.

### Decision

장기적으로 `LOCAL`, `HYBRID`, `CLOUD`, `BYOK` 모드를 지원하되 application-facing
`ModelGateway`를 유지한다. OpenAI-compatible, local model과 vLLM/Ollama 계열 endpoint는
구현·평가 후 지원 상태로 올린다.

### Consequences

- provider별 기능·structured output 차이를 adapter가 흡수해야 한다.
- credential isolation, privacy와 runtime observability가 필수다.
- 아직 구현하지 않은 provider는 문서에서 계획으로만 표시한다.

## ADR-008 — 주 오케스트레이터는 LangGraph를 유지

**Status: Accepted**

### Context

이 시스템은 자유로운 agent 대화보다 명시적 단계, 결정적 Python gate, 실패 보존과 장시간
사람 승인 경계가 중요하다.

### Decision

Topic Discovery의 주 오케스트레이터로 LangGraph를 유지하고 AutoGen으로 교체하지 않는다.
Research Lab은 현재 결정적 Python 실행기와 `ThreadPoolExecutor`를 사용하며, 필요할 때
typed LangGraph node로 단계 이관한다. Anthropic SDK 경계는 그대로 유지한다.

### Consequences

- 상태 전이와 human-in-the-loop를 명시적으로 제어할 수 있다.
- 자유 대화형 실험에는 추가 구현이 필요하다.
- 상세 비교와 이관 조건은 [AUTOGEN_VS_LANGGRAPH.md](./AUTOGEN_VS_LANGGRAPH.md)에 보존한다.

## ADR-009 — 점수·정렬·상태 전이는 결정적 Python이 담당

**Status: Accepted**

### Context

LLM이 최종 점수, 정렬이나 승인 상태를 직접 결정하면 재현성과 정책 통제가 약해진다.

### Decision

LLM은 구조화된 관찰값과 근거 설명을 제안할 수 있지만 가중치, 감점, fusion, 정렬,
임계값과 상태 전이는 Python 코드가 수행한다. 사람 승인 없이 `approved` 상태로 이동할 수
없다.

### Consequences

- 동일 입력과 설정에서 결과를 재현하고 테스트할 수 있다.
- 규칙과 가중치 변경은 코드·설정·평가 결과로 검토해야 한다.
- 모델의 유연성을 일부 제한하지만 감사 가능성을 우선한다.

## ADR-010 — 품질 게이트 임계값은 corpus 측정으로 보정하고, U+0001은 공백으로 치환한 뒤 측정

**Status: Accepted**

### Context

`DATA_QUALITY_REPORT.md` DQ-03은 고유 문서 370개 중 192개에 C0/C1 제어문자가 있고
국방논단은 100/100, 국방정책연구는 59/59라고 기록한다. 제어문자를 그대로 손상으로
계수할지, 아니면 추출 아티팩트로 보고 정규화한 뒤 판정할지에 따라 코퍼스 과반의 색인
여부가 달라진다. 임계값을 근거 없이 정하면 어느 쪽이든 대량 오분류가 발생한다.

`scripts/measure_quality_thresholds.py`로 `data/`를 읽기 전용 측정했다. 실행 전후에
`data/` 전체 해시를 비교해 원본 불변을 확인한다.

측정 결과:

- 고유 문서 370개, metadata 파일 373개. 제어문자 보유 192개
  (Brief 28, 국방논단 100, 국방정책연구 59, 연구보고서 5) — DQ-03과 일치
- 코드포인트별 **문서 수**: `U+0001` 163개, `U+0007` 29개. 나머지 C0 코드
  (`U+0002`~`U+0006`, `U+000B`, `U+000C`)는 전부 **단일 문서**에만 나타난다.
  총 발생 수 집계는 그 한 문서(제어문자 253,227개, 본문의 39.9%)에 지배된다.
- 국방논단 문서의 제어문자 비율은 1.7~5.0% 대역이며 거의 전부 `U+0001`이다.

### Decision

측정 전에 `U+0001`을 공백으로 치환한 뒤 제어문자를 계수한다. 치환은 측정에만 적용하고
저장되는 page text에는 적용하지 않는다. 임계값은 아래로 확정한다.

| threshold | 값 | 제외 문서 수 |
|---|---|---|
| `min_character_count` | 1,000 | 38 |
| `max_control_character_ratio` | 0.01 | 1 |
| `min_printable_ratio` | 0.95 | 2 |
| `min_korean_ratio` | 0.1 | 2 |
| `min_non_empty_page_ratio` | 0.25 | 3 |

`thresholds_version`은 `quality-v1-corpus370`이다.

근거:

- **치환**: 치환 없이 `max_control_character_ratio=0.01`을 적용하면 86개(23.2%)가
  제외되고 그중 78개가 국방논단이다. 렌더링 아티팩트로 한 발간물 유형의 대부분을
  버리게 된다. 치환 후에는 동일 임계값에서 정확히 1개만 제외된다.
- **0.01**: 치환 후 합법 문서의 최대치는 0.005 미만이고 손상 문서는 0.399다.
  실제 텍스트 대비 2배, 손상 문서 대비 40분의 1 지점이다.
- **min_korean_ratio 0.1**: 국방정책연구 최저값이 0.338이라 59개 전체에 존재하는 영문
  Abstract 구간과 무관하게 안전하다. 0.2로 올리면 279k자 국방AI 연구보고서(0.152)가
  잘못 제외된다.
- **min_printable_ratio 0.95**: `str.isprintable()` 기준이며 제어문자 비율과 중복되지
  않는다. p10이 0.9977이고 0.99로 올리면 국방정책연구 5개가 제외된다.
- **min_non_empty_page_ratio 0.25**: 0.5는 28개를 제외하는데 대부분 4페이지 Brief로,
  빈 페이지 하나에 걸린다. 이들의 실제 결함은 저추출이고 `min_character_count`가 이미
  잡는다.

다섯 임계값을 함께 적용하면 370개 중 39개가 걸리고, 그중 38개는 저추출이다. 저추출
이외의 사유로 걸리는 실제 발간물은 DQ-03이 지목한 손상 보고서 1개뿐이다.
`pdf_index.json`도 걸리지만 이는 발간물이 아니므로 ingestion 단계에서 제외해야 한다.

### Consequences

- 임계값은 추출 텍스트를 설명하는 값이다. parser가 바뀌면 재측정하고
  `thresholds_version`을 올려야 한다.
- `U+0007`을 보유한 29개 문서는 치환 대상이 아니므로 `warning`으로 색인된다.
  공백 대체라는 근거가 확인되면 치환 목록에 추가할 수 있다.
- 치환 목록 자체가 버전 관리 대상이다. 항목을 바꾸면 `thresholds_version`도 바꾼다.
