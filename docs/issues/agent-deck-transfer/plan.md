# Plan: Agent-Deck Transfers for CAO Chess Swarm

**Status:** Proposed  
**Source:** Comparison of [agent-deck](https://github.com/asheshgoplani/agent-deck) fleet-ops patterns against CAO chess-piece swarm  
**Depends on:** Swarm-economics Waves 0–3 (depth/terminal caps, stacked review) — already landed  
**Related:** `docs/issues/swarm-economics/design.md` (esp. T1.4 / D11–D13)

---

## Goal

Steal six battle-tested agent-deck patterns to raise swarm reliability without importing agent-deck itself. CAO stays the control plane; chess pieces stay the role ladder. We add: honest completion, durable policy + bd work graph, non-LLM verification, workspace isolation, lean external triggers, and per-wave concurrency.

## Sequencing

```
Wave A (prompt + light CAO)     Wave B (runtime)           Wave C (isolation + ingress)
─────────────────────────────   ─────────────────────────  ────────────────────────────
1. Done sentinel ──────────────► 3. External done_cmd
2. .swarm/ + bd work graph ────► (bd notes = receipts for 3)
                                 6. Wave concurrency queue
                                                            4. Worktree-per-assign (T1.4)
                                                            5. Doorbell watchers
```

| Wave | Items | Effort | Risk | Unlocks |
| --- | --- | --- | --- | --- |
| **A** | 1, 2 | Small (profiles + bd protocol + tiny parser) | Low | Trustworthy fleet status + durable king/bd state |
| **B** | 3, 6 | Medium (MCP + admission) | Medium | Mechanical acceptance; thrash bounds |
| **C** | 4, 5 | Large (workspace backend + adapters) | High | Real parallel safety; event-driven king |

Do **not** start Wave C until Wave A sentinels exist — isolation without honest completion just makes thrash harder to see.

---

## Item 1 — Done sentinel

### Problem

Handoff “success” and assign `send_message` today mean *the worker finished a turn / went IDLE*, not *the task is done*. Agent-deck’s `===AGENTDECK_DONE===` fixed conductors mistaking Stop-hook waiting for completion.

### Approach

1. **Convention (all worker pieces):** Last line of final output must be:
   ```
   ===CAO_DONE=== status=ok|fail|blocked summary=<one line>
   ```
2. **King / orchestrator prompts:** Treat absence of sentinel as incomplete; do not delete_terminal / advance wave on prose alone for assign workers; for handoff, prefer structured field over raw capture.
3. **CAO hardening (small):** On handoff completion, scan captured output for the sentinel; populate `HandoffResult` with `done_status` / `done_summary` (nullable for back-compat). Optional: reject handoff `success=true` when `--assert-done` equivalent is on and sentinel missing/fail.
4. **Assign path:** Instruct workers to put the sentinel in the `send_message` body (first or last line). King parses before cleanup.

### Surfaces

| Change | Path |
| --- | --- |
| Worker profiles | `examples/chess-pieces/{pawn,knight,bishop,queen,rook,rook-adversarial}*.md` |
| Supervisor profiles | `examples/chess-pieces/{king,orchestrator}*.md` |
| README note | `examples/chess-pieces/README.md` |
| `HandoffResult` + scan | `mcp_server/models.py`, `mcp_server/server.py` |
| Tests | unit scan + one handoff integration |

### Acceptance

- [ ] Every chess worker profile requires the sentinel.
- [ ] King profile: “no sentinel ⇒ not done.”
- [ ] `HandoffResult` exposes parsed `done_status` / `done_summary` when present.
- [ ] Unit tests for ok / fail / missing / multiline noise.

### Out of scope for Item 1

Provider Stop-hook wiring (agent-deck style). Prompt + capture scan is enough for CAO’s handoff/assign model.

---

## Item 2 — `.swarm/` policy files + bd work graph

### Problem

`.swarm/state.json` + `task-log.md` are ad-hoc mission scratch. Agent-deck separates policy, learnings, working state, and audit receipts — but CAO already has **bd** (beads): git-backed issues with claim, notes, deps, ready queue, and `bd stale`. A markdown task log would duplicate that poorly.

### Approach

Split durable **rules** (files) from **work tracking** (bd):

| Concern | Home | Owner | Purpose |
| --- | --- | --- | --- |
| Hard rules | `.swarm/POLICY.md` | Human / king (rare) | Piece mapping, security, “never X” |
| Institutional memory | `.swarm/LEARNINGS.md` | King after waves | Corrections, “do not repeat” (optional later: `bd memories`) |
| Mission pointers | `.swarm/state.json` (thin) | King | `epic_id`, `king_terminal_id`, optional overrides — **not** the worker/wave ledger |
| Wave graph + receipts | **bd** | King (primary); workers optional | Issues, claims, notes, close reasons, stall via `bd stale` |
| ~~`task-log.md`~~ | **Removed** | — | Replaced by `bd note` / close reasons |

**Drop `task-log.md`.** Do not create it in templates going forward; existing files may remain as historical read-only until archived.

#### bd protocol (king)

1. Mission start: `bd create` an epic (or `bd swarm`); record `epic_id` in thin `state.json`.
2. Each wave item → child issue under the epic (`parent-child`), with deps (`blocks`) where order matters.
3. Before `assign`/`handoff`: `bd update <id> --claim` (or note `assigned:<piece>:<terminal_id>`).
4. On worker report / handoff capture: `bd note <id> "receipt: <summary>"` (and any `NEED:` lines).
5. On `===CAO_DONE=== status=ok` (+ optional Item 3 `done_cmd` exit 0): `bd close <id> -r "<summary>"`.
6. On fail/blocked: note + leave open or reopen; do not advance dependents.
7. Stall check: `bd stale` (king-prompted in Wave A; daemon later optional).
8. Session end that mutated issues: `bd sync`.

#### Receipt authorship

**Decision (Wave A):** King writes bd notes/closes from worker reports so workers need not run `bd` (avoids parallel Dolt writers + shared-cwd thrash until Item 4). Workers emit sentinel + summary only.

Optional later: workers may `bd note` if `bd init --server` (or king-serialized) is in place; prefer wisps for ephemeral noise.

#### Thin `state.json`

Keep only pointers the runtime needs outside bd:

```json
{
  "epic_id": "bd-…",
  "king_terminal_id": "a1b2c3d4",
  "policy_version": 1
}
```

Wave membership, worker lists, gates, and decision history live on bd issues (description, notes, labels) — not a growing JSON blob. Migrate existing fat `state.json` missions by creating an epic + children once; leave old keys unread.

#### Bootstrap

- Require `bd` on PATH; `swarm init` runs `bd init` if `.beads/` missing (document server mode for multi-writer fleets).
- Creates `.swarm/POLICY.md` + `LEARNINGS.md` from templates if missing; writes thin `state.json`.
- King prompt: on start read POLICY + LEARNINGS + `bd show`/`bd ready` for `epic_id`; never invent a parallel task-log.

### Surfaces

| Change | Path |
| --- | --- |
| Templates | `examples/chess-pieces/swarm-templates/{POLICY,LEARNINGS}.md` + thin `state.json` |
| Launcher | `bin/swarm` → `init` (bd + `.swarm/` files) |
| King / orchestrator prompts | Memory + “bd work-graph protocol” sections |
| Worker profiles | No bd required in Wave A; sentinel only |
| Docs | Chess README: bd replaces task-log; gitignore note for thrashing `issues.jsonl` if needed |
| Legacy | Stop writing `task-log.md`; optional one-line pointer in README |

### Acceptance

- [ ] `swarm init` ensures `.beads/`, POLICY, LEARNINGS, thin `state.json` — **no** `task-log.md`.
- [ ] King profile documents epic → claim → note receipt → close; stall via `bd stale`.
- [ ] A sample wave is trackable with `bd children <epic>` / `bd ready` without reading a markdown log.
- [ ] Fat legacy `state.json` still boots (king prefers `epic_id` when present).

### Out of scope

- Replacing CAO `memory_store` / wiki (cross-session stigmergy stays).
- Mapping LEARNINGS fully onto `bd memories` (optional follow-up).
- Auto-installing bd via brew in CI images (document prerequisite).

---

## Item 3 — External done-conditions on handoffs

### Problem

Rook is LLM judgment. Agent-deck’s Goal framework: **Verifier is a shell command** the manager runs; worker cannot declare done. Stops 18-hour “status ok, no progress” stalls.

### Approach

1. Add optional `done_cmd` (string) to `handoff` (and later `assign` completion gate).
2. After worker terminal reaches IDLE / handoff capture completes:
   - If `done_cmd` set: run it in the worker’s working directory (timeout, no shell injection — argv list or `bash -lc` with allowlist policy TBD).
   - Exit 0 ⇒ accept; non-zero ⇒ `HandoffResult.success=false`, include stderr tail + worker sentinel.
3. King brief pattern for CI / green-check tasks:
   ```
   handoff(pawn, message=..., done_cmd="gh pr checks <n> --fail-fast")
   ```
4. Rook remains for design/risk; `done_cmd` covers **mechanical** acceptance only.
5. Progress receipts (Item 2): optional later “manager nudge” if `bd stale` shows idle claimed issues — **defer daemon**; king-prompted `bd stale` first.

### Surfaces

| Change | Path |
| --- | --- |
| MCP `handoff` param | `mcp_server/server.py` |
| `HandoffResult` fields | `done_cmd_exit`, `done_cmd_output` |
| King / pawn CI docs | chess-piece profiles + README |
| Tests | fake done_cmd exit 0 / 1 |

### Security

- Default **off** (param absent ⇒ current behavior).
- Cap timeout (e.g. 120s).
- Log full command in handoff result for audit.
- Do not pass unsanitized user strings from untrusted doorbells straight into `done_cmd` (Item 5).

### Acceptance

- [ ] `handoff(..., done_cmd="true")` succeeds; `done_cmd="false"` fails even if worker printed ok sentinel.
- [ ] Chess CI-babysit docs show king using `done_cmd` for check greenness.
- [ ] No behavior change when `done_cmd` omitted.

### Depends on

Item 1 (sentinel still required so failures distinguish “worker failed” vs “verifier failed”).

---

## Item 4 — Worktree-per-assign (T1.4)

### Problem

Shared cwd ⇒ silent LWW. Swarm-economics already designed D11 workspace backends (`shared` | `rift` | `worktree`). Agent-deck confirms worktree/sandbox as session-first-class isolation.

### Approach

Implement the **already-designed** workspace backend family; chess swarm opts in via env / assign flag.

1. **ABC** `WorkspaceBackend`: `probe`, `create`, `diff`, `remove`, `ancestors` (per design D11).
2. **Ship order:** `shared` (default) → `worktree` → optional `rift` when binary probes ok.
3. **`assign` / `handoff`:** optional `workspace="auto"|"shared"|"worktree"|"rift"`; `auto` = probe chain when `CAO_WORKSPACE_BACKEND=auto`.
4. **Merge path:** worker commits on its branch; king (or later `merge_arbiter`) merges via worktree object store. Defer full D13 arbiter agent to a follow-up; Wave C ships create/remove + document manual merge.
5. **Chess default for parallel assign:** king prompt: “when launching ≥2 implementers, use `workspace=worktree` (or auto).”

### Surfaces

| Change | Path |
| --- | --- |
| New package | `src/cli_agent_orchestrator/workspaces/` |
| Wiring | `terminal_service` / MCP create params |
| Env | `CAO_WORKSPACE_BACKEND`, docs in `docs/working-directory.md` or new `docs/workspaces.md` |
| King prompt | Parallel-assign isolation rule |
| Design alignment | Cite D11–D13; do not fork a second design |

### Acceptance

- [ ] Two concurrent pawns with `workspace=worktree` cannot overwrite the same tracked file silently (conflict or separate branches).
- [ ] `shared` remains default; existing tests green without env changes.
- [ ] Cleanup removes worktrees on `delete_terminal` / successful handoff.

### Depends on

Item 1 for knowing when to tear down; Item 6 recommended so fan-out stays bounded while isolation lands.

### Explicitly deferred

Full rift-as-default, merge_arbiter profile (D13), CoW speculative N-version runs.

---

## Item 5 — Doorbell watchers

### Problem

Long-lived kings burn context if every webhook/email/CI event dumps a full payload. Agent-deck: **forward the bell, not the package** (≤200 chars, structured).

### Approach

1. **Trigger format:** `[source:type:id] hint` — e.g. `[github:pr_checks:org/repo#42] failing`  
2. **Delivery:** `send_message` to the king terminal id from thin `.swarm/state.json` (`king_terminal_id`). Optional: `bd note <related-issue> "doorbell: …"` for audit.
3. **Adapters (MVP):**  
   - CLI: `cao doorbell send --to <terminal_id> --trigger '...'`  
   - Optional: GitHub Actions step / thin HTTP webhook that only emits the short trigger (HMAC later).
4. **King prompt:** On doorbell, fetch live state (`gh pr view`, etc.) only if relevant; ignore freely; link to bd issue when acting.
5. **Never auto-`assign` from the watcher** (agent-deck rule: watchers don’t launch — avoids orphans). King decides.

### Surfaces

| Change | Path |
| --- | --- |
| Small module | `src/cli_agent_orchestrator/doorbell/` or `bin/cao-doorbell` |
| API | POST `/terminals/{id}/doorbell` or reuse inbox send |
| King prompt | “Doorbell handling” section |
| Docs | `docs/doorbell.md` |
| Thin `.swarm/state.json` | `king_terminal_id` (+ optional default issue id) |

### Acceptance

- [ ] Trigger >200 chars rejected or truncated with warning.
- [ ] King receives inbox message without watcher spawning agents.
- [ ] One documented GH Actions example for PR check failure → king.

### Depends on

Item 2 (`king_terminal_id` in thin state); Item 1 optional but useful for “CI green” follow-ups via Item 3.

### Deferred

Full agent-deck watcher engine (Gmail/Slack/ntfy adapters), phone bridge, triage reaper sessions.

---

## Item 6 — Wave concurrency queue

### Problem

`CAO_MAX_ACTIVE_TERMINALS` is a global ceiling. Agent-deck’s group `max_concurrent` + FIFO queue prevents parallel-worker OOM and thrash. Chess waves still rely on king discipline.

### Approach

1. Add `CAO_MAX_WAVE_IN_FLIGHT` (default e.g. 3) — max **implementer** children of one supervisor in `RUNNING`/`IDLE`-waiting-callback at once.
2. Excess `assign` calls enter `QUEUED` (new terminal status or side table); drain FIFO when a sibling is deleted / handoff completes.
3. `handoff` either counts against the same budget or bypasses (decision: **handoff counts** — simpler; king can still serialize via handoff-only waves).
4. King prompt: prefer assign fan-out up to the cap; do not busy-poll — queue is server-side.
5. Surface queue depth in web UI / `swarm status` later (nice-to-have).

### Surfaces

| Change | Path |
| --- | --- |
| Constants | `constants.py` |
| Admission | `terminal_service.py` / MCP `_assign_impl` |
| Status | `TerminalStatus.QUEUED` or queue table |
| King prompt | “Fan-out respects server queue” |
| Tests | assign 5 with cap 2 ⇒ 2 running + 3 queued; delete drains |

### Acceptance

- [ ] With cap=2, third assign returns `queued` (or terminal_id in queued state) without exceeding in-flight implementers.
- [ ] Completing/deleting a worker starts the next queued child with original message.
- [ ] Global `CAO_MAX_ACTIVE_TERMINALS` still applies as hard ceiling above the wave cap.

### Interaction with Item 4

Queue first (Wave B) reduces blast radius; then isolation (Wave C) makes the allowed parallelism safe.

---

## Cross-cutting conventions

Steal these markers from agent-deck / existing pawn CI flow; standardize in POLICY.md and mirror into bd notes where useful:

| Marker | Meaning | Who emits | bd |
| --- | --- | --- | --- |
| `===CAO_DONE=== status=… summary=…` | Task completion | Workers | King closes or notes fail |
| `NEED: …` | Host action required (push, secret, decision) | Workers → king | `bd note` + leave open |
| `AUTO: …` | King/system may act without human | Optional | optional note |
| `[STATUS] …` | Heartbeat / wave status line | King | optional note on epic |

Phone/Slack bridge remains out of scope until markers are consistent.

---

## Suggested implementation tickets

| ID | Title | Wave | Size |
| --- | --- | --- | --- |
| ADT-1 | Done sentinel in chess profiles + HandoffResult parse | A | S |
| ADT-2 | `.swarm/` POLICY+LEARNINGS+thin state; bd epic/receipt protocol; drop task-log | A | S |
| ADT-3 | `handoff(done_cmd=…)` verifier | B | M |
| ADT-4 | `CAO_MAX_WAVE_IN_FLIGHT` + assign queue drain | B | M |
| ADT-5 | `workspaces/` ABC + worktree backend + MCP wiring | C | L |
| ADT-6 | Doorbell CLI/API + king routing via thin state.json | C | M |

---

## Success metrics

After Waves A–C on a real multi-worker mission:

1. King never advances a wave on a worker that omitted `===CAO_DONE===`.
2. `.swarm/{POLICY,LEARNINGS}` + thin `state.json` exist; wave progress is visible via `bd children` / notes / closes — **no** `task-log.md` required.
3. At least one CI/green task accepted/rejected solely by `done_cmd` exit code.
4. Two parallel implementers with worktrees produce branch-isolated commits (no silent LWW on the same file).
5. A CI failure can wake the king via a ≤200-char doorbell without pasting logs into context.
6. Assign fan-out above `CAO_MAX_WAVE_IN_FLIGHT` queues rather than stampeding.

---

## Non-goals

- Replacing CAO with agent-deck or adopting their Go TUI / Command Center wholesale.
- Telegram/Discord bridge, MCP socket pool, self-heal observe daemon.
- YAML parallel/pipeline workflows (N7) — still reserved.
- Making rift the default workspace backend.
- Replacing bd with a custom task-log; or making workers mandatory bd writers in Wave A.
