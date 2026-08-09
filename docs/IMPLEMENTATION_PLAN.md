# Defense Research Platform Implementation Plan

이 문서는 **지금부터 수행할 개발 작업**을 우선순위와 완료 조건으로 정의한다. 장기 제품
방향은 [ROADMAP.md](./ROADMAP.md), 현재 기술 설계는
[ARCHITECTURE.md](./ARCHITECTURE.md), 평가 방법은 [EVALUATION.md](./EVALUATION.md),
결정 근거는 [DECISIONS.md](./DECISIONS.md)를 참조한다.

현재 우선순위는 agent 역할을 더 늘리는 것이 아니라 **Data / Retrieval 기반을 강화하는
것**이다. Topic Discovery와 Research Lab의 현재 동작은 회귀 baseline으로 유지한다.

## Current Priority

```text
P0 현재 시스템 검증
P1 Document Intelligence
P2 Retrieval
P3 Research Copilot
P4 Learning Companion
P5 Model Runtime
P6 Product UI
P7 Advanced Intelligence
```

| 우선순위 | 목표 | 시작 조건 | 완료 신호 |
|---|---|---|---|
| P0 | 현재 E2E와 운영 경계 검증 | 즉시 | 재현 가능한 baseline과 known failure 목록 |
| P1 | 신뢰할 수 있는 페이지 근거 | P0 baseline | 품질 gate를 통과한 page/section chunk |
| P2 | 측정 가능한 검색 개선 | P1 chunk | lexical 대비 benchmark가 있는 hybrid retrieval |
| P3 | citation-grounded 연구 지원 | P2 retrieval | 근거·인용·abstention 평가 통과 |
| P4 | 공통 기반 위의 학습 지원 | P2, P3 공통 기반 | 수준별 설명·학습 경로·knowledge check 평가 |
| P5 | local/API/BYOK runtime 확장 | 평가 harness | provider별 품질·비용·privacy 비교 |
| P6 | 연구자용 제품 경험 | 핵심 API 안정화 | 검토 가능한 Search/Learn/Research/Topics UX |
| P7 | graph·timeline·report 고도화 | 검증된 use case | 선행 기능 대비 incremental value 입증 |

## 현재 baseline

다음 기능은 이미 구현돼 있으며 신규 작업에서 보존해야 한다.

- `ResearchPublicationRepository`, `PublicationSearchAlgorithm`과 lexical search
- external issue provider abstraction과 정규화·안전 경계
- Topic Discovery 생성, 네 독립 평가기와 결정적 ranking
- 사람 승인 중단·재개와 append-only review history
- 7개 역할 Research Lab orchestration과 역할별 tool allow-list
- `ModelGateway`, `FakeModelGateway`, Claude structured output adapter
- 제한된 데이터 분석과 격리 코드 검증 계약
- 선택적 GCP API/worker/Firestore/GCS/Secret Manager 구성

현재 `PublicationChunk` 도메인 모델은 존재하지만 extraction·chunking·index pipeline에
연결되지 않았다. Vector, hybrid, reranker, RAG, Learning Companion과 Product UI는 아직
구현되지 않았다.

## P0 — 현재 end-to-end 시스템 검증

목적은 새 기능을 추가하기 전에 현재 시스템의 실제 기준선을 고정하는 것이다.

### P0.1 오프라인 실행 baseline

- [ ] `ingest`가 현재 corpus 수량과 실패 보고를 재현하는지 확인
- [ ] local lexical search의 고정 질의 결과 snapshot 확인
- [ ] offline pilot을 credentials와 네트워크 없이 실행
- [ ] research lab demo를 credentials와 네트워크 없이 실행
- [ ] human review의 네 결정과 같은 run 재개 확인
- [ ] pilot evaluation 산출물과 `unavailable` 지표 확인
- [ ] known failure와 실제 미검증 항목을 `PILOT_RESULT.md`에 갱신

### P0.2 품질 baseline

- [ ] `uv run pytest` 결과 기록
- [ ] `uv run mypy` strict 결과 기록
- [ ] `uv run ruff check .` 결과 기록
- [ ] `uv run ruff format --check .` 결과 기록
- [ ] package import와 health check 확인
- [ ] 기본 suite가 외부 API credentials를 요구하지 않는지 확인
- [ ] 현재 `data/` 불변 해시 또는 동등 검사 기록

### P0.3 선택적 운영 경로

- [ ] 자격증명이 있는 경우 Anthropic runtime path의 최소 structured-output smoke test
- [ ] GCP Terraform validate와 배포 스크립트 dry/static contract 확인
- [ ] 실제 staging Cloud Run API/worker smoke test 계획 수립
- [ ] role별 latency, token, 검색 호출과 비용 관측 항목 정의
- [ ] 실패 시 provider 원문·secret이 로그에 남지 않는지 표본 확인

Anthropic·GCP 검증을 실행하지 못하면 완료로 표시하지 않고 `not_run`과 이유를 기록한다.

## P1 — Document Intelligence

목적은 원본 PDF를 변경하지 않고 metadata, page text, quality와 chunk provenance를
재현 가능하게 만드는 것이다.

### P1 현재 구현 상태

코드 기준 상태다. 이전 로드맵 문서에서 옮겨 왔고 교차 검토로 확인했다.

- [x] 정규화된 publication ID와 source checksum
- [x] page/chunk provenance domain contract
- [x] 같은 입력에서 같은 ID/checksum을 만드는 deterministic page chunker
- [ ] JSON page adapter와 parser abstraction
- [ ] PDF 본문 직접 extraction adapter
- [ ] 저추출·제어문자·중복·orphan quality gate
- [ ] page/chunk artifact writer와 provenance audit

현재 chunker는 ingestion이나 retrieval에 연결되지 않았다. 따라서 page citation, embedding,
vector search와 RAG가 구현됐다고 간주하지 않는다.

### P1.1 `PublicationChunk` domain model 정비

현재 모델 필드는 `chunk_id`, `publication_id`, `section`, `page`, `sequence`, `text`,
`token_count`, `metadata`다. 다음 요구를 현재 domain convention에 맞게 검토한다.

- [x] `page_start` / `page_end` 또는 단일 `page` 표현 결정
- [ ] `section_title`과 현재 `section`의 호환 전략 결정
- [x] chunk text checksum 추가
- [x] `parser_version`, `chunking_version`을 typed field 또는 검증된 metadata로 보존
- [x] 동일 publication/version에서 안정적인 `chunk_id` 생성 규칙 정의
- [x] 기존 모델 사용처와 serialization 호환성 검토
- [x] 정상·blank text·페이지 경계·version 누락 테스트

완료 조건: chunk 하나만으로 publication, page range, parser/chunking version과 텍스트
checksum을 역추적할 수 있다. **성립한다.** `PublicationChunk.provenance`가
parser name/version/source checksum을, `chunking_version`과 `checksum`이 나머지를 담는다.

parser version 역추적 — 완료:

- [x] `PublicationPage`에 `ExtractionProvenance` 부여
- [x] `PublicationChunk`로 provenance 전파
- [x] provenance 변경을 chunk 경계로 처리 (P1.4 페이지 단위 OCR fallback이 한 문서 안에
      서로 다른 추출기 산출물을 섞을 수 있다)
- [x] 기존 chunker 사용처와 테스트 갱신

`chunk_id`는 v2로 올라갔고 parser name/version/source checksum을 identity에 포함한다.
parser version만 바뀌면 `provenance`와 `chunk_id`만 바뀌고 text, checksum, page span,
page range, chunk index, chunking version은 유지된다.

페이지 단위 인용 — 완료:

- [x] `PublicationPageSpan(page_number, start_offset, end_offset)` 추가
- [x] chunk text의 임의 offset에서 정확히 하나의 원본 페이지를 역추적
- [x] 빈틈·중첩 없음, chunk text 전체 길이 포함, 연속 페이지 번호, `page_start`/`page_end`
      일치를 Pydantic validator로 강제
- [x] 페이지 사이 구분자는 앞 페이지 구간에 귀속. 구분자 문자열로 역분할하지 않으므로
      본문에 빈 줄이 있어도 성립한다

인용 단위는 페이지 범위가 아니라 단일 페이지다.

남은 항목: `section_title`과 기존 `section`의 호환 전략. 현재 `section_title`은 계약에
유지하되 파서가 채우지 않으므로 항상 `None`이고, 따라서 `chunking.py`의 `changes_section`
경계는 실제 corpus에서 발화하지 않는다. `data/metadata/*.json`의 `page_texts`는
`{page, text, char_count}`뿐이라 section 정보가 원본에 없다. P1.3 파서가 heading을
추출하는 것이 전제다.

### P1.2 Parser abstraction

- [ ] parser input/output과 stable error taxonomy 정의
- [ ] parser capability에 text, pages, tables, OCR 필요 신호를 표현
- [ ] provider-specific 라이브러리를 adapter 내부에 격리
- [ ] parser version과 source checksum을 결과에 기록
- [ ] fake parser로 정상·실패·부분 추출 테스트

