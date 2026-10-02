# Contracts (v0)

These are the data shapes that more than one tranche depends on. They are drafts written before building so tranches don't redesign each other's work.

**Rules:**
- A shape is **final** once the tranche named against it parks.
- Before that, the owning tranche may change it and must say how in its §9 parked entry.
- After that, changing it needs a recorded decision in `PLAN.md` §2.
- Keep every shape as small as it can be. Add a field only when a tranche needs it.

## 0. Participants and rights (T2 enforces)

Labels come from `PLAN.md` D10. Rights attach to the label, not to who holds it.

| Right | USER | AGENT | ROLE | SYSTEM |
|---|---|---|---|---|
| Read session state and events | yes | yes | only its context pack | yes |
| Chat; add or remove notes | yes | yes | no | no |
| Submit a **New goal** | yes | yes | no | no |
| Select the project; choose the chat model | yes | no | no | no |
| Resolve approvals (plan, patch, command) | yes | no | no | no |
| Cancel a job | yes | no | no | no |
| Create or edit `.lab/allowlist.json` | yes, by hand, outside the hub | no | no | no |
| Propose edits or named commands | no | no | yes, always through an approval | no |
| Change job and task states | no | no | no | yes, as the lifecycle owner |

- **Tokens:** the browser token is USER, and the CLI token is AGENT.
- **T2 enforcement:** the browser token maps to `user` and the CLI token maps to `agent`; project/model selection and approval resolution are checked at the adapter and session boundaries.

## 1. Event (T2)

This is the append-only log and the single source of truth (S2). Every other kind of state is rebuilt by reading the events in order.

```json
{"id": 42, "ts": "2026-09-29T14:03:11Z", "actor": "role:builder",
 "kind": "tool.result", "job": "j-7", "task": "t-2", "data": {}}
```

- **`id`:** a whole number that only ever increases. Clients read new events with `GET /api/events?after=<id>`.
- **`actor`:** one of `user`, `agent`, `role:<planner|builder|debugger|reviewer>`, or `system` (§0).
- **`data.display` (optional):** interface presentation metadata stored inside `data`, with string fields `speaker` and `text`. Domain data remains alongside it in `data`; clients may render this hint as a message, but it does not change the event's actor or kind.
- **T3 event data (D12–D14):** `job.state` carries `state`, initial `goal` and `submittedBy`, and a `reason` when failed or cancelled. `approval.requested` carries the §3 identity, kind, summary, detail and pending state; `approval.resolved` carries the identity and resolution. T2 patch-approval events with an `approved` boolean remain readable. `command.result` carries the §4 fields plus `status` (`ok`, `failed`, `timeout`, `cancelled`); its `exit_code` is `-1` if stopped before an exit code was returned.
- **T6 event data (D17–D18):** the first `task.state` event fixes the task's immutable specification: `title`, `description`, `target`, `files`, `check` and `order`, with `state: "pending"`. Later events carry `state`, a `reason` code when a task fails, `debugRound` when debugging starts, and a `failure` record for an unusable role reply (`reason`, `error`, `answerExcerpt` (first 2,000 characters), `thinkingExcerpt` (last 2,000), `evalCount`, `capHit`). Reason codes: `invalid_output`, `cap_exhausted`, `invalid_plan`, `check_not_exercising`, `check_failed`, `debug_exhausted`, `review_failed`, `path_outside_task`, `patch_too_large`. A task `target` is `{path, symbol, new}`: an existing symbol, a new top-level function in an existing Python file, a new method `Class.name` of exactly one existing class, or a new file (`symbol` empty). Approval events carry the event-level `task` when they belong to one; a gated patch approval requested by `system` also carries `originRole` (`role:builder` or `role:debugger`) and `candidate`.
- **`kind`, v0 set:**
  - `chat.prompt`, `chat.reply`
  - `note.added`, `note.removed`
  - `project.selected`, `model.selected`
  - `job.state`, `task.state`
  - `approval.requested`, `approval.resolved`
  - `tool.result`, `command.result`, `index.updated`
  - `error`
- **`job` and `task`:** present only when the event belongs to one.

## 2. Job and task lifecycles (T3 builds the machine; T6 fills in the role steps)

```
job:  queued → planning → awaiting_plan_approval → running → done
                                   ↘ rejected         ↘ failed / cancelled
task: pending → building → testing ⇄ debugging (max 2 rounds) → reviewing → gated
      → awaiting_approval → applied | rejected | failed
```

