docs/IMPLEMENTATION_PLAN.md 의 P1.5 Metadata normalization 을 구현한다.
AGENTS.md 절대 규칙 1~7과 같은 문서의 "Definition of Done" 을 전부 만족시킨다.

## 배경

계약은 이미 있다. `domain/metadata.py` 의 `ExtractedPublicationMetadata`,
`ExtractedMetadataValue`, `ExtractedAuthor`, `PublicationDates`, `MetadataEvidence`,
`MetadataEvidenceSource` 와 `search/metadata.py` 의 `PublicationMetadataExtractor` ABC 다.
이번 레인은 그 계약을 실제 코퍼스에 대해 **구현**한다. 계약은 바꾸지 않는다.

`docs/DATA_QUALITY_REPORT.md` 의 6~7절이 이 작업의 요구사항 원본이다. 반드시 읽는다.
핵심 사실:

- 파일명은 `<연도>_<대표저자>_<제목>.pdf` 형태이고 대체로 저자 한 명만 준다.
- 실제 표지에는 복수 저자, 직급, 센터, 소속, 이메일이 함께 나타난다.
  `국방정책연구` 는 저자명 뒤 `*`, `**` 와 각주로 소속을 연결한다.
- PDF 37개가 파일명 240바이트 이상, 3개가 255바이트, 11개가 불완전 자모로 끝난다(DQ-04).
  이런 파일명은 제목이 잘려 있으므로 표지를 우선해야 한다.
- 발행 표기는 유형별로 다르다. `국방논단` 은 `2024년 11월 4일`(일),
  `국방정책연구` 는 `2024년 여름(40-2)`(계절호), `연구보고서` 는 `2024. 2.`(월),
  `Brief` 는 사업연도와 발행연월이 함께 나타날 수 있다.
- `metadata.processed_date` 는 추출 처리 시각이지 발행일이 아니다.
- 파일명 연도와 본문 발행 연도가 다르면 자동으로 덮어쓰지 말고 둘 다 보존한다.

## 작업

- `PublicationMetadataExtractor` 를 구현하는 추출기를 만든다. 입력은
  `ResearchPublication`, `Sequence[PublicationPage]`, 선택적 `source_path` 다.
- 표지·본문·파일명 근거를 `MetadataEvidenceSource` 로 구분해 기록한다.
  충돌하면 강한 근거가 이긴다. 약한 근거는 **버리고 병합하지 않는다.**
- 제목, 부제, organization, 권·호, DOI, 초록, 키워드를 추출한다.
- 저자는 `ExtractedAuthor` 로 복수 추출한다. 이름, 직급, 소속, 이메일, 대표저자 여부를
  가능한 범위에서 채우고 각 저자에 개별 근거와 confidence 를 붙인다.
  `국방정책연구` 의 `*`/`**` 각주 연결을 처리한다.
- 날짜는 `PublicationDates` 로 채운다. `filename_year`, `published_at`,
  `published_precision`, `issue_label`, `processed_at` 을 분리 보존한다.
  계절호는 `DatePrecision.SEASON` 과 원문 `issue_label` 을 함께 남긴다.
- 확정할 수 없으면 **추측하지 않는다.** `normalized=None` 과 `failure_reason` 을 남긴다.
- 정규화 규칙(공백, 자모, 유니코드)은 한 곳에 모으고 버전을 부여한다. 같은 입력에서
  같은 출력이 나와야 한다.

## 경계

- 작업 범위는 `src/defense_research_agent/search/metadata.py` 와 대응하는 `tests/` 뿐이다.
  구현이 커지면 `search/metadata.py` 를 `search/metadata/` 패키지로 바꿔도 된다.
  그 경우 `search/metadata/__init__.py` 는 이 레인 소유다.
- 예외: `docs/IMPLEMENTATION_PLAN.md` 의 P1.5 섹션은 **반드시 갱신한다.** 충족한 체크박스만
  체크한다. 다른 섹션은 건드리지 않는다.
- `src/defense_research_agent/domain/` 전체, `search/parsers/`, `search/embeddings/`,
  `evaluation/`, `data/readers/` 는 **수정하지 않는다.** 다른 레인이 동시에 작업 중이다.
- `src/defense_research_agent/domain/__init__.py`,
  `src/defense_research_agent/search/__init__.py`,
  `src/defense_research_agent/evaluation/__init__.py`,
  `src/defense_research_agent/services/__init__.py` 는 **수정하지 않는다.**
  최상위 배럴 재노출은 통합 단계에서 사람이 일괄 처리한다.
  필요한 심볼은 전체 모듈 경로로 import 한다.
- `pyproject.toml` 과 `uv.lock` 은 수정하지 않는다. 새 의존성이 필요하면 구현을 중단하고
  최종 메시지에 `NEEDS_DEPENDENCY: <패키지> <이유>` 로 보고한다.
- 계약(`domain/metadata.py`, `search/metadata.py` 의 ABC)이 부족하다고 판단되면
  바꾸지 말고 최종 메시지에 무엇이 왜 부족한지 보고한다.
- `data/` 아래 원본은 읽기 전용이다. 생성물은 `artifacts/` 에만 쓴다.

## 테스트

fixture 는 `DATA_QUALITY_REPORT.md` 가 기록한 실제 모양을 재현한다. 실제 코퍼스에서
재현 불가능한 입력을 대표 fixture 로 쓰지 않는다. 반드시 덮을 것:

- 표지와 잘린 파일명이 충돌할 때 표지가 이기고 파일명 읽기가 **버려진다**
- 표지가 없으면 파일명이 약한 근거로 쓰이고 confidence 가 낮다
- 복수 저자와 각주 소속 연결
- 파일명 연도 ≠ 본문 발행 연도일 때 둘 다 남고 충돌이 드러난다
- 계절호 표기가 `SEASON` precision 과 `issue_label` 로 보존된다
- 확정 불가 필드가 `failure_reason` 과 함께 반환된다
- 같은 입력에서 두 번 추출한 결과가 byte 동일하다

테스트가 통과하는 것으로 부족하다. 위 경로가 **실제로 실행되어야** 한다.
`data/` 의 실제 파일을 기본 테스트 스위트에서 읽지 않는다.

## 완료 전 반드시 통과시킬 것

```
uv run pytest && uv run mypy && uv run ruff check . && uv run ruff format --check .
```

작업이 끝나면 커밋한다.

## 최종 메시지에 담을 것

- 변경 파일 목록과 확정한 공개 시그니처
- 근거 우선순위 규칙과 정규화 규칙 버전
- P1.5 체크박스 중 실제로 충족한 것만. 충족하지 않은 항목을 완료로 보고하지 않는다.
- 계약의 부족한 점, 미해결 항목, 사람의 판단이 필요한 지점
