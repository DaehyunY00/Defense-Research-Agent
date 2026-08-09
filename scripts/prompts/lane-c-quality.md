docs/IMPLEMENTATION_PLAN.md 의 P1.6 Quality gate 를 구현한다.
AGENTS.md 절대 규칙 1~7과 같은 문서의 "Definition of Done" 을 전부 만족시킨다.

## 배경

계약과 임계값은 이미 확정돼 있다. 바꾸지 않는다.

- `domain/quality.py` — `PublicationQualityStatus`(7개), `QualityMeasurements`,
  `QualityThresholds`, `PublicationQualityVerdict`,
  `CONTROL_CHARACTER_SUBSTITUTIONS`, `DEFAULT_QUALITY_THRESHOLDS_VERSION`
- `evaluation/quality.py` — `PublicationQualityGate` ABC (`measure` / `evaluate` 분리)

임계값은 `docs/DECISIONS.md` 의 ADR-010 에서 코퍼스 370개 측정으로 확정했다.
그 ADR 과 `scripts/measure_quality_thresholds.py` 를 반드시 읽는다. 핵심:

- 측정 전에 `U+0001` 을 공백으로 치환한다. 치환은 **측정에만** 적용하고 저장되는
  page text 는 건드리지 않는다. 치환하지 않으면 국방논단 100개 중 78개가 잘못 제외된다.
- 다섯 임계값을 함께 적용하면 370개 중 39개가 걸리고 38개는 저추출이다. 저추출 이외
  사유로 걸리는 실제 발간물은 DQ-03 이 지목한 손상 보고서 1개뿐이다.

`tests/unit/evaluation/test_quality_gate_contract.py` 의 `FakeQualityGate` 가 참고 구현이다.
이번 레인은 그것을 실제 운영 게이트로 만든다.

## 작업

- `PublicationQualityGate` 의 실 구현을 만든다. `measure` 는 임계값을 적용하지 않고
  `evaluate` 는 저장된 measurements 로 재판정할 수 있어야 한다.
- 측정 항목: empty text, control character, printable ratio, Korean ratio, page density.
  `printable_ratio` 는 `str.isprintable()` 기준이며 제어문자 비율과 구분된다.
- 7개 status 를 계산한다. `duplicate` 는 대상 publication 을 추적하고,
  `orphan_pdf` 와 `manual_review` 의 판정 조건을 명시적으로 정의한다.
- 품질 미달 문서를 기본 인덱스에서 제외하는 경로를 만든다. 어디에 배선할지 판단하고
  근거를 남긴다. 다만 아래 경계에서 금지한 파일은 건드리지 않는다.
- 재추출/OCR 대기열과 failure report 를 `artifacts/` 에 생성한다. 형식과 버전을 정한다.
- `DATA_QUALITY_REPORT.md` 의 알려진 위험(DQ-01 중복, DQ-02 저추출, DQ-03 제어문자,
  DQ-04 파일명 잘림, orphan)을 **회귀 fixture 로** 반영한다.

## 경계

- 작업 범위는 `src/defense_research_agent/evaluation/quality.py` 와 대응하는 `tests/` 뿐이다.
  구현이 커지면 `evaluation/quality.py` 를 `evaluation/quality/` 패키지로 바꿔도 된다.
  그 경우 `evaluation/quality/__init__.py` 는 이 레인 소유다.
- 예외: `docs/IMPLEMENTATION_PLAN.md` 의 P1.6 섹션은 **반드시 갱신한다.** 충족한 체크박스만
  체크한다. 다른 섹션은 건드리지 않는다.
- `src/defense_research_agent/domain/` 전체, `search/` 전체, `data/readers/`,
  `services/`, `repositories/` 는 **수정하지 않는다.** 다른 레인이 동시에 작업 중이다.
  인덱스 배선이 이 파일들을 바꿔야 한다면 구현하지 말고 최종 메시지에 필요한 변경을
  구체적으로 보고한다.
- `src/defense_research_agent/domain/__init__.py`,
  `src/defense_research_agent/search/__init__.py`,
  `src/defense_research_agent/evaluation/__init__.py`,
  `src/defense_research_agent/services/__init__.py` 는 **수정하지 않는다.**
  필요한 심볼은 전체 모듈 경로로 import 한다.
- `pyproject.toml` 과 `uv.lock` 은 수정하지 않는다. 새 의존성이 필요하면 구현을 중단하고
  최종 메시지에 `NEEDS_DEPENDENCY: <패키지> <이유>` 로 보고한다.
- 임계값과 `CONTROL_CHARACTER_SUBSTITUTIONS` 를 바꾸지 않는다. 측정 근거가 있는
  값이다. 부적절하다고 판단되면 바꾸지 말고 근거와 함께 최종 메시지에 보고한다.
- `data/` 아래 원본은 읽기 전용이다. 생성물은 `artifacts/` 에만 쓴다.

## 테스트

fixture 는 실측 대역에 맞춘다. 실제 코퍼스에 존재하지 않는 밀도를 대표값으로 쓰지 않는다.
운영 기본 임계값을 그대로 쓴다. 완화한 사본으로 테스트하지 않는다. 반드시 덮을 것:

- 국방논단 대역(`U+0001` 1.7~5.0%)이 치환 덕분에 살아남는다
- 같은 밀도의 비치환 제어문자는 `corrupt_text` 로 걸린다
- 손상 보고서 대역(약 40%)이 제외된다
- 7개 status 가 전부 유효 verdict 로 도달 가능하다
- 같은 measurements 에 다른 `thresholds_version` 을 적용해 재판정할 수 있다
- 같은 입력에서 두 번 판정한 결과가 byte 동일하다

테스트가 통과하는 것으로 부족하다. 위 분기가 **실제로 참이 되어야** 한다.
`data/` 의 실제 파일을 기본 테스트 스위트에서 읽지 않는다.

## 완료 전 반드시 통과시킬 것

```
uv run pytest && uv run mypy && uv run ruff check . && uv run ruff format --check .
```

작업이 끝나면 커밋한다.

## 최종 메시지에 담을 것

- 변경 파일 목록과 확정한 공개 시그니처
- `orphan_pdf` / `manual_review` 판정 조건과 근거
- 인덱스 제외를 어디에 배선했는지, 또는 배선에 필요한 다른 레인 소유 파일의 변경 요청
- 대기열·failure report 형식과 버전
- P1.6 체크박스 중 실제로 충족한 것만. 충족하지 않은 항목을 완료로 보고하지 않는다.
- 미해결 항목과 사람의 판단이 필요한 지점
