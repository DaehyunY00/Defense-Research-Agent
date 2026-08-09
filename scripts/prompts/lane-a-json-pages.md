docs/IMPLEMENTATION_PLAN.md 의 P1.3 중 **JSON page adapter** 부분을 구현한다.
AGENTS.md 절대 규칙 1~7과 같은 문서의 "Definition of Done" 을 전부 만족시킨다.

## 범위 한정 — PDF 직접 추출은 이번 범위가 아니다

P1.3 의 "페이지별 본문 추출 adapter" 중 **PDF 본문을 새로 추출하는 부분은 하지 않는다.**
PDF 파싱 라이브러리가 의존성에 없고 라이브러리 선택은 라이선스가 걸린 사람의 결정이다.
이번 레인은 이미 존재하는 `data/metadata/*.json` 의 `page_texts` 를 `DocumentParser`
계약으로 노출하는 데 집중한다. 코퍼스가 실제로 가진 페이지 텍스트가 여기 있으므로,
이것만으로 chunking·quality·metadata 가 실제 페이지 위에서 동작하게 된다.

## 배경

`search/parsers/base.py` 에 `DocumentParser` ABC 와 `ParseResult` 계약이 이미 있다.
`data/readers/json_reader.py` 는 `page_texts` 의 존재만 검증하고 `PublicationSource` 로
전달하지 않는다. 즉 페이지 텍스트가 파이프라인에 도달하지 못한다.

실제 스키마는 다음과 같다. 최상위는 `metadata`, `full_text`, `page_texts` 이고
`page_texts` 항목은 `{page, text, char_count}` 뿐이다. section 정보는 원본에 없다.
`metadata` 는 `filename`, `category`, `num_pages`, `total_chars`, `avg_chars_per_page`,
`processed_date`, `file_size_mb`, `path` 를 문자열로 가진다.

## 작업

- `DocumentParser` 를 구현하는 JSON page adapter 를 `search/parsers/` 에 추가한다.
- `ExtractionProvenance` 를 채운다. `parser_name` 은 이 adapter 의 이름,
  `parser_version` 은 adapter 버전, `source_checksum` 은 읽은 JSON 파일의 checksum 이다.
- `page_texts` 를 `PublicationPage` 로 변환한다. `section_title` 은 원본에 없으므로
  `None` 이다. 추측해서 채우지 않는다.
- 아래를 `ParserFailure` 로 보고한다. 예외로 던지지 않는다.
  - `page_texts` 누락 또는 빈 목록
  - `page` 가 없거나 정수가 아니거나 1 미만
  - 같은 `page` 번호 중복
  - `text` 가 문자열이 아님
  - `text` 가 공백뿐인 페이지 (`EMPTY_PAGE`)
  - 파일을 읽을 수 없거나 JSON 파싱 실패
  - 전달받은 checksum 과 실제 파일 checksum 불일치 (`CHECKSUM_MISMATCH`)
- `char_count` 와 실제 `len(text)` 가 어긋나면 어떻게 할지 정하고 근거를 남긴다.
  원본을 신뢰할지 재계산할지는 한 가지로 정하고 문서화한다.
- `ParseResult.pages` 는 page 번호 오름차순이어야 한다. 원본이 정렬돼 있지 않을 수 있다.
- `requires_ocr` 판정 기준을 정한다. 근거 없이 `False` 로 두지 않는다.
- 기존 `PdfPublicationReader` 의 header/checksum 검증은 유지한다. 이 reader 가 PDF 본문을
  새로 추출하지 않는다는 사실을 docstring 에 명시한다.

## 경계

- 작업 범위는 `src/defense_research_agent/search/parsers/`,
  `src/defense_research_agent/data/readers/` 와 대응하는 `tests/` 뿐이다.
  `search/parsers/__init__.py` 는 이 레인 소유이므로 수정해도 된다.
- 예외: `docs/IMPLEMENTATION_PLAN.md` 의 P1.3 섹션은 **반드시 갱신한다.** 충족한 체크박스만
  체크하고, PDF 직접 추출이 미완임을 남긴다. 다른 섹션은 건드리지 않는다.
- `src/defense_research_agent/domain/__init__.py`,
  `src/defense_research_agent/search/__init__.py`,
  `src/defense_research_agent/evaluation/__init__.py`,
  `src/defense_research_agent/services/__init__.py` 는 **수정하지 않는다.**
  최상위 배럴 재노출은 통합 단계에서 사람이 일괄 처리한다.
- `pyproject.toml` 과 `uv.lock` 은 수정하지 않는다. 새 의존성이 필요하면 구현을 중단하고
  최종 메시지에 `NEEDS_DEPENDENCY: <패키지> <이유>` 로 보고한다.
- 기존 계약(`search/parsers/base.py` 의 공개 시그니처, `domain/` 전체)은 바꾸지 않는다.
  이미 검토를 거쳤다. 계약이 부족하다고 판단되면 바꾸지 말고 최종 메시지에 보고한다.
- `data/` 아래 원본은 읽기 전용이다. 생성물은 `artifacts/` 에만 쓴다.

## 테스트

- fixture 는 `tests/fixtures/` 에 실제 스키마를 그대로 본뜬 JSON 으로 만든다.
  `data/` 의 실제 파일을 기본 테스트 스위트에서 읽지 않는다.
- 정상, 부분 실패, 전체 실패, 경계를 모두 덮는다. 특히:
  - 페이지 순서가 뒤섞인 입력이 정렬되어 나온다
  - 중복 page 번호가 실패로 보고된다
  - 공백뿐인 페이지가 `EMPTY_PAGE` 로 보고되고 나머지 페이지는 살아남는다
  - `page_texts` 가 비어 있으면 `ParseResult` 가 failure 를 반드시 담는다
  - checksum 불일치가 감지된다
  - 같은 입력에서 두 번 파싱한 결과가 byte 동일하다
- `DeterministicPageChunker` 에 이 adapter 의 출력을 넣어 chunk 가 만들어지고
  page span 이 원본 페이지로 역추적되는 통합 테스트를 하나 추가한다.

테스트가 통과하는 것으로 부족하다. 위 실패 경로가 **실제로 실행되어야** 한다.

## 완료 전 반드시 통과시킬 것

```
uv run pytest && uv run mypy && uv run ruff check . && uv run ruff format --check .
```

작업이 끝나면 커밋한다.

## 최종 메시지에 담을 것

- 변경 파일 목록과 확정한 공개 시그니처
- `char_count` 불일치 처리와 `requires_ocr` 판정 기준, 그 근거
- P1.3 체크박스 중 실제로 충족한 것만. 충족하지 않은 항목을 완료로 보고하지 않는다.
- 미해결 항목과 사람의 판단이 필요한 지점
