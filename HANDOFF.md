# Handoff: continuing on the Linux host

State as of 2026-11-09. Branch `milestone-a` (pushed to origin), latest commit `d3d9cde`. `main` has Milestone 0 only. Nothing from Milestone A is merged.

## What this is

Cloud slicer for Bambu Lab printers. FastAPI backend with a SQLite JSON store, React/TS/Vite/three.js frontend, Bambu Studio CLI slicing, Bambu LAN MQTT/FTPS printing, invite-only email-code login, Docker Compose + Caddy. Imperial SSO is on the back burner. See `README.md` (setup, deploy) and `ROADMAP.md` (milestones and item status).

## Where Milestone A stands

Done: A.1 job states/events/preview, A.2 pool/allocator/pickup forecast, A.3 build-plate state, A.4 printer adapter interface, A.5 slice task queue and worker, A.8 statistics and admin Pipeline tab.
A.6 plates and batching is done (see below). Not started: A.7 separate printer gateway process, A.9 storage interfaces. Colour is recorded on a job but not matched. The preview slices on X1C because there is no P2S model in the catalog.

## How the pool works (read before changing it)

- Phases: submitted, previewing, previewed, pooled, assigned, slicing, ready, printing, printed | failed | unknown, plus cancelled and rejected (`backend/app/jobstate.py`). The old `state` field is kept in step. Legacy jobs with no `phase` may move anywhere (`force=True`).
- `allocator.tick()` runs every 5 s (`ALLOCATOR_INTERVAL_S`) and after submit and bed-clear. A printer is free if it is enabled, not busy/error/offline, holds no job, and its plate is `clear`.
- Holding: a job holds its printer while `printing`, or while `assigned/slicing/ready` **and** it has `pooled=True`. Jobs sliced directly through the old `/api/slice` route do not hold a printer (fixed in `924a061`).
- Pickup time is a deadline, not a start time. It affects refusal, priority (at-risk first) and emails. A pooled job is assigned and sliced as soon as a printer is free; it then waits as `ready` until the owner presses Print, or prints by itself with `auto_start`. An unstarted `ready` pool job returns to the pool after `ready_hold_min` (30).
- Plate state: `occupied`, `clear` or `unknown` per printer. **After every server restart all plates are `unknown`** and must be confirmed on the Fleet page (admin, operator, or owner of the last print) before the allocator will use that printer.
- Pooled jobs now carry `waiting_for` text (set in `allocator.waiting_reason`) shown in the Jobs list.
- Slicing runs through the `tasks` table. Workers are embedded (`EMBED_WORKERS`, default 2) or standalone (`python -m app.worker` with `EMBED_WORKERS=0` on the web process).

## Plates and batching (A.6)

- Opt-in per job (`allow_batching`, a checkbox in the submit dialog). A plate is a **lead** job plus **members** (`members` on the lead, `batch_lead` on each member). The lead carries the slice, printer and print; members mirror its phase and result (`backend/app/plates.py`, hooked into `jobstate.transition`).
- Partners must have the same filament and slicer settings, compatible colours, fit on the chosen printer (shelf packing of bounding boxes, `footprints_fit`), and stay within `plate_max_parts`, `plate_max_h` and every pickup time. The slicer's arrange is the authority: if the sliced plate fails, the last job added goes back to the pool (never to pair with these again) and the plate is sliced again.
- A lone opted-in job waits for a partner (`plates.hold_reason`) up to `max_hold_h`, or until a pickup time leaves less than `batch_slack_min` spare. It does not hold the printer while waiting.
- Cancelling a job, or an unstarted plate timing out, dissolves the plate: the other jobs return to the pool. A plate auto-starts only if every job asked for that. Any owner may start or clear it; the others are emailed.
- Statistics split filament and time by `plate_share` (part volume); the plate counts once for the printer.
- Tested in `test_batching.py` (mock slicer) and one real-engine test. Not yet tried with a real printer or in the browser.

## Key files

`backend/app/`: `main.py` (endpoints), `jobstate.py`, `allocator.py`, `forecast.py`, `pipeline.py`, `taskqueue.py`, `worker.py`, `bedstate.py`, `printing.py`, `adapter.py`, `bambu.py`, `assign.py`, `outcomes.py`, `stats.py`, `pipelinecfg.py`.
`frontend/src/`: `api.ts`, `App.tsx`, `components/{SubmitModal,FleetModal,JobsModal,AdminModal}.tsx`.
Tests: `backend/tests/` (`test_pipeline.py` is the Milestone A suite).

## Running the tests

Locally (Windows): `cd backend && .venv/Scripts/python -m pytest` (about 3 min; 456 passed, 1 skipped at last run).
In the container (real slicer, needed for the skipped tests): see the README `docker build` / `docker run ... pytest` commands.
Frontend typecheck: `cd frontend && npx tsc --noEmit`. **This was not run after the last frontend edit (`waiting_for` in `api.ts` and `JobsModal.tsx`).**

## Known problems and unverified items

1. **Not tested on the Linux host or in the Linux container** since Milestone A. CI was red on the previous push because the tests picked their sample STL by directory order (CI got `tall_300.stl`); fixed in `d3d9cde`, **CI on that commit not yet seen**.
2. `test_preview_gives_an_estimate_and_keeps_no_gcode` failed once in a full local run and passed alone. Probably timing; unconfirmed.
3. **A real pooled job was stuck on "waiting for a printer"** on the A1 Mini with the plate confirmed clear and status FINISH. Cause not confirmed. Best guess: an older directly-sliced job in `ready` was holding the printer (fixed in `924a061`, not yet deployed when this was written). After deploying, open Jobs: the line under the job should now say what it is waiting for. If it still does not start, that text is the next clue; also check the `allocator` log lines and whether the background thread runs (`ALLOCATOR_INTERVAL_S` must be above 0).
4. The A1 Mini has no AMS, so the system cannot see what is on the external spool. Assignment treats that as unknown, not as "not loaded".
5. The standalone `python -m app.worker` process was never run for real, only the queue logic is tested.
6. The pool has never run against a real printer end to end.

## Decisions to keep

- Do not add the "hold the print so it finishes just before pickup" mode yet (asked for explicitly). It would use the reserved `max_hold_h` setting and belongs with A.6.
- Imperial SSO stays on the back burner.

## Suggested next steps

1. Pull `milestone-a`, rebuild, run the container tests, confirm CI is green.
2. Restart, confirm each plate clear, submit a short job to the pool and watch it through to a real print on the A1 Mini.
3. Merge `milestone-a` into `main` once that works.
4. Then A.6, A.7, A.9, and a P2S profile for an accurate preview.
