# P1.1 잔여 작업 — chunk provenance 전파와 페이지 단위 인용

`scripts/lane-run.sh` 의 일반 템플릿 대신 이 파일을 프롬프트로 쓴다.

```bash
./scripts/lane-new.sh p11-provenance agent/contracts
cat scripts/prompts/p11-provenance.md | codex exec -C ~/dev/wt/p11-provenance -s workspace-write \
  --json -o ~/dev/wt/p11-provenance/RESULT.md - > ~/dev/wt/p11-provenance/codex.jsonl 2>&1
./scripts/lane-review.sh p11-provenance agent/contracts 'P1.1' --fix
```

---

docs/IMPLEMENTATION_PLAN.md 의 P1.1 "미해결 항목 — parser version 역추적" 을 구현한다.
아울러 같은 파일을 건드리는 페이지 단위 인용 요구를 함께 처리한다.
AGENTS.md 절대 규칙 1~7과 같은 문서의 "Definition of Done" 을 전부 만족시킨다.

## 배경 — 왜 필요한가

P1.1 완료 조건은 "chunk 하나만으로 publication, page range, parser/chunking version과
텍스트 checksum을 역추적할 수 있다" 이다. 현재 `PublicationChunk` 와 `PublicationPage`
어디에도 추출기 식별 필드가 없어 parser version 부분이 성립하지 않는다. 같은 PDF를 다른
추출기로 뽑으면 page text가 달라져 chunk checksum은 바뀌지만, chunk만 보고 어느 추출기
산출물인지 판별할 수 없다.

또한 현재 `search/chunking.py` 는 여러 페이지 텍스트를 `"\n\n"` 로 병합하면서 페이지별
offset을 남기지 않는다. 페이지 본문 자체가 빈 줄을 포함할 수 있으므로 역분할도 불가능하다.
따라서 chunk에서 얻을 수 있는 인용 단위는 "pp.12-13" 범위이지 특정 페이지가 아니다.
`IMPLEMENTATION_PLAN.md` 의 성공 기준은 인용의 문서·페이지 역추적 성공률 100% 이므로
페이지 범위로는 부족하다.

## 작업 1 — provenance 전파

- `PublicationPage` 에 `provenance: ExtractionProvenance` 를 **필수** 필드로 추가한다.
  (`defense_research_agent.domain.provenance.ExtractionProvenance`, 이미 존재한다)
- `PublicationChunk` 에 같은 값을 전파한다.
- chunker는 **provenance가 다른 페이지를 한 chunk로 병합하지 않는다.** 이미 있는
  page gap / section 변경과 나란히 provenance 변경도 chunk 경계로 처리한다.
  이유: P1.4의 페이지 단위 OCR fallback이 한 문서 안에 서로 다른 추출기 산출물을 섞는다.
- `chunk_id` 생성 규칙에 parser 식별자를 포함할지 결정하고 근거를 최종 메시지에 적는다.
  같은 입력·같은 버전에서 chunk_id가 안정적이어야 한다는 기존 성질은 유지한다.

## 작업 2 — 페이지 단위 인용

- `PublicationChunk` 에 페이지별 텍스트 구간 목록을 추가한다. chunk text 안에서 각
  페이지가 차지하는 `[start, end)` 문자 offset과 그 페이지 번호를 보존하는 형태다.
  모델 이름과 필드 이름은 기존 domain convention에 맞춰 정한다.
- 요구 성질:
  - chunk text의 임의 문자 offset으로부터 정확히 하나의 원본 페이지 번호를 얻을 수 있다.
  - 구간은 빈틈 없이 이어지고 겹치지 않으며 chunk text 전체 길이를 덮는다.
  - 첫 구간의 페이지는 `page_start`, 마지막 구간의 페이지는 `page_end` 와 일치한다.
  - 페이지 본문에 빈 줄이 있어도 성립한다. 구분자 문자열로 역분할하지 않는다.
