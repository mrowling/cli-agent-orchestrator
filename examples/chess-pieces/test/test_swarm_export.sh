#!/usr/bin/env bash
# test/test_swarm_export.sh — hermetic discovery/dry-run for `swarm export`.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SWARM_BIN="$HERE/../../../bin/swarm"
fail=0
assert_contains() { # $1=haystack $2=needle $3=label
  if printf '%s' "$1" | grep -q -- "$2"; then echo "ok: $3"; else echo "FAIL: $3 — missing '$2' in:\n$1"; fail=1; fi
}
assert_not_contains() {
  if printf '%s' "$1" | grep -q -- "$2"; then echo "FAIL: $3 — unexpectedly found '$2'"; fail=1; else echo "ok: $3"; fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Fake CAO home with project dirs + reserved containers.
MEM="$TMP/memory"
mkdir -p "$MEM/global/wiki/global"
mkdir -p "$MEM/logs/memory"
mkdir -p "$MEM/federated/wiki/federated"
mkdir -p "$MEM/github-com-acme-app/wiki/project"
mkdir -p "$MEM/aabbccddeeff/wiki/project"
# empty dir without wiki — should be skipped
mkdir -p "$MEM/orphan-no-wiki"

out="$(CAO_HOME_DIR="$TMP" "$SWARM_BIN" export --dry-run 2>&1)" || {
  echo "FAIL: dry-run exited non-zero"
  echo "$out"
  exit 1
}

assert_contains "$out" "github-com-acme-app" "lists git-remote project id"
assert_contains "$out" "aabbccddeeff" "lists hash project id"
assert_contains "$out" "project-github-com-acme-app" "dest name for remote id"
assert_not_contains "$out" "orphan-no-wiki" "skips dirs without wiki"
assert_not_contains "$out" "dry-run: global" "default omits global"
assert_not_contains "$out" "global-vault" "default omits global vault"

out2="$(CAO_HOME_DIR="$TMP" "$SWARM_BIN" export --also-global --dry-run 2>&1)" || {
  echo "FAIL: also-global dry-run exited non-zero"
  echo "$out2"
  exit 1
}
assert_contains "$out2" "dry-run: global → ./memory-export/global" "also-global lists global OKF dest"

out3="$(CAO_HOME_DIR="$TMP" "$SWARM_BIN" export --obsidian --also-global --dry-run 2>&1)" || {
  echo "FAIL: obsidian dry-run exited non-zero"
  echo "$out3"
  exit 1
}
assert_contains "$out3" "global-vault" "obsidian also-global lists vault"
assert_contains "$out3" "graph-exports" "obsidian dest under graph-exports"

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi
echo "all ok"
