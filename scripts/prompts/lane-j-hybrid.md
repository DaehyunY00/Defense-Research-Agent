docs/IMPLEMENTATION_PLAN.md 의 P2.4 `HybridSearchAlgorithm` 을 구현한다.
AGENTS.md 절대 규칙 1~7과 같은 문서의 "Definition of Done" 을 전부 만족시킨다.

## 배경 — 두 검색이 서로 다른 단위와 척도를 쓴다

이 레인의 핵심 난점이다. 먼저 이해하고 시작해라.

| | lexical | vector |
|---|---|---|
| 구현 | `search/lexical.py` `LocalLexicalSearchAlgorithm` | `search/vector/` `VectorSearchAlgorithm` |
| 결과 | `SearchMatch` (dataclass) | `VectorSearchMatch` (DomainModel) |
| **단위** | **publication** | **chunk** |
| **척도** | 누적 가중합, 상한 없음 (`lexical.py:103`) | cosine `[-1, 1]` |
| 정렬 | `(-score, publication_id)` | `VECTOR_TIE_BREAKER` |

**두 점수를 직접 더하거나 가중 평균하지 마라.** 척도가 다르고 lexical 은 상한이 없어
정규화 근거가 없다. 계획이 Reciprocal Rank Fusion 을 먼저 제시한 이유가 이것이다.
RRF 는 순위만 쓰므로 척도 문제를 우회한다. 다른 전략을 택하려면 척도를 어떻게
비교 가능하게 만들었는지 근거를 대라.

단위 불일치도 명시적으로 해결해야 한다. chunk 결과를 publication 으로 접을지, 아니면
publication 결과를 chunk 로 펼칠지 정하고 근거를 남겨라. `PublicationVectorSearchAdapter`
가 chunk → publication 투영(각 publication 의 최고 chunk)을 이미 하고 있으니 참고하되,
그 선택이 hybrid 에 적절한지는 스스로 판단해라.

## 알아야 할 사실

- 실코퍼스 chunk 의 **21%(2,393 중 514개)가 임베딩 입력 상한을 초과해 색인에서 빠진다.**
  즉 vector 는 코퍼스를 완전히 덮지 못한다. lexical 만 어떤 publication 을 아는 경우가
  실제로 발생한다. 이것이 lexical-only fallback 이 필요한 이유이며, 한쪽에만 있는
  결과를 어떻게 다룰지가 이 레인의 실질 과제다.
- `VectorIndexManifest.skipped_chunks` 로 무엇이 빠졌는지 확인할 수 있다.
- 기존 lexical 을 **제거하거나 수정하지 않는다.** 계획이 명시한다 — 약어, 무기체계명,
  정책 식별자와 정확 용어 검색의 오프라인 결정적 baseline 이다.

## 이 레인의 한계를 정직하게 유지할 것

`FakeEmbeddingProvider` 는 의미 유사도를 제공하지 않으므로 **검색 품질을 주장하지 마라.**
완료 조건은 "융합 계약이 성립하고 결정적으로 동작한다" 까지다. lexical 대비 개선
여부는 golden dataset 이 준비된 뒤 P2.6 에서 측정한다. 벤치마크 없이 "개선됐다" 고
쓰지 마라.

## 작업

- `HybridSearchAlgorithm` 을 구현한다. lexical 과 vector 를 주입받아 융합한다.
- **lexical score 와 vector score 를 각각 원값으로 보존한다.** 융합 점수만 남기고 원값을
  버리면 안 된다. 결과에서 "왜 이 순위인지" 를 두 원점수와 각 순위로 설명할 수 있어야 한다.
- **fusion 전략과 parameter, version 을 결과에 기록한다.** RRF 라면 `k` 값까지 남긴다.
  같은 입력·같은 version 에서 byte 동일 결과가 나와야 한다.
- **filter 적용 시점을 명시한다.** `allowed_publication_ids` 를 융합 전에 각 검색에
  적용할지, 융합 후 결과에 적용할지 정하고 근거를 남겨라. 두 선택은 결과가 다르다
  (융합 전 적용이면 필터링된 문서가 순위 계산에 영향을 주지 않는다). 정한 쪽을
  테스트로 고정해라.
