#!/usr/bin/env bash
# 레인 결과를 검증한다: G0 결정적 게이트 -> Claude 교차 리뷰 -> (선택) codex 되먹임.
#
#   scripts/lane-review.sh <lane> <base-ref> <section> [--fix] [--head <ref>] [--rounds N]
#
# 예) scripts/lane-review.sh a-parser agent/document-intelligence-foundation 'P1.2, P1.3' --fix
#
# 종료 코드
#   0  PASS
#   2  G0 실패 (--fix 없음)
#   3  리뷰 BLOCKED (--fix 없음)
#   4  라운드 상한 도달했는데도 BLOCKED

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

FIX=0; HEAD_REF="HEAD"; MAX_ROUNDS=2
ARGS=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --fix) FIX=1; shift ;;
    --head) HEAD_REF="$2"; shift 2 ;;
    --rounds) MAX_ROUNDS="$2"; shift 2 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
# macOS 기본 bash 3.2 는 set -u 에서 빈 배열 전개를 unbound 로 본다.
set -- ${ARGS[@]+"${ARGS[@]}"}

[ "$#" -eq 3 ] || die "usage: lane-review.sh <lane> <base-ref> <section> [--fix] [--head <ref>] [--rounds N]"
LANE="$1"; BASE="$2"; SECTION="$3"
need claude

WT="$(require_lane "$LANE")"
G0_LOG="$WT/G0.log"
REVIEW="$WT/REVIEW.md"

# 루브릭 문서가 레인 worktree 안에 실제로 있어야 한다.
# base 가 오래된 docs 를 가리키면 리뷰어가 조용히 다른 섹션으로 대체 판정한다.
PLAN="$WT/docs/IMPLEMENTATION_PLAN.md"
[ -f "$PLAN" ] || die "계획 문서가 없다: $PLAN"
SECTION_KEY="$(printf '%s' "$SECTION" | grep -oE 'P[0-9]+(\.[0-9]+)?|T[0-9]{3}' | head -1 || true)"
if [ -n "$SECTION_KEY" ] && ! grep -q -- "$SECTION_KEY" "$PLAN"; then
  die "레인 worktree 의 계획 문서에 '$SECTION_KEY' 가 없다: $PLAN
      base 브랜치에 최신 docs 가 반영됐는지 확인한다."
fi

# codex 에 지적사항을 되먹인다. 구현 당시 컨텍스트를 유지하려고 resume 을 먼저 시도하고,
# 해당 worktree 에 세션이 없으면 새 세션으로 떨어진다.
feedback_codex() {
  local body="$1"
  need codex
  info "codex 되먹임 (resume 시도)"
  if printf '%s' "$body" | codex exec resume --last -C "$WT" -s workspace-write - \
        >> "$WT/codex.jsonl" 2>&1; then
    return 0
  fi
  warn "resume 실패 — 새 codex 세션으로 진행"
  printf '%s' "$body" | codex exec -C "$WT" -s workspace-write - \
    >> "$WT/codex.jsonl" 2>&1
}

ROUND=0
while :; do
  info "레인 $LANE — 라운드 $ROUND / G0 결정적 게이트"
  if run_g0 "$WT" "$G0_LOG"; then
    info "G0 통과"
  else
    warn "G0 실패 — 로그: $G0_LOG"
    if [ "$FIX" -eq 0 ]; then
      die "G0 미통과. Claude 리뷰를 호출하지 않는다. --fix 를 주면 codex 로 되먹인다. (exit 2)"
    fi
    [ "$ROUND" -ge "$MAX_ROUNDS" ] && { warn "라운드 상한 도달"; exit 4; }
    feedback_codex "검증 게이트가 실패했다. 아래 로그의 실패를 모두 수정한다.
수정 후 uv run pytest && uv run mypy && uv run ruff check . && uv run ruff format --check .
를 직접 실행해 통과를 확인하고 커밋한다.

$(cat "$G0_LOG")"
    ROUND=$((ROUND + 1))
    continue
  fi

  info "G2 Claude 교차 리뷰 ($BASE..$HEAD_REF)"
  # --allowedTools 는 가변 인자라 프롬프트를 삼킨다. 반드시 stdin 으로 넘긴다.
  render "$PROMPT_DIR/review.md" BASE "$BASE" HEAD "$HEAD_REF" SECTION "$SECTION" \
                                 G0LOG "$G0_LOG" \
    | ( cd "$WT" && claude -p --output-format text --model opus \
          --allowedTools "Read" "Grep" "Glob" \
                         "Bash(git diff:*)" "Bash(git log:*)" "Bash(git show:*)" ) \
    > "$REVIEW" 2> "$WT/REVIEW.err" \
    || { warn "claude 리뷰 실패 — $WT/REVIEW.err"; cat "$WT/REVIEW.err" >&2; exit 1; }

  # 리뷰어가 섹션을 못 찾았으면 되먹임하지 않는다. 인자가 틀린 것이다.
  if grep -q "SECTION_NOT_FOUND" "$REVIEW"; then
    die "리뷰어가 '$SECTION' 섹션을 찾지 못했다. 섹션 인자와 base docs 를 확인한다."
  fi

  if grep -q "VERDICT: PASS" "$REVIEW"; then
    info "리뷰 PASS — $REVIEW"
    info "체크박스 확정과 QUESTION 항목 판단은 사람이 한다."
    exit 0
  fi

  warn "리뷰 BLOCKED — $REVIEW"
  if [ "$FIX" -eq 0 ]; then
    sed -n '/BLOCKER/,$p' "$REVIEW" >&2
    exit 3
  fi
  [ "$ROUND" -ge "$MAX_ROUNDS" ] && {
    warn "라운드 상한 $MAX_ROUNDS 도달. 남은 BLOCKER 는 사람이 판정한다."
    exit 4
  }

  feedback_codex "교차 검토에서 아래 BLOCKER 가 나왔다. 각 항목을 수정하거나,
지적이 틀렸다면 코드 근거를 들어 반박한다. 반박도 유효한 응답이다.
수정 후 uv run pytest && uv run mypy && uv run ruff check . && uv run ruff format --check .
를 통과시키고 커밋한다.

$(sed -n '/BLOCKER/,$p' "$REVIEW")"
  ROUND=$((ROUND + 1))
done
