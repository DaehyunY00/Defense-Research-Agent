docs/IMPLEMENTATION_PLAN.md 의 P1.4 OCR fallback boundary 를 구현한다.
AGENTS.md 절대 규칙 1~7과 같은 문서의 "Definition of Done" 을 전부 만족시킨다.

## 배경

파서 계약(`search/parsers/base.py`)에 `ParseResult.requires_ocr` 와
`ParserCapability.OCR_SIGNAL` 이 이미 있고, `PdfiumPdfParser` 가 텍스트 레이어 없는
페이지를 식별한다. 이번 레인은 그 신호를 받아 **페이지 단위로 OCR 을 시도하고, 기본
추출보다 나을 때만 채택하는 경계**를 만든다.

실제 OCR 엔진은 붙이지 않는다. 의존성이 없고 선택은 별도 결정이다. interface 와
결정적 fake 를 만들어 파이프라인과 채택 규칙을 오프라인에서 검증할 수 있게 한다.

관련 사실:
- `data/` 실측에서 저추출 문서는 38건이고 대부분 Brief 다(ADR-010).
- 품질 게이트가 `low_text` / `corrupt_text` / `orphan_pdf` 를 재추출·OCR 대기열로
  내보낸다(`evaluation/quality.py` 의 artifact writer).
- `ExtractionProvenance` 는 parser name/version/source checksum 을 담는다.
  OCR 산출 페이지는 원본 추출과 **다른 provenance** 를 가져야 하며, chunker 가 이미
  provenance 변경을 chunk 경계로 처리한다.

## 작업

- OCR provider interface 를 정의한다. 페이지 단위 입력, 페이지 단위 결과.
  - 결과에 OCR 원문, confidence, provider name/version, 입력 이미지 또는 페이지의
    checksum 을 보존한다.
  - timeout 과 부분 실패를 결과 모델로 표현한다. 예외로 던지지 않는다.
    파서 계약의 실패 처리 방식(`ParserFailure` 반환)과 같은 원칙을 따른다.
- 결정적 fake provider 를 만든다. 외부 프로세스·네트워크·자격증명 없이 동작하고
  같은 입력에서 byte 동일 결과를 낸다. 무엇을 보장하고 무엇을 보장하지 않는지
  docstring 에 명시한다. OCR 정확도를 주장하지 않는다.
- **OCR 필요 조건**을 정의한다. 어떤 페이지·문서 상태에서 OCR 을 시도하는지 명시적
  규칙으로 만든다. `requires_ocr` 신호와 품질 게이트 status 중 무엇을 근거로 삼을지
  정하고 이유를 남긴다.
- **채택 규칙**을 정의한다. OCR 결과가 기본 추출보다 나을 때만 채택한다. "낫다"의
  판정 기준을 결정적 코드로 만든다. 근거 없이 항상 채택하지 않는다.
  채택하지 않은 OCR 결과도 버리지 말고 판단 근거와 함께 보존한다.
- OCR 로 대체된 페이지는 OCR provider 를 가리키는 `ExtractionProvenance` 를 갖는다.
  원본 추출 페이지와 섞이면 chunker 가 경계로 처리하는지 확인한다.

## 경계

- 작업 범위는 `src/defense_research_agent/search/ocr/`(신규)와 대응하는 `tests/` 뿐이다.
  `search/ocr/__init__.py` 는 이 레인 소유다.
- 예외: `docs/IMPLEMENTATION_PLAN.md` 의 P1.4 섹션은 **반드시 갱신한다.** 충족한
  체크박스만 체크한다. 다른 섹션은 건드리지 않는다.
- `src/defense_research_agent/domain/` 전체, `search/` 의 다른 파일, `evaluation/`,
  `data/readers/`, `services/` 는 **수정하지 않는다.** 다른 레인이 동시에 작업 중이다.
  특히 `search/chunking.py` 는 P1.7 레인이 소유한다.
- 최상위 배럴 4개(`domain/__init__.py`, `search/__init__.py`, `evaluation/__init__.py`,
  `services/__init__.py`)를 **수정하지 않는다.** 통합 단계에서 일괄 처리한다.
  필요한 심볼은 전체 모듈 경로로 import 한다.
- `pyproject.toml` 과 `uv.lock` 을 수정하지 않는다. OCR 엔진을 포함해 어떤 새 의존성도
  추가하지 않는다. 필요하면 구현을 중단하고 `NEEDS_DEPENDENCY: <패키지> <이유>` 로
  보고한다. 표준 라이브러리만으로 구현한다.
- 기존 계약의 공개 시그니처를 바꾸지 않는다. 부족하면 바꾸지 말고 최종 메시지에 보고한다.
- `data/` 아래 원본은 읽기 전용이다. 생성물은 `artifacts/` 에만 쓴다.

## 테스트

기본 오프라인 suite 에서 실제 OCR 엔진을 호출하지 않는다. 반드시 실제로 실행될 것:

- OCR 필요 조건이 참인 페이지와 거짓인 페이지 양쪽
- OCR 결과가 기본 추출보다 나아서 **채택되는** 경로
- OCR 결과가 더 나쁘거나 동등해서 **채택되지 않는** 경로, 그리고 그 결과가 판단 근거와
  함께 보존되는지
- 페이지 단위 timeout 과 부분 실패 — 한 페이지가 실패해도 나머지가 살아남는다
- 채택된 페이지의 provenance 가 OCR provider 를 가리킨다
- 원본 추출 페이지와 OCR 페이지가 섞인 입력에서 chunker 가 경계를 만든다
- 같은 입력에서 두 번 실행한 결과가 byte 동일하다

테스트가 통과하는 것으로 부족하다. 위 분기가 실제로 참이 되어야 한다.

## 완료 전

```
uv run pytest && uv run mypy && uv run ruff check . && uv run ruff format --check .
```

작업이 끝나면 커밋한다.

## 최종 메시지에 담을 것

- 변경 파일 목록과 확정한 공개 시그니처
- OCR 필요 조건과 채택 규칙, 각각의 근거
- fake provider 가 보장하는 것과 보장하지 않는 것
- P1.4 체크박스 중 실제로 충족한 것만. 충족하지 않은 항목을 완료로 보고하지 않는다.
- 실제 OCR 엔진을 붙일 때 필요한 결정과 미해결 항목