- Only the lifecycle's owner changes its state, and each change is recorded as a `job.state` or `task.state` event.
- `cancel` is allowed from any state that isn't finished.
- A task that fails the gate or runs out of debug rounds ends as `failed`, with the reason recorded. It is never retried silently.
- **T6 tasks (D16):** each task names one `target` and its `files` are exactly `[target.path]`. Its candidate is built, checked, debugged and reviewed only in a disposable task workspace copied from the selected project; the selected project is unchanged until the USER approves the gated patch. Jobs fail fast: a failed, rejected or exhausted task fails the job, and later tasks do not run.
- **Planning (T6):** the planner runs in `planning`, after one full index sync and under the single turn slot. Code validates its tasks, records each with its first `task.state` event, and only then moves the job to `awaiting_plan_approval`; the plan approval shows every task with its exact check command. A planner or validation failure ends the job as `failed` with its reason and an `error` event carrying the failure record. A job that ends closes its unfinished tasks: `cancelled` for a cancelled or rejected job, `failed` otherwise (including restart). Until T6 task 3, the existing `run_turn` still supplies the running step.
- On restart, pending approvals become `expired` without side effects, and non-finished jobs become `failed` with reason `interrupted by restart`. Neither is replayed.

## 3. Approval (T3)

```json
{"id": "a-3", "kind": "plan|patch|command", "job": "j-7", "task": "t-2",
 "summary": "2 file(s), +14 / -3", "detail": "<unified diff or argv>",
 "state": "pending|approved|rejected|expired|superseded"}
```

- Only the `user` actor resolves an approval.
- **Who requests (D18):** `system` requests plan approvals and, after the gate, a task's patch approval, recording `task`, `originRole` and `candidate`. Until T6 retires the chat loop, `role:builder` may still request patch and command approvals for chat.
- Waiting never blocks the hub: other events keep flowing, and chat keeps working.
- An expired approval changes nothing.
- The USER alone resolves approvals and cancels jobs. A pending approval does not stop chat or other hub events.

## 4. Command allowlist (T3)

The file lives in the target project at `.lab/allowlist.json`. Only the USER creates or edits it (§0).

```json
{"commands": {"tests": ["python", "-B", "-m", "unittest", "discover", "-s", "tests"]},
 "timeout_s": 300, "max_output_bytes": 20000}
```

- Roles ask for a command **by name**, never as free text.
- The command runs with its argument list exactly as written, in the project root, with no shell.
- The result is a `command.result` event: `name`, `exit_code`, `duration_s`, and output capped to the last `max_output_bytes`. A leading `[… N bytes trimmed]` line reports omitted bytes and counts toward the cap. The cap is at least 32 bytes (PLAN.md D15).
- Until the separate role team arrives, the existing chat loop acts as the ROLE through `run_command(name)`. Browser and CLI callers cannot request a command directly. USER approval precedes every run; timeout and cancellation stop the process tree on Windows.

## 5. Context pack (T4)

This is what the context assembler hands to each role step.

```json
{"budget_tokens": 6000, "used_tokens": 5712, "dropped": 9,
 "items": [{"kind": "file|chunk|summary|graph", "path": "src/x.py",
            "lines": [10, 48], "score": 0.83, "text": "…"}]}
```

- Tokens are estimated as characters divided by 4.
- Items are chosen greedily by score until the budget is used up.
- `dropped` counts the candidates that didn't fit, so being cut off is visible and never silent.

## 6. Knowledge store (T4)

This is one SQLite file per project, kept on the hub side under `live_control/`, never inside the project.

| Table | Holds |
|---|---|
| `files` | Path, sha256, size, mtime, when indexed |
| `chunks` | ID, path, line span, kind (function, class, module, section, paragraph), text |
| `chunks_fts` | FTS5 keyword index over `chunks.text` |
| `embeddings` | Chunk ID, model, and the vector as a float32 blob |
| `graph_nodes`, `graph_edges` | Modules, classes and functions; `imports`, `defines`, `calls` and `tests` edges |

## 7. Role steps (T5 builder; T6 the rest)

**Input:** code builds each role's input; a model never chooses what it edits. Builder and debugger receive the task, its target, the pinned `region` (the exact source of the target definition, found by code), the target file and the task's context pack; the debugger's `feedback` adds the failing check output and the previous attempt. The reviewer receives one card (below).

