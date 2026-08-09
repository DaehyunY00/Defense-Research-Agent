#!/usr/bin/env bash
# 공통 헬퍼. lane-*.sh 에서 source 한다.

set -euo pipefail

# zsh 등 BASH_SOURCE 가 없는 셸에서 source 될 때를 대비한다.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROMPT_DIR="$SCRIPT_DIR/prompts"

# worktree 를 두는 위치. 저장소 자체가 Google Drive 위에 있어
# 동시 쓰기를 Drive 동기화와 분리하려고 기본값을 로컬 디스크로 둔다.
WT_ROOT="${WT_ROOT:-$HOME/dev/wt}"

die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarn:\033[0m %s\n' "$*" >&2; }

need() { command -v "$1" >/dev/null 2>&1 || die "$1 명령을 찾을 수 없다"; }

lane_dir() { printf '%s/%s' "$WT_ROOT" "$1"; }

# worktree 의 .git 은 파일이고 실제 git dir 는 상위 저장소 안에 있다. codex 의
# workspace-write 샌드박스는 worktree 만 쓰기 허용하므로 이 경로를 --add-dir 로
# 열어주지 않으면 index.lock 을 만들지 못해 커밋이 조용히 실패한다.
#
# 반드시 worktree 를 기준으로 계산한다. 메인 저장소에서 --git-common-dir 를 부르면
# 상대 경로 '.git' 이 나오고, 이는 codex 의 CWD(worktree) 기준으로 해석되어
# worktree/.git(파일)을 가리키게 된다. 실제로 이 실수로 레인 4개가 커밋에 실패했다.
git_common_dir() {
  local wt="$1" dir
  dir="$(git -C "$wt" rev-parse --git-common-dir)"
  case "$dir" in
    /*) printf '%s' "$dir" ;;
    *) ( cd "$wt" && cd "$dir" && pwd ) ;;
  esac
}

require_lane() {
  local wt; wt="$(lane_dir "$1")"
  [ -d "$wt" ] || die "레인 worktree 가 없다: $wt  (먼저 scripts/lane-new.sh $1 <base> 실행)"
  printf '%s' "$wt"
}

# render <template> KEY VALUE [KEY VALUE ...]
# 템플릿의 {{KEY}} 를 VALUE 로 치환한다. sed 를 쓰지 않으므로 이스케이프 문제가 없다.
render() {
  local tpl="$1"; shift
  [ -f "$tpl" ] || die "템플릿이 없다: $tpl"
  local content; content="$(cat "$tpl")"
  while [ "$#" -gt 0 ]; do
    local key="$1" val="$2"; shift 2
    content="${content//\{\{$key\}\}/$val}"
  done
  printf '%s' "$content"
}

# run_g0 <worktree> <logfile>
# 결정적 게이트. 첫 실패에서 멈추지 않고 전부 돌린다 —
# 되먹임 한 번에 모든 실패를 codex 에 넘기기 위해서다.
run_g0() {
  local wt="$1" log="$2" failed=0 cmd
  : > "$log"
  for cmd in \
    "uv run pytest" \
    "uv run mypy" \
    "uv run ruff check ." \
    "uv run ruff format --check ."
  do
    printf '### %s\n' "$cmd" >> "$log"
    if ( cd "$wt" && eval "$cmd" ) >> "$log" 2>&1; then
      printf '  \033[32mPASS\033[0m %s\n' "$cmd"
    else
      printf '  \033[31mFAIL\033[0m %s\n' "$cmd"
      failed=1
    fi
    printf '\n' >> "$log"
  done
  return "$failed"
}