### P1.3 PDF extraction

- [ ] 현재 `PdfPublicationReader`의 header/checksum 검증을 유지
- [ ] 페이지별 본문 추출 adapter 추가
- [ ] 기존 JSON `page_texts`와 신규 PDF 추출 결과의 선택 정책 정의
- [ ] 암호화·손상·빈 페이지·비정상 Unicode 실패 보고
- [ ] 네 publication type의 대표 fixture로 page mapping 검증

현재 Reader는 PDF 본문을 새로 추출하지 않는다는 점을 유지 문서에 명시한다.

### P1.4 OCR fallback boundary

- [ ] OCR 필요 조건과 허용 문서 상태 정의
- [ ] OCR provider interface와 fake 구현 설계
- [ ] 페이지 단위 OCR 실행과 timeout·부분 실패 표현
- [ ] 기본 추출 대비 품질 개선 시에만 OCR 결과 채택
- [ ] OCR 원문, confidence, provider/version과 checksum 보존
- [ ] OCR을 기본 오프라인 suite에서 실제 호출하지 않도록 격리

### P1.5 Metadata normalization

- [x] 제목, 부제, authors, organization, 발행일/정밀도, 권·호, DOI, 초록과 키워드 추출
- [x] 원본 표기, normalized value, confidence와 evidence page 보존
- [x] 파일명 연도·processed date·published date를 분리
- [x] 긴 파일명·불완전 자모에서는 표지 근거를 우선
- [x] 추측 대신 `null`과 실패 사유를 반환

구현 기록(2026-08-09):

- 입력은 `ResearchPublication`, 페이지 근거가 있는 `Sequence[PublicationPage]`, 선택적
  `source_path`이며, 출력은 기존 계약의 `ExtractedPublicationMetadata`다. 변경 범위는
  `search/metadata.py`, 대응 unit test와 이 P1.5 기록뿐이고 새 의존성은 없다.
- `RuleBasedPublicationMetadataExtractor`는 `cover_page > body > filename >
  processing_metadata` 순으로 필드별 후보를 선택한다. 강한 후보가 있으면 약한 후보를
  병합하지 않고 버리며, 같은 강도의 값이 충돌하면 `null`과 실패 사유를 반환한다.
- 정규화 규칙 `nfc-whitespace-v1`은 NFC 조합, 제어·format 문자의 공백 치환, 연속 공백
  축약만 수행한다. 불완전 한글 자모를 추측해 완성하지 않으며 extractor provenance version에
  규칙 버전을 포함한다. `SEASON`의 date 운반값은 봄·여름·가을·겨울을 각각
  3월·6월·9월·12월 1일로 고정하되 정확한 월·일로 해석하지 않고 precision과 원문
  `issue_label`을 함께 보존한다.
- 결정적 offline unit test는 실제 자료에서 관찰된 국방논단 제어문자 표지, 국방정책연구
  계절호와 `*`/`**` 저자 각주, 연구보고서 월 표기, Brief 저자 표기, 긴·잘린 파일명,
  동일 강도 충돌, 미확정 필드와 byte-equivalent 재실행을 포함한다. 기본 suite는 실제
  `data/`나 네트워크를 읽지 않는다.
- 교차 검토 보완(2026-08-09)으로 extractor version을 `1.0.1`로 올렸다. 연구보고서 표지의
  `연구보고서 <분류><번호>`, 발행일과 ISBN은 제목이 아닌 구조 헤더로 제외하고, 헤더 뒤
  제목 블록을 파일명에서 확인한 첫 저자 또는 보수적인 저자 목록 형식 전까지만 채택한다.
  일련번호 전용 도메인 필드는 현 계약에 추가하지 않았으며 원본 `PublicationPage.text`에
  보존한다. 국방정책연구 제목은 줄별 `†`/`‡`와 독립·저자 결합형 숫자 각주를 제거하되
  `MetadataEvidence.raw_text`에는 원문 표기를 유지한다. 지배 연구보고서 레이아웃 두 변형과
  숫자 각주 두 변형을 deterministic unit fixture로 추가했다.
- 두 번째 교차 검토 보완(2026-08-09)으로 extractor version을 `1.0.2`로 올렸다. 실제
  연구보고서의 지배적 요약 헤딩인 `요 약`을 `요약`과 동일하게 인식하고, 여러 줄
  `Key words` 블록은 국방정책연구 권·호 러닝 헤더 전에 종료한다. 실제 corpus에서
  관찰된 두 텍스트 형태를 재현하는 경계 unit test로 추출값과 evidence 범위를 고정했다.
- 읽기 전용 corpus smoke 진단에서 문서 JSON 372/372개가 예외 없이 처리됐다. 이는 실행
  안정성 지표이지 metadata 정확도 지표가 아니다. 정답셋이 없으므로 precision/recall은
  기록하지 않으며, 기대 영향은 구조화 필드의 근거 추적성과 누락·충돌 가시성 향상이다.
- extractor는 publication 승인 상태를 바꾸지 않는다. 모호한 값, 파일명/발행 연도 충돌과
  도메인상 맞는 저자·기관 판정은 계속 사람 검수 대상이다.

### P1.6 Quality gate

- [ ] `ready`, `warning`, `low_text`, `corrupt_text`, `duplicate`, `orphan_pdf`, `manual_review` 계산
- [ ] empty text, control character, printable/Korean ratio와 page density 측정
- [ ] 품질 미달 문서를 기본 인덱스에서 제외
- [ ] threshold와 제외 사유를 versioned 설정으로 기록
- [ ] 재추출/OCR 대기열과 failure report 생성
- [ ] `DATA_QUALITY_REPORT.md`의 알려진 위험을 회귀 fixture로 반영

### P1.7 Page-aware / section-aware chunking

- [ ] 페이지 근거가 사라지지 않는 chunking algorithm 정의
- [ ] section boundary 우선, 최대 길이와 overlap 규칙 정의
- [ ] 표·각주·참고문헌 처리 정책 명시
- [ ] 동일 입력·version에서 byte-equivalent chunk 순서 보장
- [ ] `artifacts/corpus/chunks.jsonl`과 manifest 생성
- [ ] actual PDF page로 citation retrace integration test

## P2 — Retrieval

목적은 기존 lexical baseline을 유지하면서 vector·hybrid 검색을 평가 가능한 방식으로
추가하는 것이다.

### P2.1 `EmbeddingProvider` interface

- [ ] document/query embedding 계약과 batch capability 정의
- [ ] model ID, dimension, normalization, input checksum과 version metadata 정의
- [ ] timeout, partial failure와 invalid dimension 오류 모델 정의
- [ ] secret과 provider 원문을 결과·로그에서 제외

### P2.2 `FakeEmbeddingProvider`

- [ ] 외부 모델 없이 결정적인 embedding 생성
- [ ] 같은 입력·설정의 byte-equivalent 결과 보장
- [ ] batch, empty input, dimension과 Unicode 테스트
- [ ] ranking 의미를 과장하지 않고 interface·pipeline 테스트에만 사용

### P2.3 `VectorSearchAlgorithm`

- [ ] 기존 `PublicationSearchAlgorithm`을 최대한 유지하는 adapter 설계
- [ ] vector index abstraction과 content-addressed manifest 정의
- [ ] publication/chunk/page provenance 반환
- [ ] 동일 점수의 결정적 tie-breaker 정의
- [ ] index/model/chunking version 불일치 차단

### P2.4 `HybridSearchAlgorithm`

- [ ] lexical score와 vector score를 별도 보존
- [ ] Reciprocal Rank Fusion 또는 명시적 fusion 전략 선택
- [ ] fusion version과 parameter를 결과에 기록
- [ ] filter를 ranking 전후 어디에 적용하는지 명시
- [ ] lexical-only fallback과 부분 실패 처리

### P2.5 `Reranker` abstraction

- [ ] 입력 candidate 수와 반환 계약 정의
- [ ] deterministic fake reranker 제공
- [ ] provider/model/version과 latency·cost trace 기록
- [ ] reranker 실패 시 원래 hybrid 순위 보존 여부를 정책화
- [ ] untrusted text와 prompt injection 경계 테스트

### P2.6 Retrieval benchmark

- [ ] 전문가 curated 30~50개 초기 golden question 작성 지원
- [ ] relevant publication/page와 relevance grade schema 구현
- [ ] lexical, BM25, vector, hybrid, reranker 비교 harness 구현
- [ ] Recall@5, Recall@10, MRR와 선택적 nDCG 계산
- [ ] p50/p95 latency, memory, index size와 build time 측정
- [ ] experiment log와 baseline comparison report 생성

상세 metric과 dataset schema는 [EVALUATION.md](./EVALUATION.md)를 따른다.

## P3 — Research Copilot

선행 조건: P1 page-aware evidence와 P2 benchmarked retrieval.

