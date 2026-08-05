# Workspace backends (D11)

CAO isolates concurrent workers via a pluggable **workspace backend** family,
mirroring terminal backends. Canonical design:
[`docs/issues/swarm-economics/design.md`](issues/swarm-economics/design.md)
**T1.4 / D11–D13**. Merge arbitration (**D13**) is **out of scope** — this
document covers isolation, selection, and safe cleanup only.

## Backends

| Backend | Role |
| --- | --- |
| `shared` | Current behaviour — inherit the supervisor/source cwd. **Shipped default.** |
| `worktree` | Portable git worktree + branch per worker (merge substrate for a future D13). |
| `rift` | Reserved/deferred CoW accelerator. Explicit `workspace=rift` errors; `auto` skips it until a real backend exists. |
| `auto` | Probe: rift (if available) → worktree → **loud** shared fallback. |

## Selection precedence

Highest wins:

1. Explicit `workspace=` on MCP `assign` / `handoff`, or HTTP create / `POST /terminals/run-step` body
2. `CAO_WORKSPACE_BACKEND` environment variable
3. Built-in default: `shared`

No environment change is required for existing deployments — `shared` remains
default and preserves current cwd behaviour.

```bash
export CAO_WORKSPACE_BACKEND=worktree   # or auto|shared|rift
```

## Worktree constraints (committed-ref)

`worktree` creates from **`HEAD`'s commit** (or equivalent committed ref) only.
Dirty uncommitted / index state in the source checkout is **never** copied into
the worker workspace. If workers must see local edits, commit or stash first,
or stay on `shared`.

Each worker gets:

- a unique branch `cao/ws-<name>`
- a checkout under `CAO_HOME_DIR/workspaces/<name>`
- that path as the real provider/terminal working directory (**D12**: provider
  memory files such as `AGENTS.md` / `.claude/CLAUDE.md` become per-workspace)

## Cleanup policy

On `delete_terminal` and successful handoff teardown:

| Worktree state | Action |
| --- | --- |
| Clean (commits on retained branch) | `git worktree remove` **without** `--force`; **branch kept** for manual merge |
| Dirty / untracked | **Preserve** the worktree; return/log `preserved_dirty` with actionable guidance |
| Already removed | Idempotent `noop` |

Never silently destroy unmerged work. Never `git worktree remove --force`.

### Manual merge (no arbiter)

After a clean remove, merge the retained branch yourself:

```bash
git -C <source_repo> merge <workspace_branch>
# or inspect first:
git -C <source_repo> log <workspace_base_ref>..<workspace_branch>
git -C <source_repo> diff <workspace_base_ref>...<workspace_branch>
```

If cleanup returned `preserved_dirty`:

```bash
# inspect / commit inside the preserved worktree, then:
git -C <source_repo> worktree remove <workspace_path>
git -C <source_repo> merge <workspace_branch>
```

Lifecycle metadata (backend/path/branch/base/cleanup) is written to
`TERMINAL_LOG_DIR/<terminal_id>.workspace.json` so it survives DB delete and
appears on assign receipts, inbox queued-start notifications, delete responses,
and `HandoffResult` (nullable fields for back-compat).

## MCP / HTTP surfaces

- MCP: `assign(..., workspace=...)`, `handoff(..., workspace=...)` on **all**
  feature-flag variants; wave queue payloads preserve `workspace` (Item 6).
- HTTP: `CreateTerminalBody.workspace`, `RunStepRequest.workspace`;
  `DELETE /terminals/{id}` returns workspace cleanup fields when present.

## Related

- [Working directory](working-directory.md) — optional `working_directory` param
  (`CAO_ENABLE_WORKING_DIRECTORY`); orthogonal to backend selection
- [Configuration](configuration.md) — `CAO_WORKSPACE_BACKEND`
