# 병렬 에이전트 개발 스크립트

codex 를 구현자로, Claude 를 교차 검증자로 쓰는 개발 루프를 스크립트로 고정한 것이다.
역할 분담의 근거는 `docs/IMPLEMENTATION_PLAN.md` 의 "Human vs Codex" 를 따른다.

## 왜 이 구조인가

`gpt-5.6-sol` 과 `opus-5` 는 오류 상관관계가 낮다. 같은 모델의 자기 리뷰는 자기 가정을
그대로 승인하지만, 교차 모델 리뷰는 그 지점을 깬다. 여기에 컨텍스트 분리가 더해진다 —
리뷰어는 구현자의 추론 과정을 보지 못하고 결과 diff 만 보므로 "왜 이렇게 했는지" 에
설득당하지 않는다.

게이트는 계층으로 쌓는다. 비싼 리뷰 앞에 싸구려 결정적 게이트를 두는 것이 핵심이다.

```text
G0  uv run pytest / mypy / ruff          무료·결정적. 실패 시 Claude 를 호출하지 않는다
G2  Claude 교차 리뷰                      진짜 게이트
G3  사람                                  도메인 판단·체크박스 확정
```

## 파일

| 파일 | 역할 |
|---|---|
| `lib.sh` | 공통 헬퍼, `render()` 템플릿 치환, `run_g0()` 결정적 게이트 |
| `lane-new.sh` | 레인용 git worktree 생성 + 독립 `.venv` |
| `lane-run.sh` | 레인에서 codex 구현 실행 (기본 백그라운드) |
| `lane-review.sh` | G0 → Claude 리뷰 → codex 되먹임 (최대 2라운드) |
| `prompts/implement.md` | codex 구현 프롬프트 템플릿 |
| `prompts/review.md` | Claude 리뷰어 프롬프트 템플릿 |

프롬프트를 고치고 싶으면 `prompts/*.md` 만 편집한다. `{{SECTION}}` 같은 자리표시자는
`lib.sh` 의 `render()` 가 치환한다.

## 사용법

### 0. 선행 조건 — 계약을 먼저 고정한다

병렬 레인을 띄우기 전에 공유 인터페이스(Protocol/ABC, 도메인 모델, 에러 taxonomy)를
base 브랜치에 **직렬로** 커밋해야 한다. 이걸 건너뛰면 레인마다 다른 인터페이스를
발명하고, 코드는 깨끗하게 merge 되는데 설계만 두 개가 된다.

계약 설계는 아키텍처 결정이라 `IMPLEMENTATION_PLAN.md` 기준 사람의 책임이다.

**base 브랜치는 최신 `docs/` 를 포함해야 한다.** 루브릭이 곧 `AGENTS.md` 와
`IMPLEMENTATION_PLAN.md` 이므로, base 의 docs 가 오래되면 구현자와 리뷰어가 모두 구버전
계획을 기준으로 판단한다. `lane-review.sh` 가 섹션 존재 여부를 미리 확인해 조기 차단한다.

### 1. 레인 생성

```bash
BASE=agent/document-intelligence-foundation   # 계약 커밋 SHA
./scripts/lane-new.sh a-parser "$BASE"
```

worktree 는 기본적으로 `~/dev/wt/<lane>` 에 만든다. 저장소가 Google Drive 위에 있어
동시 쓰기를 Drive 동기화와 분리하기 위해서다. `WT_ROOT` 로 바꿀 수 있다.

### 2. 구현 (병렬)

```bash
./scripts/lane-run.sh a-parser 'P1.2(Parser abstraction)와 P1.3(PDF extraction)' \
  'src/defense_research_agent/search/parsers/, src/defense_research_agent/data/readers/pdf_reader.py'
```

기본이 백그라운드라 여러 레인을 연달아 띄우면 그대로 병렬이 된다.
진행은 `tail -f ~/dev/wt/a-parser/codex.jsonl` 로 본다.

### 3. 검증

```bash
./scripts/lane-review.sh a-parser "$BASE" 'P1.2, P1.3'          # 보고만
./scripts/lane-review.sh a-parser "$BASE" 'P1.2, P1.3' --fix    # codex 되먹임까지
```