- [ ] corpus question과 project context Pydantic schema
- [ ] evidence retrieval과 source inspection service
- [ ] claim-citation pair를 가진 grounded answer schema
- [ ] citation correctness/completeness 검증
- [ ] 근거 부족·충돌 시 abstention 정책
- [ ] 문서 비교와 follow-up question context
- [ ] offline fake model E2E와 optional provider suite
- [ ] 사람 검토 없이 보고서 사실을 확정하지 않는 출력 경계

## P4 — Learning Companion

선행 조건: P2 공통 retrieval과 P3 citation 기반.

- [ ] 초급·중급·전문가 설명 level schema
- [ ] 개념·관련 개념·출처 기반 learning path
- [ ] 읽을거리 추천과 page citation
- [ ] quiz와 knowledge check의 정답 근거
- [ ] research notebook 저장·내보내기 경계
- [ ] 학습 질문에서 Topic Discovery로 전달하는 명시적 사용자 action
- [ ] 별도 corpus를 만들지 않고 공통 infrastructure 공유

## P5 — Model Runtime

- [ ] 현재 Fake/Anthropic baseline 평가
- [ ] OpenAI-compatible adapter 제안과 contract test
- [ ] local model adapter와 vLLM/Ollama endpoint 후보 평가
- [ ] `LOCAL`, `HYBRID`, `CLOUD`, `BYOK` 정책 schema
- [ ] runtime별 quality, latency, cost, memory, GPU와 privacy 비교
- [ ] BYOK secret lifecycle과 masking 테스트
- [ ] provider-specific 설정을 adapter 밖으로 누출하지 않는지 검증

구현하지 않은 provider는 지원 완료로 문서화하지 않는다.

## P6 — Product UI

- [ ] 연구자 인터뷰와 Search/Learn/Research/Topics workflow 정의
- [ ] citation/source inspection interaction 설계
- [ ] Notebook과 Reports 정보 구조 검증
- [ ] untrusted external content와 model-generated content 표시
- [ ] human review, 충돌과 append-only audit UX
- [ ] API contract와 role-based access 요구 정의
- [ ] 프론트엔드 framework는 별도 결정 기록 후 선택

## P7 — Advanced Intelligence

P1~P3의 데이터·검색·RAG가 안정되고 use case가 검증된 뒤 수행한다.

- [ ] entity/relation/event schema와 provenance 요구 정의
- [ ] knowledge graph가 lexical/hybrid baseline보다 주는 가치 평가
- [ ] event timeline과 시간 정밀도·충돌 처리
- [ ] report builder의 citation·human approval 계약
- [ ] trend analysis의 시점 누출과 source bias 평가
- [ ] 실험 결과에 따라 구현·보류·폐기 결정

## Definition of Done

모든 구현 task에 공통 적용한다.

- [ ] domain model과 interface 검토 완료
- [ ] 입력, 출력, 변경 파일과 의존성 기록
- [ ] unit test 작성
- [ ] failure-path test 작성
- [ ] 경계 조건 테스트 작성
- [ ] 필요한 경우 integration test 작성
- [ ] deterministic offline test 제공
- [ ] 외부 API·모델·시간·파일 시스템 의존성을 fake 또는 fixture로 격리
- [ ] `uv run pytest` 통과
- [ ] `uv run mypy` strict 통과
- [ ] `uv run ruff check .` 통과
- [ ] `uv run ruff format --check .` 통과
- [ ] 관련 documentation 업데이트
- [ ] `data/` 원본 미변경 확인
- [ ] secret·실제 `.env` 미커밋 확인
- [ ] 평가 metric과 예상 영향 기록
- [ ] human approval boundary 유지 확인

Retrieval 변경에는 추가로 적용한다.

- [ ] versioned benchmark 실행
- [ ] lexical baseline 비교
- [ ] Recall@5, Recall@10, MRR 기록
- [ ] p50/p95 latency 측정
- [ ] memory, index size와 build time 기록
- [ ] query slice별 회귀와 대표 실패 사례 기록
- [ ] fusion/rerank score trace 보존

## Human vs Codex

### 사람이 직접 책임질 것

- 문제 정의
- 국방 도메인 판단
- 데이터셋 선정
- evaluation dataset 생성·검수
- 아키텍처 결정
- 제품 우선순위
- 실험 결과 해석
- trade-off 수용 여부 결정

### Codex가 주로 수행할 것

- 승인된 설계의 구현
- 단위·실패·통합 테스트
- provider와 storage adapter 작성
- 안전한 리팩터링
- boilerplate와 schema 구현
- 문서 업데이트
- benchmark harness와 재현 가능한 report 구현

Codex가 제품 방향, 관련도 label이나 국방 도메인 판단을 자율적으로 확정하지 않는다.

권장 개발 루프:

```text
Human이 문제 정의
    -> Codex 구현
    -> Human 리뷰
    -> benchmark / evaluation
    -> Human 결과 해석
    -> 다음 구현 결정
```

## Backlog 운영 규칙

- 현재 상태와 완료 여부는 코드·테스트·실행 증거를 기준으로 표시한다.
- 여러 계층을 바꾸는 task는 구현 전에 입력·출력·변경 파일·완료 조건·테스트·의존성을
  이 문서에 추가한다.
- 구현 범위가 [ROADMAP.md](./ROADMAP.md)의 Epic 또는
  [DECISIONS.md](./DECISIONS.md)의 결정과 달라지면 먼저 문서를 갱신한다.
- 실험 결과와 생성 로그는 `artifacts/`에 기록하고 원본 `data/`에는 쓰지 않는다.
- 신규 agent 역할은 데이터·검색 기반의 명확한 병목을 해결할 때만 검토한다.

---

## Legacy file pilot plan and completed implementation record

아래 내용은 데이터 조사에서 P020까지 이어진 기존 상세 계획과 완료 기록이다. 현재
우선순위는 위의 P0~P7을 사용하며, 아래 티켓은 과거 설계 의도·수치·변경 파일을 추적할
때 참고한다. 기존의 유용한 맥락을 보존하기 위해 삭제하지 않았다.

<details>
<summary>기존 파일럿 구현 계획 펼치기</summary>

# defense-research-agent 파일럿 구현 계획

## 1. 목표

로컬의 한국국방연구원 공개 연구자료와 향후 연결할 최신 이슈 데이터를 근거로 국방정책 연구주제 후보를 생성·평가·추천하고, 최종 선택은 사람이 승인하는 멀티 에이전트 시스템을 단계적으로 구현한다.

이번 계획은 현재 확인한 실제 데이터 구조를 기준으로 한다. `data/`는 읽기 전용 원본으로 취급하며 모든 정규화·색인·실행 결과는 `artifacts/` 아래 파생 산출물로 만든다.

## 2. 설계 원칙

1. **원본 불변**: `data/` 아래에는 어떤 파일도 생성·수정·삭제·이동하지 않는다.
2. **근거 우선**: 후보 주제와 평가에는 문서 ID, PDF 상대 경로, 페이지 번호를 포함한다.
3. **품질 게이트**: 저추출·손상·중복 문서는 색인 전에 상태를 부여하고 기본 검색에서 제외한다.
4. **명시적 인간 승인**: 추천 결과는 자동 게시·확정하지 않는다. `approved` 전이는 사람만 수행한다.
5. **오프라인 재현성**: 첫 수직 프로토타입은 외부 API 대신 고정된 최신 이슈 fixture와 로컬 검색을 사용한다.
6. **교체 가능한 모델/검색기**: 생성 모델, 임베딩, 웹 검색 제공자를 인터페이스 뒤에 둔다. 테스트는 fake 구현으로 실행한다.
7. **비밀정보 비저장**: 키나 토큰을 코드·fixture·로그에 넣지 않는다.
8. **감사 가능성**: 입력 버전, 프롬프트 버전, 에이전트 결과, 사람의 판단과 시각을 실행 레코드에 남긴다.
9. **보수적 자동화**: 근거가 부족하거나 에이전트 평가가 충돌하면 추천 대신 `needs_review`를 반환한다.

## 3. 제안 아키텍처

```mermaid
flowchart LR
    A["data/ PDF + JSON (read-only)"] --> B["Corpus inventory & quality gate"]
    B --> C["Canonical documents + page chunks"]
    C --> D["Local lexical retrieval"]
    E["Offline issue fixtures"] --> F["Issue adapter"]
    F --> G["Evidence bundle builder"]
    D --> G
    G --> H["Topic generation agent"]
    H --> I1["Policy relevance evaluator"]
    H --> I2["Novelty evaluator"]
    H --> I3["Evidence & timeliness evaluator"]
    H --> I4["Feasibility & impact evaluator"]
    I1 --> J["Recommendation synthesizer"]
    I2 --> J
    I3 --> J
    I4 --> J
    J --> K["Human approval gate"]
    K --> L["Approved / rejected / revision requested"]
    K --> M["Audit artifacts"]
```

