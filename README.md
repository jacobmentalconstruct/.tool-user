# Local Memory Lab

A local agent team that runs on your own machine through [Ollama](https://ollama.com). You give it a goal for a project. Local models plan the work, write the changes, run the project's tests, fix failures and review the result. You watch every step in a browser and approve what gets applied. Any other agent can follow the same session from the command line.

The point is to move the expensive inference onto free local models, so that an AI agent doesn't need a paid subscription.

## Status

**T1 implementation is complete and awaiting USER acceptance.** The original hub failed after its reference code moved out of the repo. T1 replaced that dependency with the project's own workspace tools and restored the hub.

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
```

The hub listens only on `127.0.0.1`. The browser and the agent client get different random tokens, and only the browser can approve changes or choose the project. The tokens sit in a local file, so they guard against mistakes, not against other programs on your machine.

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
