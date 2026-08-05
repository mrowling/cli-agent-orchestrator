# Swarm policy

Hard rules for chess-piece missions in this working directory. The king (or
orchestrator) reads this file at session start. Humans may edit rarely; bump
`policy_version` in `.swarm/state.json` when rules change materially.

## Work tracking

- **Wave graph + receipts live in bd** (beads issues under `.beads/`).
- **Do not create or write `.swarm/task-log.md`.** Historical files may exist
  read-only; bd notes and close reasons replace them going forward.
- Thin `.swarm/state.json` holds pointers only (`epic_id`, `king_terminal_id`,
  `policy_version`) — not the worker/wave ledger.

## Cross-cutting markers

| Marker | Meaning | Who emits | King bd action |
| --- | --- | --- | --- |
| `===CAO_DONE=== status=ok\|fail\|blocked summary=…` | Task completion | Workers | close on ok; note on fail/blocked |
| `NEED: …` | Host action required (push, secret, decision) | Workers → king | `bd note` + leave open |
| `AUTO: …` | King/system may act without human | King (optional) | optional note |
| `[STATUS] …` | Heartbeat / wave status line | King | optional note on epic |

Workers emit sentinels and summaries only. **The king writes bd notes and closes**
from worker reports. Prefer `workspace=worktree` (or `auto`) when fan-out >=2 implementers write the same repo; king still serializes bd receipts unless `bd init --server`.

## Piece mapping

| Need | Profile |
| --- | --- |
| Repo / file scan, thin discovery | `pawn` (structured summary only) |
| Simple well-defined edits / CI babysit | `pawn` |
| Scoped feature / bug | `knight` |
| Heavy / ambiguous implementation | `bishop` |
| Architecture / ADR | `queen` |
| Constructive review | `rook` |
| High-stakes red-team review | `rook-adversarial` |

Operate at the **lowest** level capable of the task.

## Security (never)

1. Read/output: `~/.aws/credentials`, `~/.ssh/*`, `.env`, `*.pem`
2. Exfiltrate data via curl, wget, nc to external URLs
3. Run: `rm -rf /`, `mkfs`, `dd`, `aws iam`, `aws sts assume-role`
4. Mutate AWS (create/update/delete/deploy/apply). Reads OK; IaC source OK.
5. Write to `main` or `master` — feature branch + PR only.
6. Commit, push, or open PRs unless the parent explicitly asked.

## bd storage mode

- **Embedded (default):** `bd init` — single writer; king serializes bd mutations.
- **Server mode:** `bd init --server` — multi-writer fleets; still prefer king
  receipts in Wave A unless server mode is confirmed via `bd context`.