첫 프로토타입에서 “에이전트”는 각각 입력/출력 JSON 스키마와 독립 평가 책임을 가진 실행 단위다. 단순히 하나의 긴 프롬프트를 여러 이름으로 나누지 않는다.

## 4. 제안 프로젝트 구조

아래는 구현 티켓에서 생성할 구조이며 현재 존재하는 파일이 아니다.

```text
defense-research-agent/
├── data/                              # 원본, 읽기 전용
├── artifacts/                         # 생성물, data와 분리
│   ├── inventory/
│   ├── corpus/
│   ├── index/
│   └── runs/
├── configs/
│   ├── quality.yaml
│   └── scoring.yaml
├── fixtures/
│   └── issues/
├── src/defense_research_agent/
│   ├── domain/
│   ├── ingestion/
│   ├── quality/
│   ├── parsing/
│   ├── retrieval/
│   ├── issues/
│   ├── agents/
│   ├── workflow/
│   └── interfaces/
├── tests/
│   ├── fixtures/
│   ├── unit/
│   ├── integration/
│   └── e2e/
└── pyproject.toml
```

`artifacts/`는 원본이 아니며 재생성 가능해야 한다. 대형 PDF나 원문 복사본을 이 경로에 중복 저장하지 않는다.

## 5. 개발 티켓

각 티켓은 단독으로 리뷰·테스트 가능한 크기로 정의했다. “변경 파일”은 해당 티켓에서 새로 만들거나 수정할 예정인 파일이다.

### T001. 읽기 전용 코퍼스 매니페스트와 연결 감사

- **목적**: 현재 파일 구조를 코드가 재현 가능하게 스캔하고 PDF-JSON 연결, 고아 파일, 논리 중복을 명시한다.
- **입력**: `data/` 경로
- **출력**:
  - `artifacts/inventory/files.jsonl`
  - `artifacts/inventory/document_links.jsonl`
  - `artifacts/inventory/findings.json`
  - 실행 요약 표준 출력
- **변경 파일**:
  - `pyproject.toml`
  - `src/defense_research_agent/domain/source_file.py`
  - `src/defense_research_agent/ingestion/inventory.py`
  - `src/defense_research_agent/interfaces/cli.py`
  - `tests/unit/ingestion/test_inventory.py`
  - `tests/integration/test_current_data_inventory.py`
- **완료 조건**:
  - PDF 371, JSON 373, `.DS_Store` 3을 재현한다.
  - 문서 JSON 372, 집계 JSON 1을 구분한다.
  - 고유 연결 370, orphan PDF 1, 3:1 중복 그룹 1을 보고한다.
  - 비교용 키는 NFC 정규화하되 원본 이름을 그대로 보존한다.
  - `data/`의 처리 전후 SHA-256 목록이 동일하다.
- **테스트**:
  - 정상 1:1 연결, orphan, 여러 JSON→하나의 PDF, 잘못된 JSON, 분해형/조합형 한글 fixture
  - 현재 데이터 수치에 대한 읽기 전용 통합 테스트
  - 동일 입력의 산출물 정렬과 ID가 결정적인지 확인
- **의존성**: 없음

### T002. 표준 문서 모델과 안정적 문서 ID

- **목적**: 원본 레코드를 변경하지 않고 후속 파이프라인이 사용할 표준 모델로 변환한다.
- **입력**: T001 `document_links.jsonl`, 문서 JSON
- **출력**: `artifacts/corpus/canonical_documents.jsonl`
- **변경 파일**:
  - `src/defense_research_agent/domain/document.py`
  - `src/defense_research_agent/ingestion/normalize.py`
  - `tests/unit/ingestion/test_normalize.py`
- **완료 조건**:
  - `document_id`, `source_type`, 원본/NFC 파일명, 저장소 상대 경로, PDF 해시, 페이지 수, 원본 메타데이터를 보존한다.
  - `metadata.path`를 식별 키로 사용하지 않는다.
  - 논리 중복은 하나의 canonical 문서와 여러 source-record 계보로 표현한다.
  - `filename_year`, `processed_at`, 아직 확인하지 못한 `published_at=null`을 구분한다.
- **테스트**:
  - 경로가 달라도 같은 PDF는 같은 ID
  - PDF 내용이 달라지면 ID 변경
  - 원본 값 손실 없는 round-trip 검사
- **의존성**: T001

### T003. 텍스트 품질 점수와 색인 게이트

- **목적**: 불완전하거나 손상된 추출 텍스트가 검색·생성 결과를 오염시키지 않게 한다.
- **입력**: T002 canonical 문서와 `page_texts`
- **출력**:
  - 품질 필드가 추가된 canonical 문서
  - `artifacts/corpus/reextract_queue.jsonl`
- **변경 파일**:
  - `configs/quality.yaml`
  - `src/defense_research_agent/quality/text_quality.py`
  - `src/defense_research_agent/quality/gates.py`
  - `tests/unit/quality/test_text_quality.py`
  - `tests/integration/test_quality_baseline.py`
- **완료 조건**:
  - `ready`, `warning`, `low_text`, `corrupt_text`, `duplicate`, `orphan_pdf`, `manual_review` 상태를 계산한다.
  - 1,000자 이하 38개와 심각한 제어문자 문서를 기본 색인에서 제외한다.
  - 임계값과 사유를 결과에 기록한다.
  - 원본 텍스트는 보존하고 정제 텍스트는 별도 필드에 둔다.
- **테스트**:
  - 빈 페이지 비율, 제어문자 비율, 쪽당 문자 수 경계값
  - `2023_이상국_인태지역주요군사력과전략동향` 특성을 축소한 fixture
  - 품질 설정 변경 시 상태 변화 테스트
- **의존성**: T002

### T004. 자료 유형별 서지·구조 파서

- **목적**: 네 유형에서 제목, 저자, 발행일, 요약/초록, DOI, 키워드와 본문 구간을 근거 위치와 함께 추출한다.
- **입력**: T003에서 `ready` 또는 `warning`인 문서
- **출력**: 구조화 필드와 필드별 `confidence`, `evidence_page`, `raw_text`
- **변경 파일**:
  - `src/defense_research_agent/parsing/base.py`
  - `src/defense_research_agent/parsing/brief.py`
  - `src/defense_research_agent/parsing/defense_forum.py`
  - `src/defense_research_agent/parsing/policy_studies.py`
  - `src/defense_research_agent/parsing/research_report.py`
  - `tests/fixtures/gold_documents/`
  - `tests/unit/parsing/`
- **완료 조건**:
  - 네 유형 각각 최소 5개 수작업 gold 표본에서 제목과 대표저자 정확도 100%
  - 복수 저자 순서, 원문 표기, 소속/이메일을 가능한 범위에서 분리
  - 날짜 정밀도(`day`, `month`, `season`, `year`)를 보존
  - `국방정책연구`의 DOI, Abstract, Keywords를 분리
  - 실패 시 추측값 대신 `null`과 사유 반환
- **테스트**:
  - 유형별 표지 변형, 계절호, 별표 저자 각주, 공백/제어문자
  - 파일명 연도와 본문 연도 충돌
  - 긴 파일명/잘린 제목에서 표지 제목 우선
- **의존성**: T003

### T005. 페이지 근거를 보존하는 청킹과 로컬 검색

**현재 상태 (2026-08-09): 부분 구현**

- [x] `PublicationPage`와 page-aware `PublicationChunk` domain model
- [x] page range·text checksum·chunking version을 보존하는 결정적 page chunker
- [ ] JSON `page_texts` 또는 PDF parser를 `PublicationPage`로 연결
- [ ] `artifacts/corpus/chunks.jsonl` 생성과 품질 게이트 연결
- [ ] chunk-level lexical index와 page citation 검색 결과

위 체크 항목은 T005 전체 완료를 의미하지 않는다. 현재 publication-level lexical 검색은
유지되며 chunk artifact와 retrieval 연결은 아직 없다.

- **목적**: 외부 임베딩 API 없이 재현 가능한 첫 검색 계층을 만든다.
- **입력**: T004 구조화 문서와 페이지 텍스트
- **출력**:
  - `artifacts/corpus/chunks.jsonl`
  - `artifacts/index/lexical/`
  - 페이지 인용이 포함된 검색 결과
- **변경 파일**:
  - `src/defense_research_agent/domain/publication.py`
  - `src/defense_research_agent/domain/search.py`
  - `src/defense_research_agent/search/chunking.py`
  - `src/defense_research_agent/search/lexical.py`
  - `tests/unit/search/`
  - `tests/integration/test_retrieval.py`
- **완료 조건**:
  - 청크마다 `publication_id`, 페이지 시작/끝, 텍스트 checksum과 chunking version을 가진다.
  - 품질 게이트 제외 문서는 기본 인덱스에 들어가지 않는다.
  - 같은 입력에서 청크와 검색 순서가 결정적이다.
  - 검색 결과가 실제 페이지 텍스트로 역추적된다.
