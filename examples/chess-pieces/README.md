# Chess-piece agent profiles

CAO ports of the agent-swarm chess-piece role ladder:
King / Queen / Bishop / Rook / Rook-adversarial / Knight / Pawn, plus a pure
Orchestrator.

| Profile | Rank | CAO role | Default provider | Model | Use for |
| --- | --- | --- | --- | --- | --- |
| `king` | ♚ | `supervisor` | `claude_code` | `claude-opus-5` | Plan, discover, delegate — never code or review |
| `king_cursor` | ♚ | `supervisor` | `cursor_cli` | `claude-opus-5-thinking-high` | Same as `king`, on Cursor |
| `king_oc` | ♚ | `supervisor` | `opencode_cli` | `anthropic/claude-opus-5` | Same as `king`, on OpenCode |
| `orchestrator` | — | `supervisor` | `claude_code` | `claude-opus-5` | Aggressive decompose-and-delegate only |
| `orchestrator_cursor` | — | `supervisor` | `cursor_cli` | `claude-opus-5-thinking-high` | Same as `orchestrator`, on Cursor |
| `orchestrator_oc` | — | `supervisor` | `opencode_cli` | `anthropic/claude-opus-5` | Same as `orchestrator`, on OpenCode |
| `queen` | ♛ | `developer` | `claude_code` | `claude-opus-5` | Architecture / hard trade-offs |
| `queen_cursor` | ♛ | `developer` | `cursor_cli` | `claude-opus-5-thinking-high` | Same as `queen`, on Cursor |
| `queen_oc` | ♛ | `developer` | `opencode_cli` | `anthropic/claude-opus-5` | Same as `queen`, on OpenCode |
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

The king / orchestrator session is **long-lived**. Subpieces it spawns via
`assign` / `handoff` (profile names match the table above) are **ephemeral**:
prefer `handoff` (auto-teardown), and after `assign` call `delete_terminal` as
soon as the result lands — do not keep one bishop/rook/etc alive and reuse it.
