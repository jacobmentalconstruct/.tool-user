# Local Memory Lab

A local agent hub that runs models on your own machine through [Ollama](https://ollama.com). The prototype provides a shared browser and command-line session, an answer-only Chat, and a local team (planner, builder, debugger, reviewer) that turns a New goal into a tested change you approve before it reaches your project.

The point is to move the expensive inference onto free local models, so that an AI agent doesn't need a paid subscription.

## Status

**T5 (Bench) and T6 (Team) are accepted; T7 (Self-development) is accepted: the local team made its first approved change to its own code, and the prototype is complete. With T6, a New goal is planned into tasks before you approve the plan; each task is built, checked, debugged and reviewed in a disposable copy, and only a patch you approve reaches your project. Chat answers questions but cannot change files; since T8 it can also draft one validated goal into your New goal box, which you edit and submit yourself. A draft can change what was asked, so read the box before pressing New goal.** The committed T5 run compares 22 tasks across three local builder models, with and without context. qwen3.5:9b is selected at a 36.4% pass rate. See [the recorded results](bench/results/20261001T124335Z-a0abb772.json) and [PLAN.md](PLAN.md) for details. Session history persists in `live_control/events.sqlite`. To reset it, stop the hub and delete that file before restarting.

The prototype is finished when the agent can make progress on its own development in a sandboxed copy of this repo. The route there, and where we are on it, is in [PLAN.md](PLAN.md).

## Requirements

- Python 3.10 or newer, plus numpy
- Ollama running locally, with the models listed in `PLAN.md` (decision D4). The 14b planner and reviewer stay fully on a 16 GB GPU only with `OLLAMA_NUM_PARALLEL=1` set for the Ollama server (see `PLAN.md` §1)
- Developed on Windows 10 with a 16 GB GPU

## Running it

```
python lab.py hub-server
```

This opens a private browser link, which is written to `live_control/shared.json`. On Windows you can double-click `Open Shared Hub.lnk` instead. From another terminal, an agent can join:

```
python lab.py client status
python lab.py client send "Hello" --wait
python lab.py client goal "Describe a goal for the selected project"
```

New goals wait for USER plan approval in the browser. The browser and CLI both show the job stage.

The hub listens only on `127.0.0.1`. The browser and the agent client get different random tokens, and only the browser can approve changes or choose the project. The tokens sit in a local file, so they guard against mistakes, not against other programs on your machine.

When a project is selected, the hub indexes its allowed Python and Markdown files into a separate SQLite knowledge store under `live_control/`. Chat and goal turns receive a bounded context pack from that index. The hub rescans on startup and before turns to detect outside edits. Indexing waits for active turns and their commands; an applied patch's files are re-indexed at once. If Ollama embeddings are unavailable, keyword search remains available and missing vectors are retried at a later scan.

## Builder benchmark

The T5 corpus pins the source snapshot at `self@c916053` and contains 22 hole-punched tasks. Both benchmark conditions receive the task goal, target path, and punched file; the context condition also receives a 6,000-token knowledge pack. The run uses temperature 0, one attempt per condition, a 180-second task timeout, and the frozen `think: true` builder protocol with a 4,096-token output cap.

To reproduce the task checks and result validation:

```
python lab.py bench validate --baselines --contexts
python lab.py bench validate --results
```

To start a full 132-attempt local run, first make sure the GPU is free and no local roles are working:

```
python lab.py bench run --confirm-gpu-free
python lab.py bench record <raw-result-file>
```

Raw checkpoints stay outside the checkout under `%TEMP%\local-memory-lab-bench\`. Resume an interrupted run with `python lab.py bench run --confirm-gpu-free --resume <checkpoint-file>`.

## Tests

```
python -B -m unittest discover -s tests -v
```

## Where things are

- [PROJECT.md](PROJECT.md): what this is for, its boundaries, and when it counts as done
- [PLAN.md](PLAN.md): current state, decisions, the route, and the record of finished work
- [AGENTS.md](AGENTS.md): how an agent (or you) gets oriented quickly
- [docs/](docs/): working rules, architecture, design principles, and shared data contracts

## License

MIT. See [LICENSE.md](LICENSE.md).
