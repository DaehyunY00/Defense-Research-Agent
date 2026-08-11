docs/IMPLEMENTATION_PLAN.md 의 P1.7 Page-aware / section-aware chunking 을 구현한다.
AGENTS.md 절대 규칙 1~7과 같은 문서의 "Definition of Done" 을 전부 만족시킨다.

## 배경

`search/chunking.py` 의 `DeterministicPageChunker` 가 이미 동작한다. 현재 경계 조건은
`crosses_page_gap`, `changes_section`, `changes_provenance`, `exceeds_limit` 네 가지이고
`PublicationPageSpan` 으로 chunk text 의 임의 offset 에서 원본 페이지를 역추적할 수 있다.
실코퍼스 372건에서 2,393 chunk 를 생성한다.

이번 레인은 남은 P1.7 요구를 채운다. 기존 동작과 결정성을 깨지 않는다.

**`section_title` 에 대한 사실**: 현재 어떤 파서도 채우지 않으므로 항상 `None` 이고,
`changes_section` 경계는 실코퍼스에서 발화하지 않는다. `data/metadata/*.json` 의
`page_texts` 는 `{page, text, char_count}` 뿐이라 section 정보가 원본에 없다.
heading 추출은 파서 책임이며 이 레인 범위가 아니다. section 규칙은 계약대로 구현하고
합성 fixture 로 검증하되, 실코퍼스에서 발화하지 않는다는 사실을 문서에 남긴다.

## 작업

- **overlap 규칙**을 정의하고 구현한다. 현재 chunker 에는 overlap 이 없다. 도입할지,
  도입한다면 어떤 단위(문자/문장/페이지)로 할지 정하고 근거를 남긴다. 도입하면
  `PublicationPageSpan` 불변식(빈틈·중첩 없음, 전체 길이 덮음)과 어떻게 공존하는지
  명시적으로 해결한다. 도입하지 않기로 하면 그 이유를 문서에 남긴다.
- **표·각주·참고문헌 처리 정책**을 명시한다. 현재는 페이지 텍스트를 그대로 이어붙인다.
  참고문헌 페이지를 본문과 같은 chunk 에 넣을지, 각주를 분리할지 정하고 결정적 규칙으로
  구현한다. 실코퍼스에서 식별 가능한 신호만 근거로 삼는다. 식별할 수 없으면 그 사실을
  남기고 규칙을 만들지 않는다. 없는 구조를 가정하지 않는다.
- **`artifacts/corpus/chunks.jsonl` 과 manifest 생성**. manifest 에는 chunking version,
  입력 문서 수, chunk 수, parser provenance 분포, 재현에 필요한 설정을 담는다.
  같은 입력·같은 version 에서 byte 동일 파일이 나와야 한다. 생성 시각 같은 비결정적
  값을 파일에 넣지 않는다.
- **citation retrace integration test**. 실제 PDF 페이지로 chunk → offset → 원본 페이지
  역추적이 성립하는지 검증한다. `PdfiumPdfParser` 와 `PublicationPageSpan` 을 쓴다.

## 경계

- 작업 범위는 `src/defense_research_agent/search/chunking.py` 와 대응하는 `tests/` 뿐이다.
  구현이 커지면 `search/chunking/` 패키지로 바꿔도 된다. 그 경우
  `search/chunking/__init__.py` 는 이 레인 소유다.
- 예외: `docs/IMPLEMENTATION_PLAN.md` 의 P1.7 섹션은 **반드시 갱신한다.** 충족한
  체크박스만 체크한다. 다른 섹션은 건드리지 않는다.
- `src/defense_research_agent/domain/` 전체, `search/` 의 다른 파일, `evaluation/`,
  `data/readers/`, `services/` 는 **수정하지 않는다.** 다른 레인이 동시에 작업 중이다.
  특히 `search/ocr/` 는 P1.4 레인이 소유한다.
- 최상위 배럴 4개(`domain/__init__.py`, `search/__init__.py`, `evaluation/__init__.py`,
  `services/__init__.py`)를 **수정하지 않는다.** 통합 단계에서 일괄 처리한다.
- `pyproject.toml` 과 `uv.lock` 을 수정하지 않는다. 필요하면
  `NEEDS_DEPENDENCY: <패키지> <이유>` 로 보고한다.
- `PublicationChunk` 와 `PublicationPageSpan` 계약을 바꾸지 않는다. 이미 교차 검토를
  거쳤다. 부족하면 바꾸지 말고 최종 메시지에 보고한다.
- `data/` 아래 원본은 읽기 전용이다. 생성물은 `artifacts/` 에만 쓴다.
  작업 전후로 원본 해시가 같은지 확인한다.

## 기존 동작을 깨지 않을 것

- `chunk_id` 는 v2 이고 parser name/version/source checksum 을 identity 에 포함한다.
  규칙을 바꾸면 기존 chunk 와 호환이 깨진다. 바꿔야 한다면 version 을 올리고 근거를 적어라.
- 현재 네 경계 조건이 전부 실제로 발화하는 테스트가 있다. 회귀시키지 마라.
- 실코퍼스 372건에서 2,393 chunk 가 나온다. 규칙을 바꿔 이 수치가 달라지면 왜 달라지는지
  최종 메시지에 적어라. 수치 자체가 목표는 아니다.

## 테스트

반드시 실제로 실행될 것:

- overlap 을 도입했다면 페이지 span 불변식과 공존하는지, 도입하지 않았다면 그 결정이
  문서화됐는지
- 표·각주·참고문헌 규칙이 적용되는 입력과 적용되지 않는 입력 양쪽
- `chunks.jsonl` 과 manifest 가 같은 입력에서 byte 동일하게 재생성된다
- 실제 PDF 페이지로 citation retrace 가 성립한다
- 기존 네 경계 조건이 여전히 각각 참이 되는 테스트

`data/` 의 실제 파일을 기본 테스트 스위트에서 읽지 않는다. fixture 를 쓴다.
citation retrace integration test 는 `tests/fixtures/` 의 생성 PDF 를 쓴다.

## 완료 전

```
uv run pytest && uv run mypy && uv run ruff check . && uv run ruff format --check .
```

작업이 끝나면 커밋한다.

## 최종 메시지에 담을 것

- 변경 파일 목록과 확정한 공개 시그니처
- overlap 결정과 근거, 표·각주·참고문헌 정책과 근거
- manifest 스키마와 version
- 실코퍼스 chunk 수가 2,393 에서 달라졌다면 그 이유
- P1.7 체크박스 중 실제로 충족한 것만. 충족하지 않은 항목을 완료로 보고하지 않는다.
- 미해결 항목과 사람의 판단이 필요한 지점