**Output:** each role answers in JSON matching its schema, enforced through Ollama's `format` option and checked again in code. An unusable reply is recorded with its `failure` record (§1) and never counted as a verdict.

| Role | Output schema (T6) |
|---|---|
| planner | `{"tasks": [{"title", "description", "target": {"path", "symbol", "new"}, "check": "<allowlist name>"}]}`, 1–5 tasks; `check` is limited by the schema to allowlisted names. Code then rejects the plan for any unsafe or non-normalized path, the allowlist file, case collisions, repeated targets, a symbol that does not resolve to exactly one definition, a "new" name that already exists, or a new file that exists. The check should exercise the new behaviour; task 3 enforces that by running it before building |
| builder | `{"replace_block", "notes"}` for an existing target, or `{"content", "notes"}` for a new file the plan lists. Code applies it to the pinned region. |
| debugger | The same as builder. |
| reviewer | `{"verdict": "pass" \| "fail", "reasons": [str], "quote": str}`; a `fail` counts only if `quote` is a code line from the card (empty for `pass`) |

**Reviewer card:** task title and intent (the approved description), target file and symbol, the check result, the gate's path and size facts, then BEFORE (the pinned region) and AFTER (the candidate). The reviewer answers two questions: does the change do what the task says, and does it change behaviour the task did not ask for?

**Gate** (deterministic, D16): the gate passes only if all of these hold:
- the task's `check` command exits with 0 in the task workspace (D19);
- the reviewer's verdict is `pass`;
- every edited or created path is already in the USER-approved task's `files`; a listed path that does not exist yet allows creation only for a `new` target;
- the patch limits hold (at most 8 files and 40,000 characters of diff).

## 8. Role config (T6)

This is `roles.json` at the repo root, the only place models are assigned.

```json
{"planner":  {"model": "qwen2.5-coder:14b", "think": false, "temperature": 0, "num_ctx": 16384,
              "num_predict": 4096, "timeout_s": 120, "keep_alive": "5m"},
 "builder":  {"model": "qwen3.5:9b",  "think": true, "temperature": 0,   "num_ctx": 16384,
              "num_predict": 8192, "timeout_s": 180, "keep_alive": "10m"},
 "debugger": {"model": "qwen3.5:9b",  "think": true, "temperature": 0,   "num_ctx": 16384,
              "num_predict": 8192, "timeout_s": 180, "keep_alive": "10m"},
 "reviewer": {"model": "qwen2.5-coder:14b", "think": false, "temperature": 0, "num_ctx": 16384,
              "num_predict": 1024, "timeout_s": 120, "keep_alive": "5m"},
 "embedder": {"model": "nomic-embed-text"}}
```

Every role carries its output budget (`num_predict`), `timeout_s`, `think` and `keep_alive`, and every sampling setting: `temperature`, `top_p`, `top_k`, `presence_penalty`, `repeat_penalty` and `seed`. All are sent on every call, so a model's built-in defaults never apply silently; qwen3.5 models ship with `presence_penalty 1.5`. Callers hard-code none of them. Every role call records a trace: the settings sent, the model digest, a prompt fingerprint, the prompt token count and whether the prompt neared the context limit. Ollama counts only prompt tokens it did not already have cached, so on a cache hit the count, and the near-limit flag, under-report. The example above shows the core fields; `roles.json` holds the full set. The builder and debugger share a budget and use thinking, because T5's probe showed `think: false` breaks qwen3.5:9b's schema output. The opt-in probe `python lab.py bench roles --confirm-gpu-free` (`--reviewer-only` for the reviewer comparison) sets the budgets and chose the reviewer by measurement: qwen2.5-coder:14b with thinking off, which needs `OLLAMA_NUM_PARALLEL=1` to fit on the GPU (`PLAN.md` §1, D4). Its summaries are committed under `bench/probes/`.

## 9. Bench task (T5)

Stored as `bench/tasks/<id>.json`:

```json
{"id": "self-012", "source": "self@<commit>", "goal": "Implement …",
 "target": {"path": "src/local_memory_lab/…", "function": "…"},
 "gold_files": ["src/local_memory_lab/…"], "check": ["python", "-B", "-m", "unittest", "…"],
 "synthetic_reason": null}
```

- `source` is `self@<commit>` (this repo) or `fixture:<name>` (a fallback fixture, D3).
- `synthetic_reason` is required, and not null, only for synthetic tasks.