- 이 불변식들을 Pydantic validator로 강제한다. 문서화만 하지 않는다.

## 경계

- 작업 범위는 `src/defense_research_agent/domain/publication.py`,
  `src/defense_research_agent/search/chunking.py` 와 대응하는 `tests/` 뿐이다.
- `src/defense_research_agent/domain/__init__.py` 는 새 심볼 re-export가 필요하면
  **수정해도 된다.** 이 작업은 병렬 레인이 아니라 단독 실행이다.
- `pyproject.toml` 과 `uv.lock` 은 수정하지 않는다. 새 의존성이 필요하면 구현을 중단하고
  최종 메시지에 `NEEDS_DEPENDENCY: <패키지> <이유>` 로 보고한다.
- `data/` 아래 원본은 읽기 전용이다. 생성물은 `artifacts/` 에만 쓴다.
- 기존 계약(`search/parsers/base.py`, `domain/metadata.py`, `domain/quality.py`,
  `search/embeddings/base.py`)의 공개 시그니처는 바꾸지 않는다. 이미 검토를 거쳤다.

## 기존 사용처

`PublicationPage(` 생성 지점은 현재 테스트에만 있다. `ParseResult.pages`(
`search/parsers/base.py`)와 `tests/unit/search/test_parser_contract.py`,
`tests/unit/search/test_metadata_extractor_contract.py`,
`tests/unit/evaluation/test_quality_gate_contract.py`,
`tests/unit/search/test_chunking.py` 를 모두 갱신해야 한다.
`ParseResult` 는 이미 run 단위 `provenance` 를 갖고 있으므로 parser가 페이지에 같은 값을
채우는 것이 자연스럽다.

## 테스트에서 반드시 검증할 것

기존 테스트가 통과하는 것으로는 부족하다. 아래는 실제로 해당 분기가 참이 되어야 한다.

- provenance가 다른 두 페이지가 **한 chunk로 병합되지 않는다** (경계 발화 확인)
- 같은 provenance의 연속 페이지는 기존대로 병합된다
- 다중 페이지 chunk에서 임의 offset → 정확한 페이지 번호 역추적
- 페이지 본문에 빈 줄이 포함된 경우에도 역추적이 성립한다
- 구간이 겹치거나 빈틈이 있거나 chunk text 길이와 어긋나면 `ValidationError`
- 같은 입력·같은 버전에서 chunk_id와 checksum이 byte 동일 (기존 결정성 유지)
- parser version만 다른 입력에서 무엇이 바뀌고 무엇이 유지되는지 명시적으로 단언

기존 `tests/unit/search/test_chunking.py` 에는 다음 두 분기가 **어떤 테스트에서도 참이
되지 않는** 문제가 있다. 이번 작업에서 함께 해소한다.

- `exceeds_limit` — 유일한 크기 테스트가 `max_characters=10`, 페이지 4/4/20 이라
  `4+2+4=10 > 10` 이 False가 되어 사후 검사로만 분할된다. 기본값 4,000자와 실제 페이지
  길이에서는 이 분기가 주 경로다. 실제로 참이 되는 테스트를 추가한다.
- `crosses_page_gap` — 빈 페이지 테스트에서 `emit_pending()` 후 `pending` 이 비어
  이 분기를 타지 않는다. 비연속 페이지 + 동일 section + 상한 이내 조합을 추가한다.

## 완료 전 반드시 통과시킬 것

```
uv run pytest && uv run mypy && uv run ruff check . && uv run ruff format --check .
```

작업이 끝나면 커밋한다.

## 최종 메시지에 담을 것

- 변경 파일 목록
- 확정한 공개 시그니처 (`PublicationPage`, `PublicationChunk`, 새 구간 모델)
- `chunk_id` 규칙에 parser 식별자를 포함했는지와 그 근거
- P1.1 체크박스 중 실제로 충족한 것만. 충족하지 않은 항목을 완료로 보고하지 않는다.
- 미해결 항목과 사람의 판단이 필요한 지점