- **테스트**:
  - 페이지 경계를 넘는 청크, 짧은 문서, 중복 문서 제외
  - 국방 AI/인력/획득 등 골든 질의의 관련 문서 회수
  - 인덱스 재생성 동일성
- **의존성**: T004

### T006. 최신 이슈 표준 모델과 오프라인 어댑터

- **목적**: 향후 웹 검색 제공자와 현재 오프라인 프로토타입이 같은 이슈 스키마를 사용하게 한다.
- **입력**: 사람이 검토해 저장한 JSON fixture
- **출력**: 정규화된 `IssueItem[]`
- **변경 파일**:
  - `src/defense_research_agent/domain/issue.py`
  - `src/defense_research_agent/issues/base.py`
  - `src/defense_research_agent/issues/fixture_provider.py`
  - `fixtures/issues/pilot.json`
  - `tests/unit/issues/test_fixture_provider.py`
- **완료 조건**:
  - `issue_id`, 제목, 요약, 발생일/게시일, 출처명, URL, 수집시각, 원문 해시, 검토 상태를 표현한다.
  - fixture는 공개정보만 포함하고 비밀정보·토큰을 포함하지 않는다.
  - 중복 URL과 동일 내용 이슈를 결정적으로 병합한다.
- **테스트**:
  - 날짜 정밀도, URL 정규화, 중복, 필수 필드 누락
  - 네트워크 호출이 발생하지 않는지 확인
- **의존성**: 없음

### T007. 이슈-코퍼스 근거 묶음 생성

- **목적**: 각 최신 이슈에 관련된 KIDA 근거와 기존 연구의 공백을 생성 에이전트 입력으로 묶는다.
- **입력**: T005 검색기, T006 `IssueItem[]`
- **출력**: `EvidenceBundle` JSON
- **변경 파일**:
  - `src/defense_research_agent/retrieval/evidence.py`
  - `src/defense_research_agent/domain/evidence.py`
  - `tests/unit/retrieval/test_evidence.py`
  - `tests/integration/test_issue_to_evidence.py`
- **완료 조건**:
  - 이슈마다 상위 근거와 반대/인접 근거를 구분한다.
  - 모든 인용은 문서 ID와 페이지를 가진다.
  - 자료 유형 하나가 결과를 독점하지 않도록 유형별 한도를 설정할 수 있다.
  - 근거 부족 시 명시적인 `insufficient_evidence`를 반환한다.
- **테스트**:
  - 인용 역추적, 유형 다양성, 중복 청크 억제
  - 관련 문서가 없는 이슈 처리
- **의존성**: T005, T006

### T008. 연구주제 후보 생성 에이전트

- **목적**: 이슈와 근거 묶음으로 검증 가능한 연구질문 후보를 생성한다.
- **입력**: `EvidenceBundle`, 후보 수, 정책 분야 제약
- **출력**: `TopicCandidate[]`
- **변경 파일**:
  - `src/defense_research_agent/agents/model_gateway.py`
  - `src/defense_research_agent/agents/topic_generator.py`
  - `src/defense_research_agent/domain/topic.py`
  - `src/defense_research_agent/agents/prompts/topic_generator.md`
  - `tests/unit/agents/test_topic_generator.py`
- **완료 조건**:
  - 후보마다 제목, 연구질문, 정책 배경, 예상 기여, 연구 범위, 방법 후보, 근거 인용, 가정을 가진다.
  - 근거에 없는 사실은 주장으로 확정하지 않고 가정/확인 필요로 표시한다.
  - 모델 출력은 스키마 검증 실패 시 제한 횟수 내 재시도 후 실패한다.
  - 테스트는 결정적 fake model을 사용하며 API 키가 필요 없다.
- **테스트**:
  - 유효/무효 모델 출력, 인용 없는 후보 거부, 프롬프트 버전 고정
  - 같은 fake 응답의 결정적 파싱
- **의존성**: T007

### T009. 독립 평가 에이전트와 점수 보정

- **목적**: 후보를 서로 다른 평가 책임으로 검토하고 단일 모델의 자기평가 편향을 줄인다.
- **입력**: `TopicCandidate`, `EvidenceBundle`, 평가 설정
- **출력**: 평가자별 `EvaluationCard`
- **변경 파일**:
  - `configs/scoring.yaml`
  - `src/defense_research_agent/agents/evaluators.py`
  - `src/defense_research_agent/domain/evaluation.py`
  - `src/defense_research_agent/agents/prompts/evaluators/`
  - `tests/unit/agents/test_evaluators.py`
- **완료 조건**:
  - 정책 관련성, 기존 KIDA 연구 대비 신규성, 시의성/근거성, 수행 가능성/정책 파급의 네 평가를 분리한다.
  - 각 점수는 근거, 불확실성, 치명적 결함을 포함한다.
  - 평가자 간 점수 차이가 임계값을 넘으면 `needs_review`가 된다.
  - 평가 입력에는 다른 평가자의 점수를 노출하지 않는다.
- **테스트**:
  - 평가자 격리, 점수 범위, 치명적 결함 규칙, 충돌 감지
  - 근거가 빈 후보의 하향 평가
- **의존성**: T008

### T010. 추천 합성기와 인간 승인 상태기계

- **목적**: 평가 결과를 설명 가능한 순위로 합성하고 사람만 최종 상태를 바꾸게 한다.
- **입력**: 후보와 평가 카드
- **출력**:
  - 순위가 있는 `RecommendationSet`
  - `ApprovalDecision`
  - append-only 실행/판단 로그
- **변경 파일**:
  - `src/defense_research_agent/workflow/recommendation.py`
  - `src/defense_research_agent/workflow/approval.py`
  - `src/defense_research_agent/domain/approval.py`
  - `tests/unit/workflow/`
- **완료 조건**:
  - 허용 상태는 `proposed`, `needs_review`, `approved`, `rejected`, `revision_requested`다.
  - 에이전트는 `approved` 상태를 만들 수 없다.
  - 가중치와 탈락 사유를 결과에 노출한다.
  - 승인자 입력, 시각, 코멘트, 대상 후보 버전을 기록한다.
- **테스트**:
  - 상태 전이 표 전체, 무권한 자동 승인 거부
  - 동점/평가 충돌/근거 부족 처리
  - 로그 append-only 동작
- **의존성**: T009

### T011. 첫 수직 프로토타입 CLI 조립

- **목적**: 한 명의 연구자가 로컬에서 이슈 선택부터 후보 승인까지 한 흐름을 실행하게 한다.
- **입력**: 품질 통과 문서 표본, 오프라인 이슈 fixture, 실행 설정
- **출력**:
  - 콘솔의 후보·평가·근거 보기
  - `artifacts/runs/<run_id>/run.json`
  - 승인 결정 JSON
- **변경 파일**:
  - `src/defense_research_agent/workflow/pilot.py`
  - `src/defense_research_agent/interfaces/cli.py`
  - `configs/pilot.yaml`
  - `tests/e2e/test_pilot_workflow.py`
- **완료 조건**:
  - `inventory → quality → parse → retrieve → generate → evaluate → recommend → human decision`이 한 명령 흐름으로 동작한다.
  - 후보의 모든 KIDA 인용을 PDF 페이지까지 조회할 수 있다.
  - 승인 입력 없이는 실행이 `proposed`로 끝난다.
  - 실행 재현에 필요한 입력 해시·설정·프롬프트 버전을 저장한다.
- **테스트**:
  - fake model 기반 완전 오프라인 E2E
  - 승인/거절/수정요청 세 경로
  - 네트워크 차단 환경에서 성공
  - 실행 전후 `data/` 해시 불변
- **의존성**: T010

### T012. 골든셋과 회귀 평가

- **목적**: “그럴듯한 결과”가 아니라 회수율·근거성·평가 일관성을 측정한다.
- **입력**: 연구자가 검토한 질의, 관련 문서, 후보/점수 기준
- **출력**: `artifacts/runs/<run_id>/evaluation.json`
- **변경 파일**:
  - `tests/fixtures/golden_cases/`
  - `src/defense_research_agent/evaluation/harness.py`
  - `tests/integration/test_golden_cases.py`
- **완료 조건**:
  - 검색 Recall@K, 인용 유효율, 무근거 후보율, 평가자 불일치율을 계산한다.
  - 기준 이하이면 CI가 실패한다.
  - 모델이 없는 테스트와 모델이 있는 선택적 평가를 분리한다.
- **테스트**:
  - 지표 계산식과 실패 임계값
  - 고의로 깨진 인용과 중복 후보 탐지
- **의존성**: T011

### T013. 저추출 문서 재추출/OCR 어댑터

