# Local Memory Lab

A local agent hub that runs models on your own machine through [Ollama](https://ollama.com). The current prototype provides a shared browser and command-line session, workspace tools, and USER-approved changes. The project plan describes the remaining work toward an agent team that can make tested progress on its own development.

The point is to move the expensive inference onto free local models, so that an AI agent doesn't need a paid subscription.

## Status

**T3 (Lifecycles and runner) is accepted on `main`; T4 (Knowledge layer) is active on `t4-knowledge`.** Session history persists in `live_control/events.sqlite`. To reset it, stop the hub and delete that file before restarting. See [PLAN.md](PLAN.md) for the active scope and T3 evidence.

The prototype is finished when the agent can make progress on its own development in a sandboxed copy of this repo. The route there, and where we are on it, is in [PLAN.md](PLAN.md).

## Requirements

- Python 3.10 or newer, plus numpy
- Ollama running locally, with the models listed in `PLAN.md` (decision D4)
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

When a project is selected, the hub indexes its allowed Python and Markdown files into a separate SQLite knowledge store under `live_control/`. Chat and goal turns receive a bounded context pack from that index. The hub rescans on startup and before turns to detect outside edits. Indexing waits for active turns and their commands; an approved patch queues its changed files for refresh. If Ollama embeddings are unavailable, keyword search remains available and missing vectors are retried at a later scan.

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
