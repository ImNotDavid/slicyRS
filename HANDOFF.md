# Handoff: continuing on the Linux host

State as of 2026-11-09. Branch `milestone-a` (pushed to origin), latest commit `d3d9cde`. `main` has Milestone 0 only. Nothing from Milestone A is merged.

## What this is

Cloud slicer for Bambu Lab printers. FastAPI backend with a SQLite JSON store, React/TS/Vite/three.js frontend, Bambu Studio CLI slicing, Bambu LAN MQTT/FTPS printing, invite-only email-code login, Docker Compose + Caddy. Imperial SSO is on the back burner. See `README.md` (setup, deploy) and `ROADMAP.md` (milestones and item status).

## Where Milestone A stands

Done: A.1 job states/events/preview, A.2 pool/allocator/pickup forecast, A.3 build-plate state, A.4 printer adapter interface, A.5 slice task queue and worker, A.8 statistics and admin Pipeline tab.
A.6 plates and batching, A.7 printer gateway and A.9 storage interfaces are done (see below). Milestone A is complete once it has been run on real printers. Colour is recorded on a job but not matched. The preview slices on X1C because there is no P2S model in the catalog.

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
- Partners must have the same filament and layer height (also first-layer height, print sequence, vase mode), compatible colours, fit on the chosen printer (shelf packing of bounding boxes, `footprints_fit`), and stay within `plate_max_parts`, `plate_max_h` and every pickup time. The slicer's arrange is the authority: if the sliced plate fails, the last job added goes back to the pool (never to pair with these again) and the plate is sliced again.
- Other settings may differ: `plates.compatibility` diffs each job's full process settings against the lead's; differences in `PER_OBJECT` keys become that member's `plate_overrides`, sliced as per-object settings in a generated 3MF (`slicer._plate_3mf`); other plate-wide differences follow the lead (`plate_wide_differences`). `PER_OBJECT` was measured against the pinned slicer and is guarded by `test_real_engine.py`; re-check it after a slicer upgrade.
- Previews and slices made before submitting are hidden **drafts** (`draft: true`), not jobs: they are discarded when the dialog closes, replaced by a newer slice of the same part, turned into a job on pool submit or print, and purged after `RETENTION_DRAFT_HOURS` (6).
- A lone opted-in job waits for a partner (`plates.hold_reason`) up to `max_hold_h`, or until a pickup time leaves less than `batch_slack_min` spare. It does not hold the printer while waiting.
- Cancelling a job, or an unstarted plate timing out, dissolves the plate: the other jobs return to the pool. A plate auto-starts only if every job asked for that. Any owner may start or clear it; the others are emailed.
- Statistics split filament and time by `plate_share` (part volume); the plate counts once for the printer.
- Tested in `test_batching.py` (mock slicer) and one real-engine test. Not yet tried with a real printer or in the browser.

## Model preparation (Milestone 1, partly)

- Every model is repaired when loaded (`meshio.load_mesh` calls `meshcheck.repair`: duplicate vertices, zero-area and flipped faces, small holes) and the upload reply carries plain-language `checks`, shown in the left panel. `POST /api/models/{id}/orient` (`orient.py`) returns the rotation for "flat" (largest face down) or "auto" (least support); buttons are in the Rotate popover.
- **Not looked at in a browser**: the built-in browser pane here has no WebGL, so the main screen does not render. Backend behaviour is covered by `test_orient.py`; try the buttons and the advice text by hand.

- Print goals (`goals.py`): four buttons in the settings panel choose a process preset plus a few settings for the selected material (`GET /api/goals/{id}?filament=`); the result is ordinary editable settings, and any manual edit clears the highlighted goal. Switching filament re-applies the goal. Not yet looked at in a browser.

- Saved presets (`presets.py`, `SavedPresets.tsx`): a person's named settings (process preset, material, panel values), private to them; admins can share one with everyone. Applying one fills the panel; the selection clears itself once the panel no longer matches it. Not yet looked at in a browser.

- Plate thumbnail (`thumbnail.py`): the real slicer's bundle refers to `Metadata/plate_1.png` and `plate_1_small.png` but does not contain them. A numpy renderer adds both after each single-part slice (shared plates are skipped). **On by default (owner decision); `BUNDLE_THUMBNAIL=0` turns it off. Not yet seen on a printer's screen.** It cannot fail a slice.