- **목적**: 원본을 바꾸지 않고 `low_text`/`corrupt_text` 문서의 대체 텍스트를 생성한다.
- **입력**: T003 재추출 대기열과 원본 PDF
- **출력**: `artifacts/corpus/reextracted/<document_id>.json`
- **변경 파일**:
  - `src/defense_research_agent/ingestion/extractors/base.py`
  - `src/defense_research_agent/ingestion/extractors/pdf_text.py`
  - `src/defense_research_agent/ingestion/extractors/ocr.py`
  - `tests/integration/test_reextraction.py`
- **완료 조건**:
  - 원본/대체 추출기, 버전, 페이지별 신뢰도를 기록한다.
  - 대체 텍스트가 기존 품질을 실제로 개선한 경우만 채택한다.
  - 원본 JSON과 PDF를 수정하지 않는다.
- **테스트**:
  - 이미지형 PDF fixture, 잘못된 Unicode 매핑 fixture
  - 품질 악화 시 기존 결과 유지
- **의존성**: T003

### T014. 실제 최신 이슈 검색 제공자

- **목적**: 검증된 수직 흐름에 외부 최신 이슈 수집을 추가한다.
- **입력**: 검색 질의, 기간, 허용 출처 정책
- **출력**: T006과 같은 `IssueItem[]` 및 원문 스냅샷/출처 메타데이터
- **변경 파일**:
  - `src/defense_research_agent/issues/web_provider.py`
  - `configs/sources.yaml`
  - `tests/contract/test_web_provider.py`
- **완료 조건**:
  - 출처 URL, 게시일, 수집일, 원문 해시, 검색 질의를 보존한다.
  - 로봇 정책·이용약관·재시도·속도 제한·중복 제거를 적용한다.
  - 자격증명은 런타임 환경에서만 읽고 로그에 남기지 않는다.
  - 사람이 출처를 확인하기 전에는 `reviewed=false`다.
- **테스트**:
  - 네트워크를 모킹한 계약 테스트
  - 시간 초과, 속도 제한, 중복 기사, 게시일 누락
  - 비밀정보 로그 유출 검사
- **의존성**: T006, T010, T012

## 6. 티켓 의존성

```mermaid
flowchart TD
    T001 --> T002 --> T003 --> T004 --> T005
    T006 --> T007
    T005 --> T007 --> T008 --> T009 --> T010 --> T011 --> T012
    T003 --> T013
    T006 --> T014
    T010 --> T014
    T012 --> T014
```

병렬로 시작할 수 있는 티켓은 T001과 T006이다. T013은 기본 품질 게이트가 안정된 후 별도 진행할 수 있고, T014는 오프라인 수직 흐름과 회귀 기준이 통과된 뒤에만 연결한다.

## 7. 첫 번째 수직 프로토타입 범위

### 포함

- 각 자료 유형에서 품질 게이트를 통과한 최신 문서 10개씩, 총 40개
- 사람이 검토한 오프라인 최신 이슈 fixture 5개
- 유형별 제목·저자·날짜·초록/요약의 최소 파싱
- 페이지 단위 근거가 보존되는 로컬 lexical 검색
- 이슈당 연구주제 후보 최대 5개
- 4개 독립 평가 역할
- 상위 3개 추천과 평가 충돌 표시
- CLI에서 근거 확인 후 승인·거절·수정요청
- 실행별 JSON 감사 산출물
- 테스트에서는 fake model만 사용하고 네트워크 호출 금지

### 제외

- 실시간 웹 검색
- 전체 371개 PDF 색인
- OCR/재추출
- 외부 임베딩 또는 벡터 DB
- 운영 DB, 사용자 계정, 권한관리, 배포
- 자동 승인·자동 게시
- 비공개/비밀 자료

### 성공 기준

- 표본 40개가 원본 수정 없이 결정적으로 매니페스트·색인된다.
- 모든 추천 후보가 최소 2개의 KIDA 페이지 인용을 갖거나 `insufficient_evidence`로 탈락한다.
- 같은 입력과 fake model 응답으로 같은 순위와 실행 산출물이 생성된다.
- 사람이 승인하기 전에는 어떤 후보도 `approved`가 아니다.
- 골든 질의의 관련 문서 Recall@5 목표를 초기 0.8 이상으로 둔다.
- 인용의 문서/페이지 역추적 성공률은 100%여야 한다.
- 무근거 인용 허용률은 0%여야 한다.

## 8. 현재 기준 다음 Document Intelligence 티켓

읽기 전용 reader, checksum, PDF-JSON 연결, 중복 계보, `ResearchPublication` 정규화와
실패 보고는 현재 코드에 구현돼 있다. 초기 T001/T002의 산출물 이름과 정확히 같지는
않지만 핵심 경계는 `data/readers/`와 `services/ingestion.py`에 존재한다.

2026-08-09에 `PublicationChunk` 기반을 보강한 뒤의 다음 단일 작업은 **Document Parser
abstraction과 JSON page adapter**다.

완료 조건:

- 원본 JSON `page_texts`를 검증된 `PublicationPage[]`로 변환한다.
- publication ID와 page 번호를 잃지 않고 chunker에 전달한다.
- malformed page, 중복/역순 page와 metadata page count 불일치를 구조화된 실패로 남긴다.
- 원본 `data/`는 변경하지 않고 파생 page/chunk artifact만 `artifacts/`에 기록한다.
- 아직 PDF 직접 extraction, OCR, embedding, vector/hybrid search와 RAG는 연결하지 않는다.

## 9. Prompt 9~12 파일럿 완성 계획

기존 T009~T012를 현재 도메인 모델과 LangGraph 구현에 맞춰 다음 네 개의 독립
변경 단위로 구체화한다. 각 단계는 앞 단계의 Pydantic 산출물만 입력으로 사용한다.

### P009. 독립 평가와 부분 실패 집계

- **입력**: `TopicCandidate[]`, `TopicSignal[]`, 내부 `ResearchPublicationRepository`
- **출력**: 후보별 `EvaluationResult[]`, 평가 실패, 누락 기준, Python 종합점수
- **변경 파일**: `domain/evaluation.py`, `agents/evaluators.py`,
  `services/evaluation.py`, `graph/research_workflow.py`, 관련 단위·그래프 테스트
- **완료 조건**: 네 평가 역할이 다른 평가 결과를 입력으로 받지 않고 병렬 실행되며,
  제한된 재시도 뒤 실패를 후보별 기록으로 보존한다. 무근거 고득점과 직접 중복 신규성
  점수는 Python 규칙으로 제한한다.
- **테스트**: 독립성, 실제 병렬성, 범위, 근거 게이트, 부분 실패, 누락 기준,
  재현성, 직접 중복 감점
- **의존성**: T008

### P010. 결정적 랭킹과 다양성 조정

- **입력**: 후보, P009 집계, 신호 속성, `configs/scoring.json`
- **출력**: 원점수, 감점, 다양성 조정, 최종 순위와 계산 내역
- **변경 파일**: `configs/scoring.json`, `domain/ranking.py`,
  `services/ranking.py`, `graph/research_workflow.py`, `docs/EVALUATION_CRITERIA.md`,
  관련 단위·그래프 테스트
- **완료 조건**: 일곱 기준의 가중합과 감점·동점 처리를 순수 Python 함수로 수행하고,
  다양성 비활성화가 가능하며 같은 입력에서 byte-equivalent 순위가 나온다.
- **테스트**: 가중치, 모든 감점 계열, 동점, 분야·국가 편중 완화, 비활성화,
  부족한 후보 수, 재현성
- **의존성**: P009

### P011. 인간 승인 중단·재개와 주제기획 카드

- **입력**: `artifacts/runs/{run_id}/ranked_candidates.json`, 연구자 결정·수정
- **출력**: append-only `review_history.jsonl`, 승인 후보의
  `topic_planning_cards.json`
- **변경 파일**: `domain/review.py`, `repositories/review_history.py`,
  `services/review.py`, `graph/research_workflow.py`, `cli/review_topics.py`,
  README와 관련 테스트
- **완료 조건**: 승인 입력 전 그래프가 `awaiting_review`로 종료되고 최종 카드를
  만들지 않는다. 같은 `run_id`의 승인·수정승인만 카드로 변환하고 보류·제외는 차단한다.
- **테스트**: 네 결정, 중단·재개, 잘못된 ID, append-only 이력, 미승인 출력 차단
- **의존성**: P010

### P012. 재현 가능한 품질 하네스와 파일럿 판정

- **입력**: 정규화·수집 보고서, 검색 저장소, 생성·평가·랭킹 실행 산출물,
  선택적 전문가 골든셋
- **출력**: `evaluation_summary.json`, `evaluation_report.md`,
  `expert_review_template.csv`, `docs/PILOT_RESULT.md`
- **변경 파일**: `evaluation/harness.py`, `cli/evaluate_pilot.py`,
  평가 fixture·테스트, README와 파일럿 결과 문서
- **완료 조건**: 실제 계산 가능한 지표만 수치화하고 골든셋이 필요한 지표는
  `unavailable`로 표시한다. 시점 분할은 파일명 연도보다 미래인 문서를 입력에서
  제외하고 누출 건수를 명시한다.
