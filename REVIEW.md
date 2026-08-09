## 매칭된 문서

- **루브릭 2** — `docs/IMPLEMENTATION_PLAN.md:302` `## Definition of Done` (공통 17항목 + `Retrieval 변경에는 추가로 적용한다` 7항목, `docs/IMPLEMENTATION_PLAN.md:324`)
- **루브릭 3** — `docs/IMPLEMENTATION_PLAN.md:137` `### P1.2 Parser abstraction`, `docs/IMPLEMENTATION_PLAN.md:164` `### P1.5 Metadata normalization`, `docs/IMPLEMENTATION_PLAN.md:172` `### P1.6 Quality gate`, `docs/IMPLEMENTATION_PLAN.md:195` `### P2.1 \`EmbeddingProvider\` interface`
- 해당 4개 섹션에 별도 "완료 조건" 문장은 없다. 완료 조건 문장은 P1.1(`:119`)에만 있고, 이번 diff가 그 미성립을 `:122-135`에 문서화했다.

---

## 판정표 1 — AGENTS.md 절대 규칙

| # | 규칙 | 판정 | 근거 |
|---|---|---|---|
| 1 | `data/` 원본 미변경 | met | diff에 `data/` 경로 없음. `search/parsers/base.py:120-122`가 adapter에 write 금지를 계약으로 명시 |
| 2 | 자유 SQL 실행 금지 | n/a | diff에 SQL 없음 |
| 3 | 외부 콘텐츠 untrusted | met | `domain/metadata.py:54` `raw_text`를 원문 그대로 보존만 하고 렌더링·해석 경로 없음 |
| 4 | LLM 출력 Pydantic 검증 | n/a | diff에 LLM 호출 경로 없음. 신규 모델은 전부 `DomainModel`(`extra="forbid"`) |
| 5 | 점수·정렬·분기는 Python | met | `domain/quality.py:59-71`, `evaluation/quality.py:32-52` — threshold 판정이 순수 Python. LLM 개입 없음 |
| 6 | 복잡한 변경 전 계획 | met | `docs/IMPLEMENTATION_PLAN.md:122-135` 갱신 |
| 7 | 정상·실패·경계 테스트 | 부분 not_met | 아래 BLOCKER-1 참조 |

## 판정표 2 — Definition of Done

| 항목 | 판정 | 근거 |
|---|---|---|
| domain model과 interface 검토 완료 | met | `domain/provenance.py`, `domain/metadata.py`, `domain/quality.py` |
| 입력·출력·변경 파일과 의존성 기록 | met | `docs/IMPLEMENTATION_PLAN.md:135` |
| unit test 작성 | met | 5개 신규 테스트 파일, `G0.log:28-34` 전부 실행됨 |
| **failure-path test 작성** | **부분 not_met** | 실재 확인: `test_parser_contract.py:111` ENCRYPTED, `:127` 빈 결과, `test_embedding_contract.py:114` 부분 실패, `test_metadata_extractor_contract.py:89` 명시적 실패 — 모두 **실제로 실패 경로를 실행**한다. 단 quality gate의 실패 경로는 LOW_TEXT/DUPLICATE 2개뿐 → BLOCKER-1 |
| **경계 조건 테스트 작성** | **부분 not_met** | 실재 확인: `test_quality.py:36` 페이지수 역전, `:41` unit interval 밖, `:48` 0페이지, `test_embedding_contract.py:124` 빈 배치, `:178` 길이 0 벡터 — 실제 경계값을 통과시킨다. 단 `control_character_count > 0` 경계는 전무 → BLOCKER-1 |
| 필요한 경우 integration test | n/a | production consumer 없음(신규 계약을 `src/` 내에서 참조하는 코드 0건) |
| deterministic offline test 제공 | met | `test_embedding_contract.py:105` byte-equivalence, `test_parser_contract.py:171` provenance에 timestamp 부재 검증 |
| 외부 의존성 fake/fixture 격리 | met | 전부 in-memory. `FakePdfParser.supports`(`test_parser_contract.py:39`)는 suffix만 검사하고 디스크 접근 없음. **네트워크·자격증명 요구 0건** |
| `uv run pytest` 통과 | met | `G0.log` 260 passed |
| `uv run mypy` strict 통과 | met | `G0.log` 145 files, no issues |
| `ruff check` / `ruff format --check` | met | `G0.log` All checks passed / 155 formatted |
| 관련 documentation 업데이트 | met | `docs/IMPLEMENTATION_PLAN.md:122-135` |
| `data/` 원본 미변경 확인 | met | diff에 `data/` 없음 |
| secret·`.env` 미커밋 | met | diff에 자격증명 없음 |
| **평가 metric과 예상 영향 기록** | **not_met** | `domain/quality.py:67-71` 기본값 5개 중 `min_character_count=1_000`만 `DATA_QUALITY_REPORT.md:24`(DQ-02)에 대응. `min_printable_ratio=0.9`, `min_korean_ratio=0.1`, `max_control_character_ratio=0.01`의 근거·예상 제외 문서 수 기록 없음 → QUESTION-1 |
| human approval boundary 유지 | met | 자동 `approved` 전이 없음 |
| Retrieval 추가 7항목 | n/a | P2.1은 interface 정의만이고 ranking 동작 변경 0건 |

