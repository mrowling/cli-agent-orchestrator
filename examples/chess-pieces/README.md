# Chess-piece agent profiles

CAO ports of the agent-swarm chess-piece role ladder:
King / Queen / Bishop / Rook / Rook-adversarial / Knight / Pawn, plus a pure
Orchestrator.

| Profile | Rank | CAO role | Default provider | Model | Use for |
| --- | --- | --- | --- | --- | --- |
| `king` | ♚ | `supervisor` | `claude_code` | `claude-opus-4.8-thinking-high` | Plan, discover, delegate — never code or review |
| `king_cursor` | ♚ | `supervisor` | `cursor_cli` | `gpt-5.6-sol-xhigh` | Same as `king`, on Cursor |
| `king_oc` | ♚ | `supervisor` | `opencode_cli` | `anthropic/claude-opus-4.8-thinking-high` | Same as `king`, on OpenCode |
| `orchestrator` | — | `supervisor` | `claude_code` | `claude-opus-4.8-thinking-high` | Aggressive decompose-and-delegate only |
| `orchestrator_cursor` | — | `supervisor` | `cursor_cli` | `claude-opus-4.8-thinking-high` | Same as `orchestrator`, on Cursor |
| `orchestrator_oc` | — | `supervisor` | `opencode_cli` | `anthropic/claude-opus-4.8-thinking-high` | Same as `orchestrator`, on OpenCode |
| `queen` | ♛ | `developer` | `claude_code` | `claude-opus-4.8-thinking-high` | Architecture / hard trade-offs |
| `queen_cursor` | ♛ | `developer` | `cursor_cli` | `claude-opus-4.8-thinking-high` | Same as `queen`, on Cursor |
| `queen_oc` | ♛ | `developer` | `opencode_cli` | `anthropic/claude-opus-4.8-thinking-high` | Same as `queen`, on OpenCode |
| `bishop` | ♝ | `developer` | `cursor_cli` | `cursor-grok-4.5-high` | Complex / ambiguous implementation |
| `rook` | ♜ | `reviewer` | `cursor_cli` | `cursor-grok-4.5-high` | Constructive code review |
| `rook-adversarial` | ♜ | `reviewer` | `cursor_cli` | `cursor-grok-4.5-high` | Red-team / break-the-change review |
| `knight` | ♞ | `developer` | `cursor_cli` | `auto` | Scoped features and bugs |
| `pawn` | ♟ | `developer` | `cursor_cli` | `composer-2.5` | Simple well-defined tasks; CI babysit |

## Install

```bash
for f in examples/chess-pieces/*.md; do
  [[ "$(basename "$f")" == "README.md" ]] && continue
  cao install "$f"
done
```

Optional PATH:

```bash
export PATH="$PWD/bin:$PATH"
```

## Workspace bootstrap (`swarm init`)

Chess missions separate **durable rules** (`.swarm/` files) from **work tracking**
(**bd** — beads issues under `.beads/`). **`bd` must be on PATH**; `swarm init`
does not auto-install it.

```bash
cd /path/to/your/repo
swarm init    # requires bd; creates .beads/ (if missing) + .swarm/ templates
```

Creates only missing files:

| Path | Purpose |
| --- | --- |
| `.beads/` | bd issue graph (`bd init` when absent) |
| `.swarm/POLICY.md` | Hard rules, piece mapping, markers |
| `.swarm/LEARNINGS.md` | Institutional memory (king appends after waves) |
| `.swarm/state.json` | Thin pointers: `epic_id`, `king_terminal_id`, `policy_version` |

**No `task-log.md`.** Wave progress and receipts live in bd (`bd note`, close
reasons). Historical `task-log.md` files may remain read-only; do not create new ones.

### bd storage modes

| Mode | When | Init |
| --- | --- | --- |
| Embedded (default) | Single king serializes bd writes | `bd init` (via `swarm init`) |
| Server | Multi-writer / parallel agent loops | `bd init --server` |