- **테스트**: 지표 계산, unavailable, 누출 탐지, 실패 사례 보존, 출력 3종,
  오프라인 재현성
- **의존성**: P011

```mermaid
flowchart LR
    T008 --> P009 --> P010 --> P011 --> P012
```

## 10. P013 7개 역할 연구실 수직 슬라이스

- **목적**: 기존 주제 생성·평가 파이프라인 위에 연구 계획부터 PoC 제안과 독립 검토,
  최종 종합까지 수행하는 연구실 단위 오케스트레이션을 추가한다.
- **입력**: 사람의 `ResearchBrief`, 역할별 `ModelRoute`, provider-neutral
  `ModelGateway`
- **출력**: `ResearchPlan`, 전문 연구 결과 4종, 독립 검토 2종, 부분 실패와
  사람 검토 대기 상태의 `ResearchLabReport`
- **변경 파일**:
  - `domain/research_lab.py`
  - `agents/research_lab.py`
  - `services/research_lab.py`
  - `cli/research_lab_demo.py`
  - `docs/RESEARCH_LAB.md`
  - 관련 domain·agent·service·통합 테스트
- **완료 조건**:
  - 일곱 역할의 책임, 모델 라우트와 허용 도구가 코드 계약으로 분리된다.
  - 메인 연구자가 네 전문 역할에 중복 없는 과업을 배정하고 병렬 실행한다.
  - 근거 감사와 비판 검토는 같은 전문 연구 스냅샷을 독립적으로 받는다.
  - 한 역할의 실패가 다른 성공 결과를 취소하지 않고 최종 종합에 전달된다.
  - 개발 연구자는 코드·테스트 산출물을 제안할 수 있지만 배포 권한이 없다.
  - 최종 상태는 항상 사람 검토 대기이며 자동 승인은 불가능하다.
  - 실제 API 없이 Fake gateway로 전체 경로를 재현할 수 있다.
- **테스트**: 역할 수·권한, 계획 검증, 구조화 출력 scope 검증, 실제 병렬 실행,
  부분 실패, 독립 검토 context, 사람 승인 경계, 오프라인 CLI
- **후속 의존성**:
  - Claude structured-output gateway와 계약 테스트
  - GCP 비동기 실행·상태·감사 저장소

## 11. P014 역할별 검색 도구와 증거 주입

- **목적**: 현재 내부 발간물 검색과 외부 이슈 Provider를 역할 권한 안에서 실행하고,
  검색 근거를 모델 입력과 감사 결과에 연결한다.
- **입력**: `ResearchTask.requested_tools`, 역할 `allowed_tools`, 검색 질의,
  `ResearchPublicationRepository`, `ExternalIssueSearchProvider`
- **출력**: evidence ID, 출처, locator, excerpt, 신뢰 표시와 실패를 포함한
  `ResearchToolContext`
- **변경 파일**:
  - `domain/research_lab.py`
  - `agents/research_lab.py`
  - `services/research_tools.py`
  - `services/research_lab.py`
  - `cli/research_lab_demo.py`
  - 관련 agent·service·통합 테스트와 연구실 문서
- **완료 조건**:
  - 모델 계획의 요청 도구가 역할 허용 목록 밖이면 모델 실행 전에 차단한다.
  - 내부 검색 결과는 발간물 ID와 검색 점수·일치 필드·로컬 경로를 보존한다.
  - 외부 검색 결과는 정규화 URL·게시일·신뢰도와 untrusted 표시를 보존한다.
  - Provider 부분 실패가 성공 근거를 버리지 않고 태스크 context에 남는다.
  - context나 동료 결과에 없는 evidence ID를 에이전트가 인용할 수 없다.
  - Fake model 기반 오프라인 데모에서 내부·외부 근거가 실제 주입된다.
- **테스트**: 내부 검색 변환, 외부 정규화·부분 실패, adapter 미설정, 역할 권한 위반,
  허용·허위 evidence ID, CLI 근거 주입
- **다음 우선순위**:
  - 분석 연구자의 제한된 `data_analysis_sandbox`
  - Claude structured-output gateway

## 12. P015 개발·PoC 코드 샌드박스

- **목적**: 개발 연구자의 구조화된 코드 변경안을 원본과 분리된 임시 작업공간에서
  검증하고, diff와 검사 결과를 PI와 사람 검토자에게 전달한다.
- **입력**: `ProposedArtifact(kind=code_patch)`, `CodeFileChange[]`,
  `CodeSandboxValidation[]`
- **출력**: 변경 경로, unified diff, 검사별 결과, 차단 사유와
  `applied_to_source=false`, `deployed=false`인 `CodeSandboxResult`
- **변경 파일**:
  - `domain/research_lab.py`
  - `services/code_sandbox.py`
  - `services/research_lab.py`
  - `agents/research_lab.py`
  - `cli/research_lab_demo.py`
  - 관련 domain·service·통합 테스트와 연구실 문서
- **완료 조건**:
  - 생성과 교체만 지원하고 삭제를 지원하지 않는다.
  - 교체는 예상 SHA-256이 일치할 때만 허용한다.
  - `data/`, `artifacts/`, 저장소 메타데이터와 허용 범위 밖 경로를 차단한다.
  - 자유 셸 명령 대신 고정 validation enum과 상대 대상 경로만 받는다.
  - 임시 작업공간 정리 전 diff와 검사 로그를 결과에 보존한다.
  - 실행 전후 원본 대상 파일 해시가 동일하다.
  - 로컬 기본 runner는 구문 검사만 수행하고 pytest는 격리 backend 없이는 차단한다.
  - 최종 보고서가 sandbox 결과를 입력으로 받되 자동 반영·승인·배포하지 않는다.
- **테스트**: 안전한 생성, 구문 오류, 허용 경로, 체크섬 불일치, 원본 불변,
  자유 명령 비실행, pytest 격리 요구, service 미설정, CLI sandbox 결과
- **다음 우선순위**:
  - 제한된 `data_analysis_sandbox`
  - Claude structured-output gateway

## 13. P016 GCP Cloud Run Job 격리 pytest runner

- **목적**: 로컬 프로세스에서 실행할 수 없는 개발 연구자의 pytest를 최소 권한
  Cloud Run Job으로 위임하고 결과를 요청 bundle에 암호학적으로 결합한다.
- **입력**: 임시 workspace, `CodeSandboxValidation`, GCP project·region·job·bucket 설정
- **출력**: request ID와 workspace SHA-256에 결합된 `SandboxJobResultEnvelope`
- **변경 파일**:
  - `domain/sandbox_job.py`
  - `services/gcp_code_sandbox.py`
  - `services/sandbox_worker.py`
  - `cli/sandbox_worker.py`
  - `deploy/gcp-sandbox/`
  - 관련 service·worker 계약 테스트와 운영 문서
- **완료 조건**:
  - controller가 `src`, `tests`, `pyproject.toml`만 결정적 ZIP으로 만든다.
  - bundle과 request 객체는 GCS에서 create-only이며 worker 결과도 덮어쓸 수 없다.
  - Cloud Run 실행은 task 1개, 재시도 0, 고정 container와 실행별 request override만 쓴다.
  - worker는 checksum, 상대 경로, symlink, 파일 유형과 압축 해제 크기를 재검증한다.
  - pytest 명령은 Python 코드에 고정되고 model 문자열을 명령으로 받지 않는다.
  - controller는 request ID, bundle checksum, 검사 종류와 대상이 일치한 결과만 수락한다.
  - 전용 서비스 계정은 전용 버킷 객체 get/create만 가지며 list/delete/overwrite와
    소스 반영·배포 권한을 가지지 않는다.
  - Direct VPC `ALL_TRAFFIC`, Private Google Access와 egress firewall을 Terraform으로
    선언하고 worker image는 digest로 고정한다.
  - 실제 GCP 배포는 project ID, image digest와 운영자 승인 없이는 수행하지 않는다.
- **테스트**: 결정적 bundle, symlink·traversal 거부, pytest 성공·실패, 결과 checksum
  불일치, Fake Cloud Run override 계약, 원격 runner Fake GCS 왕복
- **다음 우선순위**:
  - P017 제한된 `data_analysis_sandbox`
  - P018 Claude structured-output gateway

## 14. P017 제한된 공개 데이터 분석 샌드박스

- **목적**: 방법론·데이터 연구자가 모델 생성 코드나 SQL 없이, 검토된 공개 데이터셋에
  대해 재현 가능한 소규모 기술 분석을 수행하게 한다.
- **입력**: 배포자 등록 `DataAnalysisDataset`, planner의 `DataAnalysisRequest[]`
- **출력**: 입력 SHA-256, 행 수, 결정적 통계, caveat와 출처 evidence ID를 포함한
  `ResearchToolEvidence`