- **lexical-only fallback 과 부분 실패를 처리한다.**
  - vector index 가 없거나 build 되지 않은 경우
  - vector 검색이 실패한 경우
  - 한쪽에만 나타난 결과
  각 경우 무엇을 반환하고 그 사실이 결과에 어떻게 드러나는지 정의해라.
  조용히 lexical 결과만 돌려주면 안 된다. fallback 이 일어났다는 것이 보여야 한다.
- 동일 융합 점수의 결정적 tie-breaker 를 정의한다.

## 경계

- 작업 범위는 `src/defense_research_agent/search/hybrid/`(신규)와 대응하는 `tests/` 뿐이다.
  `search/hybrid/__init__.py` 는 이 레인 소유다.
- 예외: `docs/IMPLEMENTATION_PLAN.md` 의 P2.4 섹션은 **반드시 갱신한다.** 충족한
  체크박스만 체크한다. 다른 섹션은 건드리지 않는다.
- `search/lexical.py`, `search/base.py`, `search/vector/`, `search/rerank/`,
  `search/embeddings/`, `search/chunking.py`, `domain/`, `repositories/`, `evaluation/`,
  `services/` 는 **수정하지 않는다. 사용만 한다.**
- 최상위 배럴 4개(`domain/__init__.py`, `search/__init__.py`, `evaluation/__init__.py`,
  `services/__init__.py`)를 **수정하지 않는다.** 통합 단계에서 일괄 처리한다.
  필요한 심볼은 전체 모듈 경로로 import 한다.
- `pyproject.toml` 과 `uv.lock` 을 수정하지 않는다. 새 의존성을 추가하지 않는다.
  필요하면 `NEEDS_DEPENDENCY: <패키지> <이유>` 로 보고한다. 표준 라이브러리만 쓴다.
- `data/` 아래 원본은 읽기 전용이다. 생성물은 `artifacts/` 에만 쓰고 경로는
  `defense_research_agent.path_safety.ensure_outside_read_only_data` 로 검사한다.
  자체 검사를 만들지 마라.

## 테스트

반드시 **실제로 참이 되어야** 할 분기:

- 두 검색이 같은 publication 을 다른 순위로 반환할 때의 융합
- 한쪽에만 나타난 결과의 처리 (양방향 모두)
- vector index 미build / vector 검색 실패 → lexical-only fallback,
  그리고 그 사실이 결과에 드러난다
- `allowed_publication_ids` 필터가 정한 시점에 적용된다
- 동일 융합 점수 tie-breaker 가 실제로 발동
- limit 이 후보 수보다 작아 **절단이 일어나는** 경우.
  운영에서는 항상 limit < 후보 수다
- 같은 입력·같은 fusion version 에서 byte 동일 결과
- lexical 원점수와 vector 원점수가 결과에 보존된다

`data/` 나 `artifacts/corpus/chunks.jsonl` 을 기본 스위트에서 읽지 않는다. fixture 를 쓰되
**실코퍼스를 대표하는 크기**를 쓴다. chunk 텍스트 중앙값은 약 7,000바이트다. 30바이트
fixture 로만 검증하면 실제 동작을 대표하지 못한다.

## 완료 전

```
uv run pytest && uv run mypy && uv run ruff check . && uv run ruff format --check .
```

작업이 끝나면 커밋한다.

## 최종 메시지에 담을 것

- 변경 파일 목록과 확정한 공개 시그니처
- 선택한 fusion 전략과 근거, parameter 와 version
- 단위 불일치(publication vs chunk)를 어떻게 해결했는지와 근거
- filter 적용 시점과 근거
- fallback 정책과 그것이 결과에 드러나는 방식
- P2.4 체크박스 중 실제로 충족한 것만. 충족하지 않은 항목을 완료로 보고하지 않는다.
- 미해결 항목과 사람의 판단이 필요한 지점
