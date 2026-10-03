# Project Plan

Product intent is in `PROJECT.md`. This file holds the route: current state, decisions, stop conditions, non-goals, tranches, reference map, the current tranche and decision, and the record of parked tranches (§9).

## 1. Observed state at the start of T0 (2026-09-29)

This section is a snapshot and is not rewritten; later changes are recorded in §9.

One commit (`ff457af`). Its 3 unit tests passed until `.parts-bin/` moved out; they now error on import (finding 8). Python 3.13.6, numpy 2.3.3 already installed, Ollama 0.18.3. Remote: `github.com/jacobmentalconstruct/.tool-user`.

**What works:**

- **Shared hub:** one process on 127.0.0.1. The browser (the approving token) and the CLI client (agent token) see one queue, one conversation and one Ollama session.
- **Tools:** sandbox file tools in `files/`, and project tools (`list_project`, `read_project_file`, `create_project_file`) with exclusion and link checks.
- **Patches:** reviewed single-file and multi-file patches with a diff shown for approval, backups, and rollback.
- **Memory:** notes and an 8-turn window, both lost on restart.

**Size:**

| Area | Lines |
|---|---|
| `src` Python | 1,512 |
| `shared_ui.html` | 181 |
| Tests | 94 |
| Legacy Tk desktop app (inside `src`) | 338 |
| Parts-bin code the hub loaded at runtime (static import closure) | 1,490 |

**Findings:**

1. **A fresh clone does not run.** `project_tools.py` and `patch_tools.py` import `core.*` and `tools.*` from `.parts-bin/`, which is gitignored and has no committed files. `core/__init__.py` also pulls in `diagnostics`, `state` and `tree`, which the app never uses.
2. **The worker is a free-running tool loop.** `engine.run_turn` gives the model every tool for 6 rounds with no streaming and a 180 s timeout. Small models are weakest in exactly this setup. The engine also hard-codes patch and overwrite denial rules, logic that belongs to the tools.
3. **`SharedSession` owns too much.** It holds the queue, conversation, notes, project choice, model choice, approvals and event list in one class. Contrary to `ARCHITECTURE.md`, there is no separate owner per domain.
4. **All state is in memory.** Events are capped at 500 and there is no restart recovery. Approvals block the only worker thread for up to 300 s. Every agent message carries one hard-coded vendor name as its speaker label. The browser polls the full state every 750 ms.
5. **Duplication and dead code:**
   - Two file-tool families: sandbox `files/` and project.
   - The client's compatibility path for hubs that predate request IDs; current hubs always return one.
   - The legacy Tk app, 338 lines.
   - `launcher.py` imports tkinter only to show an error box.
6. **Tests write into the real `files/` and `live_control/` folders.** Leftovers exist (`files/python_dir_smoke/`).
7. **Framework documents were only partly adopted, and the README was stale** (it said "complete and parked"). Both were resolved in T0 (§9).
8. **The hub is currently broken (2026-09-29).** The user moved `.parts-bin/` out of the repo into `_SANDBOX/.parts-bin/`, which simplifies the boundary. `locations.PARTS_BIN` still points inside the repo, so the patch and project tool imports fail. The hub can't start, and the test suite errors on import. T1 fixes this by rewriting those pieces into our own code; we will not re-point the path (D1).
9. **Reference material** (per D1, never imported): `_SANDBOX/.parts-bin/` and the DataMODEL skeleton. See §6 for what each is useful for.

**Model measurements (T0, 2026-09-29).** Ollama 0.18.3, RTX 5060 Ti 16 GB. One 200-token code generation at temperature 0; generation speed from Ollama's own `eval` timings. Throwaway script, not kept in the repo.

| Model | Load s | Gen tok/s | JSON schema `format` | `think` | VRAM / total GB |
|---|---|---|---|---|---|
| `qwen3.5:2b` | 5.6 | 132.0 | ok | yes | 4.0 / 4.0 |
| `qwen3.5:9b` | 5.4 | 61.3 (62 at 16k and 32k ctx) | ok | yes | 8.2 / 8.2 (9.2 at 32k) |
| `qwen2.5-coder:14b` | 11.5 | 6.7 (4.8 at 16k; 32k fails) | ok | no | 13.3 / 26.4 |
| `ms-ae:latest` (qwen2 14.8B) | 7.5 | 3.9 | ok | no | 13.3 / 26.4 |
| `qwen3.5:35b` (MoE) | 14.5 | 13.5 (12.5 at 32k ctx) | ok | yes | 13.8 / 25.1 |
| `nomic-embed-text` | — | 32 chunks in 2.28 s | — | — | — |

**Re-measured 2026-10-02 (T6 task 1).** The USER set `OLLAMA_NUM_PARALLEL=1` as a user-level variable; the system-level value stays 8. With it, `qwen2.5-coder:14b` at a 16k context uses 12.3 GiB, all on the GPU, at 44.1 tokens/s (load 7.2 s). `qwen3.5:35b` ignores parallel slots (Ollama logs that its architecture does not support them); its weights alone are about 22 GiB, so it never fits the 16 GB GPU and ran 45%/55% CPU/GPU at about 11 tokens/s in the T6 role probe. `phi3:mini-128k` at `OLLAMA_NUM_PARALLEL=8` used 16.0 GiB at a 4k context and 30.0 GiB at 8k, spilling both times. Ollama 0.18.3 (`AppData\Local\Programs\Ollama`) remains the measured runtime; a second install at `C:\Ollama` (0.24.0) is not used.

**Likely reason the qwen2 models are slow** (inferred from the numbers; not checked by changing the setting): the machine sets `OLLAMA_NUM_PARALLEL=8` and `OLLAMA_MAX_LOADED_MODELS=3`. For full-attention models (the qwen2 family), Ollama reserves conversation memory (KV cache) for 8 parallel requests, which pushes a 9 GB model to about 26 GB and half onto the CPU. The qwen3.5 models' hybrid attention keeps that memory small, so they aren't affected. This is a machine-wide setting the user may rely on elsewhere, so the plan works around it (D4) instead of changing it.

## 2. Decisions

All recorded 2026-09-29.

- **D0 Dependencies:** Python standard library plus numpy. Nothing else without a new decision.
- **D1 Strangler-fig isolation:** the user's other projects (`_SANDBOX/.parts-bin/`, DataMODEL and its `.tools`, and any others) are **references only**. We may copy, translate or rewrite any part of them into this repo, where it becomes this project's own code, and it should come out smaller and cleaner than the source. We never import from them, call them, attach them, run them, depend on them, or modify them. This project owns its own tool layer, command runner and agent loop.
- **D2 Tight scope:** the prototype proves one thing: *local models, given good context and deterministic checks, turn a goal into approved, tested changes.* Anything that doesn't serve that proof is deferred (§4). This covers the conversation-level background workers (extractor, fact graph, memory curator), the intent router and the rolling summarizer. Routing is explicit instead: the browser and CLI each have **Chat** and **New goal** entrances.
- **D3 Bench tasks come from real code.**
  - Tasks are made by "hole-punching": remove a function body that existing tests cover, describe the goal in words, and let the tests decide success. The files touched are the known answer for scoring search quality.
  - **Primary source: this repo itself**, snapshotted at the start of T5, when T1–T4 have given it real tests. That makes the bench a rehearsal for self-development (D8).
  - **Fallback source, used only if the repo yields fewer than 15 tasks:** a trimmed copy of `_SANDBOX/_ProjectMAPPER` (standard library only, 28 test files) in `bench/fixtures/`, checked in isolation at that point.
  - Synthetic tasks are allowed only when a specific behaviour can't be tested any other way, and each one needs a reason in the task file.
