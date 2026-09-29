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
- **D4 Model assignments** (set from the T0 measurements in §1; T5's bench confirms or changes them in `roles.json`):

  | Use | Model | Why |
  |---|---|---|
  | Builder and debugger | `qwen3.5:9b` | Fits entirely on the GPU even with a 32k context, generates 62 tokens/s, JSON schema output OK |
  | Planner and reviewer | `qwen3.5:35b` (MoE) | 13.4 tokens/s with part of it on the CPU; JSON schema output and thinking mode OK. A different model from the builder, and its outputs are short |
  | Embedder | `nomic-embed-text` | 768 dimensions; 32 chunks in 2.3 s including load |
  | Cheap helper jobs | `qwen3.5:2b` | 132 tokens/s. Used only if a tranche needs it |

  - `qwen2.5-coder:14b` and `ms-ae:latest` stay bench candidates only. They are slow here because of the machine-wide `OLLAMA_NUM_PARALLEL=8` (§1). T5 re-measures them only if the user lowers that setting.
  - Role steps run one at a time, so the 9B and the 35B take turns in VRAM, costing about 5–15 s per swap. Jobs group steps by model where the lifecycle allows.

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

## 5. Tranches

Each tranche follows `docs/WORKFLOW.md`: declare, get approval, implement, consolidate, verify, review, park. T2 onward is provisional.

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

**ID:** T2 — Event log.

**Current:** T1 was accepted by USER and merged to `main` at `7d5f5d8`. T2 is active on `t2-event-log`; USER approved declaring and entering it on 2026-09-29. §3 stop conditions and §4 non-goals remain frozen (D6).

**Expected outcome:** SQLite is the append-only source of truth for shared session events. Session state is rebuilt from the log, actors and rights follow `docs/CONTRACTS.md` §§0–1, clients can read by cursor, and restart restores the event-backed state (S2).

**Scope (task list, in order):**
1. Add an append-only SQLite event store with increasing IDs and `after` cursor reads, matching the event contract.
2. Split shared session ownership into domain components whose state is derived from the event log; persist and restore the session state required by S2.
3. Apply named actor labels and enforce T2 rights, including preventing AGENT project/model selection and approval resolution.
4. Connect the existing browser and CLI read/write paths to the event-backed session and cursor API; add focused persistence, rights, and integration coverage.

**Progress:**
- [x] 1. Add append-only SQLite event storage and cursor reads.
- [x] 2. Split session domain ownership and rebuild required state from events across restart.
- [x] 3. Enforce contract actor labels and rights.
- [x] 4. Integrate browser/CLI paths and add event, restart, rights, and integration coverage.

**Now:** Integration review found the worker queue duplicated prompt contents; reducing it to event IDs so workers read prompts from SQLite before final parking review.

**Non-goals:** T3 job/approval lifecycles and command runner; T4 knowledge/indexing; new user-facing entrances or UI redesign; changing §3 or §4; new dependencies beyond the recorded standard library plus numpy.

**Acceptance criteria:**
- `python -B -m unittest discover -s tests -v` passes with event log, restart, rights, and existing regression coverage.
- Tests prove event IDs increase, cursor reads return only events after the requested ID, and stored events cannot be updated or deleted through the store.
- A restart test proves selected project, model, notes, event history, and existing job-state fields restore from the SQLite log as applicable to current T2 state.
- Rights tests prove AGENT cannot select project/model or resolve approvals, while permitted USER and SYSTEM actions are attributed to the contract actor labels.
- HTTP/API tests prove state reads are event-backed and event reads support `after` cursors.
- `git diff --check` passes; no changes to §3 or §4.

**Known risks:** existing in-memory `SharedSession` couples unrelated state; mapping all current mutations into durable events may reveal contract gaps. Event schema changes after T2 parks require a decision per `docs/CONTRACTS.md`.

**Declaration state:** USER approved T2 declaration and entry on 2026-09-29.

## 8. Current Decision

**Project definition:** DEFINED. **Plan status:** APPROVED (2026-09-29). §3 and §4 are frozen (D6).
**Implementation permission:** YES for T2 (USER, 2026-09-29).

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
