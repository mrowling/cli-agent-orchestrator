# Chess-piece agent profiles

CAO ports of the agent-swarm chess-piece role ladder:
King / Queen / Bishop / Rook / Rook-adversarial / Knight / Pawn, plus a pure
Orchestrator.

| Profile | Rank | CAO role | Default provider | Use for |
| --- | --- | --- | --- | --- |
| `king` | ♚ | `supervisor` | `claude_code` | Plan, discover, delegate — never code or review |
| `orchestrator` | — | `supervisor` | `claude_code` | Aggressive decompose-and-delegate only |
| `queen` | ♛ | `developer` | `claude_code` | Architecture / hard trade-offs |
| `bishop` | ♝ | `developer` | `cursor_cli` | Complex / ambiguous implementation |
| `rook` | ♜ | `reviewer` | `cursor_cli` | Constructive code review |
| `rook-adversarial` | ♜ | `reviewer` | `cursor_cli` | Red-team / break-the-change review |
| `knight` | ♞ | `developer` | `cursor_cli` | Scoped features and bugs |
| `pawn` | ♟ | `developer` | `cursor_cli` | Simple well-defined tasks; CI babysit |

## Install

```bash
for f in examples/chess-pieces/*.md; do
  [[ "$(basename "$f")" == "README.md" ]] && continue
  cao install "$f"
done
```

Optional PATH (mirrors agent-swarm’s `bin/swarm`):

```bash
export PATH="$PWD/examples/chess-pieces/bin:$PATH"
# or: export PATH="/path/to/cli-agent-orchestrator/examples/chess-pieces/bin:$PATH"
```

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

## Launch pieces

Interactive picker (fzf) — always launches in **`$PWD`**:

```bash
swarm                         # fzf: pick a piece → cao launch in $PWD
swarm king                    # king-<basename> session for $PWD
swarm king my-app             # king-my-app session for $PWD
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

Workers are spawned by the king/orchestrator/queen via `assign` / `handoff`
(profile names match the table above).
