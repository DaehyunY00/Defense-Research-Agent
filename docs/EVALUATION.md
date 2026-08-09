# Defense Research Platform Evaluation

이 문서는 데이터 수집, retrieval, RAG와 model/runtime 품질을 어떻게 측정할지 정의한다.
현재 Topic Discovery의 점수·랭킹 계산식은
[EVALUATION_CRITERIA.md](./EVALUATION_CRITERIA.md), 현재 파일럿에서 실제 측정된 값과
`unavailable` 항목은 [PILOT_RESULT.md](./PILOT_RESULT.md)를 참조한다.

평가의 목적은 demo가 그럴듯한지 판단하는 것이 아니라, 변경 전후의 품질·지연·비용·
안전성을 같은 데이터와 절차로 비교하는 것이다. 실제로 실행하지 않은 결과는 기록하지
않고, 전문가 라벨이 없는 지표는 임의로 추정하지 않는다.

## 1. Evaluation layers

```text
source files
    -> ingestion and document intelligence evaluation
    -> retrieval evaluation
    -> RAG application evaluation
    -> model/runtime evaluation
    -> human interpretation and decision
```

각 계층은 상위 계층의 실패를 숨기지 않는다. 예를 들어 retrieval이 관련 페이지를
회수하지 못했는데 답변 문장만 자연스럽다고 RAG를 성공으로 판정하지 않는다.

## 2. Data ingestion evaluation

### 목적

원본 PDF·JSON을 변경하지 않고 검색 가능한 텍스트와 metadata를 얼마나 정확하게
생성했는지 평가한다.

### 지표와 검사

| 항목 | 정의 또는 검사 방법 |
|---|---|
| extraction success rate | 처리 대상 문서 중 parser가 구조화 결과를 만든 비율 |
| empty-text rate | 문서 또는 페이지 텍스트가 비어 있는 비율 |
| corrupted-text rate | 제어문자·비인쇄문자·비정상 문자 비율이 임계값을 넘는 비율 |
| metadata completeness | 필수 서지 필드의 채움률과 필드별 confidence |
| page mapping correctness | 추출 페이지와 실제 PDF 페이지의 대응 정확도 |
| section mapping correctness | section title과 실제 문서 구간의 대응 정확도 |
| table handling | 표본 표의 행·열·각주 보존 여부와 텍스트 왜곡 |
| OCR fallback rate | 기본 추출 실패 후 OCR을 사용한 문서·페이지 비율 |
| OCR acceptance rate | OCR 결과가 품질 기준을 개선해 채택된 비율 |
| provenance completeness | parser, parser version, checksum, page와 source locator 보존 여부 |

### 평가 표본

- 네 publication type별 정상 문서
- 1,000자 이하 저추출 문서
- 제어문자 손상이 큰 문서
- 긴 파일명·불완전 자모 문서
- 표, 각주, 복수 저자와 계절호가 있는 문서
- metadata가 없는 orphan PDF와 논리 중복 JSON 그룹

문서 표본은 사람이 PDF 원문을 보며 제목, 저자, 발행 정보, 페이지와 텍스트를 검수한다.

## 3. Retrieval evaluation

### 비교 대상

```text
현재 lexical baseline
BM25
Vector
Hybrid
Hybrid + Reranker
```

새 검색기는 동일한 query set, corpus snapshot, filters와 cutoff를 사용해 baseline과
비교한다. 기존 lexical을 제거하지 않으며 exact-term query의 별도 결과도 보존한다.

### 품질 지표

| 지표 | 용도 |
|---|---|
| Recall@5 | 상위 5개에서 관련 publication/page를 얼마나 회수했는지 측정 |
| Recall@10 | 더 넓은 후보 집합의 회수율 측정 |
| MRR | 첫 관련 결과가 얼마나 위에 나타나는지 측정 |
| nDCG@K | 관련도 등급이 있을 때 순위 품질 측정 |
| zero-result rate | 결과가 없는 질의 비율 |
| duplicate-result rate | 같은 publication 또는 중복 chunk가 과도하게 노출되는 비율 |
| citation retrace rate | 결과가 실제 publication/page/section으로 역추적되는 비율 |

### 운영 지표

- p50 / p95 latency
- peak memory usage
- index build time
- index size
- embedding 호출 수와 비용
- reranker 호출 수와 비용
- query별 lexical/vector/fusion/rerank trace completeness

### Query slice

전체 평균 외에 다음 slice를 별도로 본다.

- exact term, 약어, 무기체계명과 정책 식별자
- 자연어 개념 질문
- 한국어 띄어쓰기·동의어 변형
- 날짜와 publication type filter
- 관련 문서가 없는 질문
- 단일 문서가 아니라 여러 문서 비교가 필요한 질문

### 통과 원칙

- 품질 개선이 latency·memory·cost 악화를 정당화하는지 사람이 판단한다.
- 한 지표 개선만으로 baseline을 교체하지 않는다.
- benchmark dataset과 index metadata가 versioned되지 않으면 비교 결과를 채택하지 않는다.
- regression threshold는 초기 baseline을 실제 측정한 후 명시한다.

## 4. RAG evaluation