- **D4 Model assignments** (set from the T0 measurements in §1; T5's bench confirms or changes them in `roles.json`; builder protocol amended 2026-10-01 from the T5 probe):

  | Use | Model | Why |
  |---|---|---|
  | Builder | `qwen3.5:9b` | Fits entirely on the GPU even with a 32k context and generates 62 tokens/s. T5's controlled probe found JSON-schema output valid with thinking enabled and invalid with thinking disabled; the builder uses thinking on and a 4,096-token output cap. |
  | Debugger | `qwen3.5:9b` | Same 9B model as the builder; T6 will validate its role-specific output protocol. |
  | Planner | `qwen2.5-coder:14b`, thinking off | Chosen 2026-10-02 (T6 task 2). With the fixed prompt (fewest tasks; no test tasks unless asked; use the only check) it planned 15 of 22 one-function goals and 3 of 4 real two-function changes exactly, about 14 s each, with no thinking loops. qwen3.5:9b managed 10 of 22 and 1 of 4, with 8 cap-outs; qwen3.5:35b spills onto the CPU (195 s per goal) and is dropped. Recorded experiments (`bench/experiments/2026-10-02-planner-and-builder.jsonl`; 26 goals, every third held back): temperature 0 gave 14/18 tune and 6/8 holdout; 0.2 gave 13 and 6; adding a validation retry gave 13 and 5, because the extra prompt sentence changed first answers; best of 3 at 0.7 gave 15 and 6, no holdout gain at 3 times the calls. Adopted: temperature 0, single call |
  | Reviewer | `qwen2.5-coder:14b`, thinking off | Chosen by the T6 role probe (2026-10-02): fully on the GPU at `OLLAMA_NUM_PARALLEL=1`, about 3 s per card, no budget loops. qwen3.5:9b with thinking passed clean cards but caught 1 of 10 plants and hit its token cap on 6 of 20 cards |
  | Embedder | `nomic-embed-text` | 768 dimensions; 32 chunks in 2.3 s including load |
  | Cheap helper jobs | `qwen3.5:2b` | 132 tokens/s. Used only if a tranche needs it |

  - `qwen2.5-coder:14b` and `ms-ae:latest` stay bench candidates only. They are slow here because of the machine-wide `OLLAMA_NUM_PARALLEL=8` (§1). T5 re-measures them only if the user lowers that setting.
  - Role steps run one at a time, so the 9B and the 35B take turns in VRAM, costing about 5–15 s per swap. Jobs group steps by model where the lifecycle allows.
  - **Amended 2026-10-02 (T6 task 1, USER):** with `OLLAMA_NUM_PARALLEL=1` (§1), `qwen2.5-coder:14b` fits fully on the GPU and joins the reviewer comparison with thinking off. `qwen3.5:35b` is dropped from the reviewer comparison because it can never be fully GPU-resident here; the planner model is measured in task 2 (9b against the 14b coder). Before each role call, code unloads other models and records the share of the model held on the GPU, so a CPU-spilled call is visible.

- **D5 Names stay** as Local Memory Lab / `local_memory_lab` / repo `.tool-user` until T7. Renaming is optional at the end and never mid-build.
- **D6 Scope guard:**
  - Once this plan is APPROVED, the stop conditions (§3) and the not-building list (§4) are frozen. A new idea goes into §4 "Deferred", never into a tranche.
  - Changing §3 or §4 needs the user's explicit decision, recorded here.
  - Each tranche is small (about one working session), ends with the tests passing and the hub working, and is parked before the next one is declared.
- **D7 Document layout:**
  - `AGENTS.md` (the vendor-neutral start-here file, for any USER or AGENT) at the root. No vendor-specific instruction files (D9).
  - The standing framework adapted for this project in `docs/`: `ARCHITECTURE.md`, `DESIGN-PRINCIPLES.md`, `WORKFLOW.md`.
  - The shapes shared across tranches in `docs/CONTRACTS.md`.
  - `PROJECT.md` and `PLAN.md` at the root.

- **D8 Done means the agent makes progress on itself** (the user's criterion, in its simplest form: *progress rather than decline*):
  - **Where:** in a separate git worktree of this repo (`_SANDBOX/.tool-user-selfdev`, on a `selfdev/<goal>` branch). The running hub never edits its own running code.
  - **Pre-registered goals:** at the end of T6, three small goals from this plan's own backlog are written into §7 **before any attempt**. They can't be swapped after an attempt.
  - **Rules:**
    - Goals go in through **New goal**.
    - Only local models do the inference.
    - The USER approves or rejects in the hub, and may re-run a goal, but must not hand-edit the agent's changes.
    - Any other agent may only advise through chat.
  - **Progress, not decline:** a goal passes when its branch does what the goal asked, the full test suite and `tests/test_architecture.py` still pass, and the branch is merged into `main` through a normal review, with `selfdev` credited in the commit message.

- **D9 Vendor-neutral and anonymous (2026-09-29):**
  - Docs, code, labels, prompts and commit messages never name a particular agent product, vendor or person.
  - Participants are identified only by abstract labels (D10).
  - Commits made by an agent end with the trailer `Actor: AGENT` instead of any name.
  - Any agent should feel equally at home here, including this project's own team when it works on itself.
  - Naming the runtime (Ollama) and model tags in configuration is fine: those are dependencies, not participants. Legal notices (`LICENSE.md`) keep the copyright holder's name as the law requires.
- **D10 Participants and rights (2026-09-29):** there are four labels. Rights attach to the **label**, not to whoever holds it, so a USER could later be an agent. That would need its own decision, and is deferred.
  - **USER** (`user`) holds approval authority. Today this is the project owner, using the browser token.
  - **AGENT** (`agent`) is any other participant, through the CLI token.
  - **ROLE** (`role:<planner|builder|debugger|reviewer>`) is one of the local team's steps.
  - **SYSTEM** (`system`) is the hub's own lifecycle code.
  - In these documents, "the user" means the USER.
- **D11 Setup and development are separate phases (2026-09-29):**
  - **T0, setup:** done by the USER with one planning AGENT. Its parking is that phase's stop condition.
  - **From T1 on:** the development team (any AGENTs, and later the project's own ROLEs) orients from the docs alone, declares each tranche, implements it after the USER approves, and parks it.
  - The USER and any advising AGENT review, discuss strategy and give input through chat and the hub. They don't implement unless they declare a tranche themselves.
  - The rights table is in `docs/CONTRACTS.md` §0. The code enforces it from T2 on, when the event log introduces actors. Until then, the hub's two tokens map to USER and AGENT.
- **D12 T3 event payloads (2026-09-29):** T3 uses the existing `job.state`, `task.state`, `approval.requested`, `approval.resolved` and `command.result` kinds; it adds no kind or top-level event field. `job.state` carries `data.state`, the initial `data.goal`, and `data.reason` on failure/cancellation. Approval events carry the §3 approval identity, kind and state; T2's older patch approval events remain readable. `command.result` carries the §4 result fields. This records the event-data schema introduced by T3 before implementation; any later shape change needs another decision.
- **D13 Goal-submission provenance (2026-09-29):** the first `job.state` event also carries `data.submittedBy` (`user` or `agent`) and optional `data.display` text for the shared timeline. SYSTEM remains the event actor because it owns job-state transitions (§0); `submittedBy` records who requested the goal. No new event kind or top-level field is added.
- **D14 Command outcome metadata (2026-09-29):** `command.result` keeps the required §4 fields (`name`, `exit_code`, `duration_s`, `output`) and adds `data.status` (`ok`, `failed`, `timeout`, or `cancelled`) so a timeout or USER cancellation is distinguishable from a command's nonzero exit. `exit_code` is `-1` when the process was stopped before it returned an exit code. No top-level event field or event kind changes.
- **D15 Command output tail (2026-09-29):** §4's "trimmed from the end" is interpreted as retaining the final output bytes, because test summaries appear at the end. `command.result.data.output` includes a leading `[… N bytes trimmed]` line when capped, with that marker counted within `max_output_bytes`; `N` counts all omitted bytes. The cap must be at least 32 bytes so the marker fits. UTF-8 decoding drops a partial leading character after byte trimming. This changes the §4 output contract interpretation, not the event shape.
- **D16 Task workspace and gate paths (2026-10-01, with the T6 amendment; effective on USER approval of T6):** an unapproved candidate is created, tested, debugged and reviewed only in a disposable task workspace copied from the selected project through the workspace exclusion rules. Until the USER approves the final gated patch, the selected project is unchanged, byte for byte. The gate allows an edited or created path only if it is already in the USER-approved task's `files`; a listed path that does not exist yet permits creation. `docs/CONTRACTS.md` §7's "or declared in `new_files`" is removed.
- **D17 `task.state` payload (2026-10-01, with the T6 amendment; effective on USER approval of T6):** the first `task.state` event for a task fixes its immutable specification in `data`: `id`, `title`, `description`, `target`, `files`, `check` and `order`. `target` is `{"path", "symbol", "new"}`: an existing function, class or method (`new: false`); a new top-level function in an existing Python file (`symbol` is its plain name, `new: true`; amended 2026-10-02, USER); a new method of exactly one existing class (`symbol` is `Class.name`, `new: true`; amended 2026-10-02, USER; task 3 inserts it at the end of that class); or a new file (`symbol: ""`, `new: true`). `files` is exactly `[target.path]`. Later events carry `data.state`, `data.reason` (a reason code such as `invalid_output`, `cap_exhausted`, `check_failed`, `debug_exhausted`, `review_failed`, `path_outside_task`, `patch_too_large`) when failed, `data.debugRound`, and `data.failure` for an unusable role reply (reason, error, first 2,000 characters of the answer, last 2,000 of the thinking, `evalCount`, `capHit`). The planner does not choose task IDs; code assigns them. No new event kind or top-level field is added.
- **D18 Approval task identity and provenance (2026-10-01, with the T6 amendment; effective on USER approval of T6):** approvals record `task` when they belong to one. Candidates are attributed to the ROLE that produced them (builder or debugger). SYSTEM, as lifecycle owner, requests the final patch approval after the gate and records `data.originRole` and `data.candidate`; ROLEs still propose only through an approval (`docs/CONTRACTS.md` §0). No new event kind or top-level field is added.
- **D19 Task check approval (2026-10-01, USER decision with the T6 amendment; effective on USER approval of T6):** approving the plan covers each task's single named check, run only in that task's scratch workspace. Every command against the selected project still needs USER approval each time it runs. For task checks only, the scratch workspace counts as "the project folder" in S4. The check's allowlist is read from the live selected project when the plan is made, never from the scratch copy, and plan validation rejects any task whose `files` include the allowlist file. The plan approval view shows the exact check command and arguments for each task.
- **D20 Check-first rule (2026-10-02, USER decision with T6-park-1):** before building, a task's check runs in its task workspace. If it already passes, the check does not exercise the change, and the task fails as `check_not_exercising`. A goal that adds new behaviour therefore needs a check that fails before the change, such as an existing failing test the USER provides. Option (b), a warning shown on the card and the approval, was rejected: the reviewer caught 1 of 9 logic mutants inside new functions (T6 task 1), so review cannot stand in for the check. Option (c), a code task and its test task run as one pair with check-first applied to the pair, is carried to T7 as an idea only (§7 "Carry to T7"); §4 is unchanged.
- **D21 Self-development goal checks (2026-10-02, USER decision with T7 approval; reviewer note T7-decl-1):** each pre-registered goal's check is one module per goal in `selfdev_checks/`, outside default discovery (`python -B -m unittest discover -s tests`), run only by name through the allowlist. The AGENT writes and commits the modules on `selfdev/base` before any attempt, and they are never edited afterwards. Each imports its goal's target inside the test method with the copy's `src` on `sys.path`, so it fails by assertion for its own goal (G3's new-function check excepted: the missing name is its failure). Only the USER writes `.lab/allowlist.json`, using the exact JSON in §7. A merged goal's check moves unchanged into `tests/`. With T7's approval the USER also approved the three goals and their allowlist JSON as written, L2 and cap losses as recorded limitations, and dropping the full bench rerun from T7 (its planned line-level answer-leak check lapses with it).

## 3. Target end state and stop conditions

**Target:** the completion condition in `PROJECT.md` §9. Development of this prototype stops when **all** of these hold:

- **S1 Runs from a fresh clone, in isolation:**
  - Clone, `pip install -r requirements.txt` (numpy), start the hub, and the tests pass on Windows.
  - Nothing is imported from, and nothing at runtime refers to, any path outside the repo (D1). A test enforces this.
  - Tests use temporary folders only.
- **S2 One event log:**
  - One append-only SQLite log is the single source of truth. The browser, CLI and workers read from it rather than keeping their own copies.
  - After a restart, history, notes, the chosen project and job state are all back.
  - Every event carries its actor label (D10) and the rights table is enforced (`docs/CONTRACTS.md` §0).
  - Clients read new events by cursor, not by re-fetching the whole state.
- **S3 Visible lifecycles:**
  - Jobs and approvals are explicit state machines (`docs/CONTRACTS.md`).
  - Waiting on an approval doesn't block the hub.
  - A running job can be cancelled.
  - Both views show the current stage.
  - **Chat** and **New goal** are separate entrances (D2).
- **S4 Command runner:**
  - Runs only commands on a per-project allowlist file, in the project folder.
  - Each command runs after approval, with a timeout and capped output, and the result goes in the event log.
  - Refusals are tested.
- **S5 Knowledge layer:**
  - A hybrid search index (SQLite FTS5 keyword search plus numpy embedding similarity), an `ast` code graph, and summaries made without a model.
  - Refreshed after each applied change.
  - A context assembler builds a pack under a token budget for each role step.
  - Indexing runs only while no role step is running.
- **S6 Team:**
  - Planner, builder, debugger and reviewer run as separate steps with a fixed JSON output format (`docs/CONTRACTS.md`), each role's model set in one config file.
  - A deterministic gate checks that the allowlisted tests pass, the reviewer passed, and edits stay inside the task's files. The USER's approval follows the gate.
  - The old free-running tool loop (`engine.run_turn`) is deleted.
- **S7 Bench:**
  - At least 15 real-code tasks (D3).
  - Records per model and role: pass rate, time, tokens/s and invalid-output rate.
  - Records search quality (were the right files in the top 5?) and builder success with and without context packs.
  - Results are committed.
- **S8 Self-development (D8):** at least **one** of the three pre-registered goals passes and is merged, with zero paid-model calls, and nothing regresses.
- **S9 Smaller and cleaner:**
  - T1 cuts runtime Python (`src/**/*.py` plus the parts-bin code it loaded: 1,512 + 1,490 = **3,002 lines**) to **2,250 lines or fewer**, with no loss of behaviour.
  - At the end: no module over about 400 lines, no legacy or dead code, no duplicate capability, no import cycles, and no core module imports an interface module.
- **S10 Docs current:** `README.md` (plain, short), `AGENTS.md`, `docs/`, `PROJECT.md` and `PLAN.md` match the code, with no stale statements.

## 4. Not building (frozen when the plan is approved; D6)

- An MCP server or direct tool shell for external agents. They follow and advise through the CLI client.
- Cloud or paid model providers, remote access, multiple users, accounts, or auth beyond the local tokens.
- Model fine-tuning or training.
- An LLM approver with authority. The gate is deterministic checks plus the USER.
- Changes applied without the USER's approval.
- The hub changing its own running code. Self-development happens only in the selfdev worktree (D8).
- Frameworks and servers: LangChain and similar, vector-database or graph-database servers, spaCy, tree-sitter.
- A trained or deterministic embedder of our own, line-level dedup storage, or a manifold, projection or fusion framework.
- Web browsing or search tools. Image, voice or multimodal input.
- An in-browser IDE, file-tree editor or syntax highlighting beyond diffs.
- Parallel jobs, multiple simultaneous projects, or several hub sessions.
- Automatic model routing or selection beyond the static role config.
- Installers, PyPI publishing, new launchers, and macOS/Linux work beyond "the tests pass".

**Deferred (only by a new user decision after S1–S10 are met):**
- Intent router.
- Rolling conversation summarizer.
- Subject/verb/object and entity extraction, the conversation fact graph and conflict detection, and the memory curator.
- Scaffold-plan tool (folders plus multiple new files as one approved plan).
- Comparing embedders.
- Renaming (D5).
- Logic-mutant reviewer cards (flipped comparisons, swapped `and`/`or`, changed numbers, removed guards that survive the tests) in the role probe, for T7's bench rerun (USER, 2026-10-02).
- Recursive decomposition on execution failure: when a task fails its check after debugging, split it and rerun until its parts pass (USER idea, 2026-10-02). It extends T6's non-goal of re-planning on failure; a T7 candidate. Pass or fail should come from the task's check, not a small-model judge.
- Edit-task bench for the builder and debugger: real edits from history, each with a check that fails before the edit and passes after (USER, 2026-10-02; a candidate for T7's bench rerun). T6's role probe covers edits for the reviewer only.
- Builder sees the check's expectations, guarded by a hidden check (USER decision, 2026-10-03, reviewer note T8-pre-1): declared as T9 (renumbered with T8-pre-2).
- Goal-draft bridge from Chat to the New goal box (USER decision, 2026-10-03, reviewer note T8-pre-2): declared as T8, before T9.

## 5. Tranches

Each tranche follows `docs/WORKFLOW.md`: declare, get approval, implement, consolidate, verify, review, park. T3 onward is provisional.

| # | Tranche | Produces | Meets |
|---|---|---|---|
| T0 | **Setup (no product code):** documents per D7, and a `.gitignore` update. Measure candidate models with manual `/api/generate` calls (load time, tokens/s, JSON-schema `format`, `think`) and record the numbers here. Commit the baseline. | A complete and accurate starting record | S10 (start) |
| T1 | **Standalone and smaller** (draft in §7): rewrite the parts-bin pieces we use (exclusions, safe paths, staged writes, search/replace patching, diff, backups) as our own smaller modules and remove every `.parts-bin` import. Delete the legacy Tk app, the sandbox `files/` tool family, the client's compatibility path and the launcher's tkinter use. Tests use temporary folders. Add `requirements.txt` and `tests/test_architecture.py` (no imports from outside the repo, no import cycles, core never imports `interfaces`). | A hub that runs again, and a smaller codebase | S1, S9 (T1 part) |
| T2 | **Event log:** a SQLite append-only log. `SharedSession` is split so each domain has one owner and reads from the log. Named actors, cursor API, restart recovery. | One shared state for everyone | S2 |
| T3 | **Lifecycles and runner:** job and approval state machines, non-blocking approvals, cancel, **Chat** and **New goal** entrances, and the allowlisted command runner. UI shows stages. | Visible jobs, and test runs | S3, S4 |
| T4 | **Knowledge layer:** chunking, FTS5 plus embedding index, `ast` code graph, summaries without a model, context assembler, re-indexing after a change. A fake Ollama server for tests. | Context packs | S5 |
| T5 | **Bench:** snapshot this repo and hole-punch tasks from it (D3, with the fallback if needed), a harness, and a single builder step. Measure search quality and builder success per model, with and without context. Set the final `roles.json`. | Committed numbers, and a role config | S7 |
| T6 | **Team:** planner, debugger and reviewer added around the builder on the job machine. The gate plus the USER's approval. Role config. `run_turn` deleted. | Goal in, approved changes out | S6 |
| T7 | **Self-development and park:** set up the selfdev worktree and its allowlist, run the three pre-registered goals (D8), merge the ones that pass, size and import-graph checks (the full bench rerun was dropped by the USER, D21), docs, push. **End.** | Evidence for every stop condition | S8, S9, S10 |

T4 and T5 can overlap: the search-quality part of the bench needs only T4. T6 needs T3 and T5. The three D8 goals are registered at the end of T6.

## 6. Reference map

Surveyed 2026-09-29. These are sources to read and rewrite from, not dependencies (D1). Paths are relative to the folder that contains this repo (on the owner's machine, `C:\Jacob\_AppDesign\_SANDBOX\`). Total size is about 91k lines, so we take only the pieces we need and keep them smaller than the originals.

| Our need | Tranche | Best reference | Take | Leave |
|---|---|---|---|---|
| Safe paths, exclusions, staged writes, search/replace patching, diff, backups | T1 | `.parts-bin/project-mapping-guts` (`core/paths`, `exclusions`, `writes`, `diff`, `backups`; `tools/patcher`, `project_patcher`) | The logic we already rely on, rewritten into about 3 modules | Tk UIs, snapshots, the controller and dispatcher |
| Framework documents | T0 | `.DataMODEL/.framework/` | `DESIGN-PRINCIPLES`, `WORKFLOW` and `AGENT-START-HERE` (folded into `AGENTS.md`), adapted into `docs/` | `.tools`, journal and receipt instructions |
| Command runner | T3 | `.DataMODEL/.dev/PLAN.md` S9 (the `exec` design); AgenticToolbox `core/_IsoProcessMS.py` (93 lines, isolated process with streaming log) | Allowlist, timeout, capped output; process isolation pattern | The MCP/authority model |
| Chunking | T4 | Tripartite `chunkers/code.py` (Python AST: functions, classes, import block, module summary) and `chunkers/prose.py` (heading-aware Markdown, paragraph fallback) | Both are about 420 lines; target about 200 | tree-sitter (a new dependency), `compound.py` (824 lines) |
| Code graph | T4 | AgenticToolbox `relation/_CodeGrapherMS.py` (135 lines, AST definitions and calls) | The visitor pattern, plus imports and test→code links | The graph layout and viewer |
| Knowledge store schema | T4 | Tripartite `db/schema.py`: `source_files`, `chunk_manifest`, `embeddings`, `graph_nodes`, `graph_edges`, `ingest_runs` | Table shapes for one SQLite file next to our event log | `verbatim_lines` line dedup (clever, but out of scope) |
| Keyword search | T4 | AgenticToolbox `db/_LexicalSearchMS.py` (98 lines, SQLite FTS5 BM25) | Hybrid search: BM25 plus embedding similarity, standard library only | — |
| Vector search | T4 | AgenticToolbox `meaning/_SearchEngineMS.py` (hybrid idea only) | Score fusion | faiss, chroma, sqlite-vec (numpy dot product is enough at our scale) |
| Token budget | T4 | BDNeural `contract/token_budget.py` (124 lines, character-estimate budget and slicing) | Budget and slice helpers for the context assembler | The HyperHunk wire format and ontology machinery |
| Context assembly under a budget | T4 | GraphManifold `extraction/extractor.py` ("gravity_greedy" token-budgeted evidence bag), `math/scoring.py` (normalize, graph scoring), `hydration/hydrator.py` | The greedy-under-budget algorithm, plus score normalization and fusion | The manifold, projection and fusion framework (about 27k lines), FastAPI |
| Summaries without a model | T4 | AgenticToolbox `core/_HeuristicSumMS.py` (81 lines: headers, signatures, docstrings) | Free file summaries for context packs and the index | — |
| Role and persona config | T6 | AgenticToolbox `db/_RoleManagerMS.py` (119 lines) | The shape of a role record: prompt, model, output schema | Persisting roles in a database (one config file is enough) |
| Task list for plans | T6 | AgenticToolbox `db/_TasklistVaultMS.py` (151 lines, nested tasks with status) | Plan and task status shape | Unlimited nesting (plans are flat task lists) |
| Entity and relation extraction | Deferred (§4) | Tripartite `pipeline/extract.py` (228 lines, qwen2.5-0.5b, JSON entities and relations to graph tables) | Prompt and graph-write pattern, if a later decision brings it in | llama-cpp (we use Ollama) |
| Deterministic embedder | Not building (see §4) | GraphManifold `training/` (BPE → co-occurrence → NPMI → SVD, numpy only) | Nothing for now | All of it. Possibly a bench comparison later, by decision |

Surveyed and found not needed: AgenticToolbox's app factory, catalog, stamper and Tk UI services; GraphManifold's runtime, projection and FastAPI UI; BDNeural's Splitter/Emitter apps, corpora and journal; Tripartite's GUI, viewer, export and PyInstaller release.

## 7. Current Tranche

**ID:** T8 — Goal-draft bridge from Chat to the New goal box (declared 2026-10-03; USER decision with reviewer note T8-pre-2; amended the same day for reviewer note T8-decl-1).

**Current:** T1–T7 are accepted on `main`; the prototype is complete (§3). This tranche comes from the §4 Deferred list by USER decision. The builder-expectations tranche declared in `7202c99` is renumbered T9 and waits, unreviewed and unapproved, until T8 is parked and accepted. There is no implementation permission and no standing authorization (§8).

**Branch:** `t8-goal-draft`.

**Expected outcome:** in the browser, the USER can ask Chat to draft one goal from the recent conversation, or take one assistant reply as the draft source, and get a validated goal sentence placed in the New goal box of that browser only. A draft never submits anything and never creates a job or an approval. The USER edits or clears it and presses New goal as today; plan validation, plan approval and patch approval apply unchanged.

**Proposed D22, Goal drafts (recorded in §2 with the code, on USER approval):** a goal draft is a validated suggestion for the New goal box, never a submission.
- **Who:** only the USER browser can request a draft. The fill goes only to the requesting browser, in that request's response. The draft's `chat.reply` record is in the shared event log, so every client shows it (S2).
- **Model:** the draft is one schema-constrained call using the planner's role configuration in `roles.json` (`qwen2.5-coder:14b`, thinking off, temperature 0; JSON-format output is reliable there, and the 9b ignores the format with thinking off). Settings are recorded, not tuned.
- **Input:** what the model needs to name a valid target, as `planner_request` gives the planner: the source text (the recent chat turns, or the one chosen reply), the project's file list (bounded like the planner's), the live allowlist's check names, and a context pack for the source text.
- **Never blocks behind a job (M2).** A draft takes the one turn slot without waiting. A running job holds that slot for its whole `running` phase. If the slot is not free, the endpoint answers "busy" at once, fills nothing, and records a `goalDraft` with `valid: false` and reason `busy`. When the slot is free, the draft pauses indexing like any role step.
- **Not conversation (M3).** A draft's `chat.reply` carries no `turn`, so `SessionState` never adds it to the chat history, and drafts never feed the chat model or later drafts as conversation.
- **Reply shape:** `{goal, target: {path, shape, symbol}}`, where `shape` is one of `existing_symbol`, `new_function`, `new_method` or `new_file`.
- **The validated target is advisory (S1).** Only the goal text reaches the box. The planner re-plans from that text and never sees the draft's `target`, so the goal sentence must state the shape in words, and the validator checks it:
  - `new_function`: "new function `name`" and "existing file";
  - `new_method`: "new method `name`" and the class name;
  - `new_file`: "new file";
  - `existing_symbol`: the symbol's name.
  The G3 confusion can still happen after submission; the probe measures it.
- **Validation, one path.** Code checks the reply with the same rules as plan validation in `team/plan.py`. The path rules (normalized relative path, inside the project, not the allowlist file) move into one shared helper used by both, and `check_target` is reused unchanged. Plus:
  - `goal` is one line of at most 400 characters;
  - it names its target and no other project file: either the full path, or a path suffix of whole components that resolves to the target alone (G1's working goal said `team/steps.py`). A suffix that also matches another project file is rejected;
  - `shape` must agree with `symbol`: `existing_symbol` resolves to exactly one definition; `new_function` is a plain name not yet defined in an existing Python file; `new_method` is `Class.name` for exactly one existing class; `new_file` has an empty symbol and names a missing path in an existing folder. This catches the G3 confusion of a new function planned as a new file.
- **Check-first stays visible (D20).** For every shape, the reply in Chat carries a note that a check failing before the change must exist, and lists the allowlisted check names. D20 also stops an edit of an existing symbol whose check already passes, as G1 needed its own failing check. The hub does not claim to know which check fails.
- **Failure fills nothing.** If the call or the validation fails, the box keeps its text, and the reasons are shown in Chat.
- **Recorded verbatim.** Each draft is recorded as a `chat.reply` whose `data.goalDraft` holds `source`, `goal`, `target`, `valid`, `reasons` and the model settings. No new event kind. The text placed in the box is byte-identical to the recorded `goal`.

**Scope (task list, in order):**
1. **Validator.** In `team/plan.py`, extract the shared path helper and keep `validate_plan` behaviour identical. Add `validate_draft` (in a small `team/draft.py`, with the draft schema and prompt) on top of it and `check_target`. Tests, with no model calls, accept and reject each shape, including a new function declared as a new file, a goal that does not state its shape in words, the allowlist file, a path outside the project, a goal naming two files, an ambiguous path suffix, and an over-long goal.
2. **Session and endpoint.** `SharedSession.draft_goal(source, actor)`, where `source` is `"conversation"` (the recent turns) or the event ID of one `chat.reply`. Plus `POST /api/goal-draft`, USER only, returning `{ok, goal}` only when valid. Tests:
   - a draft creates no `job.state` and no `approval.requested` event;
   - a failed validation returns no goal;
   - the returned text equals the recorded `data.goalDraft.goal`;
   - the AGENT client is refused;
   - only the requesting request receives the text;
   - a draft's `chat.reply` carries no `turn` and does not enter the chat history;
   - while the turn slot is held, the endpoint answers "busy" at once and fills nothing.
3. **Page.** A "Draft goal" button by the Chat form, and "Use as goal" on assistant replies; both call the endpoint and fill `#goal` only from a successful response. No submit is triggered. The architecture test gains an AST scan of `team/draft.py` and the goal-draft endpoint handler: neither calls `submit_goal`, `request_approval` or `transition_job` (L2).
4. **Probe and park.** An opt-in probe, `python lab.py bench drafts --confirm-gpu-free`, run only after the USER confirms the GPU is free. It drafts goals from short scripted conversations built around G1–G3 and five bench goals, then plans each valid draft on a throwaway copy, with no approval and no apply. It reports: drafts valid; valid drafts that plan validly; whether each plan's target agrees with the draft's validated target (S1); the rejection reasons; and time per draft with model load time (L1). There is no threshold; the results go in the park record. D22 and the `docs/CONTRACTS.md` changes (§0 rights row, `chat.reply` `goalDraft` data, the endpoint) land in the same commit as the code they describe (S10).

**Non-goals:** submitting goals automatically; drafting more than one goal or task; editing or picking checks; any change to plan validation behaviour, approvals or the pipeline; the builder-expectations work (T9); tuning the draft model's settings.

**Acceptance criteria:**
- `python -B -m unittest discover -s tests -v` passes, including the validator, session, endpoint and architecture tests above, with no model calls.
- The architecture test's AST scan proves that neither `team/draft.py` nor the goal-draft endpoint handler calls `submit_goal`, `request_approval` or `transition_job`.
- The probe's record is in `bench/probes/` with the three numbers and the reasons.
- `docs/CONTRACTS.md` and D22 match the code; `git diff --check` is clean.

**Known risks:**
- The planner model drafting goals may name the wrong file or shape. The validator catches what code can know; intent stays with the USER.
- During a running job, drafts answer "busy" until the job waits for an approval or ends.
- Loading the 14b for a draft swaps out the chat model (a few seconds).
- `session.py` is at 300 lines; the draft logic lives in `team/draft.py` so the session gains only a thin method.

**USER gates:** the USER approves this declaration, confirms the GPU is free before the probe, and accepts the park.

**Progress:**
- [ ] 1. Validator.
- [ ] 2. Session and endpoint.
- [ ] 3. Page.
- [ ] 4. Probe and park.

**Now:** T8 approved by the USER (2026-10-03) after the reviewer's re-check of `c57d2ae`; task 1 (validator) in progress on `t8-goal-draft`. To resume: read this line, then `git log --oneline -10`.

---

### Next declaration (waiting): T9 — Builder expectations

**ID:** T9 — Builder sees the check's expectations, guarded by a hidden check (declared 2026-10-03 as T8 with reviewer note T8-pre-1; renumbered T9 by USER decision with T8-pre-2; waits until T8 is parked and accepted; not reviewed or approved).

**Current:** T1–T7 are accepted on `main` (`aa48592`); the prototype is complete (§3). This tranche comes from the §4 Deferred list by USER decision. There is no implementation permission and no standing authorization: the USER approves this declaration, confirms the GPU is free before each GPU run, and accepts the park (§8).

**Branch:** `t9-expectations`.

**Expected outcome:** a measured answer to one question: does giving the builder the visible check's test cut its output-cap losses and raise real passes, without teaching to the test? The answer is adopted only under the rule below; otherwise a negative result is recorded and nothing changes.

**Baseline, stated exactly.** In the 12-of-22 full-pipeline bench (`bench/experiments/2026-10-02-pipeline-bench.jsonl`), the builder's input was the task goal, target path, symbol, placement, the punched region, the punched target file, and a 6,000-token context pack built by `bench/harness.py` `build_context` from the punched copy. That index covers every allowed `.py` and `.md` file, `tests/` included. An audit of the 22 cached packs, recorded in task 1, found:
- every pack held items from `tests/` (7 to 44 per pack);
- 20 of 22 packs held the task's own graded test method (all but self-006 and self-017), including all 6 tasks that kept capping (self-003, -007, -012, -014, -019, -020).

So the baseline was not clean: the builder often already saw its visible test, ranked among other items. A clean baseline is rerun with `tests/` excluded from the builder's context pack.

**One variable.** Arm A (clean baseline): the pipeline as in the 12-of-22 run, with `tests/` excluded from the context pack. Arm B: the same, plus one new builder input field, `check_test`, holding the visible test method's source, its class's `setUp`/`setUpClass` and the module-level helpers it calls, taken by `ast` from the pristine snapshot's test file. The planner, reviewer and debugger inputs, `roles.json` and the bench's answer-leak checks are identical in both arms.

**Hidden check.** For each task, every other test in the snapshot's default discovery, never shown to the builder. Task 1 measures which tasks have hidden coverage: whether the punched copy fails any test besides the visible one. Tasks with no hidden coverage are listed and reported separately, because a hardcoded expected value cannot be detected on them.

**Metrics (defined before any run):**
- Primary: the builder's output-cap rate (share of builder calls that hit `num_predict`).
- Passes on the visible check.
- Passes on visible plus hidden (the candidate applied to a pristine copy; the visible test and the whole default discovery pass).
- Visible-only passes, a suspected teaching-to-the-test signal: visible passes, hidden fails.
- Time per task.

**Adoption rule (set before any run).** The same split as T6 (`index % 3 == 2` is held back). Adopt B only if, on the held-back goals, visible-plus-hidden passes in B exceed A's, and B has at most 1 visible-only pass in total. Otherwise record a negative result and change nothing.

**Scope (task list, in order):**
1. **Measure before running.** Commit the pack audit above as a record. For each task, run default discovery on its punched copy and list every failing test besides the visible one (its hidden coverage); list the tasks with none. Confirm the pristine snapshot passes default discovery in a bench copy, or record which tests cannot run there (for example, tests needing git history) and exclude them from the hidden check for every arm alike. No GPU.
2. **Bench support, tested.** In `bench/` only: a switch that drops `tests/` from the context pack; the `check_test` extraction; the hidden-check scorer; leak checks unchanged. Tests for each, with every model call blocked. No product change.
3. **Runs (USER confirms the GPU is free first).** Arm A, then arm B, on all 22 goals, one run each, with every row recorded in `bench/experiments/` (settings, prompt hash, model digest, Ollama version, the per-goal metrics above).
4. **Decide by the rule and park.** Apply the adoption rule as written. If B is adopted, wiring `check_test` into the team pipeline is a separate USER decision and a later tranche; T9 changes no product code. Park with the evidence.

**Non-goals:** recursive decomposition; changes to the planner, reviewer, debugger or `roles.json`; prompt tuning beyond adding the one field; more runs per goal; new bench tasks; product pipeline changes.

**Acceptance criteria:**
- Task 1 records exist: `bench/experiments/` holds the pack audit and the hidden-coverage table, including the no-coverage list.
- `python -B -m unittest discover -s tests -v` passes, with tests for the pack switch, the extraction and the hidden scorer.
- Rows for both arms on all 22 goals, with every metric above.
- The adoption decision is applied exactly as written and stated in §9, with its numbers.

**Known risks:**
- The baseline already showed the visible test in 20 of 22 packs, so the effect of B may be small. Arm A may even do worse than the 12-of-22 run, since it removes test text the builder had.
- One run per goal makes small differences noisy. The rule judges held-back goals only, which are 7.
- Hidden coverage may be thin (the reviewer found only 10 of 22 targets called by name in another test), so the visible-only signal is blind on some tasks.
- `check_test` can make inputs longer; the cap rate is measured, not assumed.

**Progress:**
- [ ] 1. Measure before running.
- [ ] 2. Bench support, tested.
- [ ] 3. Runs.
- [ ] 4. Decide and park.

**Now:** waiting; T8 (goal-draft bridge) comes first.

---

### Previous declaration: T7 — Self-development and park

**ID:** T7 — Self-development and park (accepted 2026-10-03; retained for implementation history).

**Current:** T1–T6 are accepted on `main` (pushed at `cb13bbb`). S1–S7 are met by their tranches; S8 (self-development), S9 (end-state size and cleanliness) and S10 (docs current) remain. There is no implementation permission until the USER approves this declaration (§8).

**Branch:** `t7-selfdev`. Goal attempts happen in a separate worktree, `_SANDBOX/.tool-user-selfdev`, on `selfdev/<goal>` branches (D8). The hub runs from this checkout; the worktree is only its selected project.

**Expected outcome:** the local team turns at least one pre-registered goal from this plan's own backlog into a tested change that the USER approves in the hub and that merges into `main` with `selfdev` credited, with zero paid-model calls and no regression (S8, D8). Then every stop condition S1–S10 has recorded evidence, and the project ends.

**Proposed D21, Self-development goal checks (recorded in §2 on USER approval):** each goal's check is one module per goal in `selfdev_checks/`, outside default discovery (`python -B -m unittest discover -s tests`), and runs only by name through the allowlist. The AGENT writes the modules and commits them on `selfdev/base` before any attempt; they are never edited afterwards. Each module imports its goal's target inside the test method, after putting the copy's `src` on `sys.path` the way the existing tests do, so a check fails by assertion for its own goal. The one exception is G3's new-function check, where the missing name is the failure. Only the USER writes `.lab/allowlist.json` (`docs/CONTRACTS.md` §0, §4), using the exact JSON given below for each goal.

**Pre-registered goals (D8; fixed at the first attempt, never swapped after one).** Each is entered through **New goal** exactly as written.
- **G1 Citation parts (carry item L3).** Goal: "In team/steps.py, make require_citation reject a reviewer fail when any quoted part is shorter than CITE_MIN characters, not only when the parts' total is." One task, an edit of `require_citation`. Check module `selfdev_checks/test_g1.py`. Allowlist:
  `{"commands": {"goal-g1": ["python", "-B", "-m", "unittest", "selfdev_checks.test_g1"]}, "timeout_s": 120}`
- **G2 Case-insensitive ignore rules (T1 note).** Goal: "In workspace/paths.py, make excluded match .gitignore rules without regard to letter case, as git does on Windows." One task, an edit of `excluded`. Check module `selfdev_checks/test_g2.py`. Allowlist:
  `{"commands": {"goal-g2": ["python", "-B", "-m", "unittest", "selfdev_checks.test_g2"]}, "timeout_s": 120}`
- **G3 Leftover staged files, two tasks (T1 note; the multi-task proof carried from T6).** Goal: "In workspace/patching.py, add stale_temps(folder) that returns the paths of leftover .lab-stage- and .lab-recover- files in that folder, then make staged_apply raise a ValueError naming them, before writing anything, when any target folder has such files." Two tasks: a new top-level function, then an edit of `staged_apply`. Task 2 sees task 1's applied change. Nothing is deleted, so every change stays inside the gated diff (D16, D18). Check module `selfdev_checks/test_g3.py`, with two test classes. `NewFunctionTests` calls `stale_temps`. `ApplyTests` plants a leftover file, then expects `staged_apply` to raise a `ValueError` naming it and the target to stay unchanged. Allowlist:
  `{"commands": {"goal-g3-new": ["python", "-B", "-m", "unittest", "selfdev_checks.test_g3.NewFunctionTests"], "goal-g3-apply": ["python", "-B", "-m", "unittest", "selfdev_checks.test_g3.ApplyTests"]}, "timeout_s": 120}`

**Scope (task list, in order):**
1. **Selfdev setup.** Create the worktree from `main` on `selfdev/base`, and commit the three check modules there. Record in §7: the worktree commit; each check failing for its own goal's reason, not an import error from another goal; and default discovery passing in the worktree.
2. **Reviewer check (pause).** The goal texts, check modules and allowlist JSON freeze at the first attempt, so the reviewer reviews them first. No attempt starts until the USER releases that review.
3. **Goal attempts.** Goals run in order. Before each goal, the USER writes that goal's allowlist JSON into the worktree's `.lab/allowlist.json` by hand, selects the worktree in the hub and submits the goal. The USER approves or rejects the plan and the patch in the browser. The AGENT only watches through the client and advises in chat (D8).
   - Each goal gets at most 3 attempts. A USER plan rejection counts as one, recorded as `rejected`.
   - Each attempt is recorded in §7: job ID, task states, reason code, role origin and time.
   - Each attempt's events are committed as an export, `bench/selfdev/<goal>-<job>.json`: the job's events, per-role models and approvals. The export is made by a read-only script over the git-ignored `events.sqlite`.
   - A failed attempt leaves the worktree reset to `selfdev/base` before the next attempt.
4. **Review and merge.** For each goal that passes, the AGENT commits the applied change on `selfdev/<goal>` as `selfdev <goal>: …`, unchanged from what the USER approved, with the trailer `Actor: ROLE`.
   - On that branch, default discovery, `tests/test_architecture.py` and the goal's named check must all pass.
   - The diff gets the D8 normal review (reviewer note, USER release).
   - The hub is stopped before the branch merges into `t7-selfdev`, because the hub never changes its own running code (§4), and restarted after.
   - Once merged, the goal's check module moves unchanged into `tests/` in a separate AGENT commit, so default discovery guards it from then on.
   - A goal whose branch fails any of these is recorded as not passed, not fixed by hand.
5. **End checks and park.**
   - S1: a fresh clone into a temporary folder, `pip install -r requirements.txt`, then the test suite.
   - S9: module sizes, the import graph, and a dead-code pass using a standard-library `ast` scan for top-level functions, classes and constants in `src` never referenced elsewhere in `src` or `tests`. Each find is reviewed and removed or justified; no new dependency.
   - S7 follow-up for L3: rerun the reviewer probe (`python lab.py bench roles --confirm-gpu-free`) only if G1 merged.
   - S10: a docs pass.
   - Then the §9 record with evidence for each stop condition, and the push after USER acceptance.

**Carry-to-T7 items:** L3 is G1, and the multi-task proof is G3. L2 (the pre-build check is recorded as `building`) stays a recorded limitation, because changing the task-state sequence is a contract change that the end of the project does not need. Cap losses stay a recorded limitation. Option (c) of D20 stays an idea.

**Non-goals:** any §4 item, including the deferred T7 candidates (logic-mutant cards, recursive decomposition, edit-task bench); new roles, prompts or settings tuning; contract or event changes; rerunning the 132-attempt builder bench (see "Needs the USER" item 4); renaming (D5); hand edits to the team's changes; any paid-model call during attempts.

**Acceptance criteria:**
- **S8:** at least one goal's branch is merged into `t7-selfdev` with `selfdev` credited. Its job events show local models only and USER approvals for the plan and the patch. Evidence: `git log --grep "^selfdev"`, the attempt records in §7 and `bench/selfdev/`. S8 is met when `t7-selfdev` merges into `main` after USER acceptance (D8).
- **No regression:** "full suite" means default discovery. `python -B -m unittest discover -s tests -v` and `-p "test_architecture.py"` pass on `t7-selfdev`.
- **S1:** the fresh-clone test run passes (command and output recorded).
- **S9:** no module over 400 lines; the architecture test passes; each dead-code find is removed or justified.
- **S10:** `rg -n "in progress|not yet declared|awaits USER" README.md AGENTS.md PROJECT.md docs` shows no stale status; `git diff --check` is clean.

**Known risks:**
- The cap loses about a quarter of builder attempts, so a goal may use all 3 attempts.
- G3 needs the planner to give each task its own check, which no earlier run has tested.
- The check modules are indexed like any Python file, so context packs may show the planner and builder the expected behaviour. That is intended: the checks are the specification.
- The worktree copy into each task workspace includes `bench/`, so copies take longer.
- The USER's attention is needed at every allowlist change, plan approval and patch approval.

**Needs the USER at approval:**
1. The three goals and their allowlist JSON, as written.
2. D21 as proposed above.
3. L2 and cap losses left as limitations.
4. Dropping "run the full bench" from §5's T7 row. The builder is unchanged since T5. The planned line-level answer-leak check (a bench-hardening item that T6 deferred to T7) lapses with it.

**Progress:**
- [x] 1. Selfdev setup.
- [x] 2. Reviewer check.
- [x] 3. Goal attempts.
- [x] 4. Review and merge.
- [x] 5. End checks and park.

**Now:** T7 is parked and accepted (2026-10-03) and merged into `main`; S1–S10 have recorded evidence (§9), so the prototype is complete. Further work needs a new USER decision on the §4 Deferred list. To resume: read this line, then `git log --oneline -10` on `t7-selfdev`.


**Task 1 evidence (2026-10-02):**
- Worktree `C:/Jacob/_AppDesign/_SANDBOX/.tool-user-selfdev` on `selfdev/base`, created from `t7-selfdev` at `87e61a8`; the check modules are committed there as `14f2662` (`selfdev_checks/test_g1.py`, `test_g2.py`, `test_g3.py`), amended before any attempt as `05e0015` (T7-checks-1 R1: `ApplyTests.test_a_clean_folder_still_applies_several_files`).
- Before-results, each run from the worktree root exactly as its allowlist entry: `selfdev_checks.test_g1` fails (`ValueError not raised`; its second test, whole-line quotes still count, passes); `selfdev_checks.test_g2` fails (the three case variants are not excluded; the unmatched-paths test passes); `selfdev_checks.test_g3.NewFunctionTests` errors on the missing `stale_temps` (the allowed name failure); `selfdev_checks.test_g3.ApplyTests` fails only on its refusal test (`ValueError not raised`); its clean multi-file apply test passes on the base. No check fails because of another goal.
- `python -B -m unittest discover -s tests` in the worktree: 144 tests pass.
- Each goal can pass: in a throwaway copy (`live_control/tmp`, deleted afterwards; never the worktree), minimal reference fixes made all four checks pass and the 144-test suite still passed. These fixes are not committed and are not shown to the team.
- The allowlist JSON for each goal is in the declaration above; the USER writes it before each goal (D21).

**Task 2 evidence (2026-10-02):** reviewer note T7-checks-1 asked for one G3 positive-path test, added before any attempt (`05e0015`); the reviewer's re-check passed and recommended release (reviewer note `T7-checks-1`). The USER released the attempts. Allowlists: G1's and G3's were written by the reviewer agent at the USER's explicit direction, verbatim from §7 (the hub read exactly `goal-g1`, and `goal-g3-new` with `goal-g3-apply`, timeout 120); G2's was written by the USER by hand.

**Attempts (task 3):**
- **G1, attempt 1: passed.** Job `5cc7c9a7-0cb8-41f0-be30-276abc6b2b07`, 2026-10-03 04:40:18–04:43:05 UTC (about 2 min 47 s including USER approvals). The planner made one task, an edit of `src/local_memory_lab/team/steps.py` `require_citation`, with check `goal-g1`. `goal-g1` failed first (exit 1), then the builder's candidate passed (exit 0) with no debug round. The reviewer passed it, then the gate. The USER approved the plan and the patch (`originRole: role:builder`), and the change was applied. Event actors: `system` and `user` only. Export: `bench/selfdev/G1-5cc7c9a7-0cb8-41f0-be30-276abc6b2b07.json`.
- **G1 on `selfdev/g1` (`e1c673f`, `Actor: ROLE`, unchanged from the approved patch):** `goal-g1` passes; `python -B -m unittest discover -s tests`, 144 pass; the architecture test passes; `git diff --check` is clean.
- **G1 D8 normal review (reviewer note `T7-g1-1`): passed.** `e1c673f` matches the approved patch line for line; the export matches the hub log; all roles were local models; approvals were by `user` only. G1 merges at task 4.
- **G2, attempt 1: not passed (`review_failed`).** Job `8b312139-cf0b-400e-9072-6f17816ca987`, 10:09:25–10:11:29 UTC. `goal-g2` failed first, then passed with the builder's candidate; the reviewer failed it, quoting `fnmatch.fnmatch(lower, ".*-bin")`. Export: `bench/selfdev/G2-8b312139-cf0b-400e-9072-6f17816ca987.json`.
- **G2, attempt 2: not passed (`invalid_output`).** Job `9c59e3fa-9451-4221-91f0-7a5df52fb867`, 10:14:28–10:15:34 UTC. `goal-g2` failed first, then passed; the reviewer's fail quoted `if fnmatch.fnmatch(lower, ".*-bin")`, which is not a card line, and gave the `fnmatchcase` → `fnmatch` swap as its reason. Export: `bench/selfdev/G2-9c59e3fa-9451-4221-91f0-7a5df52fb867.json`.
- **G2: not passed; stopped by the USER after 2 of 3 attempts; the third is unused.** The candidate relied on `fnmatch.fnmatch`, which ignores case only on Windows (through `os.path.normcase`), so it did not meet "without regard to letter case" everywhere; the USER chose not to risk approving a Windows-only fix.
- **G3, attempts 1 and 2: not passed (`invalid_plan`).** Jobs `a395cc95-957c-4e9a-85c9-cf226a3f53a9` (10:24:28–10:24:52 UTC) and `67668e0b-24ef-4ef8-bd3e-61cbf24fe44f` (10:27:42–10:27:57 UTC). Both times plan validation rejected task 1 with "src/local_memory_lab/workspace/patching.py already exists": the planner marked the new function `stale_temps` as a new file (empty `symbol`). Nothing ran. Code-rejected plans count as attempts (USER decision). Exports: `bench/selfdev/G3-a395cc95-957c-4e9a-85c9-cf226a3f53a9.json`, `bench/selfdev/G3-67668e0b-24ef-4ef8-bd3e-61cbf24fe44f.json`. Reviewer note `T7-g1-1` reports a third G3 attempt with the same result; the event log (58 events, the last at 10:27:57 UTC) has no third G3 job, so it is not recorded here. The USER then closed the attempts and moved to task 4: G3 is not passed.

**Task 4 evidence (2026-10-03):**
- The hub (`pythonw lab.py hub-server`, idle) was stopped before the merge, as §4 requires.
- `selfdev/g1` was merged into `t7-selfdev` with `--no-ff` (`e448284`; the team's commit `e1c673f` is unchanged).
- `selfdev_checks/test_g1.py` was moved unchanged into `tests/test_g1.py` (`0d2ff41`, a pure rename). `python -B -m unittest discover -s tests`: 146 tests pass (144 plus G1's two).
- The hub was restarted the same way; `python lab.py client status` answers and shows all five jobs.

**Limitations to record at park (from `T7-g1-1`; not fixed in T7):**
- A `review_failed` or `invalid_output` reviewer failure keeps the reasons and quote but not the candidate; the workspace is discarded, so a rejected candidate cannot be inspected afterwards.
- An `invalid_plan` failure keeps only the validation error, not the planner's answer.
- The frozen G2 check passes on Windows for a Windows-only fix (`fnmatch.fnmatch` folds case there).
- The planner (qwen2.5-coder:14b) confused "new function in an existing file" with "new file" on every G3 run, so the multi-task proof carried from T6 remains unproven.


## 8. Current Decision

**Project definition:** DEFINED. **Plan status:** APPROVED (2026-09-29). §3 and §4 are frozen (D6).
**Implementation permission:** YES for T8 (USER, 2026-10-03). No standing authorization: the USER confirms the GPU is free before the probe and accepts the park. T9 (builder expectations) waits behind T8.
**Standing authorization for T6 (USER, 2026-10-02; ended when T6 was parked on 2026-10-02):** within T6's declared scope, the implementing AGENT may change role settings and prompts, run experiments, fix bugs, adjust tests and refactor T6 modules without asking first. The condition: every change is recorded and reversible. Each experiment appends its settings, prompt fingerprint, model digest, Ollama version and per-goal outcomes to `bench/experiments/`, and each tuned change is its own commit linked to that record. Tuning uses about two-thirds of the bench goals; a change is kept only if it also holds on the held-back third. Still needing the USER: machine-wide settings, Ollama or other downloads, pushing, deleting anything not created by the AGENT, D-decisions and contract semantics, §3/§4, scope or non-goal changes, reviewer notes, and parking or accepting the tranche. This authorization ends when T6 is parked.

## 9. Parked Tranches

**T0, setup: PARKED 2026-09-29, and the parking was accepted by the USER the same day.** Merged into `main` and pushed.
- **Outcome met:** onboarding and standing documents are in place and match the observed state:
  - `AGENTS.md`, `README.md`;
  - `docs/ARCHITECTURE.md` (moved from the root; `.tools` line replaced), `docs/DESIGN-PRINCIPLES.md` (copied, plus a one-line note on this project's seams), `docs/WORKFLOW.md` (the `.tools` section replaced by this repo's record-keeping), `docs/CONTRACTS.md` (v0);
  - `PROJECT.md` and `PLAN.md`.
- **Also done:**
  - `.gitignore` now ignores `_projectmapper/` and no longer mentions `.parts-bin/`.
  - Stale runtime files deleted: `live_control/` inbox, transcript, logs and the dead `shared.json`. The one patch backup in `live_control/backups/` was kept, since it may be user data. The `files/` demo files are left for T1, which removes that folder with its tools.
- **Evidence:**
  - Model measurements are in §1, taken with a throwaway script that isn't kept in the repo.
  - The size baseline (3,002 runtime Python lines) is a static import count, in §1 and S9.
  - The test suite fails on import, as expected (finding 8).
- **Limitations:**
  - The hub is not runnable until T1.
  - The cause of the qwen2 slowness is inferred, not verified.
  - I ran the test suites of `_ProjectMAPPER` and `_TaskWORKER` in place once, with `-B`. That was before D1 was tightened to forbid running reference projects; both failed on import, and I made no changes to either.
- **Deferrals:** none beyond §4.
- **Amended 2026-09-29, after review:**
  - Vendor-neutral pass (D9): agent product names removed from all docs, and the vendor-specific instruction file removed. `AGENTS.md` is the only start-here file.
  - Branch policy added to `docs/WORKFLOW.md`. T0 lives on `t0-setup`.
  - T1 scope gained the `.lab/` exclusion and the neutral speaker label.
  - D9 extended to people and commit messages. D10 added: USER, AGENT, ROLE and SYSTEM labels, with a rights table in `docs/CONTRACTS.md` §0. "Human" is replaced by USER throughout, and the two T0 commits were reworded to carry `Actor: AGENT`.
- **Handoff (D11):** parking T0 ends the setup phase. From here the development team declares and implements tranches, and the USER approves, reviews and steers.
- **Next step:** the team orients and declares T1, starting from the draft in §7.

**T1, standalone and smaller: PARKED and ACCEPTED 2026-09-29.** Built on `t1-standalone`; merged to `main` as `7d5f5d8`.
- **Outcome met:** the hub runs from this repo without reference-code imports. Project listing, reading, creation, reviewed patch application, cancellation, backups, and rollback are implemented in the owned workspace package. Built-in exclusions cover common generated folders, lockfiles, and bytecode. The legacy app, sandbox file-tool family, and pre-request-ID client path are removed. The selected project starts empty. Runtime source is 1,104 lines, down from 3,002 in the T0 baseline.
- **T1 update:** the project and patch tools use the in-repo workspace package; requested exclusions, plan, status, and cleanup fixes are complete. The final source count is 1,104 lines and the 14-test suite passes; the HTTP smoke check and manual hub/browser startup check pass.
- **Evidence:**
  - `python -B -m unittest discover -s tests -v` — 14 tests passed.
  - `python lab.py hub-server` — server started. The browser/API probe returned HTTP 200 for both the browser page and state API; the selected project was `None` and `appFolder` was absent.
  - `tests/test_http_smoke.py` — selected a temporary project through HTTP; cancellation left its file unchanged; approval applied the patch and produced a backup.
  - `python -c "import pathlib; print(sum(len(p.read_text(encoding='utf-8').splitlines()) for p in pathlib.Path('src').rglob('*.py')))"` — 1,104 lines (S9 T1 limit: 2,250).
  - `rg -ni 'parts-bin|PARTS_BIN' src tests` — no matches.
  - `rg -ni 'openai|codex|chatgpt|anthropic|claude|google|gemini|microsoft|copilot' src` — no matches.
  - `git diff --check` — clean. Largest Python module: 171 lines.
  - Diff review from T0 commit `0fd3798` — changed files align with the T1 scope recorded here.
- **Limitations:** model inference was not part of the T1 smoke run; T1 verifies the hub and approval plumbing without Ollama.
- **Notes only (unchanged by these fix-ups):** gitignore matching remains case-sensitive; a crash can leave staged temp files; startup errors may be invisible under `pythonw`; tool definitions still omit some parameter descriptions.
- **Deferrals:** none beyond §4.
- **Next step:** T1 accepted; reorient and proceed with the USER-approved T2 declaration in §7.

**T2, event log: PARKED and ACCEPTED 2026-09-29.** Built on `t2-event-log`; merged into `main` as `b16b1d4`.
- **Outcome met:** SQLite at `live_control/events.sqlite` is the append-only event source. Conversation, notes and workspace selections are rebuilt from events on startup. USER/AGENT actions carry contract actor labels, restricted actions are checked by the HTTP adapter and session, and workers read prompts by event ID. Browser and CLI fetch new events with an `after` cursor. Fresh sessions prefer `DEFAULT_MODEL` when installed. The session retains recent model turns but no duplicate in-memory event history. Unfinished prompts are marked interrupted on restart and are not replayed.
- **Evidence:**
  - `python -B -m unittest discover -s tests -v` — 25 tests passed, including event immutability and ordering, event cursor reads, event-backed HTTP state, actor rights, restart restoration, interrupted prompt handling, default-model preference, recent-turn limit, and T1 regressions.
  - `git diff --check main..HEAD` — clean.
  - `tests/test_session_state.py` — selected project, model, notes, conversation history and recent model context restored from SQLite; HTTP state omits a copied event list and `/api/events?after=1` returns only newer events. A fresh session prefers `DEFAULT_MODEL` over an earlier-listed installed model, and the projection retains only `MAX_RECENT_TURNS`.
  - `tests/test_http_smoke.py` — AGENT is denied project/model selection and approval resolution; USER patch approval still works.
- **Limitations:** model inference was not part of the T2 tests. A prompt interrupted by restart is visibly marked as an error and is not resumed automatically to avoid repeating project side effects. Pending approval UI state is transient; approval/job lifecycles and durable job-state projection belong to T3. No `job.state` events exist yet because T2 adds no job lifecycle. `DEFAULT_MODEL` and `MAX_RECENT_TURNS` are imported from `agent/engine.py`; move them to a neutral module before T6 deletes it.
- **Deferrals:** T3 lifecycles and runner, as planned; otherwise none beyond §4.
- **Next step:** reorient from the accepted T2 state and declare T3 from its provisional draft when ready.
- **Review follow-up (2026-09-29):** USER returned four findings before acceptance; all were resolved before T2 was accepted and merged: restore default model preference, remove the in-memory event-history copy, use `MAX_RECENT_TURNS`, and document optional `data.display` metadata.

**T3, lifecycles and runner: ACCEPTED 2026-09-29 after USER review fixes.** Merged into `main` and pushed at `5fd56d6`.
- **Acceptance record:** USER accepted the re-park on 2026-09-29; the final park commit is `5fd56d6`.
- **Outcome met:** New goal and Chat are separate in the browser and CLI. New goal follows the declared job path with USER plan approval; job and approval states are event-backed and restored after restart. Pending approvals expire and unfinished jobs fail on restart without replay. USER-only cancellation works. One turn runs at a time, while approval waits yield to chat; later prompts see earlier completed turns. The existing chat loop requests named, USER-approved commands from `.lab/allowlist.json`; execution uses exact argv without a shell, in the project root, with timeout, capped tail output and `command.result` events. Windows timeout and cancel tests confirm child processes are gone. The task transition table is in place without running task instances.
- **Evidence:**
  - `python -B -m unittest discover -s tests -v` — 50 tests passed in 8.365 s after USER review fixes, including ordered chat history, four parked approvals, a running goal sharing the turn slot, retained final command output, and all prior coverage. The builder environment needed filesystem access for Python temporary directories.
  - `python -B -m unittest discover -s tests -p test_approvals.py -v` — 7 approval and scheduling tests passed.
  - `python -B -m unittest discover -s tests -p test_command_runner.py -v` — 8 command-runner tests passed, including output-tail retention and the real Windows parent/child timeout and cancel case.
  - `python -B -m unittest discover -s tests -p test_patch_tools.py -v` — 4 tests passed; agent patch tools refused `.lab/allowlist.json`.
  - `python lab.py hub-server` — hub started; the browser page contained the New goal form and the authenticated state API returned a jobs list. The smoke server was stopped and its generated link file removed.
  - `git diff --check 54c6134..HEAD` — clean; `git diff --unified=0 54c6134..HEAD -- PLAN.md` shows no edits to §§3–4. Largest Python module: `session.py`, 400 lines.
  - D12–D15 record T3 event-data and output-contract decisions; `docs/CONTRACTS.md` reflects them.
- **Limitations:** live Ollama inference was not part of the automated or hub smoke tests. Goals receive notes but no chat history. A chat-started command stops only by timeout because chat has no USER cancellation path. Command output is duplicated in `tool.result` display text as well as `command.result.data.output`. Cancellation is checked at model/tool boundaries; a tool call already in progress may finish. An abrupt hub crash marks a running job failed on restart but cannot stop a command process that was already running when the hub died.
- **Deferrals:** the T6 role team, gate and task execution remain as planned; otherwise none beyond §4.
- **Next step:** T4 knowledge layer, declared and active on `t4-knowledge`.

**T4, knowledge layer: PARKED 2026-09-30 on `t4-knowledge`; accepted by USER 2026-09-30, merged to `main` at `c287f3f`, and pushed with the acceptance record by `6f9c388`.**
- **Outcome met:** The selected project has a separate SQLite knowledge index with deterministic Python/Markdown chunks and summaries, FTS5, stored embeddings, and an `ast` graph. Hybrid retrieval uses reciprocal rank fusion and keyword-only fallback. Chat and goal turns receive bounded context packs. Project scans run at selection/startup and idle boundaries; they detect outside edits by hash, retry missing embeddings, and remove stale files. USER-approved patch paths are queued for refresh, and indexing waits until turns and commands are idle. `session.py` remains at 400 lines. No event or contract schema changed.
- **Evidence:**
  - `python -B -m unittest discover -s tests -v` — 71 tests passed in 11.131 s after the T4 review fixes.
  - `python -B -m unittest discover -s tests -p "test_context_pack.py" -v` — 2 tests passed, including §5 shape/accounting and the session path.
  - `python -B -m unittest discover -s tests -p "test_knowledge_integration.py" -v` — 6 tests passed: selection/restart scans, outside-edit refresh before a turn, approved-patch refresh and rejected-patch refusal, missing-embedding retry, keyword fallback, and no indexing during a turn or command.
  - `python -B -m unittest discover -s tests -p "test_architecture.py" -v` — 2 tests passed.
  - `git diff --check 5fd56d6..HEAD` — clean. `git diff --unified=0 5fd56d6..HEAD -- PLAN.md` shows no edits to §§3–4.
  - T4 review fixes: all five session tests that invoke `context_for` inject a fake embedder, so automated tests do not call Ollama; empty context-pack results are omitted from notes.
- **Limitations:** Automated tests use fake/dummy embedders, including session-level tests; live Ollama inference was not run. The indexer handles allowed `.py`, `.md`, and `.markdown` files. Scan errors are retained in the knowledge service but are not shown in the browser. Full hash scans happen before a new turn, so large projects may delay turn startup; unchanged files skip chunking and embedding.
- **Deferrals:** none beyond §4.
- **Next step:** Review the T5 Bench declaration in §7. T5 remains provisional until USER approval.

**T5, bench: PARKED and ACCEPTED 2026-10-01 on `t5-bench`; merged into `main` after USER acceptance.** The implementation task commits are `fe10979` (builder output protocol), `0268b91` (record builder benchmark), and `b737676` (reproduction docs and role test); review fix `b7a15b9` validates recorded metrics and selection. The independent review returned one medium validation finding; it was fixed, verified and re-reviewed before this re-park. The acceptance record is committed on `t5-bench` before merge.
- **Outcome met:** The repo-pinned corpus contains 22 hole-punched tasks from `self@c916053`; each task uses its single named test. The harness builds context only from punched copies and gives both conditions the same goal, target path and punched file; the context condition adds the bounded T4 pack. The harness completed 132/132 attempts across qwen3.5:9b, 4b and 2b, with and without context. The measured builder is qwen3.5:9b using the T5-frozen thinking-on protocol; T6 roles remain configured.
- **Evidence:**
  - `python lab.py bench validate --baselines --contexts` — 22 pristine checks passed, all 22 punched checks failed as intended, and 22 context packs passed answer-leak checks.
  - `python lab.py bench run --confirm-gpu-free` — 132/132 attempts completed after USER confirmed the GPU was free; raw run `20261001T124335Z-a0abb772`.
  - `python lab.py bench record <raw-result-file>` — imported `bench/results/20261001T124335Z-a0abb772.json`; selected qwen3.5:9b and wrote `roles.json`.
  - `python lab.py bench validate --results` — 22 task records valid; 1 committed result valid; `roles.json` builder is measured.
  - `python -B -m unittest discover -s tests -p "test_roles.py" -v` — 1 role-config test passed.
  - `python -B -m unittest discover -s tests -p "test_bench_runner.py" -v` — 7 tests passed, including rejection of stored metrics or builder choices that contradict the attempts.
  - `python -B -m unittest discover -s tests -v` — 95 tests passed.
  - `python -B -m unittest discover -s tests -p "test_architecture.py" -v` — 2 tests passed.
  - `git diff --check` — clean before parking.
  - Review resolution: `python lab.py bench validate --results` recomputes every committed run's metrics and chosen builder from the attempt rows; the recorded run passes this stronger check.
  - The second read-only review found no further code issues; its temporary-state note about AGENTS.md was resolved by this re-park update.
  - The recorded run used source commit `fe109793e85c5e3c03619f5aa896268602329892`, all three candidates were available, and top-five search recall was 0.682. qwen3.5:9b passed 16/44 attempts (36.4%), invalid-output rate 29.5%; its pass rates were 31.8% without context and 40.9% with context. qwen3.5:4b passed 6/44 (13.6%), invalid rate 47.7%; qwen3.5:2b passed 2/44 (4.5%), invalid rate 65.9%.
  - The probe raw replies are in `%TEMP%/local-memory-lab-bench/probe-20261001T123927Z-qwen35-9b-thinking.json`. On the same `self-001` input, `think:false` returned a whole-file payload that failed the required one-edit schema (193 eval tokens); `think:true` returned schema-valid JSON (281 eval tokens) but failed the task check. The probe selected `target-stub-one-edit-json-think-enabled-v5`, `think:true`, and a 4,096-token output cap; the full run, not the probe, selected the model.
  - Earlier exploratory runs are history only: v1 (`20260930T153923Z`, 75/132 invalid; qwen3.5:9b 27% pass); v2 (`20260930T170215Z`, 1/4 invalid); v3 (`20260930T170647Z`, 23/23 invalid with `think:false`); v4 (`20260930T171151Z`, 132/132 invalid with `think:false`). Only v5 is committed and selects a role.
- **Limitations:** The comparison covers 22 tasks from one pinned commit and one attempt per task/model/condition; results are model- and prompt-specific and may vary. Of 63 invalid replies in run `20261001T124335Z-a0abb772`, 62 stopped exactly at `eval_count == 4096`, the output cap; all 13 invalid qwen3.5:9b replies did so. In 62 of those capped replies, answer text was empty, meaning the output limit went to thinking. The invalid-output rate mainly reflects the output cap, not the model's code quality: qwen3.5:9b passed 9 of 10 context attempts that produced valid output. Context improved qwen3.5:9b (9 vs 7 passes) and qwen3.5:4b (5 vs 1); qwen3.5:2b tied (1 vs 1). The output-protocol probe used one task per setting; the full run provides the broader measure. Probe replies and earlier v1–v4 raw runs remain local in `%TEMP%` and are not committed.
- **Deferrals:** No T6 role orchestration or deterministic gate was added; no event or contract schema changed. Other deferrals remain in §4.
- **Next step:** T6 is declared in §7 for USER review. T5 notes:
  - Set the builder's output limit or thinking budget as part of T6's builder setup, and check it there.
  - Save the model's thinking text as well as its answer on invalid replies; the answer-only excerpt was empty in 62 of 63 rows.
  - `roles.json` sets the debugger to `think: false`, which the T5 probe showed breaks the required output format on qwen3.5:9b. Do not use that setting in T6 unless it is shown to work.

**T6, team: PARKED and ACCEPTED 2026-10-02 on `t6-team`; merged into `main` after USER acceptance.** Task commits: `9c7e6c5` (task 1 substrate), `a40580f` (task 2 planner), `10a0259`/`9248147` (task 3 pipeline), `c4d0637` and `94e6614` (task 4 retirement and proof); park-review fixes `8fdb0e5`, D20 and docs `c5016b0`, reason codes `f6a1dd9`.
- **Outcome met:** a New goal is planned by the planner into 1–5 validated tasks with one `target` each before the USER approves the plan. Each task runs check-first (D20), then builder, check, up to two debugger rounds, reviewer and gate, only inside a disposable task workspace (D16). Only a gated patch the USER approves reaches the project, through the transactional apply with new-file support, drift check, backup and re-index. Every task and job ends with a recorded reason code (D17). Chat is answer-only; `run_turn`, the tool router and the `create_project_file` bypass are deleted. Roles (D4): qwen3.5:9b builder and debugger with thinking on; qwen2.5-coder:14b planner (temperature 0) and reviewer with thinking off; every sampling option is sent explicitly.
- **Evidence:**
  - `python -B -m unittest discover -s tests -v` — 144 tests pass.
  - `python -B -m unittest discover -s tests -p "test_architecture.py" -v` — passes: only `workspace/` writes project files, Chat sends no tools, source names no location outside the repo, no import cycles.
  - `git diff --check main..t6-team` — clean. `rg -n "run_turn|agent\.engine|create_project_file" src tests` — no matches. Vendor-name scan of `src`, `tests`, `docs` and `roles.json` — no matches.
  - Size: largest module 309 lines (`bench/runner.py`); `session.py` 300 lines (400 at the start of T6); `src` 4,831 lines; T6 diff against `main` is 5,719 insertions and 1,079 deletions across 70 files, most of it bench records and tests.
  - `python lab.py bench team --confirm-gpu-free` — run `20261002T172029Z-22830cfd`: planned one correct task, the check failed first, the debugger fixed it on round 2, the reviewer passed it correctly, the live project was unchanged while the patch approval was pending, and the applied change passes the live tests and is indexed (49 s, all calls fully on the GPU). Run `20261002T171904Z-89fe1a23` is the no-debugger path (34 s).
  - Role and planner probes: `bench/probes/roles-*.json` and `planner-*.json` (task 1 and 2 evidence in §7).
  - Full-pipeline bench (`bench/experiments/2026-10-02-pipeline-bench.jsonl`): 12 of 22 goals pass (tune 7/15, held-back 5/7) against 9 of 22 for the builder alone; 5 debugger rescues.
  - Reviewer park review T6-park-1 verified the cancel, approval and indexing paths with every model call blocked; its fixes and the USER's D20 decision are acknowledged in `8fdb0e5` and `c5016b0`.
- **Limitations:** the real-model proof is a single-task job. The output cap remains the main pipeline loss (6 of 10 bench failures), and neither a larger budget nor a retry without context recovered it. The reviewer catches few logic mutants inside new functions (1 of 9), so D20 requires a check that fails before the change; a goal adding new behaviour together with its test needs an existing failing test. Bench results are one run per goal.
- **Runtime artifacts:** no background process is running. `live_control/work/` (throwaway experiment tools and logs, about 100 MB) and `live_control/tmp/` are git-ignored runtime folders; the 129 empty job folders that L1 now prevents were removed from `live_control/scratch/`.
- **Deferrals:** the §7 "Carry to T7" list (L2 task-state sequence, L3 citation length, multi-task proof, cap losses, D20 option (c)); §4 is unchanged.
- **Next step:** reorient against `main` and declare T7 from its draft in §5, with the "Carry to T7" list, for USER approval.

**T7, self-development and park: PARKED and ACCEPTED 2026-10-03 on `t7-selfdev`; merged into `main` after USER acceptance, which meets S8. The prototype is complete (§3).** Permission `87e61a8`; selfdev setup `39aff87` (goal checks `14f2662`, amended before any attempt as `05e0015`); G1 by the team `e1c673f`, merged as `e448284`, check moved `0d2ff41`; S9 pass `ba331c8`; S10 README `be525c7`; reviewer probe `7cb37a1`; builder samples `0543703` and `1bf513f`.
- **Outcome met:** the local team turned a pre-registered goal from its own backlog into a tested change that the USER approved in the hub, with local models only (D8). G1, the stricter citation rule (carry item L3), passed on its first attempt: one task, check-first, builder, reviewer and gate, about 2 min 47 s including the USER's approvals. It passed the D8 normal review (`T7-g1-1`) and is merged into `t7-selfdev` with `selfdev` credited (`Actor: ROLE`). S8 is met when `t7-selfdev` merges into `main` after USER acceptance. G2 (stopped by the USER after 2 attempts) and G3 (planning failures) did not pass; both are recorded in §7.
- **Evidence for each stop condition:**
  - **S1:** `git clone --branch t7-selfdev . <temporary folder>`, then `python -m pip install -r requirements.txt` (already satisfied; nothing installed), then `python -B -m unittest discover -s tests`: 146 tests pass. `python lab.py hub-server` started in the clone and `python lab.py client status` answered with an empty session; the hub was then stopped. Outside-path and import isolation: `tests/test_architecture.py` passes.
  - **S2–S5:** the T2, T3 and T4 records above; their tests are in the passing suite.
  - **S6:** the T6 record above.
  - **S7:** the T5 record above. Reviewer probe rerun after G1, run `20261003T110738Z-78034a35` (`bench/probes/`, a free GPU): the configured 14b reviewer gave no invalid reply in 20 cards, passed 9 of 10 clean cards and caught 10 of 10 seeded-bad cards (T6: 9 of 10 and 7 of 10). An offline re-score of the T6 probe replies found that none of the 9 valid fails turns invalid under the G1 rule. A first rerun shared the GPU with another program and was stopped, not recorded.
  - **S8:** `git log --grep "^selfdev"` shows `e1c673f`. The attempt records are in §7, and exports for all five jobs are in `bench/selfdev/`. Job events show `system` and `user` actors only.
  - **S9:** `python -B -m unittest discover -s tests -p "test_architecture.py" -v`: 5 tests pass (no import cycles, core never imports interfaces, only `workspace/` writes project files, no outside locations). The largest module is 309 lines (`bench/runner.py`); `src` totals 4,828 lines (T1 met its 2,250-line limit at 1,104 lines; later tranches added the knowledge layer, bench and team). Dead-code pass with a standard-library `ast` scan: one unused constant was removed; the remaining hits are `ast.NodeVisitor` methods, which are called by name.
  - **S10:** README intro and knowledge paragraph corrected (`be525c7`); the status lines in README, AGENTS.md and PLAN.md are updated in this park. `rg -n "in progress|not yet declared|awaits USER" README.md AGENTS.md PROJECT.md docs` finds only the T7 state line, which this park replaces. The vendor-name scan of `src`, `tests`, `docs`, `roles.json` and the top-level docs is clean, and `git diff --check main..t7-selfdev` is clean.
- **Experiments (USER requests, measurement only, `roles.json` unchanged):**
  - Builder at temperature 0.6 (`bench/experiments/2026-10-03-builder-temperature-sample.jsonl`): on three tasks that loop at temperature 0 at any budget, 5 of 6 runs stopped looping but 0 passed; two tasks that pass at temperature 0 still passed.
  - Outline first, then build (`2026-10-03-builder-outline-sample.jsonl`): 2 of 3 looping tasks still hit the cap, the third finished but failed; the two passing tasks still passed. The capped thinking shows the model rewriting full code against details it cannot see, such as exact values a test expects. A plan does not supply that information.
  - Together they say the cap marks tasks the builder cannot solve as posed, not answers it nearly found.
- **Limitations:**
  - Only G1 of three goals passed. G2's candidate relied on `fnmatch.fnmatch`, which ignores case only on Windows; the frozen G2 check passes on Windows for such a fix. The planner (14b) planned G3's new function as a new file on every run, so the multi-task proof carried from T6 is unproven.
  - The event log holds 2 G3 jobs; reviewer note `T7-g1-1` reports 3. The USER closed the attempts with G3 not passed.
  - A reviewer `review_failed` or `invalid_output` keeps reasons and quote but not the candidate, and an `invalid_plan` keeps only the validation error, not the planner's answer, so failed attempts cannot be fully inspected afterwards.
  - The output cap remains the main builder loss; sampling and an outline step did not recover it (above).
  - L2: the pre-build check is recorded as `building`, not `testing`.
  - `lab.py hub-server` ignores extra arguments, so `hub-server --help` starts a second hub that overwrites `live_control/shared.json`. This happened once during T7; the extra hub was stopped and the real one restarted.
  - D9 slip: 14 AGENT commits from `b57f2d3` on ended with a co-author line that named an agent vendor. History was rewritten on USER instruction (2026-10-03, `git-filter-repo`, commit messages only; file contents unchanged). Hashes quoted in the docs are updated; recorded data files keep the old hashes, which `bench/history-rewrite-2026-10-03.txt` maps to the new ones.
- **Runtime artifacts:** the selfdev worktree `C:/Jacob/_AppDesign/_SANDBOX/.tool-user-selfdev` (on `selfdev/base`, with the USER-written `.lab/` that is not tracked) stays as D8 infrastructure. `live_control/work/` holds the throwaway experiment tools and logs, git-ignored. No background process is running except the USER's hub.
- **Deferrals:** none beyond §4; §3 and §4 unchanged.
- **Next step:** after USER acceptance, merge `t7-selfdev` into `main` (S8 met) and push. The prototype is then complete. Any further work starts from a new USER decision on the §4 Deferred list. The evidence above points first at the builder's missing information (for example giving it the check's expectations) and at recursive decomposition (§4).
