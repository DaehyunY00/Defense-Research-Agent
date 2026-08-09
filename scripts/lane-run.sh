#!/usr/bin/env bash
# 레인에서 codex 구현을 실행한다.
#
#   scripts/lane-run.sh <lane> <section> <dirs> [--fg]
#
# 예) scripts/lane-run.sh a-parser 'P1.2(Parser abstraction)와 P1.3(PDF extraction)' \
#         'src/defense_research_agent/search/parsers/, src/defense_research_agent/data/readers/pdf_reader.py'
#
# 기본은 백그라운드 실행이다. 여러 레인을 연달아 띄우면 그대로 병렬이 된다.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

FG=0
ARGS=()
for a in "$@"; do
  case "$a" in
    --fg) FG=1 ;;
    *) ARGS+=("$a") ;;
  esac
done
# macOS 기본 bash 3.2 는 set -u 에서 빈 배열 전개를 unbound 로 본다.
set -- ${ARGS[@]+"${ARGS[@]}"}

[ "$#" -eq 3 ] || die "usage: lane-run.sh <lane> <section> <dirs> [--fg]"
LANE="$1"; SECTION="$2"; DIRS="$3"
need codex

WT="$(require_lane "$LANE")"

PROMPT="$(render "$PROMPT_DIR/implement.md" SECTION "$SECTION" DIRS "$DIRS" EXTRA "")"
printf '%s' "$PROMPT" > "$WT/PROMPT.md"

info "레인 $LANE — codex exec 시작"
info "  섹션 : $SECTION"
info "  범위 : $DIRS"

run_codex() {
  # 프롬프트는 stdin 으로 넘긴다.
  printf '%s' "$PROMPT" | codex exec \
    -C "$WT" \
    -s workspace-write \
    --json \
    -o "$WT/RESULT.md" \
    - > "$WT/codex.jsonl" 2>&1
}

if [ "$FG" -eq 1 ]; then
  run_codex
  info "완료. 결과: $WT/RESULT.md"
else
  run_codex &
  info "백그라운드 PID $!"
  info "  로그   : tail -f $WT/codex.jsonl"
  info "  결과   : $WT/RESULT.md"
  info "  리뷰   : scripts/lane-review.sh $LANE <base-ref> '$SECTION'"
fi