RAG 평가는 retrieval과 generation을 분리해 기록하고 end-to-end 결과를 함께 본다.

| 지표 | 판정 질문 |
|---|---|
| citation correctness | 인용한 출처와 페이지가 해당 주장을 실제로 지지하는가? |
| citation completeness | 검증 가능한 주요 주장에 필요한 인용이 모두 있는가? |
| groundedness | 답변 내용이 제공된 근거 범위를 벗어나지 않는가? |
| answer relevance | 질문과 연구 목적에 직접 답하는가? |
| hallucination rate | 근거 또는 알려진 사실과 불일치하는 주장의 비율은 얼마인가? |
| unsupported claim count | 근거가 필요한데 인용으로 지지되지 않은 주장 수는 얼마인가? |
| abstention behavior | 근거 부족·충돌 시 답변을 제한하고 부족한 점을 설명하는가? |
| source diversity | 필요한 경우 독립 출처와 publication type을 적절히 사용했는가? |
| evidence conflict handling | 상충 근거를 숨기지 않고 구분하는가? |

평가 단위는 answer 전체뿐 아니라 claim-citation pair를 포함한다. 자동 evaluator를 사용할
수 있지만, 그 출력도 Pydantic으로 검증하고 사람 검수 표본과 일치도를 측정한다.

## 5. Model and runtime evaluation

비교 대상은 `Local vs API`, `Model A vs Model B`, `LOCAL / HYBRID / CLOUD / BYOK`다.

| 차원 | 기록 항목 |
|---|---|
| quality | task별 schema success, groundedness, 전문가 점수 |
| latency | end-to-end, provider call, role별 p50/p95 |
| cost | request·token·검색·rerank 단위 비용 |
| memory | host RAM, model memory와 peak usage |
| GPU | 필요 GPU, VRAM, throughput와 concurrency |
| reliability | timeout, refusal, retry, invalid output 비율 |
| privacy | 데이터가 나가는 경계, retention, logging과 tenant isolation |
| operability | 배포 난이도, 관측성, version pinning과 rollback |

비밀정보는 실험 로그에 기록하지 않는다. provider 원문 오류, 전체 prompt와 민감한 입력도
기본 실험 산출물에서 제외한다.

## 6. Golden evaluation dataset

국방 도메인 전문가 또는 연구자가 직접 curated dataset을 만든다. 초기에는 30~50개의
고품질 질문으로 시작하고, 질의 slice와 실패 사례가 충분히 포함되면 100개 이상으로
확장한다.

권장 스키마:

```text
question
topic
difficulty
relevant_publication_ids
relevant_pages
expected_evidence
notes
```

추가 권장 metadata:

- `case_id`
- `dataset_version`
- `created_by` / `reviewed_by`
- `query_slice`
- `relevance_grade`
- `acceptable_abstention`
- `adjudication_status`

### 작성 절차

1. 연구자가 실제 연구 질문과 예상 근거를 작성한다.
2. 다른 검토자가 publication과 page label을 독립 확인한다.
3. 불일치는 합의 또는 adjudication으로 해결한다.
4. corpus snapshot과 dataset version을 고정한다.
5. 모델이 생성한 정답을 검토 없이 golden label로 사용하지 않는다.

국방 도메인 판단, 데이터셋 선정과 최종 관련도 라벨은 사람이 책임진다.

## 7. Experiment log

모든 retrieval, chunking, embedding, reranker와 model 변경은 다음 형식으로 기록한다.

```text
experiment_id
date
change
dataset_version
corpus_version
retriever
embedding_model
chunking_version
reranker
model_runtime
metrics
latency
memory
cost
notes
decision
```

권장 상태:

- `planned`
- `running`
- `completed`
- `invalidated`
- `accepted`
- `rejected`

`decision`에는 수치뿐 아니라 사람이 어떤 trade-off를 수용했는지 기록한다. 실제 측정값이
없는 행은 빈 값 또는 `not_run`으로 두며 임의 숫자를 넣지 않는다.

## 8. Evaluation workflow

```text
Human이 질문과 label을 정의
    -> Codex가 benchmark harness를 구현
    -> offline baseline 실행
    -> 변경안 실행
    -> metric·latency·resource 비교
    -> Human이 오류 사례와 trade-off를 해석
    -> 채택·보류·폐기 결정
```

회귀 benchmark는 외부 credentials 없이 실행 가능한 핵심 suite와, credentials가 있을 때만
실행하는 선택적 provider suite로 분리한다.

## 9. Reporting rules

- corpus, dataset, parser, chunking, embedding, retriever와 model version을 함께 기록한다.
- 평균만 보고하지 않고 query slice와 대표 실패 사례를 포함한다.
- `unavailable`, `not_run`, `failed`를 0점이나 성공으로 바꾸지 않는다.
- 평가 데이터에 외부 비신뢰 콘텐츠가 포함되면 출처·URL·게시일·수집일을 보존한다.
- 평가 결과와 로그는 `artifacts/evaluation/` 아래에 저장하고 원본 `data/`를 변경하지 않는다.
- 새 품질 주장은 재현 가능한 실험 ID와 결과를 근거로 해야 한다.
