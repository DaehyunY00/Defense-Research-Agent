docs/IMPLEMENTATION_PLAN.md 의 P2.2 `FakeEmbeddingProvider` 를 구현한다.
AGENTS.md 절대 규칙 1~7과 같은 문서의 "Definition of Done" 을 전부 만족시킨다.

## 배경

계약은 이미 있다. `search/embeddings/base.py` 의 `EmbeddingProvider` ABC,
`EmbeddingBatchResult`, `EmbeddingVector`, `EmbeddingFailure`, `EmbeddingErrorCode` 다.
`tests/unit/search/test_embedding_contract.py` 의 `FakeHashEmbeddingProvider` 는 계약이
구현 가능함을 보이는 최소 예시일 뿐이다. 이번 레인은 파이프라인 테스트에 쓸 수 있는
제대로 된 `FakeEmbeddingProvider` 를 `src/` 에 만든다.

## 이 구현의 목적과 한계를 정직하게 유지할 것

P2.2 체크박스에 "ranking 의미를 과장하지 않고 interface·pipeline 테스트에만 사용" 이
명시돼 있다. 이 provider 는 **검색 품질을 주장하지 않는다.** 결정적이고 계약을 만족하는
벡터를 만들어 인터페이스와 파이프라인을 오프라인에서 검증하는 것이 전부다.

의미 유사도가 있는 것처럼 보이게 만들려는 유혹을 피한다. 문자 n-gram 해시 같은 방식으로
"비슷한 문자열이 비슷한 벡터"가 되면 벤치마크에서 실제보다 좋은 수치가 나와 P2.6 의
lexical baseline 비교를 왜곡한다. 어떤 방식을 택하든 그 방식이 무엇을 보장하고 무엇을
보장하지 않는지 docstring 에 명시한다.

## 작업

- `EmbeddingProvider` 를 구현하는 `FakeEmbeddingProvider` 를
  `src/defense_research_agent/search/embeddings/` 에 추가한다.
- 외부 모델·네트워크·자격증명 없이 동작한다.
- 같은 입력과 같은 설정에서 **byte 동일** 결과를 보장한다. 플랫폼과 실행 순서에
  의존하지 않는다. `hash()` 처럼 프로세스마다 달라지는 것을 쓰지 않는다.
- `dimension`, `normalized`, `max_batch_size` 를 생성자에서 설정 가능하게 한다.
  `normalized=True` 면 실제로 단위 정규화된 벡터를 낸다.
- 배치 처리에서 입력 하나가 실패해도 나머지를 버리지 않는다. 실패는
  `EmbeddingFailure` 로 `input_index` 와 함께 보고한다.
- `max_batch_size` 초과, 빈 입력, 빈 배치, 과도하게 긴 입력을 계약의 error code 로
  다룬다. 새 error code 를 만들지 않는다.
- `input_checksum` 은 임베딩한 **정확한 텍스트**의 checksum 이다. 정규화를 적용한다면
  정규화 후 텍스트를 기준으로 하고 그 사실을 문서화한다.
- 유니코드 입력에서 안전해야 한다. 한글, 결합 문자, 이모지, 서로게이트 경계를 고려한다.

## 경계

- 작업 범위는 `src/defense_research_agent/search/embeddings/` 와 대응하는 `tests/` 뿐이다.
  `search/embeddings/__init__.py` 는 이 레인 소유이므로 수정해도 된다.
- 예외: `docs/IMPLEMENTATION_PLAN.md` 의 P2.2 섹션은 **반드시 갱신한다.** 충족한 체크박스만
  체크한다. 다른 섹션은 건드리지 않는다.
- `src/defense_research_agent/domain/` 전체, `search/parsers/`, `search/metadata`,
  `search/base.py`, `search/lexical.py`, `search/chunking.py`, `evaluation/`,
  `data/readers/` 는 **수정하지 않는다.** 다른 레인이 동시에 작업 중이다.
- `src/defense_research_agent/domain/__init__.py`,
  `src/defense_research_agent/search/__init__.py`,
  `src/defense_research_agent/evaluation/__init__.py`,
  `src/defense_research_agent/services/__init__.py` 는 **수정하지 않는다.**
  필요한 심볼은 전체 모듈 경로로 import 한다.
- `pyproject.toml` 과 `uv.lock` 은 수정하지 않는다. numpy 를 포함해 어떤 새 의존성도
  추가하지 않는다. 필요하면 구현을 중단하고 최종 메시지에
  `NEEDS_DEPENDENCY: <패키지> <이유>` 로 보고한다. 표준 라이브러리만으로 구현한다.
- `search/embeddings/base.py` 의 공개 시그니처는 바꾸지 않는다. 이미 검토를 거쳤다.
  부족하다고 판단되면 바꾸지 말고 최종 메시지에 보고한다.
- `data/` 아래 원본은 읽기 전용이다. 생성물은 `artifacts/` 에만 쓴다.

## 테스트

- 같은 입력·같은 설정에서 byte 동일 (`model_dump_json()` 비교)
- 서로 다른 dimension·normalization 설정이 서로 다른 결과를 낸다
- `normalized=True` 일 때 벡터 norm 이 1 이다 (부동소수 허용 오차 내)
- 배치 중 일부 실패가 나머지를 버리지 않는다
- 빈 입력, 빈 배치, `max_batch_size` 초과가 계약대로 처리된다
- 한글·결합 문자·이모지 입력에서 dimension 과 checksum 이 안정적이다
- `embed_query` 와 `embed_documents` 의 결과 형태가 일치한다
- `EmbeddingBatchResult` 의 dimension 검증에 걸리지 않는다

테스트가 통과하는 것으로 부족하다. 위 실패 경로가 **실제로 실행되어야** 한다.
네트워크나 자격증명을 요구하는 테스트를 만들지 않는다.

## 완료 전 반드시 통과시킬 것

```
uv run pytest && uv run mypy && uv run ruff check . && uv run ruff format --check .
```

작업이 끝나면 커밋한다.

## 최종 메시지에 담을 것

- 변경 파일 목록과 확정한 공개 시그니처
- 벡터 생성 방식과 그 방식이 **보장하지 않는 것**
- `input_checksum` 이 정규화 전 텍스트 기준인지 후인지
- P2.2 체크박스 중 실제로 충족한 것만. 충족하지 않은 항목을 완료로 보고하지 않는다.
- 미해결 항목과 사람의 판단이 필요한 지점
