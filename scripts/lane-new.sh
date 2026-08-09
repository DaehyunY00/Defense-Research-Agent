#!/usr/bin/env bash
# 레인용 git worktree 를 만들고 독립 .venv 를 준비한다.
#
#   scripts/lane-new.sh <lane> <base-ref>
#
# 예) scripts/lane-new.sh a-parser agent/document-intelligence-foundation

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

[ "$#" -eq 2 ] || die "usage: lane-new.sh <lane> <base-ref>"
LANE="$1"; BASE="$2"
need git; need uv

WT="$(lane_dir "$LANE")"
[ -e "$WT" ] && die "이미 존재한다: $WT"

git -C "$REPO_ROOT" rev-parse --verify "$BASE" >/dev/null 2>&1 \
  || die "base ref 를 찾을 수 없다: $BASE"

mkdir -p "$WT_ROOT"

info "worktree 생성: $WT  (branch agent/$LANE, base $BASE)"
git -C "$REPO_ROOT" worktree add "$WT" -b "agent/$LANE" "$BASE"

info "의존성 동기화 (레인별 독립 .venv)"
( cd "$WT" && uv sync --python 3.12 --extra dev )

# .env 는 gitignore 라 worktree 에 따라오지 않는다. 오프라인 스위트에는 불필요하지만
# 있으면 복사해 둔다.
if [ -f "$REPO_ROOT/.env" ]; then
  cp "$REPO_ROOT/.env" "$WT/.env"
  info ".env 복사됨"
fi

info "완료. 다음: scripts/lane-run.sh $LANE '<섹션>' '<디렉터리>'"
