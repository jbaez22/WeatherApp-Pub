#!/usr/bin/env bash
# git-repo-status.sh — full local + all-remotes git status check.
#
# Answers "what is pending and/or needs to be committed and pushed?" without
# trusting stale cached remote-tracking refs. Every remote gets a fresh
# `git fetch --prune` before anything is compared against it (a cached ref
# can look completely normal while being silently out of date — see
# ~/.claude/instructions/git-remote-state-verification.md).
#
# Reusable across projects — copy this file into any repo's tools/ directory.
# No arguments. Run from anywhere inside the repo.

set -euo pipefail

run() {
  echo "+ $*"
  "$@"
}

cd "$(git rev-parse --show-toplevel)"
branch="$(git rev-parse --abbrev-ref HEAD)"

echo "=================================================================="
echo " Repo: $(pwd)"
echo " Branch: $branch"
echo "=================================================================="

echo
echo "── Working tree status ──────────────────────────────────────────"
run git status --short --branch
staged=$(git diff --cached --name-only | wc -l | tr -d ' ')
unstaged=$(git diff --name-only | wc -l | tr -d ' ')
untracked=$(git ls-files --others --exclude-standard | wc -l | tr -d ' ')
echo
echo "Staged: $staged | Unstaged: $unstaged | Untracked: $untracked"

remotes=$(git remote)
if [ -z "$remotes" ]; then
  echo
  echo "No remotes configured — nothing to compare against."
  exit 0
fi

echo
echo "── Remotes ───────────────────────────────────────────────────────"
run git remote -v

for remote in $remotes; do
  echo
  echo "── Fetching '$remote' (fresh state, not cached) ─────────────────"
  run git fetch --prune "$remote"
done

echo
echo "── Branch sync per remote ───────────────────────────────────────"
for remote in $remotes; do
  ref="refs/remotes/$remote/$branch"
  if ! git show-ref --verify --quiet "$ref"; then
    echo "$remote: branch '$branch' does not exist on this remote"
    continue
  fi
  echo "+ git rev-list --left-right --count $branch...$remote/$branch"
  counts=$(git rev-list --left-right --count "$branch...$remote/$branch")
  ahead=$(echo "$counts" | awk '{print $1}')
  behind=$(echo "$counts" | awk '{print $2}')
  if [ "$ahead" = "0" ] && [ "$behind" = "0" ]; then
    echo "$remote: up to date"
  else
    echo "$remote: $ahead commit(s) ahead, $behind commit(s) behind"
    [ "$ahead" != "0" ] && git log "$remote/$branch..$branch" --oneline --decorate | sed "s/^/  local-only:  /"
    [ "$behind" != "0" ] && git log "$branch..$remote/$branch" --oneline --decorate | sed "s/^/  remote-only: /"
  fi
done

echo
echo "── Tags: local vs. each remote ──────────────────────────────────"
local_tags=$(git tag -l | sort -u)
if [ -z "$local_tags" ]; then
  echo "No local tags."
else
  for remote in $remotes; do
    echo
    echo "+ git ls-remote --tags $remote"
    remote_tags=$(git ls-remote --tags "$remote" | awk '{print $2}' | sed 's|refs/tags/||; s|\^{}||' | sort -u)
    missing_on_remote=$(comm -23 <(echo "$local_tags") <(echo "$remote_tags"))
    missing_locally=$(comm -13 <(echo "$local_tags") <(echo "$remote_tags"))
    if [ -z "$missing_on_remote" ] && [ -z "$missing_locally" ]; then
      echo "$remote: all tags in sync"
    else
      [ -n "$missing_on_remote" ] && echo "$remote: local tags NOT pushed: $(echo "$missing_on_remote" | tr '\n' ' ')"
      [ -n "$missing_locally" ] && echo "$remote: remote tags not fetched locally: $(echo "$missing_locally" | tr '\n' ' ')"
    fi
  done
fi

echo
echo "=================================================================="
echo " Summary"
echo "=================================================================="
if [ "$staged" = "0" ] && [ "$unstaged" = "0" ] && [ "$untracked" = "0" ]; then
  echo "Working tree: clean"
else
  echo "Working tree: NOT clean (staged=$staged unstaged=$unstaged untracked=$untracked)"
fi
echo "See sections above for per-remote branch/tag sync status."
