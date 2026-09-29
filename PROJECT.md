# Project Definition

## 1. Project Identity

**Project name:** Local Memory Lab (working name; repo `.tool-user`)

**Short description:** A local agent team that runs on Ollama models on your own machine and does real project work (plan, implement, debug, review) while a human, and optionally a supervising agent, watch the same live session and approve its changes.

**Purpose:** Move the expensive inference onto free local models. Paid agents and people supervise and approve; they don't do the work. The long-term aim is an agent setup anyone can run without a subscription.

## 2. Intended User

**Primary user:** The project owner, working on their own projects on one Windows machine (RTX 5060 Ti 16 GB, 32 GB RAM).

**Secondary participant:** A supervising agent (Claude Code, Codex) that reads the session and posts prompts or reviews through the existing client. It is an observer and escalation point, not the worker.

**Later:** Members of the public running it on similar consumer hardware.

## 3. Problem / Need

Agentic coding today depends on paid frontier models. Small local models can't run long open-ended agent loops reliably, but they can handle narrow, well-specified, verifiable steps. The need is a harness that breaks the work into those steps, feeds each one the right context, and checks the results, so that local models get useful work done.

## 4. Intended Product

One local hub process with a browser view and a CLI/agent client. Both are views of the same session state. You pick a project and give it a goal. A team of local models works through the goal in visible stages:

- **Foreground roles:** planner, builder, debugger, reviewer.
- **Background workers:** keep an index of the project so every step gets the right context.
- **Approval:** deterministic checks plus a human decide what is applied.

Every stage, tool call, approval and result appears in one shared event log.

## 5. Primary Capabilities

- Select a project and hold one shared conversation in the browser and CLI, with the same state in both.
- Use separate **Chat** and **New goal** entrances. A goal becomes a reviewable plan of small tasks.
- For each task, local models write changes, run the project's allowlisted checks, fix failures, and review the diff.
- The human approves the plan and each change. Nothing is applied without approval.
- Background workers keep a keyword and embedding index, a code graph and model-free summaries up to date, and assemble a bounded context for every step.
- History, notes, plans and job state survive a restart.
- A bench measures which local model handles which role, and how well.

## 6. Product Boundaries

### In Scope (this prototype)

See `PLAN.md` §3, the target end state and stop conditions.

### Explicitly Out of Scope

See `PLAN.md` §4, which covers what we are *not* building. It is frozen once the plan is approved (PLAN.md D6).

### Deferred / Maybe Later

Listed in `PLAN.md` §4 under "Deferred".

## 7. Project-Specific Constraints

- **Isolation (strangler-fig):** the user's other projects are references only. Their ideas and code may be copied or rewritten into this repo, but this project never imports, calls or depends on them, and never touches them.
- **Local only:** models through Ollama at `127.0.0.1`. No paid or cloud model APIs in the working path. No web access.
- **Python 3.10+:** standard library plus **numpy** (decided 2026-09-29). No other third-party dependencies without a recorded decision.
- **Models:**
  - Foreground roles use installed Qwen models up to 14B, plus `qwen3.5:35b` (MoE). Current assignments are in `PLAN.md` D4.
  - Embedders, summarizers and models under 0.5B may be any family (currently `nomic-embed-text`, `mxbai-embed-large`, `all-minilm`, `phi3:mini-128k`).
- **Hardware:** everything must run on a 16 GB GPU with 32 GB of RAM. Background work must not starve foreground work.
- **Writes:** every change to a project's files passes a human approval. Backups are kept outside the project.
- **Simplicity:** the codebase gets smaller or stays flat wherever that costs no function, clarity or constraint.

## 8. User Experience Expectations

- Local-first. One command or shortcut starts the hub.
- A dark browser UI shows the conversation, job stages, approvals and diffs. A CLI client gives agents the same view.
- Waiting, running, failed and approved are always visibly different.

## 9. Completion Condition

The prototype is complete when **the agent can make progress on its own development**. Working on a sandboxed copy of this repo, never its own running code, the local team turns at least one pre-registered goal from its own backlog into a tested change that the human approves and merges, without breaking anything. Local models do all of the inference, and both the human and a supervising agent can watch every step. Exact stop conditions are in `PLAN.md` §3 (S8) and decision D8.

## 10. Known Unknowns

- How well each installed model performs in each role. The bench answers this.
- How much the background context layer lifts builder success. This is the main experiment.

## Current Decision

**Definition status:** DEFINED (2026-09-29, accepted together with `PLAN.md`).

**Next action:** follow `PLAN.md` §7–§8.