## 판정표 3 — P1.2 / P1.5 / P1.6 / P2.1 체크박스

| 섹션 | 체크박스 | 판정 | 근거 |
|---|---|---|---|
| P1.2 | input/output·stable error taxonomy | met | `parsers/base.py:32-48` 9개 코드, `:63-91` ParseResult |
| P1.2 | capability에 text/pages/tables/OCR 신호 | met | `parsers/base.py:23-29`, `:69` `requires_ocr` |
| P1.2 | provider 라이브러리 adapter 내부 격리 | met | `parsers/base.py:12-20` — stdlib·pydantic·domain만 import |
| P1.2 | parser version·source checksum 기록 | met | `domain/provenance.py:15-17`, `parsers/base.py:66` |
| P1.2 | fake parser 정상·실패·부분 추출 테스트 | met | `test_parser_contract.py:89`, `:111`, `:101` |
| P1.5 | 제목·부제·**authors**·organization·발행일/정밀도·권호·DOI·초록·**키워드** 추출 | **not_met** | BLOCKER-3 |
| P1.5 | 원본 표기·normalized·confidence·evidence page 보존 | 부분 not_met | 단일값 필드는 met(`domain/metadata.py:46-88`). 복수값 필드는 BLOCKER-3 |
| P1.5 | **파일명 연도·processed date·published date 분리** | **not_met** | BLOCKER-2 |
| P1.5 | **긴 파일명·불완전 자모에서 표지 근거 우선** | **not_met** | BLOCKER-4 |
| P1.5 | 추측 대신 `null`과 실패 사유 반환 | met | `domain/metadata.py:74-81`, `test_metadata.py:37`, `:57` |
| P1.6 | 7개 status 계산 | 부분 not_met | enum은 `domain/quality.py:20-29` 전부 존재. 유효 verdict로 실제 구성되는 status는 READY/LOW_TEXT/DUPLICATE 3개뿐 → BLOCKER-1 |
| P1.6 | **empty text·control character·printable/Korean ratio·page density 측정** | **not_met** | BLOCKER-1 |
| P1.6 | 품질 미달 문서를 기본 인덱스에서 제외 | not_met | `domain/quality.py:31-34` `is_indexable` 존재하나 `src/` 내 호출자 0건 |
| P1.6 | threshold·제외 사유를 versioned 설정으로 기록 | met | `domain/quality.py:59-71`, `:80-81`, `test_quality.py:98` |
| P1.6 | 재추출/OCR 대기열·failure report 생성 | not_met | 해당 코드 없음 |
| P1.6 | `DATA_QUALITY_REPORT.md` 위험을 회귀 fixture로 반영 | not_met | DQ-01 duplicate만 `test_quality_gate_contract.py:118`에 반영. DQ-03(제어문자), DQ-04(파일명 잘림), orphan은 fixture 0건 |
| P2.1 | document/query embedding·batch capability | met | `embeddings/base.py:120-134` |
| P2.1 | model ID·dimension·normalization·input checksum·version | met | `embeddings/base.py:59-65`, `:47-49`, `test_embedding_contract.py:142` |
| P2.1 | timeout·partial failure·invalid dimension 오류 모델 | met | `embeddings/base.py:21-29`, `:69-78`, `test_embedding_contract.py:114`, `:150` |
| P2.1 | secret·provider 원문 결과·로그에서 제외 | met | 결과 모델에 raw response 필드 없음 + `DomainModel`의 `extra="forbid"`(`domain/common.py:36`)로 추가 불가. `message: Label`(max 500) |

