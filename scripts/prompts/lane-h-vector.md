docs/IMPLEMENTATION_PLAN.md 의 P2.3 `VectorSearchAlgorithm` 을 구현한다.
AGENTS.md 절대 규칙 1~7과 같은 문서의 "Definition of Done" 을 전부 만족시킨다.

## 배경

재료가 모두 준비돼 있다.

- `search/embeddings/` — `EmbeddingProvider` 계약과 `FakeEmbeddingProvider`
- `artifacts/corpus/chunks.jsonl` — 331 문서, 2,393 chunk.
  `scripts/build_corpus_chunks.py` 로 재생성하며 byte 동일이 보장된다.
  각 chunk 가 publication, page range, `PublicationPageSpan`, provenance,
  checksum, chunking version 을 갖는다.
- `search/base.py` — 기존 `PublicationSearchAlgorithm` ABC 와 `SearchMatch`
- `search/lexical.py` — `LocalLexicalSearchAlgorithm`

**기존 lexical search 를 제거하거나 바꾸지 않는다.** 계획이 명시한다 — lexical 은
약어, 무기체계명, 정책 식별자와 정확 용어 검색의 오프라인 결정적 baseline 이다.

## 이 레인의 한계를 정직하게 유지할 것

`FakeEmbeddingProvider` 는 의미 유사도를 제공하지 않는다. 따라서 **검색 품질을 주장하지
않는다.** 이 레인의 완료 조건은 "계약이 성립하고 결정적으로 동작한다" 까지다.
Recall/MRR 같은 수치는 golden dataset 이 준비된 뒤 P2.6 에서 측정한다.

docstring 과 계획 문서에 이 경계를 명시해라. 벤치마크 없이 "개선됐다" 고 쓰지 마라.

## 작업

- `VectorIndex` abstraction 을 정의한다. chunk 단위로 색인하며 content-addressed
  manifest 를 갖는다. manifest 에는 embedding model id, embedding version,
  dimension, normalization, chunking version, 입력 chunk 수와 checksum 을 담는다.
  같은 입력·같은 설정에서 byte 동일 manifest 가 나와야 한다.
- `VectorSearchAlgorithm` 을 구현한다. 기존 `PublicationSearchAlgorithm` 계약을 최대한
  유지하되, chunk 단위 검색이므로 그 계약만으로 부족한 부분은 별도 결과 모델로 확장한다.
  기존 ABC 를 **수정하지 말고** 필요하면 나란히 두는 방식을 택하고 근거를 적어라.
- 결과에 publication, chunk, page provenance 를 반환한다. 어느 페이지에서 나온
  근거인지 역추적 가능해야 한다.
- **동일 점수 tie-breaker 를 결정적으로 정의한다.** 부동소수 비교이므로 같은 점수가
  실제로 발생한다. 무엇으로 순서를 가를지 정하고 테스트로 고정해라.
- **version 불일치를 차단한다.** index 를 만든 embedding model/version/dimension/
  normalization 과 chunking version 이 질의 시점 설정과 다르면 조용히 검색하지 말고
  거부한다. 이 차단이 실제로 발화하는 테스트를 만들어라.

## 경계

- 작업 범위는 `src/defense_research_agent/search/vector/`(신규)와 대응하는 `tests/` 뿐이다.
  `search/vector/__init__.py` 는 이 레인 소유다.
- 예외: `docs/IMPLEMENTATION_PLAN.md` 의 P2.3 섹션은 **반드시 갱신한다.** 충족한
  체크박스만 체크한다. 다른 섹션은 건드리지 않는다.
- `search/base.py`, `search/lexical.py`, `search/chunking.py`, `search/embeddings/`,
  `search/parsers/`, `search/ocr/`, `search/metadata.py`, `domain/`, `evaluation/`,
  `services/` 는 **수정하지 않는다.** 다른 레인이 동시에 작업 중이다.
  특히 `search/rerank/` 는 P2.5 레인이 소유한다.
- 최상위 배럴 4개(`domain/__init__.py`, `search/__init__.py`, `evaluation/__init__.py`,
  `services/__init__.py`)를 **수정하지 않는다.** 통합 단계에서 일괄 처리한다.
  필요한 심볼은 전체 모듈 경로로 import 한다.
- `pyproject.toml` 과 `uv.lock` 을 수정하지 않는다. numpy 를 포함해 어떤 새 의존성도
  추가하지 않는다. 필요하면 구현을 중단하고 `NEEDS_DEPENDENCY: <패키지> <이유>` 로
  보고한다. 표준 라이브러리만으로 구현한다.
- `data/` 아래 원본은 읽기 전용이다. 생성물은 `artifacts/` 에만 쓴다.
  생성물 경로는 `defense_research_agent.path_safety.ensure_outside_read_only_data` 로
  검사한다. 자체 검사를 만들지 마라. 기존 writer 6곳이 모두 이 함수를 쓴다.

## 테스트

기본 오프라인 스위트에서 네트워크·자격증명을 요구하지 않는다. 반드시 실제로 실행될 것:

- 같은 입력·같은 설정에서 byte 동일 index manifest
- 동일 점수 tie-breaker 가 실제로 발동하는 입력
- embedding model/version/dimension/normalization 불일치 차단이 각각 발화
- chunking version 불일치 차단이 발화
- 결과에서 chunk offset → 원본 페이지 역추적
- 빈 index, 빈 질의, limit 0, limit 초과 등 경계
- `allowed_publication_ids` 필터가 적용되는 경로와 적용되지 않는 경로

테스트가 통과하는 것으로 부족하다. 위 분기가 실제로 참이 되어야 한다.
`data/` 의 실제 파일이나 `artifacts/corpus/chunks.jsonl` 을 기본 스위트에서 읽지 않는다.
fixture 를 쓴다.

## 완료 전

```
uv run pytest && uv run mypy && uv run ruff check . && uv run ruff format --check .
```

작업이 끝나면 커밋한다.

## 최종 메시지에 담을 것

- 변경 파일 목록과 확정한 공개 시그니처
- 기존 `PublicationSearchAlgorithm` 계약을 어떻게 다뤘는지와 근거
- tie-breaker 규칙과 version 차단 조건
- index manifest 스키마와 version
- P2.3 체크박스 중 실제로 충족한 것만. 충족하지 않은 항목을 완료로 보고하지 않는다.
- 미해결 항목과 사람의 판단이 필요한 지점
