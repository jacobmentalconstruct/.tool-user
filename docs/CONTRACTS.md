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
- **Current code differences, fixed in T2:** the AGENT token can still choose the chat model, and the actor labels still read `human` and `agent`.

## 1. Event (T2)

This is the append-only log and the single source of truth (S2). Every other kind of state is rebuilt by reading the events in order.

```json
{"id": 42, "ts": "2026-09-29T14:03:11Z", "actor": "role:builder",
 "kind": "tool.result", "job": "j-7", "task": "t-2", "data": {}}
```

- **`id`:** a whole number that only ever increases. Clients read new events with `GET /api/events?after=<id>`.
- **`actor`:** one of `user`, `agent`, `role:<planner|builder|debugger|reviewer>`, or `system` (§0).
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

## 3. Approval (T3)

```json
{"id": "a-3", "kind": "plan|patch|command", "job": "j-7", "task": "t-2",
 "summary": "2 file(s), +14 / -3", "detail": "<unified diff or argv>",
 "state": "pending|approved|rejected|expired|superseded"}
```

- Only the `user` actor resolves an approval.
- Waiting never blocks the hub: other events keep flowing, and chat keeps working.
- An expired approval changes nothing.

## 4. Command allowlist (T3)

The file lives in the target project at `.lab/allowlist.json`. Only the USER creates or edits it (§0).

```json
{"commands": {"tests": ["python", "-B", "-m", "unittest", "discover", "-s", "tests"]},
 "timeout_s": 300, "max_output_bytes": 20000}
```

- Roles ask for a command **by name**, never as free text.
- The command runs with its argument list exactly as written, in the project root, with no shell.
- The result is a `command.result` event: `name`, `exit_code`, `duration_s`, and the output, trimmed from the end if it's too long.

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

**Input** to every role: `{"role", "goal", "task", "context_pack", "feedback"}`. `feedback` carries earlier test output or reviewer reasons.

**Output:** each role must answer in JSON matching its schema. The schema is enforced through Ollama's `format` option. Invalid output counts as a failed step and is recorded in the bench.

| Role | Output schema (v0) |
|---|---|
| planner | `{"tasks": [{"id", "title", "description", "files": [path], "check": "<allowlist name>"}]}`, 1–5 tasks |
| builder | `{"edits": [{"path", "search_block", "replace_block"}], "new_files": [{"path", "content"}], "notes"}` |
| debugger | The same as builder. Its input `feedback` holds the failing output. |
| reviewer | `{"verdict": "pass" \| "fail", "reasons": [str]}` |

**Gate** (deterministic, T6): the gate passes only if all of these hold:
- the task's `check` command exits with 0;
- the reviewer's verdict is `pass`;
- every edited or created path is in the task's `files` (or declared in `new_files`);
- the patch limits hold (at most 8 files, 20 edits, and 40,000 characters of diff).

## 8. Role config (T6)

This is `roles.json` at the repo root, the only place models are assigned.

```json
{"planner":  {"model": "qwen3.5:35b", "think": true,  "temperature": 0.2, "num_ctx": 16384},
 "builder":  {"model": "qwen3.5:9b",  "think": false, "temperature": 0,   "num_ctx": 16384},
 "debugger": {"model": "qwen3.5:9b",  "think": false, "temperature": 0,   "num_ctx": 16384},
 "reviewer": {"model": "qwen3.5:35b", "think": true,  "temperature": 0,   "num_ctx": 16384},
 "embedder": {"model": "nomic-embed-text"}}
```

The values shown are `PLAN.md` D4, set from T0's measurements. T5's bench confirms or changes them.

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