`--fix` 는 BLOCKER 를 `codex exec resume` 으로 되먹인다. resume 을 쓰는 이유는 구현 당시
컨텍스트를 유지한 채 고치기 위해서다. 새 세션으로 던지면 자기 코드를 처음 보는 상태로
다시 읽어야 한다.

라운드 상한은 2다. 3라운드째에도 남는 BLOCKER 는 대개 모델 간 의견 충돌이거나 사양
자체가 애매한 경우라 사람이 판정하는 편이 빠르다.

종료 코드: `0` PASS / `2` G0 실패 / `3` BLOCKED / `4` 라운드 상한.

### 4. 통합 (직렬, 사람)

```bash
git checkout "$BASE"
for LANE in a-parser b-metadata c-quality d-embedding; do
  git merge --no-ff "agent/$LANE"
done
# 배럴 재노출을 여기서 한 번에
$EDITOR src/defense_research_agent/domain/__init__.py
uv sync --python 3.12 --extra dev
uv run pytest && uv run mypy && uv run ruff check . && uv run ruff format --check .
```

정리는 `git worktree remove ~/dev/wt/<lane>` (브랜치는 남는다).

## 레인 경계 규칙

프롬프트 템플릿에 이미 들어 있지만 이유를 남겨 둔다.

1. **`domain/__init__.py`, `services/__init__.py` 수정 금지.**
   `domain/__init__.py` 는 264줄짜리 알파벳순 배럴이다. 레인마다 export 를 추가하면
   여기서 확정적으로 충돌한다. 재노출은 통합 단계에서 일괄 처리한다.
2. **`pyproject.toml` / `uv.lock` 수정 금지.**
   새 의존성이 필요하면 `NEEDS_DEPENDENCY:` 로 보고만 시키고 사람이 추가한다.
3. **자기 레인 디렉터리 + 대응 `tests/` 밖은 수정 금지.**

## 주의점

- **codex 가 쓰고 있는 worktree 를 리뷰하지 않는다.** 반쯤 쓰인 파일을 읽게 된다.
  레인별로 codex 종료를 확인한 뒤 리뷰를 걸거나 커밋된 SHA 를 `--head` 로 고정한다.
- **리뷰와 수정을 같은 호출에 넣지 않는다.** 자기가 고칠 걸 아는 리뷰어는 고치기 쉬운
  것만 찾는다.
- **리뷰 결과를 그대로 plan 체크박스에 반영하지 않는다.** 체크박스는 사람이 확정한다.
  `IMPLEMENTATION_PLAN.md` 의 backlog 운영 규칙이다.
- **`--allowedTools` 는 가변 인자다.** 프롬프트를 위치 인자로 주면 옵션 값으로 삼켜진다.
  스크립트는 프롬프트를 전부 stdin 으로 넘긴다.
- **섹션 인자가 틀려도 리뷰어는 오류를 내지 않는다.** 조용히 비슷한 다른 섹션으로 대체
  판정한다. 결과물이 그럴듯해 보이므로 알아채기 어렵다. 그래서 스크립트가 사전에
  grep 으로 차단하고, 프롬프트도 `SECTION_NOT_FOUND` 로 즉시 중단하도록 지시한다.
- **리뷰어에게 테스트를 실행시키지 않는다.** G0 가 이미 돌았고 로그가 `G0.log` 에 있다.
  샌드박스에서 거부되기도 하고, 중복 실행은 낭비다.

## 캘리브레이션 기록

`db068e7`(page-aware chunk 커밋)에 리뷰어를 걸어 품질을 확인했다. 확인된 유효 findings:

- `search/chunking.py` 의 `exceeds_limit` 분기가 어떤 테스트에서도 참이 되지 않는다.
  기본값 `max_characters=4000` 과 실제 페이지 길이에서는 이 분기가 주 경로다.
  테스트가 검증하는 분기와 운영에서 실행될 분기가 다르다.
- `crosses_page_gap` 분기도 미검증. 빈 페이지 테스트에서는 `pending` 이 비어 타지 않는다.
- `data/metadata/*.json` 의 `page_texts` 에 section 정보가 없어(`char_count`/`page`/`text`),
  `section_title` 경계는 실제 corpus 에서 발화 불가인데 테스트는 이를 핵심 동작으로 검증한다.

세 건 모두 사람이 코드로 재확인했다. "통과하지만 실제 경로를 검증하지 않는 테스트" 를
잡아내는 것이 이 리뷰어의 주된 값이다.
