# Working Directory Support

CAO supports specifying working directories for agent handoff/delegation operations.

Workspace **isolation** (per-worker git worktrees / future CoW) is a separate
surface — see [Workspace backends](workspace-backends.md) (swarm-economics
**T1.4 / D11–D13**). `working_directory` chooses *which path* to start from;
`workspace=` chooses *whether* that path is shared or isolated.

## Configuration

Enable working directory parameter in MCP tools:

```bash
export CAO_ENABLE_WORKING_DIRECTORY=true
```

Workspace backend (independent; default `shared` — no behaviour change):

```bash
export CAO_WORKSPACE_BACKEND=shared   # or worktree|auto|rift
```

Selection precedence for workspace backends: explicit `workspace=` on
assign/handoff/HTTP > `CAO_WORKSPACE_BACKEND` > `shared`. See
[workspace-backends.md](workspace-backends.md).

## Behavior

- **When disabled (default)**: Working directory parameter is hidden from tools, agents start in supervisor's current directory
- **When enabled**: Tools expose `working_directory` parameter, allowing explicit directory specification
- **Default directory**: Current working directory (`cwd`) of the supervisor agent
- **With `workspace=worktree`**: CAO creates a branch-isolated worktree from a
  **committed** ref and uses that path as the provider/terminal cwd so parallel
  workers cannot silently overwrite the same tracked files (**D11/D12**)

## Usage Example

With `CAO_ENABLE_WORKING_DIRECTORY=true`:

```python
# Handoff to agent in specific package directory
result = await handoff(
    agent_profile="developer",
    message="Fix the bug in UserService.java",
    working_directory="/workspace/src/MyPackage"
)

# Assign task with specific working directory
result = await assign(
    agent_profile="reviewer",
    message="Review the changes in the authentication module",
    working_directory="/workspace/src/AuthModule"
)

# Parallel implementers — isolate with worktrees (D11)
result = await assign(
    agent_profile="knight",
    message="Implement feature A",
    workspace="worktree",
)
```

## Path Validation and Security

All working directory paths are canonicalized and validated before use. Paths are resolved via `os.path.realpath` to normalize symlinks and `..` sequences.

### Allowed directories

- The user's home directory and any subdirectory (`~/projects/foo`)
- External volumes and mount points (e.g., `/Volumes/workplace/project`)
- Custom paths like `/opt/projects`, NFS mounts, corporate dev desktops
- Any real directory that is **not** a blocked system path

### Blocked (unsafe) directories

The following system directories are explicitly blocked:

`/`, `/bin`, `/sbin`, `/usr/bin`, `/usr/sbin`, `/etc`, `/var`, `/tmp`, `/dev`, `/proc`, `/sys`, `/root`, `/boot`, `/lib`, `/lib64`

On macOS, `/private/etc`, `/private/var`, and `/private/tmp` are also blocked (since `/etc` -> `/private/etc`, etc.).

### Symlink handling

Symlinks are resolved at validation time. A symlink pointing to a blocked system path (e.g., `~/escape` -> `/etc`) is rejected after resolution.

## Why Disabled by Default?

When the `working_directory` parameter is visible to agents, they may hallucinate or incorrectly infer directory paths instead of using the default (current working directory). Disabling by default prevents this behavior for users who don't need explicit directory control. If your workflow requires delegating tasks to specific directories, enable this feature and provide explicit paths in your agent instructions.

Similarly, `workspace=shared` remains the shipped default because isolation
changes where agents write files — a large behavioural change that deserves an
opt-in period (**D11**).