## 판정표 4 — 추가 확인 항목

| 항목 | 결과 |
|---|---|
| 레인 경계 | `domain/__init__.py` **포함됨**(신규 모듈 re-export 12개 추가만, 기존 export 변경 0건). `services/__init__.py`, `pyproject.toml`, `uv.lock` **미포함** |
| 네트워크·자격증명 요구 | 없음 |
| fake의 실제 동작 과대 대표 | **있음** → BLOCKER-1 |
| serialization 호환성 | 깨지지 않음. 기존 `PublicationChunk`/`PublicationPage`/`ResearchPublication` 필드 변경 0건, 순수 additive |

---

## BLOCKER

**BLOCKER-1 — 실제 corpus의 지배적 분기(제어문자)가 어떤 테스트에서도 참이 되지 않고, fake가 그것을 은폐한다**

`tests/unit/evaluation/test_quality_gate_contract.py:34-41`

```python
control_character_count = (0,)
printable_ratio = (1.0,)
korean_ratio = (1.0,)
```

`FakeQualityGate.measure`가 이 세 값을 입력과 무관하게 상수로 반환한다. `evaluate`(`:43-76`)는 character-count와 duplicate 두 규칙만 적용하므로 `CORRUPT_TEXT`를 구조적으로 반환할 수 없다. `tests/unit/domain/test_quality.py:14-24`의 `_measurements` 기본값도 `control_character_count: 0`이고, `:36-95`의 어떤 override도 이 값을 건드리지 않는다. 즉 **diff 전체에서 `control_character_count > 0`이 참이 되는 지점이 0건**이며, `domain/quality.py:71` `max_control_character_ratio=0.01`은 어디에서도 적용되지 않는다.

`docs/DATA_QUALITY_REPORT.md:25`(DQ-03)에 따르면 실제 corpus 370개 중 **192개(52%)** 에 C0/C1 제어문자가 있고, 유형별로 `국방논단` 100/100, `국방정책연구` 59/59다(`:117-118`). 운영 기본값에서 제어문자 측정은 소수 예외가 아니라 **과반 문서가 통과하는 지배적 분기**다. `test_healthy_publication_is_ready_and_indexable`(`:96`)가 READY를 주장하지만, 실제 corpus의 `국방논단` 문서로는 `control_character_count=0`이라는 fake의 전제 자체가 재현 불가능하다. 테스트는 통과하지만 검증하는 것은 계약이 아니라 fake의 상수다.

같은 이유로 `printable_ratio`/`korean_ratio` 임계값(`domain/quality.py:69-70`), `manual_review_page`(`:83`), `WARNING`/`ORPHAN_PDF`/`MANUAL_REVIEW` status가 유효 verdict로 한 번도 구성되지 않는다. `test_quality.py:86-95`가 `WARNING`을 쓰지만 `pytest.raises` 안이라 객체가 만들어지지 않는다.

**BLOCKER-2 — P1.5 "파일명 연도·processed date·published date 분리"가 모델 구조상 성립하지 않는다**

`src/defense_research_agent/domain/metadata.py:30`, `:97-98`

`MetadataField`에는 `PUBLICATION_DATE` 하나뿐이고 `ExtractedPublicationMetadata`에는 `publication_date`/`date_precision`만 있다. `filename_year`에 해당하는 필드도, `processed_date`에 해당하는 필드도 없다. `DatePrecision`(`:38-43`)이 추가되어 "발행일/정밀도" 요구가 충족된 것처럼 보이지만, 세 날짜를 분리 보존하는 수단이 없다.

`fields_must_not_repeat`(`:100-106`)가 같은 field의 중복을 금지하므로, `DATA_QUALITY_REPORT.md:166`이 요구하는 "파일명 연도와 본문 발행 연도가 다르면 자동 덮어쓰지 말고 경고와 함께 둘 다 보존"이 **구조적으로 불가능**하다. `MetadataEvidence.page_number=None`(`:51`, `test_metadata.py:72`)은 근거의 약함만 표시할 뿐 값의 의미(파일명 연도 vs 발행일)를 구분하지 않는다.

