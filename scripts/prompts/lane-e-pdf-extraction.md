docs/IMPLEMENTATION_PLAN.md 의 P1.3 중 **PDF 본문 직접 추출** 부분을 구현한다.
AGENTS.md 절대 규칙 1~7과 같은 문서의 "Definition of Done" 을 전부 만족시킨다.

## 배경

P1.3 의 JSON page adapter 부분은 이미 끝났다(`search/parsers/json_page_parser.py`).
이번 레인은 PDF 에서 페이지별 본문을 **직접 추출**하는 adapter 를 추가한다.

라이브러리는 `pypdfium2` 로 확정됐다. 근거는 `docs/DECISIONS.md` 의 ADR-011 이다.
이미 의존성에 추가돼 있으므로 `pyproject.toml` 을 건드릴 필요가 없다.

계약은 이미 있다. `search/parsers/base.py` 의 `DocumentParser` ABC, `ParseResult`,
`ParserCapability`, `ParserErrorCode`, `ParserFailure` 와
`domain/provenance.py` 의 `ExtractionProvenance` 다. 계약은 바꾸지 않는다.

실제 코퍼스에서 확인된 사실:

- `data/연구보고서/` 의 한 문서는 206페이지이고 1페이지에 표지 제목이 있다.
- 본문이 전혀 없는 페이지가 존재한다(위 문서의 2페이지는 0자).
- 추출 텍스트에 `\r\n` 이 섞여 나온다.
- `DATA_QUALITY_REPORT.md` DQ-03: 고유 문서 370개 중 192개에 C0/C1 제어문자가 있다.
  `U+0001` 이 163개 문서에 나타나며 공백 대체로 쓰인다. 이 정규화는 품질 게이트의
  측정 단계 책임이다(ADR-010). **파서는 추출 원문을 그대로 보존한다.**

## 작업

- `DocumentParser` 를 구현하는 PDF adapter 를 `search/parsers/` 에 추가한다.
  `pypdfium2` import 는 이 adapter 안에만 존재한다. `base.py` 는 어떤 PDF 라이브러리도
  import 하지 않는다.
- `supports()` 는 PDF 를 주장하고, `capabilities` 는 실제 능력만 선언한다.
  `pypdfium2` 는 표 추출을 제공하지 않으므로 `TABLES` 를 주장하지 않는다.
- `ExtractionProvenance` 를 채운다. `parser_version` 은 adapter 버전이며, 추출 결과가
  달라질 수 있는 변경에서 올린다는 사실을 docstring 에 명시한다.
- 페이지별 텍스트를 `PublicationPage` 로 만든다. `provenance` 는 필수다.
  `section_title` 은 이번 범위가 아니다. `None` 으로 두고 추측하지 않는다.
- 아래를 `ParserFailure` 로 보고한다. 예외로 던지지 않는다.
  - PDF 헤더 불일치, 손상된 구조
  - 암호화된 문서
  - 전달받은 checksum 과 실제 파일 checksum 불일치
  - 본문이 없는 페이지 (`EMPTY_PAGE`)
  - 페이지 단위 디코딩 실패 — 해당 페이지만 실패로 보고하고 나머지는 살린다
  - 전체 페이지에서 텍스트를 하나도 얻지 못한 경우 (`EMPTY_DOCUMENT`)
- 비정상 유니코드(서로게이트, 미정의 코드포인트)를 만나면 조용히 버리지 않는다.
  보존할지 실패로 보고할지 정하고 근거를 남긴다.
- `requires_ocr` 판정 기준을 정한다. 텍스트 레이어가 없는 스캔 PDF 를 식별하는 근거를
  명시한다. 근거 없이 `False` 로 두지 않는다.
- 기존 JSON `page_texts` 와 신규 PDF 추출 결과 중 **무엇을 쓸지 정하는 선택 정책**을
  구현한다. P1.3 체크박스 항목이다. 어느 쪽이 기본인지, 언제 다른 쪽으로 넘어가는지,
  둘이 불일치할 때 어떻게 되는지를 결정론적으로 정의한다.
