docs/IMPLEMENTATION_PLAN.md 의 {{SECTION}} 을 구현한다.
해당 섹션의 체크리스트 항목을 완료 조건으로 삼고, AGENTS.md 의 절대 규칙 1~7과
같은 문서의 "Definition of Done" 섹션을 전부 만족시킨다.

부분 구현이나 초안을 목표로 하지 않는다. Definition of Done 을 실제로 충족시킨다.
충족시킬 수 없는 항목이 있으면 임의로 생략하지 말고 최종 메시지에 이유와 함께 보고한다.

레인 경계:
- 작업 범위는 {{DIRS}} 와 대응하는 tests/ 뿐이다.
- src/defense_research_agent/domain/__init__.py 와
  src/defense_research_agent/services/__init__.py 는 수정하지 않는다.
  새 심볼의 배럴 재노출은 통합 단계에서 사람이 일괄 처리한다.
- pyproject.toml 과 uv.lock 은 수정하지 않는다. 새 의존성이 필요하면 구현을 중단하고
  최종 메시지에 "NEEDS_DEPENDENCY: <패키지> <이유>" 로 보고한다.
- 위에 지정한 디렉터리 밖의 기존 파일은 수정하지 않는다.

데이터와 안전 경계:
- data/ 아래 원본 파일은 읽기 전용이다. 생성물은 artifacts/ 에만 쓴다.
- 네트워크나 자격증명이 필요한 테스트는 기본 스위트에 넣지 않는다. fake 또는 fixture 로 격리한다.
- fake 구현이 실제 동작을 과대 대표하지 않게 한다. 통과만 하는 테스트를 만들지 않는다.

완료 전 반드시 통과시킬 것:
  uv run pytest && uv run mypy && uv run ruff check . && uv run ruff format --check .

작업이 끝나면 변경사항을 커밋한다.

최종 메시지에 담을 것:
  - 변경 파일 목록
  - 확정한 공개 인터페이스 시그니처
  - {{SECTION}} 체크박스 중 실제로 충족한 것만. 충족하지 않은 항목을 완료로 보고하지 않는다.
  - 미해결 항목과 사람의 판단이 필요한 지점
{{EXTRA}}
