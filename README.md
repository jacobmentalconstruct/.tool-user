# Local Memory Lab

A small dark themed shared chat for a local Ollama model. It uses Python 3 and the standard library.

## Run

1. Start Ollama (or leave its desktop app running).
2. Double-click `Open Shared Hub.lnk`, or run `python lab.py hub-server` and open the private browser link in `live_control/shared.json`.
3. Pick an installed model. The default is `qwen3.5:4b` when available.
4. Chat, add long term notes on the right, or ask: “Create HelloWORLD.md with some sample text.”

The agent can write, read, and list text files inside the root `files` folder. The file name and contents are separate inputs. An existing file opens a dark Cancel / Overwrite dialog showing both the old and proposed text. Cancel leaves the file alone. File names must be simple names, not paths. Writes are limited to 100,000 characters, and reads to 100,000 bytes.

Short term memory keeps the latest eight complete conversation turns, including tool results. Long term notes are explicit notes in the right panel, sent with each request. Both live only in memory and disappear when the app closes. The model stays loaded in Ollama for 30 minutes after each request, subject to Ollama's settings.

The file action design uses the supplied project mapper's approach of keeping filesystem rules out of the UI, creating files exclusively, and reviewing changes. The mapper's patch engine is reused beneath the small agent tools; its full desktop interface is not part of this experiment.

## Older desktop experiment

The earlier Tk session remains under `src/local_memory_lab/legacy/` for reference. It is independent of the browser hub and has no root shortcut. Run `python lab.py desktop` for the ordinary standalone Tk window, or `python lab.py live-desktop --inbox <path> --transcript <path>` for the monitored variant.

## Shared browser hub

For one conversation both you and Codex can use, double-click `Open Shared Hub.lnk` from File Explorer. It opens the current shared session in your browser, or starts one if needed. You can also run `python lab.py hub-server` and open the private browser link written to `live_control/shared.json`. The browser has a dark chat interface, model picker, notes, overwrite approval, and patch diff approval. Codex uses `python lab.py client send "Hello" --wait` to send a prompt and watch the reply. `python lab.py client status` shows whether the session is busy or awaiting approval.

The hub is one local process with one prompt queue, one Ollama conversation, and session-only memory. It listens only on `127.0.0.1`. The browser link and agent API use different random tokens; the normal agent API cannot approve overwrites or choose the project folder. These tokens are stored in the local control file for convenience, so they are a workflow guard rather than a hard boundary against programs that can read this workspace. Tokens change each launch. Closing the server ends the shared session.

## First project tools

In the shared browser hub, paste an existing project folder path and click **Use**. The field starts with this app's folder for a quick experiment. The selected folder is visible in the browser and lasts only for this hub session. It cannot be changed while a prompt is running or queued.

The shared model can call `list_project` to inspect one folder at a time, `read_project_file` to read UTF-8 text up to 100,000 bytes, and `create_project_file` to create text up to 100,000 characters in an existing folder. Paths are relative to the selected project. The create tool refuses existing files. Project listing and file access skip linked paths, the mapper's built-in and `.gitignore` exclusions, and the hub's own control and common secret files. The original three file tools still operate only in the separate `files` folder.

## Reviewed project patches

`patch_project_file` changes one unique text block in an existing file. `patch_project_files` accepts several path/search/replacement entries and applies them as one project patch. The browser shows a unified diff and waits for **Cancel** or **Apply patch**. A cancellation leaves every file unchanged. Approval triggers a fresh source check, a backup of the original bytes, and a staged apply. The parts-bin patch engine attempts rollback if any file in a multi-file apply fails. Backups are stored under `live_control/backups/`, away from the selected project.

One patch may include at most eight files and twenty replacement entries. Each target must be a UTF-8 text file of at most 100,000 bytes, and the review diff is limited to 40,000 characters. Matching text must be unique in its target file. Directory deletion, folder creation, and backup restoration are not exposed as agent tools. Run `python -B -m unittest discover -s tests -v` to check approval, backups, and source-change handling.

## Source layout

`lab.py` is the thin entrypoint. `src/local_memory_lab/session.py` owns the shared queue, conversation, notes, project selection, and approvals. `src/local_memory_lab/agent/` owns the Ollama loop and bounded tools, including patch validation and backup coordination. `src/local_memory_lab/interfaces/` contains the browser server, page, launcher, and command-line adapter; each uses the same shared session. `src/local_memory_lab/legacy/` holds the older standalone Tk experiment. `files/` contains generated text, `live_control/` contains local runtime communication and backups, and `.parts-bin/` remains the reference project mapper code.

## Parked state

The current experiment is complete and parked. The supported entrypoint is `Open Shared Hub.lnk`; the shared browser session provides the single conversation, project inspection, reviewed single-file and multi-file patches, and backups. No new tools or structural changes are planned until development resumes.
