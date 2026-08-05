# Doorbell ingress (Item 5)

Lean external triggers for long-lived kings: **forward the bell, not the package**.
Watchers emit a ≤200-character structured pointer; the king decides relevance and
fetches live state only when needed.

## Canonical trigger format

Single line, maximum **200 characters** (never truncated — over-length triggers are rejected):

```text
[source:type:id] hint
```

| Part | Constraints |
| --- | --- |
| `source` | `[A-Za-z0-9._-]{1,32}` — e.g. `github`, `ci`, `pager` |
| `type` | `[A-Za-z0-9._-]{1,32}` — e.g. `pr_checks`, `workflow_run` |
| `id` | `[A-Za-z0-9._:/#@-]{1,64}` — e.g. `org/repo#42`, run id |
| `hint` | Non-empty printable text after `] ` — no newlines or control characters |

Example:

```text
[github:pr_checks:org/repo#42] failing
```

Validation lives in `cli_agent_orchestrator.doorbell.validation` (`validate_doorbell_trigger`,
`is_doorbell_trigger`). Kings recognize the same shape in inbox messages (see chess
`king` / `orchestrator` profiles).

## CLI delivery

```bash
# Explicit king terminal
cao doorbell send --to abcd1234 --trigger '[github:pr_checks:org/repo#42] failing'

# Route via .swarm/state.json king_terminal_id (cwd-relative)
cao doorbell send --trigger '[github:pr_checks:org/repo#42] failing'
```

| Option | Behavior |
| --- | --- |
| `--to` | 8-char hex terminal id. **Wins** over state file. |
| `--trigger` | Required canonical trigger (validated before HTTP). |
| `--state-file` | Override `.swarm/state.json` when `--to` is omitted. |

### State routing

When `--to` is omitted, the CLI reads `king_terminal_id` from `.swarm/state.json`
(relative to the current working directory). Fat/legacy keys are ignored. Actionable
errors when:

- state file missing or invalid JSON
- `king_terminal_id` missing, null, empty, or not 8-char hex

Thin template:

```json
{
  "epic_id": "bd-…",
  "king_terminal_id": "abcd1234",
  "policy_version": 1
}
```

Requires `cao-server` running and reachable at `CAO_API_HOST` / `CAO_API_PORT`
(default `127.0.0.1:9889`). When auth is enabled, set `CAO_AUTH_LOCAL_TOKEN` so
the CLI attaches `Authorization: Bearer …` (same pattern as MCP → API hops).

## Delivery path

Doorbell uses the existing inbox route — **no** dedicated doorbell endpoint:

```http
POST /terminals/{receiver_id}/inbox/messages?sender_id=doorbell&message=<validated-trigger>
```

The inbox `message` body is **exactly** the validated trigger string (no wrapper).
Delivery queues like any `send_message` inbox item; the server may deliver immediately
when the king terminal is IDLE.

### Anti-goals (explicit)

- No watcher engine (Gmail/Slack/ntfy adapters) in CAO core
- No auto-`assign` / auto-`handoff` / worker spawn from doorbell ingress
- No Slack/phone bridge
- No log/webhook payload passthrough into king context
- No truncation of over-length triggers

Kings: validate shape → decide relevance → optionally fetch live state → optionally
`bd note` — never launch workers solely because a doorbell arrived.

## GitHub Actions example (PR check failure)

Safe pattern: fixed marker fields + trusted GitHub run identifiers only. Do **not**
interpolate PR titles, commit messages, or secrets into the trigger.

```yaml
- name: Doorbell king on check failure
  if: failure()
  env:
    CAO_AUTH_LOCAL_TOKEN: ${{ secrets.CAO_AUTH_LOCAL_TOKEN }}
  run: |
    PR="${{ github.event.pull_request.number }}"
    REPO="${{ github.repository }}"
    RUN_ID="${{ github.run_id }}"
    TRIGGER="[github:pr_checks:${REPO}#${PR}] run_${RUN_ID}_failed"
    cao doorbell send --trigger "${TRIGGER}"
```

Run from the repo root where `.swarm/state.json` holds `king_terminal_id`, or pass
`--to` explicitly. Keep `TRIGGER` under 200 characters; shorten `hint` if needed.

## Related docs

- Chess swarm bootstrap: `examples/chess-pieces/README.md`
- Thin state + bd protocol: `examples/chess-pieces/swarm-templates/state.json`
- Agent-deck transfer plan Item 5: `docs/issues/agent-deck-transfer/plan.md`
