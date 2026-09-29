# Agents: Start Here

This file is the entry point for any agent or person working on this repo: Claude Code, Codex, or, later, this project's own local team working on a self-development copy.

## What this is

Local Memory Lab is a local agent team that runs on Ollama models. A shared hub (browser plus CLI) lets a human and a supervising agent watch it work and approve its changes. The prototype is **done** when the team makes real progress on its own development in a sandboxed copy of this repo (`PLAN.md` D8).

## Orient cheaply (in this order, and stop when you have enough)

1. `PLAN.md` **§8 Current Decision**: plan status and what is permitted right now.
2. `PLAN.md` **§7 Current Tranche**: the active tranche, its scope, non-goals and acceptance criteria.
3. `git log --oneline -15` and `git status --short`. Tranche commits are named `T<n>: …`.
4. `PLAN.md` **§9 Parked Tranches**, the newest entry: what was proven, with what evidence, and what is next.
5. Only if the work touches them: `PLAN.md` §2 (decisions), §3 (stop conditions), §4 (not building), §6 (reference map), and `docs/CONTRACTS.md`.

**Review with the user:** compare the newest §9 entry with `git diff <previous T-commit>..HEAD --stat`, then run the tests. Anything claimed in §9 but not shown in the diff or tests is a finding.

## Standing documents (read the relevant section in full when a decision touches it)

| Document | Holds |
|---|---|
| `PROJECT.md` | What we are building, for whom, constraints, completion condition |
| `PLAN.md` | Current state, decisions, stop conditions, non-goals, tranches, reference map, the tranche record |
| `docs/WORKFLOW.md` | The tranche cycle, the parking bar and where records go |
| `docs/ARCHITECTURE.md` | Ownership hierarchy and call direction (see its closing **Core Constraint**) |
| `docs/DESIGN-PRINCIPLES.md` | One application, many participants; one reality; visible lifecycles (see its **Design Test**) |
| `docs/CONTRACTS.md` | Data shapes shared across tranches: events, jobs, approvals, roles, context packs, bench tasks, allowlist |

## Rules that are never negotiable

- **Orient before proposing. Propose before implementing.** Implement only a tranche the user has approved (`PLAN.md` §8). Plan *with* the user.
- **Isolation (D1).** The user's other projects (`C:\Jacob\_AppDesign\_SANDBOX\.parts-bin\`, DataMODEL, and others) are references only. Read them and rewrite what we need into this repo, smaller. Never import, call, run, attach or modify them.
- **Scope guard (D6).** The stop conditions (`PLAN.md` §3) and the not-building list (§4) are frozen. A new idea goes into §4 "Deferred" and nowhere else.
- **Smaller wherever it's free.** Prefer deleting to adding. No module over about 400 lines. No dead code, and no second way to do the same thing.
- **Dependencies:** Python 3.10+ standard library plus numpy. Nothing else without a recorded decision.
- **Writes to a project always pass a human approval.** The hub never edits its own running code. Self-development happens only in the selfdev worktree (D8).
- **Proof over vibes.** A claim of done names the command that shows it.

## Layout

```
lab.py                      thin entry point: hub-open | hub-server | client (plus desktop/live-desktop until T1)
src/local_memory_lab/
  session.py                shared session state (split into domain owners in T2)
  locations.py              repo-relative paths
  agent/                    Ollama loop and bounded tools (tools rewritten in T1; loop replaced in T6)
  interfaces/               browser server + page, launcher, CLI client (adapters only)
  legacy/                   old Tk experiment; deleted in T1
tests/                      unittest suite
docs/                       standing framework and contracts
files/, live_control/       runtime folders (gitignored contents)
```

## Commands

- **Tests:** `python -B -m unittest discover -s tests -v`
- **Hub:** `python lab.py hub-server`, then open the browser link written to `live_control/shared.json`. Or double-click `Open Shared Hub.lnk`.
- **Agent client:** `python lab.py client status`, `python lab.py client watch`, `python lab.py client send "…" --wait`
- **Models:** Ollama at `127.0.0.1:11434`. Role assignments are in `PLAN.md` D4.

> **Known state (T0):** the hub and tests currently fail on import, because the old `.parts-bin` code moved out of the repo. T1 fixes this. See `PLAN.md` §1, finding 8.