- Nozzle sizes (`catalog.PROCESS_PRESETS`, `assign.nozzle_of`): a process preset belongs to one nozzle (0.4 mm presets keep their old ids; others end in `@0.6` etc.), a job's nozzle comes from its preset (`catalog.job_nozzle`), and only printers whose fitted nozzle matches are eligible. A printer's nozzle is what its MQTT report says (`nozzle_diameter`), else the admin-set `nozzle_mm`, else 0.4. The UI offers only nozzle sizes that enabled printers have (interpretation of "connected printers": enabled printers, online or not). The slicer's profile names differ by printer model for the same layer height, so `profiles.process_name` finds them by layer height. Not yet looked at in a browser.

- Settings panel (1.1): `backend/app/settings_schema.json` is generated from Bambu Studio's source at the release pinned in the Dockerfile (`python backend/scripts/gen_settings_schema.py --tag <tag>`; rerun when the engine version changes). Pages, groups, order, labels, units, ranges and choices therefore match Bambu Studio (Quality, Strength, Speed, Support, Others; 249 options); "Basic" is our own short list (`catalog.BASIC_KEYS`). Developer-mode options, post-processing scripts, notes and tooltips are left out. Values are validated on submit (`catalog.validate_settings`); the real-engine test `test_the_engine_accepts_every_setting_the_panel_can_send` slices once per setting. Mock-mode defaults are Bambu Studio's own where known, with the old hand-set values on top (`catalog.SETTING_DEFAULTS`). Not yet looked at in a browser; Filament and Printer settings are not in the panel.

- Move tool (1.2): `Transform.position` is an offset (mm) from the middle of the bed, `(0, 0)` meaning "let the slicer place it" (arrange on, as before). A non-zero position makes `slicer._placed_3mf` put the part at bed centre + offset in a 3MF and run with arrange off (`--arrange 0`); the real-engine test `test_a_part_placed_by_hand_is_printed_where_it_was_put` checks the G-code. `assign.fits` then checks the footprint stays on each printer's plate where it was put (no turning), so the position can rule printers out; such jobs never join shared plates. Dragging is in `Viewport.tsx` (controls off while dragging, clamped to the plate); typing X and Y is in the Move popover. Not yet looked at in a browser.

