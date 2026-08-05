#!/usr/bin/env bash
# test/test_swarm_init.sh — hermetic assertions for `swarm init`.
# Uses a fake `bd` on PATH; no real beads install required.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SWARM_BIN="$HERE/../../../bin/swarm"
fail=0
assert_eq() { # $1=actual $2=expected $3=label
  if [ "$1" = "$2" ]; then echo "ok: $3"; else echo "FAIL: $3 — got '$1' want '$2'"; fail=1; fi
}
assert_file() { # $1=path $2=label
  if [ -f "$1" ]; then echo "ok: $2"; else echo "FAIL: $2 — missing $1"; fail=1; fi
}
assert_missing() { # $1=path $2=label
  if [ ! -e "$1" ]; then echo "ok: $2"; else echo "FAIL: $2 — should not exist: $1"; fail=1; fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
STUB="$TMP/bin"
mkdir -p "$STUB"
cat > "$STUB/bd" <<'STUBSH'
#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  init)
    mkdir -p .beads
    touch .beads/.initialized
    ;;
  *)
    echo "fake bd: unknown command ${1:-}" >&2
    exit 1
    ;;
esac
STUBSH
chmod +x "$STUB/bd"

run_init() {
  (cd "$1" && PATH="$STUB:$PATH" "$SWARM_BIN" init)
}

# --- missing bd: actionable error, no mutation ---
WORK="$TMP/no-bd"
mkdir -p "$WORK"
set +e
(cd "$WORK" && env -i HOME="$HOME" PATH="/usr/bin:/bin" "$SWARM_BIN" init 2>"$TMP/no-bd.err")
rc=$?
set -e
assert_eq "$rc" "1" "missing bd exits 1"
grep -q "bd (beads) not found on PATH" "$TMP/no-bd.err" && echo "ok: missing bd message" || { echo "FAIL: missing bd message"; fail=1; }
assert_missing "$WORK/.beads" "missing bd does not create .beads"
assert_missing "$WORK/.swarm" "missing bd does not create .swarm"

# --- first init: .beads + policy/learnings/thin state, no task-log ---
WORK="$TMP/first-init"
mkdir -p "$WORK"
run_init "$WORK"
assert_file "$WORK/.beads/.initialized" "bd init ran"
assert_file "$WORK/.swarm/POLICY.md" "POLICY.md created"
assert_file "$WORK/.swarm/LEARNINGS.md" "LEARNINGS.md created"
assert_file "$WORK/.swarm/state.json" "state.json created"
assert_missing "$WORK/.swarm/task-log.md" "task-log.md not created"
grep -q '"epic_id": null' "$WORK/.swarm/state.json" && echo "ok: thin state epic_id null" || { echo "FAIL: thin state epic_id"; fail=1; }
grep -q '"king_terminal_id": null' "$WORK/.swarm/state.json" && echo "ok: thin state king_terminal_id null" || { echo "FAIL: thin state king_terminal_id"; fail=1; }
grep -q '"policy_version": 1' "$WORK/.swarm/state.json" && echo "ok: thin state policy_version" || { echo "FAIL: thin state policy_version"; fail=1; }

# --- repeat init: preserves existing files ---
echo "custom policy marker" > "$WORK/.swarm/POLICY.md"
echo '{"epic_id":"bd-legacy","waves":["w1"],"extra":true}' > "$WORK/.swarm/state.json"
run_init "$WORK"
grep -q "custom policy marker" "$WORK/.swarm/POLICY.md" && echo "ok: POLICY preserved" || { echo "FAIL: POLICY overwritten"; fail=1; }
grep -q '"waves"' "$WORK/.swarm/state.json" && echo "ok: fat state preserved" || { echo "FAIL: fat state rewritten"; fail=1; }
grep -q '"epic_id":"bd-legacy"' "$WORK/.swarm/state.json" && echo "ok: fat epic_id preserved" || { echo "FAIL: fat epic_id lost"; fail=1; }

# --- existing .beads: skip bd init, still create missing .swarm files ---
WORK="$TMP/existing-beads"
mkdir -p "$WORK/.beads"
echo "preexisting" > "$WORK/.beads/keep"
run_init "$WORK"
assert_eq "$(cat "$WORK/.beads/keep")" "preexisting" "existing .beads not re-inited"
assert_file "$WORK/.swarm/POLICY.md" "swarm files created with existing .beads"

# --- init rejects extra args ---
WORK="$TMP/bad-args"
mkdir -p "$WORK"
set +e
(cd "$WORK" && PATH="$STUB:$PATH" "$SWARM_BIN" init extra 2>/dev/null)
rc=$?
set -e
assert_eq "$rc" "1" "init with extra args exits 1"

exit $fail