- `pypdfium2` 자원을 확실히 해제한다. 파일 핸들이 남으면 안 된다.

## 경계

- 작업 범위는 `src/defense_research_agent/search/parsers/` 와 대응하는 `tests/` 뿐이다.
  `search/parsers/__init__.py` 는 이 레인 소유이므로 수정해도 된다.
- 예외: `docs/IMPLEMENTATION_PLAN.md` 의 P1.3 섹션은 **반드시 갱신한다.** 충족한 체크박스만
  체크한다. 다른 섹션은 건드리지 않는다.
- `src/defense_research_agent/domain/` 전체, `search/` 의 다른 파일, `evaluation/`,
  `data/readers/`, `services/` 는 **수정하지 않는다.**
- `src/defense_research_agent/domain/__init__.py`,
  `src/defense_research_agent/search/__init__.py`,
  `src/defense_research_agent/evaluation/__init__.py`,
  `src/defense_research_agent/services/__init__.py` 는 **수정하지 않는다.**
  최상위 배럴 재노출은 통합 단계에서 사람이 일괄 처리한다.
- `pyproject.toml` 과 `uv.lock` 은 수정하지 않는다. `pypdfium2` 는 이미 추가돼 있다.
  그 외 의존성이 필요하면 구현을 중단하고 최종 메시지에
  `NEEDS_DEPENDENCY: <패키지> <이유>` 로 보고한다.
- `search/parsers/base.py` 의 공개 시그니처는 바꾸지 않는다. 이미 검토를 거쳤다.
  부족하다고 판단되면 바꾸지 말고 최종 메시지에 보고한다.
- `data/` 아래 원본은 읽기 전용이다. 열기만 하고 쓰지 않는다. 생성물은 `artifacts/` 에만
  쓴다. 작업 전후로 원본 해시가 같은지 확인한다.
- OCR 은 이번 범위가 아니다. P1.4 다.

## 테스트

- fixture PDF 는 `tests/fixtures/` 에 **생성해서** 넣는다. `data/` 의 실제 파일을 기본
  테스트 스위트에서 읽지 않는다. `pypdfium2` 로 최소 PDF 를 만들거나 바이트를 직접
  구성한다.
- 네 publication type 의 대표 fixture 로 page mapping 을 검증한다. 페이지 번호가 실제
  원본 페이지와 일치해야 한다.
- 반드시 실제로 실행될 것:
  - 정상 다중 페이지 추출
  - 본문 없는 페이지가 `EMPTY_PAGE` 로 보고되고 나머지 페이지는 살아남는다
  - 손상된 PDF 가 실패로 보고된다 (예외로 던지지 않는다)
  - 암호화된 PDF 가 `ENCRYPTED` 로 보고된다
  - checksum 불일치가 감지된다
  - 텍스트를 하나도 얻지 못하면 `ParseResult` 가 failure 를 반드시 담는다
  - 같은 입력에서 두 번 추출한 결과가 byte 동일하다
  - 추출 후 원본 파일 해시가 변하지 않는다
- 추출된 텍스트를 `DeterministicPageChunker` 에 넣어 page span 이 원본 페이지로
  역추적되는 통합 테스트를 하나 추가한다.

테스트가 통과하는 것으로 부족하다. 위 실패 경로가 **실제로 실행되어야** 한다.

## 완료 전 반드시 통과시킬 것

```
uv run pytest && uv run mypy && uv run ruff check . && uv run ruff format --check .
```

작업이 끝나면 커밋한다.

## 최종 메시지에 담을 것

- 변경 파일 목록과 확정한 공개 시그니처
- JSON page_texts 와 PDF 추출 결과의 선택 정책, 그 근거
- `requires_ocr` 판정 기준과 비정상 유니코드 처리 방침, 그 근거
- P1.3 체크박스 중 실제로 충족한 것만. 충족하지 않은 항목을 완료로 보고하지 않는다.
- 미해결 항목과 사람의 판단이 필요한 지점