Check active mode with `bd context`. For autonomous multi-agent fleets that mutate
issues concurrently, prefer server mode; Wave A still has the **king write receipts**
from worker sentinels.

### Sample wave (bd children + deps + ready)

Mission epic `bd-a1b2` with three wave items — scout, then implement (blocked on
scout), then review (blocked on implement):

```bash
bd create "Mission: add login" -t epic --json          # → bd-a1b2; store in state.json
bd create "Scout auth surface" -t task --json          # → bd-c3d4
bd create "Implement login endpoint" -t task --json    # → bd-e5f6
bd create "Review login PR" -t task --json               # → bd-g7h8
bd dep add bd-e5f6 bd-c3d4    # implement blocked by scout
bd dep add bd-g7h8 bd-e5f6    # review blocked by implement
bd dep add bd-c3d4 bd-a1b2 --type parent-child
bd dep add bd-e5f6 bd-a1b2 --type parent-child
bd dep add bd-g7h8 bd-a1b2 --type parent-child

bd children bd-a1b2           # list wave items under epic
bd ready                      # only scout is unblocked initially
```

King workflow: claim before `handoff`, `bd note` receipt from worker report, `bd close`
on `===CAO_DONE=== status=ok`, `bd stale` when stuck, `bd sync` after mutations.

### Doorbell ingress (external triggers)

CI and host scripts can wake the king with a ≤200-char trigger (no log dumps):

```bash
cao doorbell send --trigger '[github:pr_checks:org/repo#42] failing'
```

Routes via `.swarm/state.json` `king_terminal_id` or `--to <terminal_id>`.
See `docs/doorbell.md`.

## Server + Web UI

```bash
swarm start                 # cao-server in background; waits for /health
swarm status                # UP/DOWN, pid, urls
swarm ui                    # open http://127.0.0.1:9889/ in a browser
swarm stop                  # stop cao-server
swarm stop --sessions       # also: cao shutdown --all
```

Logs/pid live under `~/.aws/cli-agent-orchestrator/swarm/` (or `$CAO_HOME_DIR/swarm`).
Override bind address with `CAO_API_HOST` / `CAO_API_PORT`. Foreground: `swarm start --fg`.

## Memory export

Export every **project** memory scope under `$CAO_HOME_DIR/memory` (skips
`global` / `federated` / `logs` containers unless you ask):

```bash
swarm export                         # OKF bundles → ./memory-export/project-<id>/
swarm export -o ~/cao-memory-export  # custom output directory
swarm export --also-global           # also global (+ federated if present)
swarm export --obsidian              # Obsidian vaults via graph API (needs swarm start)
swarm export --dry-run               # list dests only
```

`CAO_HOME_DIR` must point at the home that holds your real memory tree
(default `~/.aws/cli-agent-orchestrator`). Obsidian mode writes under
`$CAO_GRAPH_EXPORT_ROOT` (default `$CAO_HOME_DIR/graph-exports`).

## Launch pieces

Interactive picker (fzf) — always launches in **`$PWD`**:

```bash
swarm                         # fzf: pick a piece → cao launch in $PWD
swarm king                    # king-<basename> session for $PWD
swarm king_cursor my-app      # king_cursor-my-app on Cursor
swarm king_oc                 # king_oc-<basename> on OpenCode
swarm orchestrator            # orchestrator-<basename>
swarm knight "fix the bug"    # skip fzf; message is one quoted arg
```

Equivalent without the helper:

```bash
cao launch --agents king --working-directory "$PWD" --auto-approve
```

`swarm` auto-installs a missing piece profile from this directory into the
agent-store on first launch. Set `SWARM_CONFIRM=1` to keep the cao confirmation
prompt; set `SWARM_YOLO=1` to pass `--yolo`.

