# Agents: Start Here

This file is the entry point for anyone working on this repo: the USER, any AGENT, or, later, this project's own local team working on a self-development copy. Participants are known only by the labels USER, AGENT, ROLE and SYSTEM (`PLAN.md` D10); rights attach to the label (`docs/CONTRACTS.md` §0). Nothing here assumes a particular vendor or tool. If your agent auto-loads a different file name, point it here.

## What this is

Local Memory Lab is a local agent team that runs on Ollama models. A shared hub (browser plus CLI) lets the USER watch it work and approve its changes, and lets any AGENT follow and advise through the CLI. The prototype is **done** when the team makes real progress on its own development in a sandboxed copy of this repo (`PLAN.md` D8).

## Orient cheaply (in this order, and stop when you have enough)

1. `PLAN.md` **§8 Current Decision**: plan status and what is permitted right now.
2. `PLAN.md` **§7 Current Tranche**: the active tranche, its scope, non-goals and acceptance criteria.
3. `git log --oneline -15`, `git branch` and `git status --short`. Tranche commits are named `T<n>: …` and built on a `t<n>-<slug>` branch. `main` holds the last accepted state (`docs/WORKFLOW.md`).
4. `PLAN.md` **§9 Parked Tranches**, the newest entry: what was proven, with what evidence, and what is next.
5. Only if the work touches them: `PLAN.md` §2 (decisions), §3 (stop conditions), §4 (not building), §6 (reference map), and `docs/CONTRACTS.md`.

**Review with the USER:** compare the newest §9 entry with `git diff <previous T-commit>..HEAD --stat`, then run the tests. Anything claimed in §9 but not shown in the diff or tests is a finding.

## First session (for an AGENT joining the team)

1. Orient with the steps above, then read `docs/WORKFLOW.md` in full. It's short.
2. Check the known state yourself: `python -B -m unittest discover -s tests`. At the T0 baseline this had one import error (`PLAN.md` §1, finding 8); use the current tranche record for the expected state.
3. If a tranche is parked pending USER acceptance, review that park first. Once accepted, declare the next tranche from its draft, following "Working as a team" in `docs/WORKFLOW.md`. Present it to the USER and wait for approval before changing any code.
4. Immediately after approval, record `Implementation permission: YES for T<n> (USER, <date>)` in §8 before touching code. During implementation keep the one-line-per-scope-task `Progress:` checklist and short `Now:` line in §7 current; commit each completed task on its tranche branch as `T<n> wip: <task>` with the `Actor: AGENT` trailer. Reserve `T<n>: <outcome>` for the parking commit.

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

- **Orient before proposing. Propose before implementing.** Implement only a tranche the USER has approved (`PLAN.md` §8). Plan *with* the USER.
- **Isolation (D1).** The USER's other projects (the `.parts-bin` folder next to this repo, DataMODEL, and others; see `PLAN.md` §6) are references only. Read them and rewrite what we need into this repo, smaller. Never import, call, run, attach or modify them.
- **Scope guard (D6).** The stop conditions (`PLAN.md` §3) and the not-building list (§4) are frozen. A new idea goes into §4 "Deferred" and nowhere else.
- **Smaller wherever it's free.** Prefer deleting to adding. No module over about 400 lines. No dead code, and no second way to do the same thing.
- **Dependencies:** Python 3.10+ standard library plus numpy. Nothing else without a recorded decision.
- **Writes to a project always pass the USER's approval.** The hub never edits its own running code. Self-development happens only in the selfdev worktree (D8).
- **Vendor-neutral and anonymous (D9).** Never name an agent product, vendor or person in docs, code, labels, prompts or commits. Agent commits end with `Actor: AGENT`.
- **Proof over vibes.** A claim of done names the command that shows it.

## Layout

```
lab.py                      thin entry point: hub-open | hub-server | client
src/local_memory_lab/
  session.py                shared session coordinator; event-backed projections live in session_state.py
  event_store.py            append-only SQLite event log and cursor reads
  session_state.py          restored event-backed domain projections
  lifecycles.py             job projection and job/task transition rules
  approvals.py              approval state projection
  command_runner.py         USER-approved named command execution
  locations.py              repo-relative paths
  workspace/                 safe project paths, patches, and backups
  agent/                    Ollama loop and bounded project tools (loop replaced in T6)
  interfaces/               browser server + page, launcher, CLI client (adapters only)
tests/                      unittest suite
docs/                       standing framework and contracts
live_control/               runtime folder (gitignored contents)
```

If a `_projectmapper/` folder exists locally, it is a stale, gitignored snapshot made before T0. It is not a source of truth.

## Commands

- **Tests:** `python -B -m unittest discover -s tests -v`
- **Hub:** `python lab.py hub-server`, then open the browser link written to `live_control/shared.json`. On the owner's machine you can instead double-click `Open Shared Hub.lnk`, whose target paths are machine-specific.
- **Agent client:** `python lab.py client status`, `python lab.py client watch`, `python lab.py client send "…" --wait`
- **Models:** Ollama at `127.0.0.1:11434`. Role assignments are in `PLAN.md` D4.

> **T3 accepted on `main`:** the work and USER acceptance record are in `PLAN.md` §§7–9. No tranche is active on `main`; T4 is next in §5.