추가로 `DatePrecision`은 `YEAR`/`MONTH`/`DAY`만 가진다. `DATA_QUALITY_REPORT.md:151`에 기록된 `국방정책연구`의 `2024년 여름(40-2)` 계절호 표기(고유 59개 전체, corpus의 16%)는 표현할 precision이 없다. 같은 문서 `:161`은 `season`을 명시적으로 요구한다.

**BLOCKER-3 — P1.5 `authors`/`keywords`가 다중값으로 표현 불가능하다**

`src/defense_research_agent/domain/metadata.py:25`, `:35`, `:69`, `:100-106`

`MetadataField.AUTHORS`와 `MetadataField.KEYWORDS`가 enum에 있어 요구가 충족된 것처럼 보이지만, 이를 담는 `ExtractedMetadataValue.normalized`는 `str | None` 단일 스칼라이고, `evidence`도 `MetadataEvidence` 단일 객체이며, `fields_must_not_repeat`가 `AUTHORS`를 두 번 넣는 것을 금지한다. 결과적으로 저자 N명을 넣으려면 하나의 문자열로 뭉개야 하고, 저자별 `confidence`와 `evidence page`는 소실된다.

이는 P1.5의 "원본 표기, normalized value, confidence와 evidence page 보존"(`docs/IMPLEMENTATION_PLAN.md:167`)과 정면으로 충돌한다. `DATA_QUALITY_REPORT.md:176-188`은 `authors[]`에 `name, role, affiliation, email, is_primary, raw_text, evidence_page, confidence`를 요구하고, `:171-173`은 실제 표지에 복수 저자·직급·소속·이메일이 함께 나타나며 `국방정책연구`는 `*`/`**` 각주로 소속을 연결한다고 기록한다. 기존 `ResearchPublication.authors`도 `list[Label]`(`domain/publication.py:58`)이라 신규 계약이 기존 모델보다 표현력이 낮다.

**BLOCKER-4 — P1.5 "표지 근거 우선"에 코드 기제도 테스트도 없다**

`src/defense_research_agent/search/metadata.py:39`, `:41-46`

`extract(..., source_path: Path | None = None)` 파라미터와 "cover page wins and the file-name reading is dropped rather than merged" docstring이 있어 요구가 반영된 것처럼 보인다. 그러나 이를 강제하는 validator도, 우선순위를 판정하는 코드도 없다. 그리고 `tests/unit/search/test_metadata_extractor_contract.py`의 세 테스트(`:80`, `:92`, `:102`)는 **한 번도 `source_path`를 전달하지 않는다.** `FakeCoverPageExtractor.extract`(`:32-66`)도 `source_path`를 완전히 무시한다. 표지-파일명 충돌 상황이 계약 테스트에 존재하지 않는다.

`DATA_QUALITY_REPORT.md:26`(DQ-04)이 대상으로 지목한 240바이트 이상 PDF 37개와 불완전 자모로 끝나는 11개가 바로 이 경로에서 처리돼야 하는 문서다.

## SCOPE

- `PublicationPage`/`PublicationChunk`에 `ExtractionProvenance` 부착(P1.1 완료 조건의 parser version 역추적)은 `docs/IMPLEMENTATION_PLAN.md:122-135`에 미해결로 명시 선언되고 P1.7 착수 전 완료로 의존성이 기록됨 — VERDICT 미반영.

## QUESTION

- `domain/quality.py:69-71`의 `min_printable_ratio=0.9`, `min_korean_ratio=0.1`, `max_control_character_ratio=0.01`은 근거 기록이 없다. `DATA_QUALITY_REPORT.md:25`에 따르면 `국방논단` 100/100과 `국방정책연구` 59/59가 제어문자를 포함하며 다수는 `U+0001`을 시각적 공백으로 사용한다. 이 기본값으로 corpus 370개 중 몇 개가 `corrupt_text`로 색인 제외되는지, 그리고 `U+0001`을 공백 치환 후 판정할지 제어문자로 계수할지는 도메인 판단이 필요하다.
- `min_korean_ratio=0.1`은 `국방정책연구` 59개 전체에 존재하는 영문 `Abstract`/`Keywords` 구간(`DATA_QUALITY_REPORT.md:73`)과 영문 비중이 높은 문서를 어떻게 취급할지에 대한 정책 결정을 전제한다.

VERDICT: BLOCKED
