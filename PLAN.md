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
  | Planner | `qwen3.5:35b` (MoE) | 13.4 tokens/s with part of it on the CPU; JSON schema output and thinking mode OK. T6 task 2 measures it against 9b and the 14b coder, because it can never be fully GPU-resident here (§1) |
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
- **D17 `task.state` payload (2026-10-01, with the T6 amendment; effective on USER approval of T6):** the first `task.state` event for a task fixes its immutable specification in `data`: `id`, `title`, `description`, `target`, `files`, `check` and `order`. `target` is `{"path", "symbol", "new"}`: an existing function, class or method (`new: false`); a new top-level function in an existing Python file (`symbol` is its plain name, `new: true`; amended 2026-10-02, USER); or a new file (`symbol: ""`, `new: true`). `files` is exactly `[target.path]`. Later events carry `data.state`, `data.reason` (a reason code such as `invalid_output`, `cap_exhausted`, `check_failed`, `debug_exhausted`, `review_failed`, `path_outside_task`, `patch_too_large`) when failed, `data.debugRound`, and `data.failure` for an unusable role reply (reason, error, first 2,000 characters of the answer, last 2,000 of the thinking, `evalCount`, `capHit`). The planner does not choose task IDs; code assigns them. No new event kind or top-level field is added.
- **D18 Approval task identity and provenance (2026-10-01, with the T6 amendment; effective on USER approval of T6):** approvals record `task` when they belong to one. Candidates are attributed to the ROLE that produced them (builder or debugger). SYSTEM, as lifecycle owner, requests the final patch approval after the gate and records `data.originRole` and `data.candidate`; ROLEs still propose only through an approval (`docs/CONTRACTS.md` §0). No new event kind or top-level field is added.
- **D19 Task check approval (2026-10-01, USER decision with the T6 amendment; effective on USER approval of T6):** approving the plan covers each task's single named check, run only in that task's scratch workspace. Every command against the selected project still needs USER approval each time it runs. For task checks only, the scratch workspace counts as "the project folder" in S4. The check's allowlist is read from the live selected project when the plan is made, never from the scratch copy, and plan validation rejects any task whose `files` include the allowlist file. The plan approval view shows the exact check command and arguments for each task.

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
- Edit-task bench for the builder and debugger: real edits from history, each with a check that fails before the edit and passes after (USER, 2026-10-02; a candidate for T7's bench rerun). T6's role probe covers edits for the reviewer only.

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
| T7 | **Self-development and park:** set up the selfdev worktree and its allowlist, run the three pre-registered goals (D8), merge the ones that pass, run the full bench, size and import-graph checks, docs, push. **End.** | Evidence for every stop condition | S8, S9, S10 |

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

**ID:** T6 — Team (amended 2026-10-01 after the reviewer's return note on `4b2600c`).

**Current:** T1–T5 are accepted on `main`; T5's bench, results and role configuration are merged from `t5-bench`. S2–S5 are complete. The builder part of S7 is complete; the remaining per-role evidence closes with T6's real-team run. §3 stop conditions and §4 non-goals remain frozen (D6). T6 was returned for amendment, is re-declared below for USER review, and has no implementation permission. Known live bug until task 4: Chat's `create_project_file` tool writes a new file without approval (`agent/tool_router.py:52`); avoid Chat on a real project until it is removed.

**Branch:** `t6-team`.

**Expected outcome:** replace the free-running `run_turn` path with a durable planner → builder → debugger → reviewer team on the T3 job machine. A goal is planned into 1–5 bounded tasks before the USER approves the plan. Each task's candidate is built, checked, debugged and reviewed only in a disposable task workspace (D16); only a gated patch the USER approves reaches the selected project, through the transactional apply. Jobs end with visible success, failure or cancellation (S6; `docs/CONTRACTS.md` §§2–4 and 7–8).

**Central invariant (D16):** an unapproved candidate is created, tested, debugged and reviewed only in a disposable task workspace. Until the USER approves the final gated patch, the selected project is unchanged, byte for byte.

**Scope (task list, in order):**
1. **Contracts and execution substrate.**
   - Disposable task workspace (in `workspace/`): copy the selected project through the workspace exclusion rules (no `.lab/`, no generated folders), not the bench's `git archive`; record each planned file's before-bytes (or absence) at copy time so `staged_apply`'s drift check rejects a stale candidate.
   - Durable task records: a Tasks projection in `SessionState` from `task.state` events (D17); fail fast, so a failed, rejected or exhausted task fails the job and later tasks do not run.
   - Approvals carry `task` in both projections; candidates are attributed to the role that produced them; SYSTEM requests the final patch approval after the gate, recording the originating role and candidate (D18).
   - Gate path rule: every edited or created path must already be in the USER-approved task's `files`; a listed path that does not exist yet allows creation there (D16). "Or declared in `new_files`" is removed.
   - Role config: `roles.json` carries `num_predict`, `timeout_s`, `think` and `keep_alive` per role; callers hard-code none of them. An opt-in probe (about 5 bench tasks with context, after USER GPU-free confirmation) sets the builder and debugger budget and timeout together and records per-role time, tokens/s and invalid rate, plus the reviewer comparison. The debugger uses `think: true` with the builder's budget unless the probe proves `false` works; `test_roles.py` asserts the result. If the builder is still invalid on more than 1 of 5 with-context tasks at the largest budget the timeout allows, trim the context budget and re-probe once; if it still fails, stop and report to the USER. The timeout is never raised silently.
   - Narrow role forms: a task names one `target` (D17), one file plus one symbol or one new file; code extracts that symbol's source and pins it as `search_block`; the builder (and debugger) writes only the replacement, or one `content` for a new path the plan lists. `format` schemas restrict builder/debugger paths to the task's files, the planner's `check` to allowlisted names, and the planner's `target.path` to existing paths unless flagged `new`. The debugger's input is pre-filled with the failing check output and the previous attempt.
   - One shared structured-call helper for Ollama; no fourth HTTP client.
   - `docs/CONTRACTS.md` §§1–3 and 7–8 are updated for D16–D19 in the same commit that implements them (planner `target`, gate path rule, no `new_files` widening, role config fields), so docs never lag the code (S10).
   - Reviewer, card-shaped: one card per task built from data the job already holds (before: the pinned `search_block`; after: the candidate's replacement; intent: the approved task description; facts: the check result and the gate's path and size results; optionally the task's T4 pack). Targeted questions: does the change do what the task says, and does it alter behaviour the task did not ask for? Output is the schema-constrained `{"verdict", "reasons"}` with a `num_predict` cap, and a `fail` must cite a line in the card. The reviewer model (qwen3.5:35b from D4 or qwen3.5:9b) is chosen by the role probe, which uses only cards whose check passed, as production does: clean candidates from T5 attempts that passed their check, plus a few seeded known-bad candidates that still pass their check (for example, a change that alters behaviour outside the task's request). It scores `pass` on clean cards and `fail` on seeded-bad ones; D4 is updated from that measurement. Evidence for this shape: the reviewer AGENT's 25 practice reviews of local reviewers (outside this repo; observation only), where one-shot 35b looped or truncated in 3 of 4 runs and per-function cards on 9b did best. Nothing here imports, calls or references that oversight tool; only the ideas are ported (D1).
2. **Real planning before plan approval.** The job goes `queued → planning` (planner runs) → tasks persisted → `awaiting_plan_approval` (the USER sees the real tasks) → `running`. Code validates every task before approval: normalized relative paths only, no absolute paths, no `..`, no `.lab/`, no duplicate or case-colliding paths, a bounded file count, a real allowlisted `check` read from the live project's allowlist (D19), a check for every new-function task that exercises its new behaviour (the task 1 reviewer limitation), no task whose `files` include the allowlist file, and exactly one `target` per task with `target.path` in `files` and `symbol` resolving to exactly one definition (via the T4 AST graph) or `new` naming an absent path. The plan approval view shows each task's exact check command and arguments. Approved tasks are immutable. T3 tests that encode the goal-text placeholder are updated deliberately.
3. **Candidate execution in the workspace.** Before building, the task's check runs in the task workspace; if it already passes, the check does not exercise the change and the task fails as `check_not_exercising` (USER, 2026-10-02). The builder edits the task workspace and the check runs there; a failing check goes to the debugger for at most 2 rounds; a passing check goes to the reviewer, then the gate. Distinct reason codes select the next step: `cap_exhausted` (one retry with a larger budget or trimmed context), `check_failed` (debugger), `debug_exhausted` / `review_failed` / `path_outside_task` (task fails; job fails fast); malformed output (`invalid_output`) is never counted with a gate refusal. Every unusable role reply records an answer excerpt (first ~2,000 characters), a thinking excerpt (last ~2,000), `eval_count` and whether the cap was hit. Task checks run under plan approval in the task workspace only (D19).
4. **Application, retirement and proof.**
   - Apply: the USER approves the gated diff; a drift check runs, then the existing transactional apply and backup.
   - `staged_apply` accepts a "was absent" before-state: drift check (still absent), backup record, and rollback that deletes the created file.
   - Close the approval bypass: remove `create_project_file`; afterwards the only mutation path is candidate → gate → USER patch approval → transactional apply.
   - Knowledge: one full sync before planning; no indexing during a role call; after an approved patch, refresh only the changed paths and wait for it before building the next task's context. `end_activity` no longer schedules a full rescan at every idle boundary.
   - Chat becomes a bounded conversational surface: history plus a T4 context pack, with no project tools, no mutation and no commands. New goal is the only entrance to the team. `run_turn`, `agent/engine.py` and the tool router are deleted, not renamed.
   - Cancel and restart remove scratch workspaces and child processes. Update adapters, contracts and docs.
   - Run the opt-in real-model job and park.

**Progress:**
- [x] 1. Contracts and execution substrate (workspace, task records, approvals, gate rule, role config and probe, narrow forms, one transport).
- [ ] 2. Real planning before plan approval, with code validation and immutable tasks.
- [ ] 3. Candidate execution in the workspace with reason codes and thinking capture.
- [ ] 4. Apply with new-file support, bypass closed, knowledge refresh, Chat replaced, `run_turn` removed, real-model proof.

**Now:** parked 2026-10-02 in task 2. Planning-before-approval code is committed (`09e4d53`, probe `9371900`); task 2 stays open until a planner is chosen. Planner probe (`bench/probes/planner-20261002T120743Z-555dc2ae.json`, clean `9371900`, 22 bench goals): qwen3.5:9b gave 10 valid plans, 7 on the gold target, 8 cap-outs from thinking loops over task count and check choice, 43 s average; qwen2.5-coder:14b gave 6 valid, 4 on target, but its first task named the gold target on 14 of 22, and 16 plans were rejected for extra tasks (existing test files marked new, unresolved symbols, repeats), 27 s average; qwen3.5:35b (5 goals, 55% on GPU) gave 2 valid, 195 s average, and is dropped. Proposed next, awaiting USER approval: a transient comparison of (A) the single-shot planner with two prompt fixes (fewest tasks; use the only check) against (B) a narrowed planner (code picks the check when there is one; ask for one target with full context; validate; retry once with the exact validation error; then ask whether another definition is required, up to 5), on 9b and the 14b coder, over the 22 goals plus about 4 two-function goals from real commits. Adopt the winner with tests, set the planner in `roles.json`/D4, record the evidence and tick task 2; then task 3. Also planned: flag a prompt Ollama truncated, using its reported prompt token count.

**Task 1 evidence (2026-10-02):**
- Substrate: task workspace, D17 task records, D18 approvals, D16 gate, role config, narrow forms, card reviewer with a required `quote`, one Ollama transport, per-call model unloading and GPU-residency recording. `python -B -m unittest discover -s tests -v`: 119 tests pass. `test_role_steps.py`, `test_gate.py`, `test_task_workspace.py`, `test_role_probe.py` and `test_roles.py` cover them.
- Builder and debugger (`bench/probes/roles-20261002T023003Z-fcc36f5a.json`, 9b, 8,192 budget, context on): builder 4 of 5 valid (1 cap hit), 1 passed; debugger 4 of 5 valid, 3 passed. Within the threshold of at most 1 invalid in 5.
- Reviewer (`bench/probes/roles-20261002T104736Z-40904584.json`, 20 cards per model: 10 creation, 10 from real edits in `bench/review_edits.json`, all fully on the GPU): qwen2.5-coder:14b passed 9 of 10 clean cards and caught 7 of 10 plants at about 3 s per card; qwen3.5:9b with thinking passed 9 of 10 clean but caught 1 of 10 and hit its 6,144-token cap on 6 cards. qwen3.5:35b was dropped: it can never be fully GPU-resident here (§1). Decision: qwen2.5-coder:14b, thinking off (D4).
- Transient comparisons on the 14b, run outside the repo and not adopted: with plain plants, the single pass caught 10 of 10 side-effect plants and passed 9 of 10 clean cards. Two narrow passes on a stripped card passed only 3 of 10 clean cards with 31 false claims. A progressive two-pass (full card each time, pass 1 feeding pass 2) passed 9 of 10 clean and caught 9 of 10 plants, but made 6 false intent claims. Code-computed effect facts on the card lowered catches (side effects 10 to 5, logic 7 to 3).
- Logic mutants that still pass the tests (16 across 10 functions): the single-pass 14b caught 6 of 7 on edits but only 1 of 9 inside new functions, where BEFORE is a stub. Limitation carried into task 2: a new-function task needs a check that exercises its behaviour, because review cannot stand in for it.
- Measurement note: Ollama's `eval_count` omits thinking tokens on completed replies but reports the full count when the cap is hit; tokens/s stays correct, and elapsed time is the true cost.

**USER decision recorded:** D19 (T6-amend-1): plan approval covers each task's single named check in its task workspace only; commands against the selected project still need per-run approval.

**Non-goals:** T7 self-development goals and the selfdev worktree; re-planning on failure (T7 candidate); parallel jobs or simultaneous role steps; the wider multi-file builder form (needs a benchmark first); new model providers or dependencies; changes to §3 or §4; event or contract schema changes without a §2 decision; automatic approval by a model; browser redesign; bench hardening (line-level leak check, bench thinking capture) before T7; and work beyond S6.

**Acceptance criteria:**
- Role schemas, config loading (`num_predict`, `timeout_s`, `think`, `keep_alive`), planner validation and thinking capture: `python -B -m unittest discover -s tests -p "test_role_steps.py" -v` and `-p "test_roles.py"`. A stubbed role returning empty content with thinking text and `eval_count == num_predict` is recorded as `cap_exhausted`, and the thinking excerpt survives replay.
- **Plan and paths:** plan approval shows the planner's tasks, not the goal text; approved tasks cannot be changed by any role; validation rejects absolute paths, `..`, `.lab/`, duplicates, oversized task lists, unknown checks, a missing or unresolvable `target`, and any task whose `files` include the allowlist file; the allowlist is read from the live project, not the scratch copy: `-p "test_team_plan.py"`.
- **Isolation and new files:** rejected, failed, cancelled, timed-out and still-pending tasks leave the selected project byte-for-byte unchanged; `staged_apply` creates a new file, rejects the patch if that path appeared meanwhile, and rollback deletes a file it created: `-p "test_task_workspace.py"`.
- **Gate:** named check, reviewer pass, task-file limit (including paths the builder declared as new) and patch caps: `-p "test_gate.py"`.
- **Pipeline and failure paths** with stubbed roles: planner invalid JSON, builder cap exhaustion, illegal path, check failure, debugger exhaustion, reviewer fail, USER patch rejection, cancel during a model call, cancel during a command, restart with a pending approval, an outside edit while approval is pending, and stale-candidate application. Approvals keep their job and task identity after replay and show the same in browser and CLI. A change applied by task 1 is retrievable in task 2's context, and no full rescan runs between role calls: `-p "test_team_pipeline.py"`.
- **Bypass closed:** an architecture test proves no module outside `workspace/` writes under the selected project root, and Chat has no tools: `-p "test_architecture.py"`.
- **Isolation, tightened (S1):** `test_architecture.py` rejects any import of, or path to, a location outside the repo, not only `.parts-bin` by name.
- **Reviewer:** a stubbed reviewer `fail` that cites no line from the card is rejected as invalid output and is not counted as a verdict; the reviewer's input is limited to the task card and its optional context pack: `-p "test_role_steps.py"`.
- **Old path gone:** `rg -n "run_turn|agent\.engine|create_project_file" src tests` returns no product references.
- **Role probe (opt-in, GPU-free confirmation):** `python lab.py bench roles --confirm-gpu-free` records per-role time, tokens/s and invalid rate with the configured budgets, and each reviewer model's verdict accuracy on check-passing cards (clean and seeded-bad); the builder is invalid on at most 1 of 5 with-context tasks, and raw replies including thinking text are kept.
- **Real-model proof (opt-in, GPU-free confirmation):** `python lab.py bench team --confirm-gpu-free` runs one deliberately boring job on a disposable project with one known failing check so the debugger runs; it passes reviewer and gate, waits at USER approval while the live project is shown unchanged, then is approved, applied and refreshed, and the job succeeds. Per-role timings, model load/unload times, and the reviewer's verdict, reasons and whether the verdict was correct are recorded.
- Full suite, docs and size: `python -B -m unittest discover -s tests -v`; `git diff --check`; no module over 400 lines and `session.py` no larger than today.

**Known risks:** the 35b planner/reviewer (~14.5 GB) and the 9b builder/debugger swap in 16 GB VRAM at every role change; `keep_alive` and per-role timeouts are set in task 1 and the real run records load times. Raising `num_predict` costs time (~8k tokens ≈ 150 s at ~55 tokens/s against a 180 s timeout), so the cap and timeout move together. Per-function review cards miss bugs from two functions interacting across files; the task's check must cover those. A false reviewer `fail` costs a rerun because the job fails fast; a false `pass` is still caught by the check and USER approval. Narrow one-region tasks may make some goals need several tasks; re-planning is deferred to T7 pending T6's reason codes. Copying large projects into a task workspace costs time; the exclusion rules keep it bounded. `session.py` is at 400 lines, so new ownership goes in a small `team` area (roles and config, task orchestration, workspace and gate). The Chat bypass bug stays live until task 4.

**Declaration state:** T6 declared 2026-10-01 after USER acceptance of T5; returned for amendment by the reviewer the same day; re-declared with this amendment; reviewer note T6-amend-1 (accept after F1 target field and F2 reviewer probe) applied with D19, pending USER approval.

---

### Previous declaration: T5 — Bench

**ID:** T5 — Bench (accepted; retained for implementation history).

**Current:** T1–T4 are accepted on `main`; T4's code is merged at `c287f3f`, and the accepted state plus revised T5 declaration are pushed at `6f9c388`. Its full suite passed 71 tests. S3–S5 are complete; §3 stop conditions and §4 non-goals remain frozen (D6). T5 was approved by USER on 2026-09-30 and remains active during a USER-requested long pause; this is a progress checkpoint, not a tranche park or acceptance.

**Branch:** `t5-bench`.

**Expected outcome:** a reproducible, committed bench of at least 15 real-code hole-punch tasks from a pinned T1–T4 repository snapshot; measured top-five search quality and builder success with/without context; and a `roles.json` assignment supported by builder model results (D3, D4, S7, `docs/CONTRACTS.md` §§7–9).

**Scope (task list, in order):**
1. Pin the T5 source snapshot by commit. For each task, replace one covered function body with its original leading docstring (if any) followed by `raise NotImplementedError`, preserving its signature and decorators. Give the task a goal, target path, exact gold files and one test ID in the §9 task format. Require at least 15 valid tasks. Use only D3's trimmed fixture fallback if this repo yields fewer than 15; synthetic tasks need a specific `synthetic_reason`.
2. Build task validation and an isolated harness. Verify each unmodified task passes its single test and each hole-punched task fails that test. Build that task's T4 knowledge index from its punched copy, never the pristine source, and test that the resulting context pack does not contain the removed body. For both conditions, give the builder the same goal, target path and punched target file; the with-context condition additionally receives a T4 pack with the §5 budget of 6,000 tokens. Validate the §7 JSON output, constrain edits to declared files, score search results against gold files, and leave the pinned snapshot and working tree unchanged.
3. Run one attempt per model, task and condition at temperature 0, with a 180-second per-task timeout, using only these builder candidates: `qwen3.5:9b`, `qwen3.5:4b` and `qwen3.5:2b`. Do not silently substitute other models; record unavailable candidates. `qwen2.5-coder:14b` and `ms-ae:latest` stay excluded while `OLLAMA_NUM_PARALLEL=8` per D4. Record per-model pass rate, duration, tokens/s, invalid-output rate, top-five search quality, and builder success by context condition. Set `roles.json` from the measured evidence, retaining other role assignments from D4 for T6.
4. Commit the task corpus, harness, results, role configuration and concise run/reproduction documentation; run focused checks and the full regression suite.

**Progress:**
- [x] 1. Pin the snapshot and create/validate at least 15 covered hole-punch tasks (D3 fallback only if needed).
- [x] 2. Implement isolated task validation, builder runs, context comparisons and search scoring.
- [x] 3. Run the named local model comparisons and record required metrics; set `roles.json` from results.
- [x] 4. Document reproduction, verify the full suite and park with committed evidence.

**Task 1 evidence:** 22 task records pin `self@c916053`; all 22 named tests passed on the pristine snapshot and failed after their target body was punched in a separate temporary copy. `python -B -m unittest discover -s tests -p "test_bench_tasks.py" -v` — 3 helper tests passed.

**Task 2 evidence:** `python lab.py bench validate --baselines --contexts` — 22 original tests passed, 22 punched tests failed as intended, and all 22 packs built from punched copies excluded their removed bodies. Focused harness and scoring tests pass.

**Task 3 evidence:** after USER confirmed the GPU free, `python lab.py bench run --confirm-gpu-free` completed 132/132 attempts and `python lab.py bench record <raw-result-file>` selected `qwen3.5:9b`. `python lab.py bench validate --results` reports 1 valid run. The run is `bench/results/20261001T124335Z-a0abb772.json`, source commit `fe109793e85c5e3c03619f5aa896268602329892`, protocol `target-stub-one-edit-json-think-enabled-v5`, `think:true`, temperature 0, 4,096 output tokens, and all three named candidates available. Results: 9b pass 36.4% (31.8% without context, 40.9% with), invalid output 29.5%; 4b pass 13.6% (4.5% without, 22.7% with), invalid 47.7%; 2b pass 4.5% in each condition, invalid 65.9%. Search top-five recall is 0.682. `roles.json` assigns the measured 9b builder with thinking on and retains T6 assignments.

**Review handoff (2026-09-30, reviewer AGENT → builder AGENT).** Read this block first when resuming T5.

- **USER approvals:** GPU availability reconfirmed by USER 2026-10-01; USER-approved model/prompt fixes completed. Task 3 implementation and protocol are committed as `fe10979` (`T5 wip: fix builder output protocol`).
- **Exploratory runs v1–v4:** raw, uncommitted runs remain in `%TEMP%/local-memory-lab-bench/`; they are history only and do not select a role.

- **T5 output-protocol probe (2026-10-01):** `self-001`, `qwen3.5:9b`, same punched target and input, temperature 0. `think:false` returned a whole-file `content` payload instead of the required one-edit schema (193 eval tokens); `think:true` returned schema-valid JSON (281 eval tokens) with a 4,096-token cap. The latter implementation did not pass the task check. Raw replies and token counts: `%TEMP%/local-memory-lab-bench/probe-20261001T123927Z-qwen35-9b-thinking.json`. The comparison selected `target-stub-one-edit-json-think-enabled-v5`; the full benchmark measures task success.

  | Run | Prompt version | `think` | Invalid output |
  |---|---|---|---|
  | `20260930T153923Z` (full) | v1 | not set | 75/132; qwen3.5:9b passed 27% (32% with context, 23% without), 4b 7%, 2b 0%; top-five recall 0.68 |
  | `20260930T170215Z` | v2 | not set | 1/4 |
  | `20260930T170647Z` | v3 | false | 23/23 |
  | `20260930T171151Z` (full) | v4 | false | **132/132**: 94 wrong keys, 38 not valid JSON, 16 hit the 2,048-token output cap |
  | `20261001T124335Z-a0abb772` (full, recorded) | v5 | true | 63/132 invalid (corrected from 43 on 2026-10-01; committed metrics 13 + 21 + 29); 132/132 attempts recorded; qwen3.5:9b selected at 36.4% pass rate |

- **Diagnosis (confirmed by the 2026-10-01 probe below):** with the same `self-001` input, `think:false` produced a whole-file payload without the required edit schema; `think:true` produced schema-valid structured output. The thinking-on implementation failed the task test, so the probe only selected the output protocol; builder quality was decided by the full run.
- **Approved limited fix, in order; nothing beyond this:**
  1. On invalid output, save a shortened copy of the model reply (about the first 2,000 characters) in the result row.
  2. `choose_builder` raises an error when the best pass rate is 0, rather than choosing by list order.
  3. Define the prompt version string once as a constant; `_run_identity` reads `task_source` from the tasks instead of hardcoding it.
  4. **Completed 2026-10-01 after USER confirmed GPU availability:** the `self-001` probe saved both raw replies and `eval_count`. Thinking-on produced valid JSON while `think:false` did not. Freeze thinking-on v5 with the 4,096-token cap; D4 and `docs/CONTRACTS.md` §8 record the protocol. `roles.json` is set from the full benchmark.
  5. **Completed:** commit `fe10979`; clean full run `20261001T124335Z-a0abb772` completed and was recorded. It is the only run that sets `roles.json`. Keep v1–v4 listed as history in the park record.
- **Then:** Task 4 documentation and checks are complete, including the independent review fix. Re-park T5 on `t5-bench` for USER review; merge only after USER acceptance, then declare T6 from §5.

**Now:** T5 was accepted and merged; this prior declaration is retained for implementation history.

**Non-goals:** T6 planner, debugger, reviewer, deterministic gate, job-machine role orchestration or deletion of `run_turn`; T7 self-development goals; paid or remote models; changes to §3 or §4; new dependencies; benchmarking unrelated roles or changing the frozen event/job/approval contracts. Bench edits run only in disposable copies, never directly against the live repository.

**Acceptance criteria:**
- Fast task-unit tests cover task formats and the body-punch transformation without running the full corpus: `python -B -m unittest discover -s tests -p "test_bench_tasks.py" -v`.
- Opt-in corpus validation confirms one pinned `self@<commit>` source, at least 15 tasks, each original single test passes, each punched single test fails, and no punched-copy context pack contains its removed body: `python lab.py bench validate --baselines --contexts`. It runs outside the default suite so the normal regression run stays fast.
- Harness tests prove task copies are isolated, both conditions receive the goal/path/punched file, builder JSON is validated, edits outside declared gold files are rejected, and invalid outputs are counted: `python -B -m unittest discover -s tests -p "test_bench_harness.py" -v`.
- Context/scoring tests prove the pack is built from a punched copy without its removed body; scoring tests show gold-file recall in top five and separate builder success with and without context: `python -B -m unittest discover -s tests -p "test_bench_scoring.py" -v`.
- The committed run contains at least 15 tasks and per named candidate records pass rate, elapsed time, tokens/s, invalid-output rate, top-five search quality, and builder success under both context conditions: `python lab.py bench validate`.
- `roles.json` parses and names the measured builder assignment while preserving planned T6 role assignments: `python -B -m unittest discover -s tests -p "test_roles.py" -v`.
- The full suite and architecture check pass: `python -B -m unittest discover -s tests -v`; `python -B -m unittest discover -s tests -p "test_architecture.py" -v`; `git diff --check`.

**Known risks:** T1–T4 may yield fewer than 15 independently hole-punchable functions; only then use D3's fixture fallback, copied and exercised inside this repo in isolation. The named models may be unavailable; do not silently add candidates, and stop to tell the USER if none can run. The 90+ bounded calls may take hours on 16 GB GPU hardware and results can vary; record model tags, Ollama version, hardware, settings, timeouts and unavailable candidates. Run the full bench only after the USER confirms the GPU is free, and never while local roles are working. A task can be invalid if its single test does not pass pristine code and fail the punched copy; validate both before model runs. Keep all model inference local and all generated patches inside disposable task copies. Bench code goes in new modules only; `session.py` is at the 400-line cap and must not grow. Do not change the event or contract schema without a decision in §2.

**Declaration state:** T5 was declared from §5 after USER acceptance of T4 on 2026-09-30, approved by USER on 2026-09-30, returned from review for one in-scope fix, and re-PARKED 2026-10-01 pending USER acceptance.

## 8. Current Decision

**Project definition:** DEFINED. **Plan status:** APPROVED (2026-09-29). §3 and §4 are frozen (D6).
**Implementation permission:** YES for T6 (USER, 2026-10-01)

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