- G-code viewer and supports preview (1.9, 1.4): `gcode.ts` keeps every layer's segments with their feature type (and travel moves) and rebuilds each layer's lines when the legend changes (`apply`); `Viewport.tsx` takes `hiddenFeatures`, `showTravel` and `progress` (playback 0..1 over the visible layers). `overhang.ts` estimates which faces need support from the part's geometry and the support threshold angle, drawn as a red overlay when the toolbar's triangle button is on (checked against the backend's `orient.measure` in Node). **None of this has been looked at in a browser** (no WebGL here).

- Multi-part plates (1.6): a plate is a list of parts (`PartIn`: model, rotation, scale, position = bounding-box centre in mm from the bed middle); copies are just more parts. `multipart.py` builds and checks them (outlines with shapely, at most 12 parts, 0.5 mm gap, envelope, names of parts that overflow a bed); `/api/assign`, `/api/slice` and `/api/jobs/preview` accept `parts` (a plate of one part is the single-part form), a plate job stores `spec.parts` (one input STL per distinct model/turn/scale, `job_is_plate`) and is sliced with each part at its own offset and arrange off; the envelope box is what printer assignment tests, so a printer takes the plate exactly when that box fits. `POST /api/plate/arrange` runs the slicer's arrange (`slicer.arrange`, no slicing) and returns each part's centre and an extra Z turn: **the real slicer turns parts and has no switch to stop it**. Plates never join other people's plates. Frontend: `plate.ts`, `footprint.ts` (hulls, distances, dragging with neighbours; checked in Node), `PartsPanel.tsx`, multi-mesh `Viewport.tsx`. Previews of plates are estimated on the preview printer with the slicer free to arrange. **Not looked at in a browser.**

- Not built, on purpose: alerts for problems *during* a print (pause, printer error codes, offline mid-job). Today only the final result is recorded and only finish, uncleared-plate and pickup-time emails exist. It is roadmap item 2.16, built with 2.6 and 2.14; ask the owner the design questions in that row first. Also deferred by the owner: 1.12 (cut and measure) and the Filament and Printer settings pages and more materials (until the permission system).

- UI rework (Milestone U) in progress: design decisions are in the ROADMAP row U.1. Mockups live in a Claude Design canvas (private, owner's account): https://claude.ai/artifact/TrffeSf7WRGRLEF9ADmUnv with a design system sheet (light and dark, violet accent, Space Grotesk and Inter), Jobs and Fleet pages (desktop, interactive) and a phone job-status screen. Awaiting the owner's feedback before any code changes; the guided-steps workspace, the Submit to pool dialog and Admin are not mocked up yet.

## Printer gateway (A.7)

- A printer is reached either directly by the server (as before) or through a **gateway**: `python -m app.gateway_agent` (image: `Dockerfile.gateway`, compose: `deploy/docker-compose.gateway.yml`) on the printers' network. It connects *out* to the server over a WebSocket (`/gateway/ws`), so the server needs no route to the printers and the site opens no port.
- Admin > Gateways creates a gateway and shows its token once (only a hash is stored). A printer's "Connected through" setting assigns it (`printer.gateway`); empty means direct. Protocol and trust model: top of `backend/app/gateways.py`.
- The server sends the gateway its printers (with decrypted access codes, over the socket only); the gateway reports status, which goes through the same `printerstate.apply` as direct links, so bed state, results and the allocator are unchanged (bed state stays in the server, not the gateway). Prints: the server offers the sliced file at a random URL that only that gateway's token can fetch, for 15 minutes; the gateway downloads it, uploads to the printer and answers with the printer's own error text. A dropped gateway turns its printers OFFLINE and a print then says the gateway is offline.
- Parked on purpose: making the gateway a fully separate component (own folder, versioned protocol contract, no shared code). See "Later" in `ROADMAP.md` for the trigger.
- The LAN connector moved to `bambulan.py` (no database dependency, shared by both sides); `bambu.py` is the server-side wiring.
- Tested with a live server and a real agent (fake printer links) in `test_gateway.py`, and by hand with two containers on a Docker network (connect, status, reconnect after a server restart). **Never run against a real printer through a gateway.**

## Storage (A.9)

- `backend/app/storage/` is the only place that knows how records and files are kept. The rest of the app uses `storage.db` (collections, rate limiter, task queue) and `storage.files` (areas keyed by namespace `JOBS`/`MODELS` and id). Interfaces: `storage/interfaces.py`.
- Backends: `sqlite` (default) and `local` files (same on-disk layout as before, so existing data works unchanged); `memory` (database only, for tests, shared by nothing). Select with `DB_BACKEND` / `FILE_BACKEND`; add one by writing a class and registering a factory in `storage/__init__.py`.
- A remote file backend returns local cached paths from `path`/`workdir` (the slicer needs real files) and uploads in `publish`, which callers already call after the slicer writes.
- `test_storage.py` runs one contract over every backend and fails if code outside `storage/` mentions sqlite, `JOBS_DIR`/`MODELS_DIR` or `shutil.rmtree`. The whole suite also passes with `DB_BACKEND=memory`.

## Key files

How to run development as a coordinator with subagents, and the safety rules: `docs/COORDINATION.md`.
`backend/app/`: `main.py` (assembles the app), `routes/` (HTTP endpoints by area), `jobstate.py`, `allocator.py`, `forecast.py`, `pipeline.py`, `taskqueue.py`, `worker.py`, `bedstate.py`, `printing.py`, `adapter.py`, `bambu.py`, `assign.py`, `outcomes.py`, `stats.py`, `pipelinecfg.py`.
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


## UI rework: shell and pages (built, checked in the browser pane except the Workspace)

- `frontend/src/Shell.tsx` is the new top level: sidebar (icon strip on the Workspace, full elsewhere, bottom tab bar under 900 px), hash routes `#/workspace|jobs|fleet|admin`, light/dark/auto switch (stored in localStorage), account menu (Radix dropdown). The Workspace (`App.tsx`) stays mounted behind an error boundary so a plate in progress survives a look at Jobs, and a browser without WebGL shows a message instead of a blank app.
- `components/Frame.tsx` lets JobsModal, FleetModal and AdminModal render either as pop-ups or inline as pages (`inline` prop). `shell.css` holds the ICRS look; `theme.css` the brand tokens; fonts: Geist (body), Geist Mono (headings), London Underground (`public/fonts/london-underground`, OFL, not yet used on screen).
- Backend: `GET /api/printers` adds `users` and `last_user` for operators and admins (`routes/common.printer_people`, `tests/test_printer_people.py`).
- Guided Workspace (built and walked through in the browser pane with a cube: upload, goal, slice, submit, job appears under Jobs): `App.tsx` has `workflow` (guided or full, remembered in localStorage) and `step` 1 to 4; the old panel blocks are the consts `modelBlock`, `printerCard`, `filamentCard`, `goalsBlock`, `fullSettings`, composed per step in `guidedPanel` or all together in the full view. Slicing marks the preview stale when anything changes (`sliceSig`). In the guided view the bottom bar only has the file name and Download; Queue and direct Print remain in the full view. `Viewport` shows a notice when WebGL is missing instead of crashing.
- Not verified: the 3D picture itself (the pane has no WebGL), opening a finished job from Jobs into the Workspace, and the phone layout of the Workspace. Not built: choosing a specific printer in the Submit dialog (the mockup showed it; the backend only lets the pool choose), material chips instead of a dropdown, London Underground font on screen.
- Guided step 2 now has a Supports control (`components/SupportControl.tsx`: none, normal or tree, build plate only, overhang angle, and the overhang highlight). Owner decision: only supports are added to the guided steps for now, placed under goal and material. Goals keep the support choices when they are applied. The whole workspace (buttons, cards, goal buttons, tools, info boxes, settings tabs) was restyled to ICRS in `shell.css`; checked in the browser pane only for step 2 in the dark theme.
- Lay flat and Auto-orient are now top-level buttons in the viewport tool column (not inside the Rotate pop-up). The guided view starts with "Support critical regions only" on (unless the person changes it; `criticalTouched` in App.tsx). The account menu bug was the old `.menu` CSS (absolute positioning) clashing with the Radix menu; it is now `.acct-menu`.
- Milestone U finishing pass: `components/Brand.tsx` (Lettermark and Harry as CSS masks of `assets/*.svg` from the Figma kit; `vite.config.ts` sets `assetsInlineLimit: 0` so they stay real files), used in the sidebar, login and empty states; the `.dot` class (London Underground face) exists but is not used anywhere: the owner found it did not suit times and counts, and wants it kept for a future overview or dashboard; guided step 2 uses chips for material and nozzle; phone rules for the workspace at the end of `shell.css`. Audit by script at 375 px: no horizontal overflow and no unnamed buttons or fields on any page.
- Sidebar: every row is a 48 px icon cell, so icons, Harry, the avatar and labels stay at fixed x while the sidebar width (232 to 64 px) and label opacity transition (`shell.css`, 'Sidebar geometry'). Measured: all icon centres at x = 32 in both states.

## Notification service (2.14) and mid-print alerts (2.16)

- `app/notify.py`: `EVENTS` catalogue (label, default for owners, default for operators), `emit(kind, subject, body, owners=, operator_body=, link=, job=)` queues email to each wanted person and posts to admin-set Discord channels (`store.channels`, webhook host restricted to discord.com, the URL is never returned by the API, `allowed_mentions` off). Delivery runs in a thread (`NOTIFY_SYNC=1` in tests runs it inline); recent deliveries are kept in memory for the admin tab.
- Callers moved onto it: allocator pickup warnings, bed-state finished, plate waiting and plate cleared messages, and the failed-print result in `outcomes._record_final`. The old direct `mailer.send_in_background` calls there are gone.
- `app/alerts.py` is called from `printerstate.apply`: pause (on entering PAUSE), offline (on entering OFFLINE while a job is printing) and new HMS codes. Shared plates tell every owner via `bedstate.last_plate`.
- API (`routes/notifications.py`): `GET/PUT /api/me/notifications` (per person on/off), admin `GET /api/admin/notifications` and channel add, update, delete and test. UI: Admin has a Notifications tab; the account menu has a Notifications dialog (`components/NotifyPrefs.tsx`).
- Fixed: Fleet showed "N people share this plate" for one user because every older finished job on an occupied printer counted; only the latest plate counts now (`routes/common.printer_people`).
- Not verified with real hardware: a real pause or error on a printer, and a real Discord webhook (tests use a stand-in).

## Central permission check (2.15)

- `app/permissions.py` holds every "may this person do this" rule, documented as a table at the top. `can()` returns a bool; `require()` raises 404 (`hide=True`, for other people's jobs and models) or 403. `auth.require_admin` and `auth.is_admin` go through it.
- Moved onto it: Jobs list and every job action (`routes/jobs._owned_job`), model access (`routes/common._checked_model`), plate clear and mark occupied (`bedstate`), who sees printer users and connection details (`routes/printers`), sharing and deleting presets (`routes/catalog`), and which notification events a person sees in their settings (`notify.preferences`). Removed the unused `plates.is_owner` and `presets.can_delete`.
- One small behaviour change: a disabled account is refused every action even if its session were still alive (sessions are already revoked on disable). Emitting to operators still uses the operator flag only, as before.
- Tests: `tests/test_permissions.py`. New actions for 2.2 and 2.4 (queue, pause, resume) belong in `ACTIONS` with a row in the table.

## Staff-only pages (owner request after 2.15)

- Fleet is for operators and admins only; Admin for admins. In the menu they sit under a "Staff" divider with a yellow edge (the brand's alert yellow), and their page header carries a yellow tag ("Operators and admins only" / "Admins only"). Members do not see the Fleet link and `#/fleet` falls back to the Workspace for them (`Shell.tsx`, `allowed()`); `/api/printers` stays open because the Workspace needs it.
- Members confirm their own plate is clear from Jobs: `GET /api/jobs` adds `plate_to_clear` when the job's print is over and its plate is still occupied, and Jobs shows a "Confirm plate is clear" button with the same three ticks (`components/PlateCheck.tsx`, shared with Fleet).
- A home overview for members is on the roadmap as 2.18.

## Job page (2.9), My jobs and All jobs

- `GET /api/jobs/{id}/page` (`routes/jobpage.py`) returns everything the page needs: settings changed from the preset (with labels), the goal, material, nozzle, printer and plate, parts (with whether each model file still exists and is the viewer's), other parts on a shared plate (owners only for staff), estimate against actual time, result, events, whether the G-code exists, `plate_to_clear`, and `photo` (always null until cameras, 2.7).
- Permissions: new `job.view` (owner, operator, admin) for the page, the G-code preview and the history; `job.manage` still guards every action. `job.see_all` now includes operators: `GET /api/jobs` is My jobs, `GET /api/jobs?scope=all` is All jobs (403 for members).
- Every job now stores `spec` with settings and `goal`, including direct slices (before, a directly sliced job did not keep its settings). `GET /api/models/{id}` gives name and size for putting a part back on the plate; uploads now store the model's size.
- Frontend: routes `#/jobs` (My jobs), `#/all-jobs` (staff), `#/jobs/<id>` (`components/JobPage.tsx`). "Same settings again" goes through `Shell` to `App` (`reuseRequest`): material, preset, goal and settings, and the parts if their files still exist; otherwise a message asks for the file. Fleet's user names link to job pages. The waiting reasons no longer tell members to use the Fleet page.
- Checked in the browser pane: the page content, Show all settings, Same settings again landing on step 2 with PETG and Strong, and All jobs. Not checked: the 3D preview itself (no WebGL in the pane), the page on a phone.

## My jobs and job page redesign (built from the Claude Design boards)

- Owner asked for My jobs as a searchable grid of cards that can be ordered any way, and a reworked job page. Mockups: canvas boards "My jobs (card grid)" (`MyJobs.dc.html`: search by name, material or printer; order by date, name, status, print time or material, reversible; filter chips; each card has a part thumbnail, status, material swatch, goal, time, printer, progress and a "plate needs clearing" flag) and "Job page" (`JobDetail.dc.html`: breadcrumb, actions, a key-numbers strip, large sliced preview with a layer slider and a Toolpaths / Plate photo switch, the plate's parts, a finished-plate photo slot, and Settings / History / Files tabs). Built: `components/JobsGrid.tsx` (My jobs and All jobs; search, order by date, name, status, print time, material and, on All jobs, owner, reversible, remembered per browser; filter chips with counts; cards with a server-drawn picture, status, material, goal, time, shared-plate note, what happens next, and yellow flags for a plate to clear or a pickup at risk) and the rewritten `components/JobPage.tsx` (breadcrumb, status sentence, actions, key-numbers strip, preview with Toolpaths / Plate photo switch, layer slider and colour legend, On this plate, Finished plate, and Settings / History / Files tabs). `GET /api/jobs/{id}/thumbnail.png` draws the job's part or whole plate once with the existing numpy renderer and keeps it with the job's files (404 once the files are removed; the card then shows only the hatching). `fmtTime` moved to `format.ts`; `JobsModal.tsx` is gone.
- "Open in workspace" was removed from the app (owner request): the Jobs lists no longer offer it and the `openRequest` path from Shell to App is gone; the job page's own preview replaces it.

## Live print progress (2.1)

- `live_progress(job)` in `routes/common.py`: only for a job in phase `printing` whose printer state is RUNNING, PAUSE, PREPARE or SLICING; returns percent, remaining minutes, ETA (status time plus remaining), layer and total, nozzle and bed temperature. Added as `live` to `GET /api/jobs` entries and to the job page.
- `components/LiveProgress.tsx` draws it (bar plus a line of numbers; `full` adds temperatures and the finish time). Used on job cards (instead of the "what next" line), the job page (`.live-panel`, page polls every 5 s while printing) and Fleet cards (from the printer status directly).
- Checked in the browser pane with a faked running printer; not yet seen with a real print.

## Wait estimate (2.5), maintenance and needs attention (2.6)

- `app/upkeep.py`: `unavailable(printer)` (the reason a printer is out of the pool, or None), `set_maintenance`, `flag_attention` (called from `outcomes` on a failed print and from `alerts` on a new error code mid-print), `mark_ready`, and `wait_s` (rest of the running print plus estimates of jobs sent to it).
- Out-of-pool printers: `assign.evaluate` adds the reason as a blocker unless `assume_available` (so feasibility and submission still work), `allocator.free_printers` skips them, and `waiting_reason` names them.
- API: `POST /api/printers/{id}/maintenance {on, note}` and `POST /api/printers/{id}/ready`, both `printer.maintain` (operators and admins). `GET /api/printers` adds `maintenance` (note visible to everyone), `attention`, `unavailable` and `wait_s`; `/api/assign` candidates add `wait_s`.
- UI: the Workspace printer menu shows the wait or the first blocker; Fleet cards show a yellow Needs attention box with Mark ready, an In maintenance box with Back in service, and a Maintenance… button with a note field.

## Pause, resume and stop from the web (2.4)

- `bambulan.LanLink.control(action)` publishes `{"print": {"command": "pause"|"resume"|"stop"}}` and waits up to 12 s for the reported state; `gateways.RemoteLink.control` forwards it as a `control` message and `gateway_agent.handle_control` runs it on the gateway's link (gateways need updating to this version for remote printers).
- `app/printcontrol.py`: checks the job is printing and the printer state allows the command, stores `control: {action, by, at}` on every job on the plate and an event, sends the command, audits it. `POST /api/jobs/{id}/pause|resume|stop`; permissions `print.pause` and `print.stop`.
- Follow-on effects keyed off `recent_control` (2 minutes): `alerts` names who paused and skips them (`notify.emit(skip=…)`); `outcomes` writes "Stopped from the web by …", tells the others, and does not flag the printer.
- UI: job page live panel (Pause or Resume, Stop with a confirm, and a note when a shared plate can only be stopped by staff); Fleet cards (staff) for the printer's current job. Checked in the pane only for the no-connection message; the real commands need a printer.

## Staff queue (2.2)

- `app/queue.py`: jobs carry `queue_rank` (position in their lane) and `pinned_printer`. `sort_key` puts lined-up jobs first, then hand-placed pool jobs, then the automatic order. `allocator.assign_pool` uses it and offers a lined-up job only to its own printer; batching partners never come from another printer's line; `waiting_reason` says "Lined up for <printer>, N ahead: …"; `forecast.predict` uses only the pinned printer.
- `fits(job, printer)` refuses a line-up when the printer cannot print the job (assign rules with `assume_available`) or is in maintenance or needs attention; the Queue page uses the same answers to colour drop targets red.
- API (permission `queue.manage`, operators and admins): `GET /api/queue`, `POST /api/queue/move {job_id, lane, index}` (re-numbers the whole lane), `POST /api/queue/reset`. Moves are audited and recorded as `queue_moved` events on the job.
- `components/QueuePage.tsx` at `#/queue` (staff menu): HTML5 drag and drop with drop position from the pointer, plus arrows and a "Line up for…" menu per card; printing and already-assigned jobs show as locked cards. Checked in the browser pane with synthetic drag events (reorder, refused ABS on an open printer, line-up); not yet with a real mouse.
- The old All-settings "Queue" button, `api.queue` and `POST /api/jobs/{id}/queue` were removed (owner decision); `queue_depth` on printers stays but is now always 0 unless something else marks jobs queued without printing them.
- Auto-start (owner decision): `plates.auto_start` now always returns True, so every pool job starts as soon as it is sliced and the plate is clear (still gated by `printing.start(require_clear=True)`). `expire_holds` now returns any pool job still `ready` after the hold time to the pool (that only happens when the automatic start failed). The Submit dialog lost its "start automatically" box; `auto_start` in the submit body is accepted and ignored. Directly sliced jobs (Print button) are unchanged.

## Printer camera (2.7)

- `app/camera.py`: `snapshot(host, access_code)` (TLS to port 6000, self-signed certificate accepted, 80-byte login `0x40, 0x3000, 0, 0` + "bblp" + access code, then 16-byte frame headers with JPEG payloads), `read_frame`, `supported(printer)` (A1, A1 mini, P1P, P1S), `cached_snapshot` (2.5 s per printer, one fetch at a time), `printer_picture`, and `photograph_plate(job_id, printer_id)` called from `outcomes._record_final` (picture about 20 s after a print ends, saved as `plate_photo.jpg` with the job's files, `photo_at` on the job).
- Links: `LanLink.snapshot`, `RemoteLink.snapshot` (gateway message `snapshot`, base64 JPEG back), `gateway_agent.handle_snapshot`. Gateways need this version for remote cameras.
- API: `GET /api/printers/{id}/camera.jpg` (any signed-in user, owner decision), `GET /api/jobs/{id}/photo.jpg` (job.view); printers carry `camera: bool`, the job page `camera` and `photo` (URL).
- UI: `components/CameraView.tsx` (refreshes every 3 s while the tab is visible); job page preview switch gains "Live camera" while printing or waiting to be cleared, Plate photo and Finished plate show the saved picture; Fleet cards have a Camera button.
- Tests use a fake TLS camera with a generated certificate (`tests/test_camera.py`). Not yet seen on a real A1 mini.
- Light for the plate photo (owner request): `LanLink.light(on)` sends Bambu `ledctrl` for `chamber_light` (the work light on an A1); the printer's `lights_report` is read into `status.light`. `camera.lit_picture` switches the light on (unless it already is), waits 2 s for the exposure, takes the picture, and switches it off again; a light that cannot be switched does not stop the picture. Gateways forward a `light` message. Live views do not touch the light.

## Timelapse (2.8)

- Asked for per print: `timelapse` on the job (Submit dialog box, `POST /api/jobs/{id}/submit {timelapse}`, or `POST /api/jobs/{id}/print?timelapse=true`). `printing.start` records the card's existing videos (`timelapse_before`), sends `"timelapse": true` in the Bambu `project_file` command (also through gateways), and sets `timelapse_wanted`.
- `app/timelapse.py`: after the result (`outcomes`), a background thread waits 45 s, then every 30 s lists `/timelapse` over FTPS, takes the newest new video once its size is the same on two checks, copies it to `timelapse.mp4|avi` in the job's files, deletes it from the card, and records `timelapse_saved` (or `timelapse_missing` after 20 minutes). `LanLink.list_timelapses`, `timelapse_size`, `fetch_timelapse`. Gateway printers: the video is recorded but not fetched yet.
- `GET /api/jobs/{id}/timelapse` (job.view). Job page: Timelapse option in the preview switch (video for MP4, download for AVI) and a line in Files. The test FTPS server now understands NLST, RETR and DELE.

## Home (2.18) and My Parts (2.17)

- `GET /api/home` (`routes/home.py`): `mine` (to_collect, printing with live progress, waiting with reasons, recent), `pool` (printers free, jobs waiting, typical wait from `forecast.predict` on a one-hour PLA part), `notices` (unexpired), and for operators and admins `fleet`. Notices: `store.notices`, `POST /api/admin/notices`, `DELETE /api/admin/notices/{id}`; admins post them from Home.
- The app now lands on `#/home` (Shell default route). Menu: Home, Workspace, My jobs, My parts, then the staff pages.
- My Parts: `GET /api/parts?q=&folder=` (own models only, with jobs made from each), folders `POST/PATCH/DELETE /api/parts/folders`, `PATCH /api/parts/{id}` (rename, folder), `DELETE /api/parts/{id}` (removes the files; jobs keep their copies), `GET /api/parts/{id}/thumbnail.png`. `store.folders`; models carry `folder_id`. Retention: `model_files_days` now defaults to 0 (keep). An install where an admin already saved retention settings keeps its saved value; check Admin, Storage.
- "Open" and "Add to plate" go through Shell to App (`partRequest`).

## Phone menu, filament inventory (2.10), fleet dashboard (2.11)

- Phone: the bottom bar shows the first four pages (Home, Workspace, My jobs, My parts) and a More menu with the rest (`PHONE_TABS` in `Shell.tsx`; owner decision).
- Filament: `store.spools`; `filament.public`, `add`, `take`, `after_print` (called from `outcomes._record_final`), `totals`. API: `GET /api/filament`, `POST /api/filament/spools`, `PUT/DELETE /api/filament/spools/{id}` (permission `filament.manage`). Page `#/filament` for operators and admins.
- Dashboard: `dashboard.summary(days)` from jobs and events (utilisation from print start to end clipped to the window; shared-plate members count once; queue waits from the first `pooled` to `assigned` event). Page `#/dashboard` for admins; `components/Charts.tsx` holds `Bars` and `DailyColumns` (colours `--series-1/2`: light #001EFF/#B91C1C, dark #0084FF/#EF4444, both validated with the dataviz palette script).
- Fixes after owner review: spool cards used the class `spool`, which the workspace already uses for its 44 px round filament icon, so they rendered squashed; now `spool-card`. Colour and brand are dropdowns (`COLOURS`, `BRANDS` in `FilamentPage.tsx`, with Other… for a custom name and colour). The My Parts search box no longer stretches (it sat in a column flex parent with a 280 px basis).

## Failure learning (2.13)

- `app/failures.py`: `CAUSES`, `set_cause` (stored as `cause` on every job on the plate, with an event), `needs_cause(printer_id)` (the unexplained failed job on the latest plate), `patterns()` (flags and cause counts; thresholds `MIN_PRINTS`, `MIN_FAILS`, `MIN_RATE`, `STREAK`, window `WINDOW_DAYS`).
- API: `PUT /api/jobs/{id}/cause` (permission `failures.explain`: owner on the plate, operator, admin), `GET /api/failures` (`failures.view`: operators and admins), bed clear accepts `cause` and `cause_note`; printers carry `needs_cause`; the job page carries `cause`, `can_explain`, `cause_labels`; Home's staff card and the admin dashboard (`learning`) carry the flags. The frontend's `CAUSE_LABELS` in `api.ts` must match the backend list.

## Users, groups and permissions (3.0)

- Design: `docs/PERMISSIONS_DESIGN.md`. Data and helpers: `app/groups.py` (`RIGHTS`, built-in roles, `ensure()` run at start-up, `memberships`, `rights_in`, `groups_with`, `job_group`, `printer_groups`, `printer_allows`, `user_can_use`, `set_membership`, `describe`). Collections `groups`, `roles`; users carry `memberships` and `main_group`; jobs carry `group_id`; printers carry `groups` (empty = shared).
- Compatibility: everyone is always in the default group; the old `operator` flag still means "Operator in the default group" (the Users tab toggle keeps working and is kept in step), so existing accounts behave exactly as before. All 824 earlier tests pass unchanged.
- `app/permissions.py` was rewritten per group (same API; action table at the top). New actions: `printer.pick`, `group.members`; `job.see_all` and the All jobs list are limited to groups with "see_all"; the Queue shows and moves only jobs of groups with "queue".
- Printers limited to groups: the allocator, `queue.fits`, `forecast.predict`, `_fleet(user)` for printer choice, and direct slices (`_direct_group`) respect them.
- APIs (`routes/groups.py`): `/api/me` now includes `groups`, `main_group`, `can`; `PUT /api/me/main_group`; group operators `GET /api/groups/managed`, `POST/PUT/DELETE /api/groups/{gid}/members[/{uid}]` (no escalation: only roles whose rights they hold; never Admin; nobody leaves the default group); admins `/api/admin/roles` and `/api/admin/groups` (CRUD, make default, appoint via members). Submit takes `group_id` and `printer_id` (needs "pick_printer" in that group; becomes `pinned_printer`). Disabling a user can hand their parts over (`transfer_parts_to`).
- UI: Admin tabs Roles and Groups, printer "Who may use it", Users tab hand-over on disable; staff menu "My groups" (`components/MyGroupsPage.tsx`); account menu "My groups" (`components/AccountGroups.tsx`); Submit dialog group picker and printer choice; menus follow `user.can`.
