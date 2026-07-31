# Design: Swarm Economics — Bounding, Measuring, and Tiering CAO's Agent Fleet

**Issue:** unfiled (proposal)
**Status:** Proposed — not implemented, no code written
**Prompted by:** Cursor, ["Agent swarms and the new model economics"](https://cursor.com/blog/agent-swarm-model-economics) (Wilson Lin, 2026-07-20)
**Scope:** Five tranches (T0-T4) that adapt Cursor's published swarm-harness
findings to CAO. Each tranche is independently shippable. T0 is a prerequisite
for honestly evaluating T2. Decisions are numbered D1-D18 for citation from
source comments once implemented, per the convention in
`docs/issues/345-okf-export-import/design.md`.

---

## Summary

Cursor rebuilt their agent swarm harness and re-ran a fixed task (implement
SQLite in Rust from the 835-page manual, no source or test suite available) under
old and new harnesses across four model mixes. The new harness won in every mix.
Two findings are directly actionable for CAO:

1. **Model economics.** Every model mix produced similar quality; cost varied
   from $1,339 to $10,565 — roughly 8x. Workers carried 69-90%+ of tokens but
   planners carried ~2/3 of dollars. A frontier planner paired with a cheap
   worker fleet was the cost-optimal configuration.
2. **Thrash is the failure mode, and thrash is measurable.** The old harness
   produced 68,000 commits and 70,000 merge conflicts in two hours before being
   paused; one file collected 7,771 conflicts across 1,173 agents; the project
   sprawled to 54 Rust crates where 9 sufficed and 64,305 lines where 9,908
   sufficed. Every fix Cursor shipped was a response to one of those metrics.

CAO already has the plumbing for finding 1 (per-call and per-profile `model`
override, threaded end to end) and unusually strong bones for Cursor's stigmergy
mechanisms (the memory wiki, plus `wiki_lint.py`'s existing contradiction and
stale-claim detection, which is a superset of Cursor's "reconciler" problem).

CAO has none of the plumbing for finding 2. There is no cost accounting, no
step-duration record, no collision telemetry, no concurrency cap, no spawn-depth
limit, and no working-directory isolation between concurrent workers. In its
current state CAO is *less* observable than the Cursor harness that had to be
paused, and its uncontrolled-growth failure mode is worse: silent last-write-wins
on a shared working tree rather than a merge conflict that at least gets counted.

This document proposes:

- **T0** — make thrash visible (step duration, swarm-health instruments,
  git-derived collision telemetry, best-effort per-provider token usage).
- **T1** — bound the swarm (spawn depth, fan-out cap, a non-orchestrating
  `worker` role, deny-by-default on unknown roles).
- **T1.4** — workspace isolation via a pluggable backend family
  (`shared` | `rift` | `worktree`), mirroring CAO's existing terminal-backend
  abstraction.
- **T2** — role-tiered model defaults, plus the A/B protocol needed to reproduce
  Cursor's cost finding on real workloads.
- **T3** — stacked, decorrelated review lenses.
- **T4** — decision records, an always-injected Field Guide, megafile flagging,
  and a licensed-breakage convention.

Explicitly out of scope: a custom VCS, and building the reserved YAML
parallel/pipeline unit (N7). Reasoning in [Out of scope](#out-of-scope).

---

## Context

### What Cursor actually found

Stripping the product marketing, five claims matter.

**Role split is a context-efficiency mechanism, not a parallelism mechanism.**
"We suspect the ability to scale the agent swarm comes from this context
efficiency, more than from parallelism itself." A planner never implements, so
its context never fills with low-level detail; a worker never plans, so it can
spend all its context on one narrow piece. The claim is that this helps even at
moderate scale, which means it is not contingent on running hundreds of agents.

**Model economics.** Token volume and dollar volume decouple. Workers carried at
least 69% of tokens (over 90% in most runs) but the expensive planner dominated
spend. In the GPT-5.5-everywhere run the workers alone cost $9,373; in the
Opus-plans/Composer-works run the entire worker fleet cost $411 at comparable
quality. "Few moments in a large task genuinely require frontier intelligence,
such as the original decomposition, the design decisions, and certain
trade-offs. Once a frontier planner has collapsed the ambiguity into a detailed,
explicit instruction, less expensive models simply have to follow it."

Note the counter-example in the same data: the Fable 5 planner spent *fewer*
planner dollars than the Opus 4.8 planner despite ~2x the per-token price,
because it emitted far fewer planning tokens — but its workers then burned
several times as many tokens and the run came out more expensive overall. Planner
quality shifts worker cost. This is why T2 cannot be evaluated without T0: the
cheapest planner is not necessarily the cheapest run.

**Named failure modes at high commit rate.** Cursor enumerates six, each with a
shipped fix:

| Failure mode | Cursor's fix |
| --- | --- |
| Split-brain design (two planners solve the same problem differently) | Planners decide, never delegate decisions; no two sibling subtrees may decide the same question |
| Contention between planners | Decisions recorded in shared design docs; code carries compile-checked references to them; a reconciler merges contradictory docs and references propagate the resolution |
| Merge conflicts | A neutral third-party arbiter agent resolves on behalf of both parties (workers are bad at this — they overwrite or abandon) |
| Megafiles | Workers can flag a bloated file; commits are blocked and an outside agent decomposes it |
| Ossification (agents refuse to touch core code) | License intentional breakage: patch core, leave a comment explaining why, let the compiler propagate the break, downstream agents read the comment and adapt |
| Errors accumulating over long runs | Stacked review lenses |

**Review lenses stack because they are decorrelated.** They tried giving a
reviewer the worker's full transcript, or only its output, or nothing but the
codebase; and reviewers on different models with different training and
personality. "No single lens catches everything, but decorrelated lenses stack,
the way self-driving systems reach above-human reliability without any single
perfect component. The compute spent on review is high return, since review is
much cheaper than the work it audits."

**Stigmergy beats messaging.** The Field Guide is a folder owned entirely by the
agents whose `index.md` is injected into every agent at start, with a line budget
as its only constraint. Rationale: model weights are frozen, so surprise
encounters are exactly what is worth capturing so the next trajectory is shorter.

### What CAO already has

CAO is well positioned on three of the five.

- **Role split exists and is prompt-enforced.** `agent_store/code_supervisor.md:28-30`
  ("NEVER write code directly yourself") and the mandated review cycle at
  `:35-48` ("This review cycle (steps 3-5) MUST continue until the Code Reviewer
  approves the code").
- **Model override is fully plumbed.** `AgentProfile.model`
  (`models/agent_profile.py:75`) and a per-call override threaded MCP →
  HTTP → service: `handoff`/`assign` accept `model`
  (`mcp_server/server.py:869-919`, `:1140-1145`),
  `POST /sessions/{name}/terminals?model=` validates it
  (`api/main.py:2073`, `:2103-2104`, capped by `constants.py:650`
  `MODEL_ID_MAX_LEN = 128`), and precedence is documented at
  `terminal_service.py:191-197` — per-call beats the profile's static field.
  Every provider adapter appends `--model` (e.g. `providers/kiro_cli.py:313`,
  `providers/claude_code.py:356`).
- **Stigmergy substrate exists and exceeds Cursor's.** The memory wiki
  (`services/memory_service.py`, `docs/memory.md`) has five scopes, a compiler
  (`services/wiki_compiler.py`), a linter that already detects orphans,
  **contradictions**, and stale claims (`services/wiki_lint.py`), and a healer
  with a blast-radius budget (`services/wiki_healer.py:912`). Cursor's
  "reconciler merges contradictory docs" is a feature CAO has most of.
- **Transcripts are already persisted.** Per-terminal append log at
  `services/log_writer.py:75` (`TERMINAL_LOG_DIR / f"{terminal_id}.log"`,
  `constants.py:124-126`), plus a full unbounded scrollback snapshot written on
  teardown (`terminal_service.py:1273-1297`).
- **A backend-abstraction pattern is established.** `backends/base.py` ABC with
  capability probing (`supports_event_inbox()` at `:27`), `backends/factory.py:26`,
  `backends/registry.py:16`, two implementations (`tmux_backend`, `herdr_backend`).
  The same shape is used for memory archives (`MemoryArchiveBackend` ABC +
  registry, per `docs/issues/345-okf-export-import/design.md`). T1.4 should reuse
  it rather than invent anything.
- **Honest tiering is an existing project principle.** `workflow_service.py:16-17`:
  "Reserved seams ... raise `NotBuiltYetError` when reached — never silently
  downgraded to sequential." T0 should follow this for token accounting.

### Verified gaps

Every row below was verified against source. Nothing here is inferred.

| # | Gap | Evidence |
| --- | --- | --- |
| G1 | **All concurrent workers share one working directory.** With `CAO_ENABLE_WORKING_DIRECTORY` off (default, `mcp_server/server.py:42`), `working_directory` is not even a tool parameter, and the child inherits the supervisor's live pane cwd. | `mcp_server/server.py:226-242` → `terminal_service.py:862-887` → `clients/tmux.py:204-241` (`start_directory`); fallback `os.getcwd()` at `clients/tmux.py:68-69`; stated at `docs/working-directory.md:21-24` |
| G2 | **No worktree, branch, snapshot, or copy-on-write isolation exists anywhere in `src/`.** Path handling is security validation only (blocklist, `clients/tmux.py:36`), not isolation. | `resolve_and_validate_path` at `clients/tmux.py:38-75`; no `git worktree`/`git checkout`/branch-creation call sites in `src/` |
| G3 | **Every built-in role can orchestrate.** All three role defaults include `@cao-mcp-server`, the server that defines `handoff` and `assign`. Every worker is therefore also a planner. | `constants.py:511-515` |
| G4 | **No-role defaults to `developer`; unknown role falls back to fully unrestricted `["*"]`.** Already live: `agent_store/workflow_scout.md:4` declares `role: workflow_scout`, absent from `ROLE_TOOL_DEFAULTS`, so it resolves unrestricted. | `utils/tool_mapping.py:126-133` (unknown → `["*"]` with a warning), `:134-138` (none → `developer`) |
| G5 | **Child tool sets are not intersected with the parent's,** and a child profile declaring `*` escalates to unrestricted regardless of parent. | `mcp_server/server.py:158-160`, `:163-167` (comment states intersection is deliberately not enforced) |
| G6 | **No spawn-depth, spawn-count, or fan-out limit.** `_assign_impl`/`_handoff_impl` have no caller-role check, no depth counter, no budget. The only guard is "am I in a terminal at all". | `mcp_server/server.py:1002-1012` |
| G7 | **No server-side concurrency cap.** `POST /sessions/{name}/terminals` performs no count check; `create_terminal` has no admission control. Deferred-init tasks are held in a set only to prevent GC, which is not a bound. The 10-terminal threshold is advice injected into the LLM's context, not enforcement. | `api/main.py:2059-2172`; `terminal_service.py:85`; `mcp_server/server.py:50` (`TERMINAL_CLEANUP_NUDGE_THRESHOLD`); parallelism-as-goal comments at `api/main.py:2083-2086` and `mcp_server/server.py:986-989` |
| G8 | **Step duration is unrecoverable.** `workflow_run_step` has `updated_at` (last mutation) but no `started_at`. `StepResult` carries no timestamps at all. `_handoff_impl` computes `start_time` and discards it. | `clients/database.py:500-516`; `models/workflow_runtime.py:137-149`; `mcp_server/server.py:722` |
| G9 | **Exactly one telemetry instrument exists,** a counter, inert unless the `[otel]` extra is installed. No histograms, no gauges, no active-terminal metric. | `telemetry/metrics.py:28-36`, `:39-42` |
| G10 | **No token or cost accounting.** A `"metric"` AG-UI event type is reserved for "(tokens, latency, cost)" with no producer. | `services/agui_stream.py:97` |
| G11 | **No file-size, hotness, or churn concept.** No git-blame analysis, no per-file edit counters, no LOC thresholds. The only byte caps are zip-bomb and workflow-spec limits. | `constants.py:498-500`, `:535-544` |
| G12 | **No designed transcript review lens.** `cao-mcp-server` exposes 18 tools, none of which read another terminal's output. `cao-ops-mcp` has `read_session_output`/`get_terminal_output` but is operator-facing and wired into no packaged profile. A reviewer holding `fs_read` can read a peer's log file directly — same OS user, `0o700` is no barrier — which is incidental access, not a lens. | `ops_mcp_server/server.py:454`, `:541`; `utils/tool_mapping.py:34`; `constants.py:124-126` |
| G13 | **`OutputMode.FULL` is not full.** It returns the StatusMonitor rolling buffer, bounded to `state_buffer_max` (32 KB default), falling back to a tmux capture only when empty. | `terminal_service.py:117-125`, `:1094-1103` |
| G14 | **No role→model mapping.** `ROLE_TOOL_DEFAULTS` is tools-only; custom roles are `Dict[str, List[str]]` and structurally cannot carry a model. The `agents` settings section has exactly `dirs`, `extra_dirs`, `disabled_dirs`, `roles`. `agents.roles` has no writer and must be hand-edited. | `constants.py:511-515`; `utils/tool_mapping.py:82-102`; `docs/configuration.md:37-48` |
| G15 | **Memory injection ranks by recency, not relevance.** The default path's entire ranking function is a sort on `updated_at`. BM25 exists in the codebase but feeds the *recall* path and profile search, not injection. The agent-curated path requires an IDLE `memory_manager` terminal in the same session and falls back otherwise. Injection happens on the first user message only. | `memory_service.py:2614`, `:2555-2687`, `:2694-2714`, `:2716-2787`; `services/memory_scoring.py:5-6`, `:22-31`; `services/profile_search.py:127`; `terminal_service.py:92-115` |
| G16 | **`reviewer.md` contradicts its own role.** `:33-35` instructs it to write output to a file and reference absolute paths; `role: reviewer` grants `fs_read`/`fs_list` but no `fs_write`. | `agent_store/reviewer.md:3`, `:33-35`; `constants.py:513` |
| G17 | **`memory_manager.md` references a tool that does not exist.** `:22` says "Use `session_context` to understand what has happened in this session so far". No such MCP tool exists; the only match is an internal provider method. | `agent_store/memory_manager.md:22`; `providers/kimi_cli.py:192` |
| G18 | **Shared-cwd provider memory files are clobber-prone.** Every concurrent Codex worker rewrites `<cwd>/AGENTS.md`; every Claude Code worker rewrites `<cwd>/.claude/CLAUDE.md`. `locked_atomic_rewrite` prevents torn writes but not last-write-wins on each other's delimited block. | `plugins/builtin/codex_memory.py:39`, `:53`; `plugins/builtin/claude_code_memory.py:32`, `:47` |

### The composite risk

G1, G3, G4, G6, and G7 compose into a single reachable failure:

> A supervisor assigns three `developer` workers. Each inherits the supervisor's
> cwd. Each holds `@cao-mcp-server`, so each can assign three more. Nothing
> counts depth, nothing counts terminals, and nothing isolates writes. The
> practical ceiling is OS, tmux, or provider-API resource exhaustion, reached
> without warning or backpressure — a fork bomb of agents editing one working
> tree.

And because of G8-G13, the run leaves behind no step durations, no collision
count, no file-hotness data, and no transcript-level review lens with which to
reconstruct what happened. Cursor's old harness at least had a VCS making
collisions visible enough to chart; CAO's equivalent event is a silent overwrite.

This is the ordering argument for the whole document: **observability and bounds
before economics.** Tiering models on an unbounded, unmeasured swarm produces a
number you cannot attribute.

---

## T0 — Make thrash visible

Goal: for any run, answer "how long did each step take, how much did it cost, and
did agents fight each other". Every Cursor fix was downstream of a metric like
this.

### D1 — Record step start time

Add `started_at TEXT` to `workflow_run_step` as an additive migration following
the established pattern at `clients/database.py:459-469` (the same mechanism that
added `tier` and `generation` to `workflow_run`). Populate on transition into a
running state; leave `NULL` for rows rebuilt from older journals.

Surface `started_at`/`finished_at`/`duration_ms` on `StepResult`
(`models/workflow_runtime.py:137-149`), which currently carries no timestamps.
`WorkflowRunResult` (`:152-171`) already has `started_at`/`finished_at`; add a
computed `duration_ms` there too rather than making every consumer subtract
strings.

Note the constraint at `clients/database.py:477-479`: `reprompted` and
`terminal_id` are deliberately in-memory-only and defaulted on journal rebuild.
`started_at` must be genuinely journaled, not follow that pattern, or duration is
lost on resume.

### D2 — Surface handoff duration

`_handoff_impl` already computes `start_time = time.time()` at
`mcp_server/server.py:722` and throws the value away. Add `duration_ms` to
`HandoffResult` (`mcp_server/models.py:8-14`) and return it. Near-zero cost, and
it makes the blocking-delegation path measurable without touching the journal.

### D3 — Swarm-health instruments

Extend `telemetry/metrics.py` beyond its single counter (G9). Minimum viable set,
all following the existing lazy no-op-without-`[otel]` pattern at
`telemetry/metrics.py:3-8`:

| Instrument | Type | Attributes |
| --- | --- | --- |
| `cao.agent.step.duration` | histogram (ms) | `provider`, `agent_profile`, `model`, `role`, `outcome` |
| `cao.agent.terminals.active` | up-down counter | `session`, `provider` |
| `cao.agent.spawn.depth` | histogram | `orchestration_type` |
| `cao.agent.step.attempts` | histogram | `agent_profile` — bounded by `constants.py:583-584` |
| `cao.review.rejections` | counter | `reviewer_profile`, `reviewer_model`, `lens` |
| `cao.repo.collisions` | counter | see D5 |

The `model` and `role` attributes are what make T2 measurable; without them the
cost comparison cannot be sliced.

### D4 — Token and cost accounting, honestly

CAO deliberately never speaks to an LLM API — "the agents remain full CLI
processes with their native authentication and capabilities" (`README.md:12-17`).
That is the reason G10 exists, and it should not be worked around by estimating.
Byte-count or `len // 4` heuristics (as used for wiki index sizing at
`clients/database.py:83`) are not acceptable as cost figures.

Proposal: add an optional provider-adapter capability.

```
BaseProvider.get_usage(terminal_id) -> Usage | None
```

`Usage` carries `input_tokens`, `output_tokens`, `cached_tokens`,
`model`, and `source` (an enum recording *where* the number came from). Adapters
that can read their CLI's own session record implement it; every other adapter
returns `None`. Claude Code and Codex both persist per-session usage on disk and
are the natural first two. `None` must propagate to the UI and API as "unknown",
never as zero.

This mirrors the existing soft-enforcement precedent at `terminal_service.py:140-146`,
where providers that cannot honour tool restrictions log a loud warning rather
than pretending. Same principle: report the capability gap, do not paper over it.

Dollar figures require a price table, which is a maintenance burden and goes
stale. Recommendation: ship tokens only, and let cost be computed by the operator
from an optional `models.pricing` settings map that defaults to empty. Reporting
tokens with a missing price is honest; reporting a wrong price is not.

### D5 — Git-derived collision telemetry

This is CAO's substitute for Cursor's purpose-built VCS. Cursor built the VCS for
throughput CAO does not need (1,000 commits/sec); the *reusable* insight is that
"every change passes through the VCS, so it is where collisions first become
visible". CAO can recover the same visibility from git without owning the layer.

Add `services/repo_hotspots.py`, computing per run:

- **Files touched by more than one terminal** — Cursor's file-hotness chart, the
  metric that found their 7,771-conflict file.
- **File LOC/byte growth over the run** — their megafile chart, and the input to
  D16.
- **Clobber events** — worker A's write to a region overwritten by worker B
  within the same run. Under a shared cwd (G1) this is CAO's analogue of a merge
  conflict, and unlike a conflict it is currently invisible.
- **Total LOC produced** — their strongest quality proxy. Both harnesses passed
  100% of the SQLite suite, but at 64,305 lines versus 9,908. Passing tests did
  not distinguish the harnesses; volume did.

Implementation should be snapshot-diff based (cheap, works under a shared cwd)
and must degrade to a no-op outside a git repository. It must not require a
commit — agents are not assumed to commit.

### T0 acceptance

A run produces a report answering: wall-clock per step; retries per step; tokens
per model where the provider exposes them; files touched by >1 agent; clobber
count; net LOC. Without all six, T2 is unfalsifiable.

---

## T1 — Bound the swarm

Goal: make the composite risk unreachable. These are small changes; two of them
alter existing defaults and therefore need the documentation treatment mandated
by `CODEBASE.md:138-157`.

### D6 — Spawn-depth counter

Inject `CAO_AGENT_DEPTH` into each spawned window, incremented from the parent's
value (absent ⇒ 0). Reject in `_assign_impl`/`_handoff_impl` when the child depth
would reach `CAO_MAX_AGENT_DEPTH` (proposed default **3**: supervisor → planner →
worker).

This fits an existing mechanism exactly. CAO already injects
`CAO_TERMINAL_ID`, and workflow env-var injection is deny-by-default via
`WORKFLOW_ENV_ALLOWLIST` (`constants.py:626-628`) with a 256-char value cap
(`:635`). `CAO_AGENT_DEPTH` joins that allowlist.

Rejection must be a clear, actionable error returned to the calling LLM ("depth
limit reached; do the work yourself or return to your caller"), not a silent
no-op — a silent failure produces an agent that believes it delegated.

### D7 — Enforced fan-out cap

Promote the advisory `TERMINAL_CLEANUP_NUDGE_THRESHOLD` (`mcp_server/server.py:50`)
into real admission control in `terminal_service.create_terminal`, configurable
as `CAO_MAX_ACTIVE_TERMINALS` (proposed default **12**, above the existing nudge
so current well-behaved sessions are unaffected). Over the cap, fail the HTTP
create with a distinguishable status and a retry hint rather than exhausting
tmux and the provider API.

Keep the nudge. The nudge asks the LLM to tidy up; the cap stops the machine
falling over. They solve different problems.

### D8 — A `worker` role that cannot orchestrate

This is Cursor's "a worker never plans" enforced structurally rather than by
prompt. Add to `ROLE_TOOL_DEFAULTS` (`constants.py:511-515`):

```
"worker": ["@builtin", "fs_*", "execute_bash", "web_fetch"]
```

— i.e. `developer` minus `@cao-mcp-server`.

The interesting question is whether `developer` should keep `@cao-mcp-server`.
Removing it is the faithful reading of Cursor's design and directly prevents
split-brain by construction, but it is a breaking change to the default role
(G4: no-role resolves to `developer`) and would break any existing profile that
delegates. Recommendation: **add `worker`, make it the no-role default, and leave
`developer` as-is but documented as an orchestrating role.** That gets the
structural guarantee for new profiles without breaking existing ones.

Note that `developer` also loses `memory_store`/`memory_recall` if
`@cao-mcp-server` is dropped, since memory tools live on the same server. If the
project wants non-orchestrating workers that still have memory, the orchestration
tools need to be separable — either a distinct MCP server or per-tool capability
gating like the one already used for `store_lesson`
(`mcp_server/server.py:1469-1487`). **This is an open question (Q3).**

### D9 — Unknown role denies

Change `utils/tool_mapping.py:126-133` so an unrecognised role resolves to a
minimal read-only set (or errors at profile load), not `["*"]`. Fail closed.

Fix the live instance: `agent_store/workflow_scout.md:4` declares
`role: workflow_scout`, currently silently unrestricted despite a comment
claiming otherwise. Either register the role in `settings.json` `agents.roles` or
change the profile to a real role.

`agents.roles` currently has no writer in `settings_service.py` (G14) and must be
hand-edited, which makes registering a custom role awkward. Adding a setter is a
prerequisite for D9 being usable in practice.

### D10 — Fix the documentation-contract violations

Small, unblocked, and independently mergeable:

- G16: reconcile `agent_store/reviewer.md:33-35` with `constants.py:513` — either
  grant scoped `fs_write` or stop instructing the reviewer to write files.
- G17: remove or replace the phantom `session_context` reference at
  `agent_store/memory_manager.md:22`. With D14 (transcript lens) this becomes a
  real tool and the instruction can be made true instead of deleted.

---

## T1.4 — Workspace isolation

Goal: two concurrent workers cannot silently overwrite each other. This is the
highest-severity single item in the document (G1 + G2 + G18).

### The primitive comparison: rift vs. git worktree

[rift](https://github.com/anomalyco/rift) is a copy-on-write workspace tool
(APFS `clonefile` on macOS, btrfs snapshots or reflinks on Linux) advertising
sub-0.1s creation on a 10 GB directory.

**Where rift is the better spawn primitive:**

- **Environment fidelity — decisive.** A git worktree contains only *tracked*
  files. A worker spawned into a fresh worktree of a Python repo has no `.venv`;
  of a Node repo, no `node_modules`; and no `.env`, no build cache. Every worker
  in a fan-out burns wall-clock and tokens on dependency installation before
  writing a line. `rift create --copy-all` CoW-clones the entire directory
  including `node_modules` in ~0.1s at near-zero disk cost. For a swarm designed
  around many cheap short-lived workers, spawn cost is a first-order concern.
- **Dirty-tree cloning.** rift creates a detached-`HEAD` workspace that "retains
  index and working-tree state". Worktrees require a committed ref and a clean
  tree, so a supervisor with uncommitted work must either commit (polluting
  history) or hand the worker stale code.
- **Ancestry registry maps onto the task tree.** rift records parent identifiers
  in SQLite and exposes `rift list` / `rift ancestors`. That is structurally the
  same tree Cursor describes: a planner's rift is the parent, its workers' rifts
  are children, and **rift depth is agent depth**. This collapses D6 and D11 into
  one primitive instead of two parallel bookkeeping systems. This is the
  strongest architectural argument for rift.
- **Declarative bootstrap.** `.rift.toml` postcreate hooks have no worktree
  equivalent.
- **Speculative execution becomes affordable.** At ~0.1s and ~0 bytes, running
  the same task in two workspaces under two different models and diffing the
  results is cheap. Cursor's "review is much cheaper than the work it audits"
  extends to N-version programming — but only with CoW.

**Where git worktree is the better merge primitive:**

- **Merge-back.** Worktrees share the object store, so `git merge <branch>` from
  the main checkout works directly with real conflict detection — exactly the
  surface a merge-arbiter agent (D13) needs. rift copies `.git`, so the child's
  objects diverge and merge-back means treating the rift path as a local remote
  (`git fetch <path> && git merge FETCH_HEAD`). Workable, one extra hop, but rift
  has no merge story of its own; it is a workspace cloner, not a VCS, and its
  ancestry registry serves cleanup rather than merging.
- **Portability. This is disqualifying for rift as a sole default:**

| Environment | rift | worktree |
| --- | --- | --- |
| macOS (APFS) | yes — `clonefile` | yes |
| Linux btrfs / XFS | yes | yes |
| **Linux ext4** | **no** — no `FICLONE` | yes |
| **Docker overlayfs** | **no** — no reflink | yes |
| Windows | not implemented | yes |

  CAO ships to PyPI, provides a devcontainer feature
  (`docs/devcontainer-feature.md`), supports Codespaces
  (`docs/codespaces.md`), and runs pytest in GitHub Actions. ext4 and overlayfs
  are where most of that lives.
- **Maturity.** rift's README opens: "This repository is experimental and is not
  ready for use ... behavior, interfaces, and implementation details may change
  without notice."
- **Language fit.** rift's programmatic API is Bun/Node FFI (Node 26.1+ with
  `--experimental-ffi`). CAO is Python, so it must drive the CLI and parse
  stdout. Workable — CAO already shells out to tmux — but the FFI advantage is
  unavailable.

### D11 — A workspace backend family, not a choice

Do not pick one. Add a `workspaces/` backend family mirroring the terminal
backend abstraction already in the codebase (`backends/base.py` ABC with
capability probing at `:27`, `backends/factory.py:26`, `backends/registry.py:16`)
and the memory-archive registry from issue #345.

```
WorkspaceBackend (ABC)
  probe()    -> bool          # is this backend usable here?
  create(from_path, name)     -> WorkspacePath
  diff(workspace)             -> Patch          # arbiter input
  remove(workspace)           -> None
  ancestors(workspace)        -> list[WorkspacePath]
```

Three implementations:

| Backend | Role |
| --- | --- |
| `shared` | Current behaviour — inherit the supervisor's cwd. **Remains the default.** Zero regression risk. |
| `rift` | Preferred fast path when the `rift` binary is present and `probe()` succeeds. |
| `worktree` | Universal fallback; also the merge substrate for D13. |

Selection is `CAO_WORKSPACE_BACKEND` with an `auto` mode that probes
`rift` → `worktree` → `shared` and logs the selection loudly on fallback. This
is the identical pattern to `terminal_service.py:140-146`, where a provider that
cannot enforce tool restrictions warns rather than silently misbehaving.

Keeping `shared` as the shipped default matters: isolation changes where agents
write files, which is a large behavioural change deserving an opt-in period.

### D12 — Provider memory files become per-workspace

Once workers have their own workspaces, G18 dissolves for free: `AGENTS.md` and
`.claude/CLAUDE.md` are written inside each worker's own workspace
(`plugins/builtin/codex_memory.py:53`, `claude_code_memory.py:47`), so the
last-write-wins clobber on the shared delimited block disappears. Worth stating
explicitly because it is a second, independent justification for T1.4 that has
nothing to do with source-file conflicts.

### D13 — Merge arbiter agent

Cursor's finding: workers are bad at resolving collisions — "in practice, either
overwrite the other change or abandon their own" — so a *neutral third party*
resolves on behalf of both, "similar to the way merge queues work in engineering
teams".

Add a `merge_arbiter` agent profile whose only inputs are the two conflicting
patches and the relevant decision records (D15), and whose only goal is impartial
resolution. It must not be the author of either side. Serialise merges per
target repo — the queue property is what makes arbitration tractable.

`WorkspaceBackend.diff()` exists in D11 precisely so the arbiter's input is
backend-independent.

### Asks of rift

rift is a first-party repository, so these are actionable rather than
constraints. Four changes would make it a first-class CAO backend:

1. **Degrade instead of failing on ext4 and overlayfs.** A plain-copy or
   hardlink fallback when reflink is unavailable. This alone is what would allow
   `rift` to be considered as a default rather than an opportunistic fast path.
2. **A merge-back primitive**, or at minimum a documented and tested
   "rift as local remote" recipe with an exit code that distinguishes *conflict*
   from *error*. Without this, every consumer reimplements it slightly wrong.
3. **Stable `--json` output** on `create`, `list`, `ancestors`, `remove`, `gc`,
   so non-Node callers do not parse prose. A Python binding would be better; JSON
   CLI is sufficient.
4. **`rift diff` against the recorded parent.** rift knows the parent identifier
   and the CoW relationship, so it can compute this more cheaply than git can,
   and it is exactly the arbiter's input in D13.

Until (1) and (4) land, `worktree` carries the merge path and `rift` is an
opt-in accelerator.

---

## T2 — Model economics

Goal: make Cursor's 8x cost finding testable and then exploitable on CAO. Small
code, large payoff, **entirely dependent on T0**.

### D14 — Role-scoped model defaults

Widen custom roles from `Dict[str, List[str]]` to accept an object form:

```json
"agents": {
  "roles": {
    "supervisor": { "tools": ["@cao-mcp-server", "fs_read", "fs_list"],
                    "model": "<frontier>" },
    "worker":     { "tools": ["@builtin", "fs_*", "execute_bash"],
                    "model": "<fast>" }
  }
}
```

The list form must keep working (`utils/tool_mapping.py:82-102` returns
`list(custom_roles[role])` today, and `docs/tool-restrictions.md:51-71` documents
it). Resolution precedence becomes:

```
per-call model  >  profile model  >  role model  >  provider default
```

slotting beneath the two levels documented at `terminal_service.py:191-197`
without disturbing them.

This also needs the `agents.roles` setter noted in D9 — the section is currently
read-only from CAO's perspective.

**Why this matters beyond convenience:** `examples/orchestration/` already
contains `dev-opus.md`, `dev-sonnet.md`, `dev-kimi.md`, and `reviewer-opus.md` —
four near-identical profiles that exist *only* to pin different models. That is
the workaround for G14. Role-scoped defaults let a fleet be re-tiered in one
config line instead of a profile fork per model.

### D15 — A tiered preset and an A/B protocol

Ship a named preset (frontier supervisor, cheap worker, mid-tier reviewer) and,
more importantly, document the comparison protocol:

1. Fix the task, the repo state, and the time budget.
2. Run configuration A (homogeneous) and configuration B (tiered).
3. Compare on T0's outputs: tokens per role, wall-clock to completion, net LOC,
   retry count, review rejections, collision count.

Cursor's own data shows why the naive read is wrong: the Fable 5 planner spent
fewer planner dollars than Opus 4.8 despite ~2x the per-token price, but drove
its workers to several times the token volume and produced a more expensive run
overall. **Planner quality is a multiplier on worker cost.** A protocol that
measures only planner spend will reach the wrong conclusion, which is precisely
why D1-D5 precede this tranche.

Report tokens, and cost only where D4 produced real numbers. A tiering
recommendation backed by estimated tokens would be worse than no recommendation.

---

## T3 — Stacked review lenses

Goal: multiple decorrelated reviewers instead of one. Cheapest quality win in
the document — mostly prompts plus one MCP tool. CAO already stores the
transcripts (`log_writer.py:75`, `terminal_service.py:1273-1297`); it just has no
sanctioned way to read one.

### D16 — `get_terminal_transcript` on `cao-mcp-server`

Add a tool returning a peer terminal's transcript, capability-gated using the
same mechanism as `store_lesson` (`mcp_server/server.py:1469-1487`) — currently
the only capability-based authorisation in the MCP surface, and the natural
precedent.

Design notes:

- Must read the on-disk log or `.scrollback` snapshot, **not** `OutputMode.FULL`,
  which is capped at 32 KB of rolling buffer and is not a transcript (G13).
- Needs tail/window/`max_chars` parameters. `cao-ops-mcp`'s `read_session_output`
  already has this shape (`ops_mcp_server/server.py:371-450`) and should be the
  reference implementation.
- No HTTP route currently serves `TERMINAL_LOG_DIR`; one is needed, and it must
  carry a scope dependency. Note that the adjacent
  `GET /terminals/{id}/output` (`api/main.py:2320-2337`) is currently
  unauthenticated while its neighbours at `:2339-2343` are not — worth resolving
  in the same change.
- This *closes* a hole as much as it opens a feature. Today a reviewer with
  `fs_read` (→ `Read`, `utils/tool_mapping.py:34`) can read any peer's log
  because all agents run as the same OS user and `0o700` is no barrier. A
  gated tool plus path restriction is stricter than the status quo, not looser.

### D17 — Three reviewers, not one

| Profile | Lens | Catches |
| --- | --- | --- |
| `reviewer` | Code only (current behaviour) | Correctness, standards |
| `reviewer_transcript` | The worker's transcript via D16 | "Claimed done but didn't", skipped verification, silent scope reduction — invisible in the diff |
| `reviewer_adversarial` | Code, pinned to a *different* model via the per-call `model` override | Correlated blind spots of the primary model |

Then update the mandated cycle in `agent_store/code_supervisor.md:35-48` to stack
them. Cursor's evidence: "No single lens catches everything, but decorrelated
lenses stack ... The compute spent on review is high return, since review is much
cheaper than the work it audits."

The decorrelated-model lens is essentially free to build — the `model` override
is already plumbed end to end (`mcp_server/server.py:869-919`). The transcript
lens needs D16. The value is in the stacking, so the tranche is worth landing
whole.

---

## T4 — Stigmergy and coordination

Goal: agents shape a shared environment rather than messaging each other. CAO's
existing memory subsystem does most of this; the gaps are in contract, not
capability.

### D18a — Decision records and marker lint

Cursor's split-brain and planner-contention fixes are the same mechanism:
planners make decisions themselves, record them in shared docs, and code carries
a **compile-checked** reference back to the doc so a reconciler's resolution
propagates downstream.

CAO cannot do compile-checked references generically — it is language-agnostic.
The language-agnostic equivalent:

- A planner must `memory_store` a decision record (proposed: `project` scope,
  `reference` type) **before** delegating any subtree that depends on it.
- Workers cite it in code as a `CAO-DECISION: <key>` comment.
- A lint verifies every marker resolves to a live memory key, and flags code
  whose decision record has since been superseded. This is the propagation
  mechanism, achieved with grep rather than a type system.
- Wire `wiki_lint.py`'s **existing** contradiction detector to a new `reconciler`
  profile. This is the piece CAO is closest to already having.

Also adopt Cursor's prompting rule directly: no two sibling subtrees may decide
the same question. That is a `code_supervisor.md` edit, not code.

### D18b — A real Field Guide

Cursor's Field Guide is *dumber* than CAO's memory injection, and that is the
point: one agent-owned folder, `index.md` injected into every agent at start, a
line budget as the only constraint, agents responsible for curation.

CAO's injection (G15) is scored by recency (`memory_service.py:2614`),
first-message-only (`terminal_service.py:92-115`), and its agent-curated path
requires an IDLE `memory_manager` in the same session
(`memory_service.py:2694-2714`) and silently falls back otherwise. So in practice
most agents receive a recency-ranked slice, not a curated guide.

Add — *alongside*, not replacing — an always-injected, agent-curated,
hard-line-capped guide. Enforce the cap in code; `memory_manager.md:46-48` today
asks the agent to self-limit to 3,000 characters with nothing truncating its
reply. Existing enforced budgets to align with: `MEMORY_MAX_PER_SCOPE = 10`,
`MEMORY_SCOPE_BUDGET_CHARS = 1000` (`constants.py:481-485`), and the
`budget_chars: int = 3000` default (`memory_service.py:2555-2558`).

Also consider whether the promotion path
(`services/promotion_service.py:44-48`, triple-gated off by default at
`settings_service.py:369-390`) is the right home for durable guide content. It
already edits profile markdown under a delimited block, which is most of the
mechanism.

### D18c — Megafile flagging

Add a `flag_bloated_file` tool and a `decomposer` profile, triggered from D5's
LOC-growth and hotness data. Cursor's fix blocks new commits on a flagged file
while an outside agent splits it; CAO's equivalent under a shared cwd is to warn
and dispatch a decomposer. Under T1.4 isolation, it can be a real gate in the
merge queue.

### D18d — Licensed breakage

Zero code. A prompt convention in `agent_store/*.md`: an agent that judges a core
change worthwhile may patch outside its scope, leaving a
`CAO-BREAKING: <reason>` comment; agents that hit the resulting failure read the
comment and adapt rather than reverting. This addresses ossification —
"agents have learned, from working in existing codebases with humans in the loop,
not to touch core code even when it needs to change" — and is the cheapest item
in this document.

Caveat: CAO is language-agnostic and cannot rely on a compiler to propagate the
break the way Cursor's Rust setup does. The convention is therefore weaker here,
and its value depends on the project having a fast failing check. Worth shipping
anyway; worth not overselling.

---

## Out of scope

**A custom VCS.** Cursor built one to sustain ~1,000 commits/sec. CAO operates at
human-to-low-swarm tempo and has no such requirement. The reusable insight —
that the VCS is where collisions first become visible — is addressed by D5
(collision telemetry) and D11-D13 (isolation plus arbitration) without owning the
layer.

**The reserved YAML parallel/pipeline unit (N7).** Currently
`workflow_service.py:1240` raises `NotBuiltYetError`, tagged reserved via
`models/workflow.py:79-89` and `constants.py:551`. It is tempting to read
"swarms need fan-out" as "build N7", and that is backwards. Cursor is explicit
that their design "is a superset of more rigid orchestration systems. Rather than
imposing a fixed topology on the problem, the swarm's shape grows to cover the
problem's contours." A YAML `mode: parallel` block is a *declared, static*
topology — the fixed-topology approach their design supersedes.

CAO's `assign` tree already provides dynamic recursive decomposition; what it
lacks is bounds (T1) and measurement (T0). Investment should go there. N7 remains
correctly reserved, and the honest-tiering principle at
`workflow_service.py:16-17` should keep it that way rather than shipping a
half-answer.

**Windows support for isolation.** rift does not implement it and CAO already
requires tmux 3.3+, so mac/Linux is the existing floor.

---

## Sequencing

```
T0 (D1-D5)  ─────────────┬──> T2 (D14-D15)      # economics needs measurement
                         │
T1 (D6-D10) ─────────────┤
                         │
T1.4 (D11-D13) ──────────┴──> T4 D18c           # megafile gate needs isolation
      ▲
      └── needs D5 for hotspot input

T3 (D16-D17)  independent
T4 D18a/b/d   independent
```

Recommended order:

1. **D10** — documentation-contract fixes. Unblocked, trivial, corrects two
   shipped inaccuracies.
2. **T0** — D1, D2, D3 first (cheap, high information); D5 next; D4 last, since
   it is per-provider and open-ended.
3. **T1** — D9 with the `agents.roles` setter, then D6, D7, D8.
4. **T3** — independently valuable, and D16 also closes the transcript-access
   hole.
5. **T1.4** — the largest tranche. `shared` + `worktree` first for portability;
   `rift` once its asks (1) and (4) land.
6. **T2** — only once T0 can attribute the result.
7. **T4** — D18d anytime (prompts only); D18a/D18b after T0; D18c after T1.4.

## Breaking changes and documentation obligations

`CODEBASE.md:138-157` requires that changes to public commands, providers,
profile fields, or API route families update the owning canonical document in the
same change. Affected:

| Change | Owning doc |
| --- | --- |
| D8/D9 role defaults and unknown-role denial | `docs/tool-restrictions.md` |
| D14 `agents.roles` object form | `docs/configuration.md`, `docs/settings.md` |
| D11 workspace backends | `docs/working-directory.md`, plus a new `docs/workspace-backends.md` |
| D16 transcript tool and route | `docs/api.md`, `docs/control-planes.md` |
| D1 journal column | `docs/workflows.md` |
| D3/D4 instruments | `docs/otel-deployment.md` |
| D17/D18a/D18d profile prompts | `docs/agent-profile.md` |

D8 (no-role default moves from `developer` to `worker`) and D9 (unknown role
denies) are the two behaviour changes to existing defaults and need explicit
deprecation notes.

## Open questions

- **Q1** — Should `developer` retain `@cao-mcp-server`? Removing it is the
  faithful reading of "a worker never plans" and prevents split-brain by
  construction, but breaks existing delegating profiles. D8 recommends adding
  `worker` instead; that is a judgement call, not a settled one.
- **Q2** — What is the right default for `CAO_MAX_AGENT_DEPTH`? 3 is proposed on
  the reasoning that supervisor → planner → worker covers the observed
  topologies, but there is no data. T0 would supply it.
- **Q3** — Should orchestration tools be separable from memory tools? Today both
  live on `cao-mcp-server`, so a role denied orchestration also loses
  `memory_store`/`memory_recall`. Options: split the server, or extend the
  per-tool capability gate at `mcp_server/server.py:1469-1487`. This blocks a
  clean D8.
- **Q4** — Should D4 ship dollar figures at all, or tokens only? Recommendation
  is tokens plus an optional operator-supplied price map; a bundled price table
  goes stale and a stale price is worse than none.
- **Q5** — Does the Field Guide (D18b) belong in the memory wiki, in the
  promotion path, or as a standalone file? All three are defensible; the memory
  wiki brings scoping and linting, the promotion path already edits delimited
  blocks in profile markdown, and a standalone file is closest to Cursor's
  design and the easiest to reason about.
- **Q6** — For `rift`, is a Python binding worth requesting, or is a stable
  `--json` CLI contract sufficient? CAO already shells out to tmux, so the CLI is
  probably enough, but the FFI path is the one rift treats as primary.

## References

- Cursor, "Agent swarms and the new model economics" —
  <https://cursor.com/blog/agent-swarm-model-economics>
- Cursor, minisqlite (the Opus 4.8 solo run's output) —
  <https://github.com/cursor/minisqlite>
- rift — <https://github.com/anomalyco/rift>
- CAO internals: `docs/event-driven-architecture.md`,
  `docs/terminal-lifecycle.md`, `docs/tool-restrictions.md`,
  `docs/working-directory.md`, `docs/memory.md`, `docs/self-learning.md`,
  `docs/workflows.md`, `docs/control-planes.md`
- Prior design-doc convention: `docs/issues/345-okf-export-import/design.md`
