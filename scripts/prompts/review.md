{{BASE}}..{{HEAD}} 의 diff를 검증한다. 스타일 지적이나 일반적 개선 제안은 하지 않는다.
아래 세 루브릭에 대한 위반만 보고한다.

## 사전 조건

결정적 게이트(pytest / mypy / ruff check / ruff format)는 이 리뷰 전에 이미 통과했다.
로그는 {{G0LOG}} 에 있다. 테스트·린트를 직접 실행하지 말고 필요하면 이 로그를 읽는다.
판정 근거는 코드 본문 정독으로 만든다.

첫 줄에 루브릭 2와 3에 실제로 매칭한 문서 경로와 섹션 제목을 명시한다.
{{SECTION}} 을 문서에서 찾지 못하면 다른 섹션으로 대체하지 않는다.
그 경우 `SECTION_NOT_FOUND: {{SECTION}}` 한 줄과 `VERDICT: BLOCKED` 만 출력하고 끝낸다.

## 루브릭 1 — AGENTS.md 절대 규칙 1~7

특히: data/ 원본 미변경, LLM 출력의 Pydantic 검증,
점수·정렬·분기가 결정적 Python 코드인지, 외부 콘텐츠를 untrusted로 다루는지.

## 루브릭 2 — docs/IMPLEMENTATION_PLAN.md "Definition of Done" 항목

각 항목을 met / not_met / n/a 로 판정하고 근거 파일:줄을 붙인다.
특히 failure-path test 와 경계 조건 test 의 실재 여부를 테스트 코드에서 직접 확인한다.
"테스트가 있다"가 아니라 "실패 경로를 실제로 검증한다"를 확인한다.

분기 커버리지는 테스트 개수가 아니라 **어떤 분기가 실제로 참이 되는지**로 판정한다.
운영 기본값에서 지배적으로 실행될 분기가 어떤 테스트에서도 참이 되지 않으면 BLOCKER 다.

## 루브릭 3 — docs/IMPLEMENTATION_PLAN.md {{SECTION}} 섹션의 체크박스

각 체크박스가 코드에서 실제로 충족됐는지 판정한다.
구현자의 자기 보고(RESULT.md, 커밋 메시지, 문서 체크 표기)를 근거로 삼지 않는다.
코드만 근거로 삼는다.
해당 섹션에 "완료 조건" 문장이 있으면 그것이 실제로 성립하는지 별도로 판정한다.

## 추가로 반드시 확인할 것

- 레인 경계 위반: src/defense_research_agent/domain/__init__.py,
  src/defense_research_agent/services/__init__.py, pyproject.toml, uv.lock 이 diff에 포함됐는가
- 테스트가 네트워크나 자격증명을 요구하는가
- fake/fixture 가 실제 동작을 과대 대표해서 테스트가 통과만 하는 구조인가
  (특히 fixture 가 실제 corpus 데이터로는 재현 불가능한 동작을 대표하고 있지 않은지
   `data/` 의 실제 스키마를 확인해서 판정한다)
- 변경된 도메인 모델의 기존 사용처에서 serialization 호환성이 깨지는가

## 출력 형식

판정표를 먼저 제시한 뒤 아래 세 분류만 나열한다. 그 외 코멘트는 출력하지 않는다.

- `BLOCKER`  — 규칙 위반, 또는 구현했다고 볼 수 있는데 실제로는 not_met 인 항목.
              파일:줄 + 무엇이 왜 틀렸는지.
- `SCOPE`    — not_met 이지만 이번 범위 밖으로 문서에 명시 선언된 항목.
              VERDICT 에 반영하지 않는다. 한 줄로만 적는다.
- `QUESTION` — 도메인 판단이 필요해 사람이 결정해야 하는 것.

마지막 줄은 `VERDICT: PASS` 또는 `VERDICT: BLOCKED`.
BLOCKER 가 하나도 없으면 PASS 다. SCOPE 와 QUESTION 은 PASS 를 막지 않는다.
