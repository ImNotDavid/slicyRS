# Coordinating development (for the next person or agent in charge)

This project is built with one **coordinator** (a person, or an AI session) who keeps the whole picture, and optionally **subagents** that each do one well-specified task. This page says how to do that safely. Read `HANDOFF.md` (current state) and `ROADMAP.md` (what is next) first.

## The rule

The coordinator owns understanding, design decisions and verification. A subagent owns one bounded task. Never delegate "figure out what to do" or "based on your findings, fix it": write the finding and the fix yourself in the brief. Never report a subagent's result as done until you have read its diff and run the tests yourself.

## What suits a subagent

- A task with a clear acceptance test the agent can run: "make these tests pass without breaking the suite" is a good brief.
- Work that lives in its own files: new modules and their tests (examples: `orient.py`, `goals.py`, `presets.py`, `gateways.py`, thin-wall detection, nozzle sizes, the plate thumbnail, the filament inventory).
- Independent review: an agent that did not write the code reading it for bugs or security problems (the gateway, the permission check, anything that touches printers or sessions).
- Read-only investigation with a precise question ("which files read `printer.gateway`?").

## What does not

- **Design decisions.** The roadmap marks three reworks as design checkpoints: the UI rework (U.1), the notification service (2.14) and the permissions rework (2.15, 3.0). Stop and ask the owner the questions listed in that roadmap row before building. Do not delegate these or guess.
- **Anything that needs a real printer.** Real prints, gateway-to-printer runs and print-quality checks need the owner. Say exactly what to try and what to look for.
- **Anything that needs to be seen.** The built-in browser pane has no WebGL, so the main screen (3D viewport) cannot render there, and the Chrome browsers that may be connected can be on other machines that cannot reach this host's localhost. Static HTML/CSS can sometimes be checked; the viewport cannot. Say so plainly in the report and ask the owner to look.
- **Changes to the shared hotspot files** (below) by more than one agent at a time.
- Tiny tasks: briefing a cold agent costs more than doing it.

## Where things live

Backend (`backend/app/`): `main.py` only assembles the app (middleware, startup, static files). Routes are in `routes/` (`catalog`, `models`, `printers`, `jobs`, `admin`; helpers shared by them in `common.py`); auth and gateway routes are in `auth.py` and `gateways.py`. Logic is in plain modules (`allocator`, `plates`, `pipeline`, `forecast`, `bedstate`, `orient`, `meshcheck`, `goals`, `presets`, ...). Storage is behind `storage/` (never touch sqlite or data paths elsewhere; a test enforces it). The Bambu LAN connector is `bambulan.py` (no database); `bambu.py` and `gateways.py` connect it to the server. Frontend (`frontend/src/`): `App.tsx` (main screen), `components/`, `api.ts` (every API call and type), `faceRegion.ts`, `gcode.ts`.

**Hotspots** (conflict-prone; one writer at a time): `catalog.py`, `config.py`, `store.py`, `routes/jobs.py`, `jobstate.py`, `frontend/src/api.ts`, `App.tsx`, `styles.css`, `ROADMAP.md`, `HANDOFF.md`. If two tasks must both change one, do them in sequence, or have the coordinator make the shared edit first.

## Running things

Run the tests in Docker, not in a local venv: CI uses Python 3.10 on Ubuntu 22.04 and the tests need the real slicer; a newer local Python lacks modules some tests use. From the repository root, after `docker build -t slicyrs:ci .`:

```bash
docker run --rm -v "$PWD/backend/app:/srv/slicyrs/backend/app:ro" -v "$PWD/backend/tests:/srv/slicyrs/backend/tests:ro" -e SLICY_DATA_DIR=/tmp/data -w /srv/slicyrs/backend slicyrs:ci python3 -m pytest -q -p no:cacheprovider
```

That mounts the working tree over the image's code, so no rebuild is needed for backend changes (rebuild when dependencies or the Dockerfile change). Add `-e DB_BACKEND=memory` to prove nothing depends on SQLite. Frontend: `cd frontend && npx tsc --noEmit && npm run build`. A local venv (`backend/.venv`) is fine for quick runs of single test files that do not need the slicer.

## Briefing a subagent

A fresh agent knows nothing. A good brief has: the goal and why; the exact files to change and the files not to touch; what already exists and how to call it (name the functions); the acceptance test (a command and what passing looks like, or the test to write); constraints (no new dependencies, no behaviour change elsewhere, no commits unless told); what to report (files changed, test result, anything surprising) in under 200 words. For parallel work use `isolation: "worktree"` so each agent has its own copy, and keep file sets disjoint.

## Before calling anything done

1. Read the actual diff (`git diff`), not the summary.
2. Run the full backend suite in Docker, and the frontend type check and build.
3. For API changes, check the route table did not change by accident (`app.openapi()["paths"]`).
4. Update `ROADMAP.md` (status of the item) and `HANDOFF.md` (anything the next person must know, including what was not verified).
5. State what you could not verify (UI you could not see, hardware you could not use).

## Human gates (stop and ask)

- Design checkpoints above, and any change to who can do what (permissions), what is emailed or messaged, or what is stored about people.
- Anything that starts a physical machine, changes printer or gateway settings on the live stack, or deletes data.
- Pushing to GitHub and merging to `main`: this host has no GitHub credentials, so the owner pushes. Commit locally with the attribution line the harness asks for, and say what is unpushed.
- Rebuilding or restarting the live Docker stack while a print may be running (ask; a restart marks every plate unknown).

## Good first parallel tasks (disjoint files)

Thin-wall detection (`meshcheck.py`, 1.7); nozzle sizes (printer record, `assign.py`, `catalog.py`: 1.10, touches a hotspot, so do it alone); plate thumbnail in the bundle (`slicer.py`, 1.11, needs a real print to confirm); estimated wait per printer (`forecast.py`, 2.5). Reviews of recent work are always safe to run in parallel.