- **변경 파일**:
  - `domain/data_analysis.py`
  - `domain/research_lab.py`
  - `services/data_analysis_sandbox.py`
  - `resources/default_data_analysis_datasets.json`
  - `agents/research_lab.py`
  - `cli/research_lab_demo.py`
  - 관련 domain·service·통합 테스트와 문서
- **완료 조건**:
  - planner는 행을 보지 않고 등록된 dataset ID와 열 catalog만 받는다.
  - 행 수, 수치 기술통계, 그룹 건수·평균, Pearson 상관만 enum으로 요청한다.
  - 필터는 열-스칼라 고정 비교만 지원한다.
  - 임의 코드, SQL, 셸, 경로, URL과 데이터 변경 필드는 스키마에 존재하지 않는다.
  - 실패한 분석과 성공한 분석이 함께 있을 때 성공 결과를 보존한다.
  - 결과는 입력 checksum과 원본·필터 행 수를 포함하고 근거 ID로 인용할 수 있다.
  - 오프라인 7역할 데모가 분석 결과를 방법론 연구자의 실제 입력 근거로 사용한다.
- **테스트**: 연산별 결과, 필터, 부분 실패, 미등록 데이터셋·열, 중복 요청 ID,
  임의 SQL 필드 거부, 패키지 resource 포함, 연구실 통합 evidence 경로
- **다음 우선순위**:
  - P018 Claude structured-output gateway와 역할별 route 설정 (완료)
  - 주 애플리케이션 Cloud Run API·비동기 상태 저장·Secret Manager 배포

최종 배포 목표는 사용자가 저장소를 Git에 올리고 GCP 배포를 수행한 뒤 Claude API 키만
Secret Manager에 입력하면 기동되는 상태다. P017은 모델의 데이터 실행 권한을 제한하는
경계를 완성했고 P018은 Claude 모델 경계를 키 하나로 설정할 수 있게 했다. P019는
Secret Manager 키 하나로 7개 역할을 기동하는 사용자 API·비동기 실행 경로를 완성했다.
P020은 검토된 production corpus와 공식 외부 검색 adapter를 같은 키 기반 배포 경로에
연결했다.

## 15. P018 Claude 구조화 출력 게이트웨이와 역할별 라우팅

- **목적**: 7개 연구실 역할을 공식 Anthropic SDK에 연결하면서 기존 Pydantic 산출물,
  근거 검증과 사람 승인 경계를 그대로 유지한다.
- **입력**: `ANTHROPIC_API_KEY`, 선택적 timeout·재시도·역할별 모델 override,
  `ModelCallRequest`
- **출력**: 요청의 `output_schema`로 검증된 도메인 모델과 비밀정보가 없는
  `AnthropicModelCallAudit`
- **변경 파일**:
  - `agents/anthropic_model_gateway.py`
  - `agents/anthropic_research_lab.py`
  - `agents/model_gateway.py`
  - `domain/research_lab.py`
  - `cli/main.py`
  - `.env.example`, `pyproject.toml`, `uv.lock`
  - 관련 unit·SDK 계약·CLI 테스트와 `docs/CLAUDE_GATEWAY.md`
- **완료 조건**:
  - API 키 하나만 있으면 기본 7개 역할 route를 구성한다.
  - 공식 SDK의 `messages.parse()`가 Pydantic 스키마를 structured-output 요청으로
    변환하며 반환값을 애플리케이션도 재검증한다.
  - 시스템 메시지는 top-level system으로, 사용자 메시지는 provider messages로
    결정적으로 변환한다.
  - refusal, max tokens, invalid output과 provider 실패가 정제 오류로 구분된다.
  - API 키, 전체 프롬프트와 provider 원문을 로그나 CLI에 출력하지 않는다.
  - 메인 연구자 한 명과 여섯 worker가 각자의 고정 route gateway를 공유 client 위에서
    사용한다.
  - 역할별 모델은 환경변수로 교체할 수 있고 기본 요청은 현재 모델 호환성을 위해
    sampling parameter를 생략한다.
- **테스트**: 키 마스킹·설정 검증, 역할별 route, 실제 SDK의 HTTP 요청 변환,
  Pydantic 파싱, refusal·token·invalid output, 감사 레코드, CLI 비밀정보 비노출
- **다음 우선순위**:
  - P019 사용자 Cloud Run API와 비동기 상태 저장 (완료)
  - Secret Manager binding과 Git 기반 Terraform 일괄 배포 (완료)

## 16. P019 GCP 사용자 API와 비동기 연구 실행

- **목적**: Git checkout에서 배포 스크립트를 실행하고 Claude API 키를 한 번 입력하면
  7개 역할 연구실을 비동기로 사용할 수 있는 private GCP PoC를 제공한다.
- **입력**: IAM 인증 사용자 연구 요청, GCP project·region, Secret Manager의
  `ANTHROPIC_API_KEY`
- **출력**: Firestore project 상태, checksum 결합 GCS `ResearchLabRun`, append-only
  사람 검토 이력
- **변경 파일**:
  - `domain/research_project.py`
  - `repositories/research_projects.py`
  - `services/research_projects.py`
  - `services/gcp_research_runtime.py`
  - `api/app.py`, `cli/research_worker.py`
  - `deploy/gcp-app/`
  - FastAPI·Firestore·GCS·Cloud Run 계약 테스트와 운영 문서
- **완료 조건**:
  - API가 요청을 Firestore에 먼저 저장하고 202와 server-generated project ID를
    반환한다.
  - API 요청 안에서 모델 호출을 수행하지 않고 전용 Cloud Run Job을 override 실행한다.
  - worker가 `queued → running → awaiting_human_review` 상태만 결정적으로 전이한다.
  - 전체 결과는 create-only GCS 객체이며 Firestore checksum·크기·project ID와
    일치해야 API가 반환한다.
  - 승인, 수정승인, 보류와 거부는 사람 API에서만 생성되고 모델이 승인 상태를 만들 수
    없다. 보류 결과는 나중에 다시 검토할 수 있다.
  - API는 private IAM이고 Claude key는 worker만 접근한다.
  - Secret 값은 Terraform 변수·state·로그에 포함하지 않고 숫자 secret version을
    image digest 고정 worker에 연결한다.
  - 배포 스크립트가 기존 default Firestore 위치를 보존하고 현재 gcloud 사용자를 API
    invoker로 등록한다.
- **테스트**: 상태 전이, 중복 claim, dispatch 실패 정제, 결과 checksum·project binding,
  사람 검토, FastAPI 202·404·409·422, Cloud Run override, create-only GCS, 정적
  Terraform·Docker·secret 경계, 전체 회귀
- **현재 한계**:
  - 내부 코퍼스와 외부 최신 출처 production adapter는 P020에서 연결했다.
  - GCP 코드 샌드박스는 별도 배포 계약이며 주 worker에 자동 연결하지 않는다.
- **다음 우선순위**:
  - P020 검토된 내부 코퍼스 GCS 인덱스와 공식 외부 출처 검색 adapter
  - 실제 GCP staging 배포 smoke test와 비용·token 관측성

## 17. P020 검토된 내부 코퍼스와 Claude 공식 출처 검색

- **목적**: Claude API 키 하나만으로 최신 공식 출처를 검색하고, 운영자가 검토한 공개
  내부 자료를 private GCS에서 무결성 검증 후 연구 역할에 제공한다.
- **입력**: 선택적 read-only `data/`, `ANTHROPIC_API_KEY`, 공식 출처 도메인 allow-list
- **출력**: content-addressed JSONL·승인 manifest, 출처 URL·수집시각·provider
  metadata가 포함된 `ResearchToolEvidence`
- **변경 파일**:
  - `domain/corpus_index.py`, `domain/external_issue.py`
  - `repositories/publication_index.py`, `repositories/gcs_publications.py`
  - `issues/anthropic_web_search.py`
  - `services/corpus_index.py`, `services/gcp_research_runtime.py`
  - `cli/corpus_index.py`, `deploy/gcp-app/`
  - 관련 repository·provider·배포 계약 테스트와 운영 문서
- **완료 조건**:
  - 배포 스크립트가 `data/` 전후 checksum을 비교하고 승인 전 업로드하지 않는다.
  - index와 manifest는 SHA-256 객체명과 generation 0 precondition으로 생성한다.
  - worker는 GCS list 없이 exact get만 사용하고 size·SHA·record count를 검증한다.
  - 공식 검색은 같은 Claude 키, 고정 도메인 allow-list와 요청별 사용 한도를 쓴다.
  - Claude citation URL은 실제 `web_search_result` 및 허용 도메인과 일치해야 근거가 된다.
  - 검색 본문을 신뢰하지 않고 추적 query를 제거하며 URL·수집시각·provider metadata를
    보존한다.
  - 내부·외부 검색 실패는 역할별 근거 공백으로 격리한다.
- **다음 우선순위**:
- 7역할 `ResearchLabService`를 typed LangGraph로 단계 이관
- GCP staging smoke test와 token·검색비용·role latency 관측성

</details>