**Control plane is CAO only.** Pieces coordinate via `cao-mcp-server`
(`assign` / `handoff` / `send_message`). Do not use CCC (Claude Command Center
on `:8090`, `/api/inject-input`, `ccc-orchestration`) for fleet callbacks —
re-install profiles after pulling these examples so the ban is live in the
agent-store.

The king / orchestrator session is **long-lived**. Subpieces it spawns via
`assign` / `handoff` (profile names match the table above) are **ephemeral**:
prefer `handoff` (auto-teardown), and after `assign` call `delete_terminal` as
soon as the result lands — do not keep one bishop/rook/etc alive and reuse it.

### Context hygiene (kings)

CAO does **not** auto-compact king chat history. Keep the king pane lean:

- Prefer `done_summary` / the `===CAO_DONE===` one-liner over raw handoff `output`.
- Park progress in **bd** and thin append to `.swarm/LEARNINGS.md`; resume from
  POLICY / LEARNINGS / `state.json` / `bd ready`, not a swollen transcript.
- Push log dumps and large scans to a one-shot `pawn` (structured summary only).
- Doorbells stay ≤200 chars (pointers only — see above).
- If the provider pane still swells, flush state, then use the **provider’s**
  compact/new-session; rehydrate from `.swarm` + bd.

Re-install king profiles after pulling changes so lean-context rules are live in
the agent-store: `cao install examples/chess-pieces/king*.md`.

## Done sentinel

Workers (pawn through queen, rook, rook-adversarial) must end every task with a
machine-readable completion line:

```
===CAO_DONE=== status=ok|fail|blocked summary=<one line>
```

- **Handoff:** last line of captured worker output.
- **Assign:** first or last line of the worker's `send_message` body.

Supervisors (king / orchestrator) must treat **missing sentinel as incomplete**
— never `delete_terminal` or advance a wave on prose alone. For handoff, prefer
`HandoffResult.done_status` and `done_summary` when CAO populates them.

Re-install profiles after pulling these examples so the sentinel rules are live
in the agent-store.

## Wave concurrency (`CAO_MAX_WAVE_IN_FLIGHT`)

Fan out to at most **`CAO_MAX_WAVE_IN_FLIGHT`** (default **3**) concurrent
`assign`/`handoff` children per supervisor. One shared budget covers both
tools. Excess requests are **queued FIFO** by cao-server (side-table status
`queued` — no terminal row until a slot frees). Assign cleanup
(`delete_terminal`) releases a slot and starts the next queued request;
handoff releases on completion. `CAO_MAX_ACTIVE_TERMINALS` remains the hard
session ceiling — queue drain never overruns it.

Configure via env: `CAO_MAX_WAVE_IN_FLIGHT=3` (raise only intentionally).

## Mechanical acceptance (`done_cmd`)

Kings may pass an optional **`done_cmd`** on `handoff` for **mechanical**
acceptance only (CI green, tests, lint). CAO runs it **after** worker capture
completes, in the worker cwd (~120s central timeout). Exit 0 accepts; failure
sets `HandoffResult.success=false` even when `done_status=ok`. Audit fields:
`done_cmd`, `done_cmd_exit`, `done_cmd_output` (tail-truncated),
`done_cmd_timed_out`, `done_cmd_error` — separate from `done_status` /
`done_summary`.

Example:

```
handoff(pawn, message="Fix PR #42 checks", done_cmd="gh pr checks 42 --fail-fast")
```

**Rook / rook-adversarial** = design and risk review (LLM). **`done_cmd`** =
mechanical verification only. Do not conflate them.

## Cross-cutting markers

Standard markers (also documented in `.swarm/POLICY.md`):

| Marker | Meaning |
| --- | --- |
| `===CAO_DONE=== status=ok\|fail\|blocked summary=…` | Worker task completion |
| `NEED: …` | Host action required |
| `AUTO: …` | King/system may act without human |
| `[STATUS] …` | King heartbeat / wave status |
