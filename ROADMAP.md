# slicyRS roadmap (v2)

Effort: **S** about a day, **M** 2 to 5 days, **L** 1 to 3 weeks, **XL** more than 3 weeks.
Each milestone ends with an exit check so you know when it is done.

## What we decided (from our planning conversation)

| Question | Answer | What it changes |
|---|---|---|
| Audience | A department or society: 20 to 200 people, 5 to 20 printers | Queueing, notifications, approval and quotas are core, not optional. A single server is still enough at first; scale-out is moved later with explicit triggers. |
| Priorities now | **Slicing power** and **running the fleet** | Milestones 1 and 2 come first. |
| Files people bring | Mostly raw STL/STEP from CAD | Orientation, supports and good presets matter most. Opening finished Bambu Studio projects drops to "later". |
| Multi-colour / AMS | Not a priority | Moved to the end of the roadmap. |
| Extras wanted | **Integrations** and **timelapse and camera** | New milestone 4, and camera/timelapse joins the fleet milestone. |

## Where we are today

Working and tested:

- Upload STL, 3MF, OBJ and STEP; 3D preview; rotate and scale; real slicing through Bambu Studio's CLI; G-code layer preview.
- Automatic printer choice from part size, filament, enclosure, hardened nozzle, availability, loaded filament and queue length.
- Accounts: invite-only, emailed one-time codes (Brevo ready), Imperial SSO written and tested against a mock provider, admin and user roles, audit log.
- Admin console: users, printers (MQTT host, port, serial, access code, nozzle, build plate), connection test.
- Printing to a real A1 Mini (upload over FTPS, start command, clear error messages). Needs Developer Mode on the printer.
- 258 automated tests, plus a fake printer and a fake identity provider.

Not yet proven or thin:

- A fully successful real print after the build-plate fix; real Brevo delivery; Imperial SSO on the real tenant; the Docker build on Linux.
- Slicer settings are a small subset; one part per plate; no move tool; no live progress; "queue" is only a counter.

---

## MVP: the smallest version that serves 20 to 200 users on 5 to 20 printers

Decisions since the last revision: camera detection, at-risk boosts and colour matching are **on the back burner**; the maximum wait for batching is **24 hours**; the cooling margin is an **admin setting**; operators are **assigned by an admin**; statistics are **logged as completely as possible** from the start.

**In the MVP**

| Area | What | From |
|---|---|---|
| Reliable printing | Correct slicer profiles and edits-only settings (done), first successful real print, failure diagnostics from the printer, plate and filament compatibility in printer choice | 0.1, 0.10, 0.11 |
| Hosting | Real email delivery (Brevo), Linux Docker build that slices, hosted deployment with HTTPS, CI, backup, basic retention, security headers | 0.2 to 0.5, 0.7, 0.8 |
| Job model | Printer-agnostic job, state machine, event log, P2S preview and estimate at submit | A.1 |
| Allocation | Shared pool, submission order, material matching (colour recorded, not matched), optional pickup time with the submit-time forecast and refusal, at-risk and late **alerts** (no boost) | A.2 |
| Bed state | Manual confirmation by owner or operator with checklist, finish and pickup reminders, configurable operator notification delay, operators assigned by admin | A.3 |
| Boundaries | Printer interface and storage interfaces, slicing behind an interface (still in the same process) | A.4, A.9 |
| Operations | Email notifications (finished, needs clearing, failed, at risk), live progress, cancel and pause, admin settings (cooling margin, operator delay, operators) | 2.1, 2.3, 2.4 |
| Data | The full event log and telemetry sampling from section 10.1 of the architecture doc; no dashboards yet | A.8 (logging only) |
| Slicing basics | Rotate, scale, lay flat on the largest face | part of 1.2 |

**Not in the MVP** (post-MVP, in this order)
1. Batching across users (A.6) with the 24-hour hold, then the separate slicer worker (A.5).
2. Auto-orient, move and arrange; settings generated from profiles; supports; saved presets (Milestone 1).
3. Queue with automatic dispatch and filament inventory (2.2, 2.10); statistics dashboards (2.11).
4. Printer gateway as its own process (A.7), groups and quotas, approval (Milestone 3), integrations (Milestone 4).
5. Back burner: at-risk boost, colour matching, camera and automatic bed detection, multi-colour, project import.

**MVP acceptance test** (all must pass on the hosted site with real printers):
1. A new invited student signs in with an emailed code, uploads an STL, sees an estimate, and submits with a pickup time.
2. A pickup time earlier than the forecast allows is refused, with the earliest likely time offered and one click to accept it.
3. The job is allocated, sliced for the chosen printer with that printer's real start routine, printed, and the owner is emailed on completion.
4. The printer stays blocked until the owner or an operator confirms the bed clear; an operator is notified after the configured delay.
5. A printer failure is reported with the printer's own error, the job returns to the pool, and the owner is told.
6. Every step above appears in the event log with enough data to recompute the estimate error afterwards.

**Rough size:** about 6 to 9 weeks of focused work for one person (Milestone 0 essentials about 2 weeks, A.1 to A.4 about 4 weeks, operations and logging about 2 weeks). These are estimates, not commitments; the first two items are the least certain because they depend on real printers and an external mail provider.

---

## Milestone 0: Prove it and harden it (first) - COMPLETE (Imperial SSO 0.9 and backups moved to the back burner)

| # | Item | Effort | Done when |
|---|------|--------|-----------|
| 0.1 | **DONE.** **First successful real print**, then a print on every printer you own | S | A sliced part finishes on the A1 Mini with no manual steps. |
| 0.2 | **DONE.** **Real email delivery with Brevo**: verified sender domain (SPF, DKIM), test to imperial.ac.uk and ic.ac.uk | S | Codes arrive in under a minute and not in spam. |
| 0.3 | **DONE.** **Linux Docker build that actually slices.** Bambu Studio v02.09.01.70 (Ubuntu 22.04 AppImage, SHA-256 pinned); the real-engine tests and an end-to-end API slice pass inside the image; CI confirmed passing on GitHub | M | `docker compose up` slices a cube; smoke test in CI. |
| 0.4 | **DONE.** **Deployment stack with HTTPS: BUILT AND REHEARSED** (Linux host on the printers' network; domain or internal certificate; `deploy/install.sh`, `deploy/DEPLOY.md`). **Running on the real Linux machine (confirmed).** Printers reached and a real print started from the server (confirmed). **Backups: moved to the back burner** (manual stop-gap in `DEPLOY.md`) | M | The stack runs on the real host and a print starts from it. |
| 0.5 | **DONE.** **CI** (backend tests, frontend build, Docker slicing tests, weekly dependency audit); **confirmed passing on GitHub** | S | A broken change cannot merge. |
| 0.6 | **DONE.** **Server-side sessions** (opaque cookie, hashed at rest, idle 8 h and absolute 12 h limits, revoked at once on sign-out, user disable or delete; sign out all devices; admin can sign a user out) and **rate limits stored in the database** | M | Disabling a user or signing out revokes access at once; limits survive restarts. |
| 0.7 | **DONE.** **Retention and disk use:** files deleted after admin-set days (job files 30, failed jobs 7, unused models 30; 0 keeps forever), history kept, 410 with a clear message for expired files, uploads refused when the disk is nearly full, Admin Storage tab with usage, settings and cleanup now | M | Disk cannot silently fill. |
| 0.8 | **DONE.** **Security headers: DONE** (CSP, HSTS over https, no-store on API, framing and sniffing protection; tested, and the web app verified to work under the policy). **Dependency audit DONE:** vulnerable FastAPI/Starlette, authlib, cryptography, python-multipart, lxml, pytest and vite upgraded and pinned; pip-audit and npm audit report nothing; audit runs weekly in CI | S | A scanner reports no high findings. |
| 0.9 | **BACK BURNER: Imperial SSO on the real tenant** (needs ICT approval; `deploy/ENTRA_SETUP.md`) | S once approved | Emailed codes stay as the fallback. |
| 0.10 | **DONE.** **Plate and filament compatibility in printer choice** (rule read from the slicer's own profiles: a zero bed temperature for a plate means refused; tested against the real slicer both ways) | S | Incompatible printers are excluded at assignment instead of failing in the slicer. |
| 0.11 | **DONE.** **Print-failure diagnostics:** the printer's result (state, error and HMS codes, layer reached, duration) is saved on the job and shown in the Jobs list with a plain-language summary and first-layer advice | S | When a print fails, the job page says what the printer reported. |

**Exit check:** a new student signs in with an emailed code on the hosted site, uploads a part and gets a finished print, on a deployment you can rebuild from the README.

---

## Milestone A: Job pipeline foundation (new, from the architecture discussion)

Design in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Three deployables (Core, slicer worker, printer gateway), printer-agnostic jobs, a pool-based allocator, opt-in batching driven by pickup time, and a P2S preview slice at submit time. Build it in the order below; each step ships on its own.

| # | Item | Effort | Done when |
|---|------|--------|-----------|
| A.1 | **DONE (preview slice uses X1C until a P2S printer model is added).** **Job specification, state machine and event log**; submit runs a **P2S preview slice** and shows an estimate | L | A job is saved printer-agnostically, the user sees a preview and estimate, and every state change is logged. |
| A.2 | **DONE except colour matching and email-only alerts (parked).** **Allocator loop with a shared pool**, optional `pickup_by` on jobs, colour `any` or specific, submission-order default with an **at-risk boost**, **submit-time forecast that refuses unlikely pickup times** (offering the earliest likely time), at-risk and late notifications | L | Jobs wait in one pool and are assigned when a printer frees up; a pickup time the forecast calls unlikely cannot be submitted; a job slipping past its target alerts its owner early. |
| A.3 | **DONE.** **Bed state in the printer interface** (`occupied`, `clear`, `unknown` with source and confidence), manual confirmation by the owner or an operator with a checklist (bed empty, plate matches, filament matches), finish and pickup reminders, a configurable delay before operators are notified, operators able to clear at any time, parts assumed collected once the bed is cleared | L | A printer cannot start the next plate until its bed state is `clear`; a finished print always sets `occupied`; a restart sets `unknown`; every confirmation is audited; there is no timeout blocking operators and no tracking of parts after the bed is cleared. |
| A.4 | **DONE (interface and one adapter; Core still runs in the same process as the Bambu connector).** **Printer interface boundary** (adapter interface; today's Bambu code becomes the first adapter) | M | Core only talks to printers through the interface. |
| A.5 | **DONE.** **Separate slicer worker process** with a database-backed task queue | M | Slicing runs outside the web process, retries safely, and can run more than one worker. |
| A.6 | **DONE.** **Plates and batching**: opt-in, across users, footprint packing check, slicer arrange as authority, wait-for-partners using slack | XL | Two users' compatible parts print on one plate, and a failed plate returns both jobs to the pool. |
| A.7 | **DONE.** **Printer gateway as its own process** that connects out to Core | L | Core runs anywhere with no LAN access to printers; the gateway runs on-site. |
| A.8 | **DONE (API and an Admin > Pipeline view; no charts or export yet).** **Statistics from the event log**, including estimate versus actual print time | M | Admins can see usage by user, group and printer, and how accurate the preview estimate is. |
| A.9 | **DONE.** **Storage interfaces** (database and files) so Postgres and object storage are a contained change later | M | Switching backends needs no changes outside the storage layer. |

**Not started:** nothing; Milestone A is built. What remains is running it on real printers, including through a gateway. The exit check below is met.

**Exit check:** a user submits a part with a pickup time, sees an estimate, and the part is allocated, sliced for the chosen printer, and printed with no manual choice of printer; two opted-in jobs share a plate; and killing a slicer worker mid-slice does not lose the job.

Items 2.2 (queue with dispatch) and 3.x (quotas, approval) below build on this milestone rather than standing alone.

---

## Milestone 1: Slicing power for raw CAD files

Focus: people arrive with an STL or STEP and want a good print without learning a slicer.

| # | Item | Effort | Notes |
|---|------|--------|-------|
| 1.1 | **DONE for the process settings** (`settings_schema.json`, 249 options on Bambu Studio's own five pages and groups, same order, labels, units, ranges and choices; generated from the pinned release's source by `backend/scripts/gen_settings_schema.py`; every setting is checked against the real slicer). **Not done: greying out options that do not apply. DEFERRED until the permission system (3.0) exists: the Filament and Printer setting pages (changing temperatures, flow and machine limits can damage printers or waste material, so editing needs gating). Tooltips: decided not included, because of the licence, see Risks.** **Full settings coverage**, generated from the engine's own profiles instead of the hand-written list | L | Biggest item. Correct defaults, units and descriptions for every setting; stays correct when profiles update. |
| 1.2 | **DONE for a single part:** auto-orient and lay flat by picking a face (`orient.py`), and a Move tool (drag the part on the bed or type X and Y mm from the bed centre; Auto-arrange returns it to the slicer's own placement). A part placed by hand is printed exactly there (the slicer's arrange is switched off for it), must fit the plate at that spot on whichever printer takes it, and never shares a plate. **Still to do: several parts on one plate in the editor (1.6).** | M | Auto-orient picks the face down that minimises overhangs and supports. Needs position sent to the slicer, not just rotation and scale. |
| 1.3 | **DONE** (`goals.py`: Fast draft, Standard, Strong, Fine detail as one-click buttons above the preset menu; flexible filament is slowed, warp-prone materials get a brim; every goal and material is checked against the real slicer). **Sensible presets by goal**: "Fast draft", "Standard", "Strong", "Fine detail", with material-aware defaults | M | Gives beginners one choice instead of 500. Advanced users keep full control. |
| 1.4 | **DONE, TESTED BY THE OWNER:** the toolbar's triangle button highlights faces needing support in red while preparing (`overhang.ts`, using the support threshold angle setting; the share of the surface is shown in the left panel); the sliced preview shows the real supports through the 1.9 legend. **DESIGN DECIDED (owner):** (a) while preparing, highlight the faces that will need support (steep overhangs) live as the part is rotated, labelled an estimate; (b) after slicing, show the slicer's real support structures in their own colour with a toggle in the preview; tree or normal stays a setting. **Not chosen:** painting enforce or block regions. **Supports**: preview where supports go, on/off per region (block/enforce), tree vs normal | L | Today only the on/off and type settings exist. |
| 1.5 | **DONE** (`presets.py`; a saved-presets menu under the goals; admins can share a preset with everyone). **Saved user presets** and admin-published shared presets | M | |
| 1.6 | **DONE, TESTED BY THE OWNER:** a Parts panel (select, remove, Add part, copies of the selected part, Arrange all), click or drag any part, parts stop at neighbours and at the plate edge, overlapping parts block Slice and Submit; the server re-checks everything and names the parts that do not fit a printer. Note: the slicer's own arrange may turn parts about the vertical axis to pack them, and cannot be told not to; the turn is applied to the part's Z rotation. **DESIGN DECIDED (owner):** one job holds several parts. A part list with 'Add part' (upload more files) and a copies (quantity) control per part; select a part to move, rotate, scale or remove it. One shared settings panel. Parts may not overlap: dragging stops at a neighbour, and 'Auto-arrange all' repacks with the slicer's own arrange. At most 12 parts (copies count). If the parts together do not fit a printer's bed, that printer is ruled out like any other blocker and the person is told which parts overflow. A multi-part plate is one pool job with one owner and never joins other people's plates. **Multiple parts on one plate**, per-object settings | L | Builds on 1.2. |
| 1.7 | **DEFERRED for thin walls** (decided: not necessary yet; upload already warns about holes, tiny size, steep overhangs, little bed contact and several pieces: `meshcheck.py`). **Trigger to build the rest:** real failed prints (recorded by 2.13 failure learning) show thin walls as a common cause; options then are a voxel check for small parts, a ray-casting library for full coverage, or showing the slicer's own thin-wall warnings. **PARTLY DONE** (holes, tiny size, steep overhang share, little bed contact, several pieces: `meshcheck.py`, shown under the part). **Still to do: thin walls.** **Print-readiness checks on upload**: thin walls, steep overhangs, non-manifold geometry, parts too big, with plain-language advice | M | Warnings before slicing, using the same checks that already run during assignment plus mesh analysis. Also reduces failed prints. |
| 1.8 | **DONE for the common faults** (duplicate vertices, zero-area and flipped faces, small holes, applied whenever a model is loaded; reported on upload). **Mesh repair** on upload | M | Common cause of bad slices from CAD exports. |
| 1.9 | **DONE, TESTED BY THE OWNER:** legend with a checkbox per feature type, a travel-moves toggle, and layer-by-layer playback with a position slider (the parser and filters were checked in Node on real G-code). **DESIGN DECIDED (owner):** colour toolpaths by feature type only for now (outer wall, inner wall, infill, top surface, supports, bridges and so on, like Bambu Studio); a legend with a checkbox per feature type; a travel-moves toggle; keep the layer range sliders and add play/pause animation along the toolpath. **Not now:** speed, flow, layer-height or temperature colouring, and a single-layer mode. **Better G-code viewer**: colour by speed/flow/temperature, layer scrubber, travel moves, animation | M | |
| 1.10 | **DONE for nozzle sizes** (0.2, 0.4, 0.6, 0.8 mm: a process preset names its nozzle; the nozzle selector offers only sizes that the fleet's enabled printers have, a connected printer reports its own nozzle size and an admin sets it for the others; assignment, forecast, batching and slicing all use it; every printer model x nozzle slices with the real engine). **DEFERRED until the permission system (3.0) exists: more materials and filament profile management (adding or changing a material must be gated by permission).** **Nozzle sizes and more materials** (0.2, 0.6, 0.8 mm; extra filament profiles) | M | Printer record gets a nozzle size; assignment checks it. |
| 1.11 | **BUILT, ON BY DEFAULT (owner decision), NOT YET SEEN ON A PRINTER'S SCREEN** (`thumbnail.py`: the slicer's headless bundle names `plate_1.png` but does not contain it, so a numpy renderer adds it; `BUNDLE_THUMBNAIL=0` turns it off). **Plate thumbnail and name on the printer's screen** | S | Bambu Studio's bundle carries images; verify they show on the printer. |
| 1.12 | **DEFERRED (owner decision): cut and measure tools** | M | Lower priority than the rest of Milestone 1; revisit after the UI rework (Milestone U) or when someone asks for it. |

**Exit check:** a typical CAD part is sliced in slicyRS and in Bambu Studio, and time and filament agree within a few percent; auto-orient picks an orientation a reviewer agrees with on a set of ten sample parts.

---

## Milestone U: UI rework (between Milestone 1 and Milestone 2)

Do this once the viewport-heavy Milestone 1 items (1.2 move and arrange, 1.4 supports preview, 1.9 G-code viewer) are in, and before Milestone 2 adds its many new screens (queue, live progress, filament inventory, dashboard).

| # | Item | Effort | Notes |
|---|------|--------|-------|
| U.1 | **DESIGN DECIDED (owner):** users are students and members on laptops, people checking status on phones, and admins on desktop; a left sidebar of real pages (Workspace, Jobs, Fleet, Admin) that becomes a bottom tab bar on phones; its own modern identity, not a Bambu Studio imitation (a palette, type and component style to be proposed and approved in mockups); light and dark following the device with a manual switch; the biggest pain points are the workspace, the Jobs list, the Fleet page and the Submit to pool dialog; the prepare-then-preview workflow is simplified into guided steps (upload, choose goal and material, review, submit) with a way to the full settings panel for experienced users; build on a component library (accessible dialogs, menus, tabs) instead of hand-rolled ones. Mockups are made in Claude Design first, starting with the Jobs and Fleet pages and a design system sheet. **Mockups approved (owner):** angular look with cut corners and an ICRS-style hatching motif (hatching is a placeholder until the real ICRS asset and colours are supplied); Fleet cards and job details show which user is using or submitted each job; Workspace is guided steps (model, goal and material, review, submit) with a large viewport and the sidebar shrunk to an icon strip on that page. Submit to pool dialog and Admin are mocked too; the palette, fonts and 45 degree hatching now come from the ICRS brand kit in Figma. **Build started:** Radix UI primitives chosen as the component library; order is shell first, then pages, then the guided Workspace and Submit dialog. Done so far: sidebar and hash routing (Workspace, Jobs, Fleet, Admin), bottom tab bar on phones, theme switch, brand tokens and fonts (London Underground dot matrix bundled under OFL), Jobs, Fleet and Admin as pages, and operators and admins see who is using each printer. **Milestone U is DONE (owner tested the guided Workspace):** guided steps with supports under goal and material, ICRS styling everywhere, brand marks (lettermark and Harry from the Figma kit, single colour), the dot-matrix font bundled but unused (tried on times and counts, did not suit; kept for a future overview or dashboard), staff-only pages marked with the brand's alert yellow (a Staff divider in the menu, a yellow edge on Fleet and Admin, and a yellow tag on those pages), Fleet visible to operators and admins only (members confirm their own plate is clear from Jobs; a member home overview is 2.18), material and nozzle chips, a phone layout for the Workspace, and an accessibility pass (no overflow at 375 px, every button and field named). **Owner decision:** the Submit dialog keeps pool-only printer choice for now; letting operators or others pin a job to one printer is part of the permissions milestone (3.0). **UI rework**: real pages and navigation instead of one screen plus modals, a small design system, a phone and tablet layout, an accessibility pass, and splitting `App.tsx` into feature areas | L | **DESIGN TO CLARIFY WITH THE OWNER WHEN WE REACH THIS.** Ask about: who uses it most and on what devices; the pages and navigation you want; whether to keep the Bambu Studio look or give it its own identity (and any Imperial or society branding); light and dark themes; which screens hurt most today; how much to keep of the current prepare and preview workflow; and whether to move to a component library. |

**Exit check:** every existing screen works on a phone, can be reached from the main navigation, and passes a keyboard-only and screen-reader walk-through.

---

## Milestone 2: Running a fleet of 5 to 20 printers

| # | Item | Effort | Notes |
|---|------|--------|-------|
| 2.1 | **Live print progress**: percent, time left, temperatures, layer, on job and fleet pages | M | **DONE, TESTED BY THE OWNER ON A REAL PRINTER.** `routes/common.live_progress` reads the status every printer already reports (percent, minutes left, layer, nozzle and bed temperature) for a job that is printing on a busy printer, so an old reading never shows on a finished job. Shown as a bar and numbers on job cards, a "Live from <printer>" panel on the job page (refreshing every 5 s, with a clock time for the finish) and on Fleet cards; a paused print turns the bar yellow. |
| 2.2 | **Staff queue with automatic dispatch** (was "Real per-printer queue"): the shared pool already waits unassigned and dispatches to the first free printer with a confirmed-clear plate (Milestone A). Added: a Queue page for operators and admins with the pool and every printer's line as columns of job cards; drag (or arrows, or a menu) to reorder the pool or line a job up for one printer | L | **DONE, TESTED BY THE OWNER.** `app/queue.py` (`queue_rank`, `pinned_printer`, `board()`, `move()`, `reset()`), `routes/queue.py`, `components/QueuePage.tsx`. The allocator offers lined-up jobs to their printer first, then the pool in staff order, then the automatic order (pickup at risk, then submission). A job cannot be lined up for a printer that cannot print it or is out of service. "Let the pool decide" clears all hand placing. **Owner decision:** printer choice by staff only, through this page; members still cannot choose (3.0). The old "Queue" button in All settings (marked a direct slice as queued, dispatched nothing) was removed with its endpoint (owner decision). **Owner decision (auto-start):** pool jobs always start by themselves as soon as they are sliced and the printer's plate is confirmed clear, whether or not anyone is around; the Submit dialog no longer asks. A job whose automatic start fails stays ready and returns to the pool after the hold time. |
| 2.3 | **Notifications**: email on finished or failed, optional Slack or Teams; per-user opt-out. **Now delivered as part of 2.14 (the notification service).** | M | Brevo is already available for email. |
| 2.4 | **Cancel, pause and resume** from the web | M | **DONE, TESTED BY THE OWNER ON A REAL PRINTER.** `app/printcontrol.py` sends Bambu MQTT pause, resume and stop through the direct link or a gateway and waits for the printer to confirm. **Owner decisions:** the job's owners, operators and admins may pause and resume; a shared plate can only be stopped by an operator or admin (any owner may stop a plate that is only theirs); the person who pressed the button is not emailed, the other owners and operators are. A stop from the web is recorded as "Stopped from the web by …" and does not put the printer into "needs attention". Buttons on the job page's live panel and on Fleet cards. (Was: owner or admin only.) |
| 2.5 | **Estimated wait time** per printer, shown when choosing | S | **DONE, TESTED BY THE OWNER.** `upkeep.wait_s`: the rest of the running print plus the estimates of jobs already sent to the printer; shown in the Workspace printer menu ("free now" or "about 45 min wait") and on Fleet cards; none for a printer that is offline or out of the pool. |
| 2.6 | **Maintenance mode and a "needs attention" state** (HMS errors, failed last print, door open) | S | Excludes a printer from assignment without deleting it. **Also the detection side of mid-print alerts (see 2.16).** **DONE, TESTED BY THE OWNER ON A REAL PRINTER.** `app/upkeep.py`. **Owner decisions:** operators and admins put a printer into maintenance (with a note everyone can see) and mark it ready; a failed print and a printer error code mid-print both put the printer into "needs attention", out of the pool until someone marks it ready. Maintenance and attention are temporary: jobs can still be submitted and wait for the printer. Fleet cards show both, with Mark ready, Maintenance and Back in service. |
| 2.7 | **Camera view** | M | **DONE FOR THE A1 AND P1 FAMILIES; LIVE VIEW AND PLATE PHOTO (WITH THE LIGHT) TESTED BY THE OWNER.** `app/camera.py` speaks the port-6000 picture protocol (TLS, 80-byte login, JPEG frames), directly or through a gateway. **Owner decisions:** A1 first; a photo of the plate is taken about 20 s after each print ends, with the printer's light switched on for it and put back afterwards (owner request) and shown on the job page (Plate photo, Finished plate); a live picture (refreshed every 3 s, shared between viewers for 2.5 s) on the job page while printing or waiting to be cleared, and on Fleet cards; everyone signed in may see camera pictures. X1 and H2D (RTSPS video) are not supported yet. |
| 2.8 | **Timelapse per job**: fetch the finished timelapse from the printer and attach it to the job | M | Your A1 Mini already has a `timelapse` folder on its SD card, reachable over the same FTPS connection used for uploads. Needs timelapse switched on per print. **DONE, TESTED BY THE OWNER ON A REAL PRINT.** `app/timelapse.py`. **Owner decisions:** recorded only when the owner asks (a tick box in the Submit dialog, and a Timelapse box next to Print); copied off the SD card after the print (once the new video stops growing), then deleted from the card; the job's owners, operators and admins can watch or download it on the job page; deleted with the job's other files. MP4 plays in the page; AVI (P1 family) is offered as a download. Printers behind a gateway: moved to the back burner (see Later). |
| 2.9 | **Job page** (widened from "Job history, reprint, same settings again"): one page per job at `#/jobs/<id>`, opened from the Jobs list. It shows the file and plate (and the other parts on a shared plate), the goal, preset and the settings changed from it, the material, nozzle, printer and build plate, the sliced preview using the existing G-code viewer, the estimate against what really happened, the printer's failure result, the full event history, the pickup time, and actions (reprint, same settings again, cancel, download G-code). Slots are reserved for a photo of the finished plate (2.7 camera), live progress (2.1) and the timelapse (2.8), so those land on this page instead of getting screens of their own | M | **DONE, TESTED BY THE OWNER.** **Owner decisions:** settings show what changed from the preset with the full list on demand; owners, operators and admins can open a job page (operators read only); "Same settings again" with a deleted model keeps the settings and asks for the file; a shared plate shows the other part names, and owners only to operators and admins. Also (owner request): Jobs is split into **My jobs** (everyone, own jobs) and **All jobs** (operators and admins, staff marker), and Fleet names link to job pages. **Was:** Do it right after 2.15 and before 2.1, 2.7 and 2.8. Needs a stored preview picture per job (see 1.11, off by default and unconfirmed) and, later, a stored camera snapshot per job. **DESIGN TO CLARIFY WITH THE OWNER WHEN WE REACH THIS.** Ask about: how much of the settings list to show (only what differs from the preset, or everything); who may open another person's job (operators and admins only?); whether the page has a shareable link for a person's own job; what "same settings again" should do with an old model that has been deleted; and what to show for a shared plate's other owners. |
| 2.18 | **Home overview for members**: a start page for people who are not operators, now that Fleet is for operators and admins only (owner decision). It shows their own jobs at a glance (waiting, printing with progress, ready to collect, needs their plate cleared), how busy the pool is (jobs waiting, rough wait for a typical part), which materials and nozzle sizes are available, and notices from admins, without printer connection details or other people's names. A natural home for the bundled London Underground dot-matrix font (big status numbers, a departure-board look) | M | **BUILT, NEEDS A BROWSER CHECK.** `routes/home.py`, `components/HomePage.tsx`. **Owner decisions:** everyone lands on Home (first in the menu); members see their jobs at a glance (to collect, printing with progress, waiting, recently finished), how busy the pool is (printers free, jobs waiting, a rough wait for a one-hour print) and notices from admins (posted and removed on Home itself, info or warning, optional end time); operators and admins also see fleet numbers (states, needs attention, plates to clear, maintenance, jobs at risk). Materials-available was not chosen. The dot-matrix font is still unused. |
| 2.17 | **My Parts**: a library page of the person's 3D models with search and organisation, kept beyond the normal expiry. "Use again" starts a Workspace plate from a saved part; a job's page links back to its part | L | **BUILT, NEEDS A BROWSER CHECK.** `routes/parts.py`, `components/PartsPage.tsx`. **Owner decisions:** every uploaded model is kept (the model expiry now defaults to 0, keep forever; an admin can still set one), organised in folders with search by name. Each part shows a picture, size, jobs made from it and a link to the last one; Open (new plate), Add to plate, Rename, move to a folder, Delete. Sharing with groups waits for 3.0; quotas for 3.2. |
| 2.10 | **Filament inventory**: spools, remaining weight, low-stock warnings, tie AMS slots to spools | L | Feeds smarter printer choice and tells admins what to buy. **BUILT, NEEDS A BROWSER CHECK.** `app/filament.py`, `routes/filament.py`, `components/FilamentPage.tsx` (staff menu). **Owner decisions:** spools with material, colour and brand (both chosen from lists, with Other for anything else; owner request), spool and remaining weight, and price (cost per kg shown; stock value per material); operators and admins manage them. A spool can be marked as loaded on a printer, and each print on that printer in its material takes its estimated grams off (a failed print only the share it printed). AMS slot linking and low-stock warnings were not chosen. |
| 2.11 | **Fleet dashboard for admins**: utilisation, queue length, failures by printer, filament use | M **BUILT, NEEDS A BROWSER CHECK.** `app/dashboard.py`, `GET /api/admin/dashboard?days=7|30`, `components/DashboardPage.tsx` (admins). **Owner decisions:** utilisation per printer, failure rate by printer and by material with grouped reasons, queue waits (median, 90th percentile, longest, at risk, missed pickups), filament by material and by person, and prints per day; last 7 or 30 days. Big numbers use the dot-matrix font. Charts follow the dataviz rules (validated two-colour palette, hover tooltips, table view). |
| 2.12 | **H2D and other model support** | M | Needs process presets that the installed Bambu Studio lacks for some H2D layer heights. |
| 2.16 | **Mid-print alerts**: tell the right people when something goes wrong while a job is printing (a pause, a printer error code, a failed print, the printer going offline mid-job). Today only the end of a print is recorded; nothing is sent for a pause or an error during one, and the Fleet page is the only place it shows. Detection comes from 2.6 (printer state and error codes from the status the server already receives), delivery from 2.14, and what a person can do about it from 2.4 (cancel, pause, resume). Build 2.6 and the first channel of 2.14 (email) together so the alert is not built twice | M | **DONE (`app/alerts.py`), TESTED BY THE OWNER:** a pause, a printer error code (once per code per print) and a printer going offline mid-print tell the owners on the plate (full text) and the operators (short text), once per event; a failed print is covered by the existing result plus the new `failed` message. **Owner decision:** owner and operators are told. **Original design questions:** Ask about: who is told for each case (the owner, operators, or both; every owner of a shared plate); which events count and how urgent each is; whether an ongoing error re-alerts and how often; whether operators get a shorter message than owners; and what the message links to. A stopgap before Milestone 2 was offered (email the owner on a mid-print failure with the current sender) and not taken. |
| 2.14 | **Notification service** (do this first in Milestone 2; it expands and replaces 2.3): job events become messages, routed to channels (email now; Discord, Slack or Teams, and SMS as plug-in channels) under per-user preferences. Today emails are sent directly from the allocator, bed state and alerts | L | **DONE (owner design decisions below), TESTED BY THE OWNER (email and Discord):** `app/notify.py` is the one place that turns an event into messages; channels are email and admin-set shared Discord webhooks (more plug in as functions). **Owner decisions:** channels email and Discord webhook; Discord channels are shared and set by admins (not per user); channel posts may include names and file names; one message per event, no quiet hours and no repeats; owners are emailed by default for finished and failed only, so pickup-at-risk and past-pickup emails are now off unless a person opts in; operators also get printer-problem events; people switch events on or off from Notifications in the account menu. Still open from the original design list: SMS, Slack or Teams, digests and quiet hours (not wanted for now). **Original design questions:** Ask about: which channels and in what order; Discord by webhook or by a bot; which SMS provider, its cost and who pays; whether channels belong to a person, a group or both (for example a society's Discord channel); which events matter and which should be quiet by default; digests versus instant messages; quiet hours; how a person links their own Discord or phone number; and what must never be sent over SMS or a shared channel. |
| 2.15 | **Central permission check** (early in Milestone 2, before 2.2 and 2.4): one place that decides what a person may do (cancel, pause, clear a bed, change maintenance mode) instead of owner, admin and operator tests scattered through the code | M | **DONE.** `app/permissions.py`: `can(user, action, target)` and `require(...)` with the action table in its docstring (admin, job.see_all, job.manage, model.use, plate.clear, plate.mark_occupied, printer.see_people, printer.see_connection, preset.share, preset.delete). Same rules as before; routes, bed state, presets and notification preferences now ask it, and a disabled account is refused everything. Small first step towards 3.0. |
| 2.13 | **Failure learning**: record why prints fail and show patterns (plate, filament, printer) | M | **BUILT, NEEDS A BROWSER CHECK; MORE USEFUL AS HISTORY GROWS.** `app/failures.py`. **Owner decisions:** after a failed print, its owner or the operator who clears the plate picks the cause from a list (didn't stick, spaghetti, warping, layer shift, clog, filament, quality, on purpose, other) with an optional note, on the job page or while confirming the plate is clear; either can change it. Patterns over the last 60 days (printer and material, material, build plate, layer height, supports, spool; a printer's recent run) are flagged to operators and admins only, on Home and the dashboard, when at least 4 prints include 3 failures at 50% or more; flags do not take printers out of the pool, and members see no advice. Deliberate stops are left out. |

Camera and timelapse notes (to verify on your own printers before committing): Bambu printers expose their camera differently by model family. The A1 and P1 series use a proprietary image stream on a local port, while the X1 and H2D series use a standard video stream. Treat camera as one printer model first, then extend. Timelapse files are simpler: they are plain files on the printer's SD card.

**Exit check:** an admin can leave 10 printers running unattended for a week: jobs dispatch themselves, failures email the owner, a timelapse is attached to each finished job, and the dashboard shows what needs attention.

---

## Milestone 3: Governance for 20 to 200 users

| # | Item | Effort | Notes |
|---|------|--------|-------|
| 3.0 | **Users, groups and permissions rework** (do this first in Milestone 3, before 3.1 and 3.2): roles and group membership with group-level rights, built on the central check from 2.15 | L | **DESIGN TO CLARIFY WITH THE OWNER WHEN WE REACH THIS.** Ask about: which roles exist and what each may do; whether rights come from group membership; who creates and manages groups; how Imperial SSO (Entra) groups map to roles if SSO is adopted; whether people can belong to several groups and which one pays for a print; what operators and approvers are; how a person is offboarded; and how existing admins, operators and users migrate. Also decide here who may choose a specific printer when submitting to the pool (deferred from Milestone U; the backend would need a printer preference on the job). |
| 3.1 | **Groups and projects** (for example per society, course or lab) | M | Users belong to groups; jobs belong to a group. |
| 3.2 | **Quotas**: print hours and filament per user or group per term | L | Soft warnings first, then hard limits. |
| 3.3 | **Approval workflow**: jobs from selected users or groups wait for a staff member before reaching a printer | M | Per-group setting. Approver gets an email with the sliced preview. |
| 3.4 | **Cost tracking and reports**: filament and machine time by user or group, CSV export | M | Rates configurable by admins. |
| 3.5 | **Audit log export** and an optional admin second factor | S | Less urgent if Imperial SSO is the main sign-in. |
| 3.6 | **Terms and safety acknowledgement** before a first print | S | Text supplied by you. |

**Exit check:** a society can print within a monthly allowance, a course's jobs need a tutor's approval, and an admin can export a usage report for the term.

---

## Milestone 4: Integrations

| # | Item | Effort | Notes |
|---|------|--------|-------|
| 4.1 | **Slack or Teams**: job finished or failed, approvals requested, printer needs attention | M | Shares the notification system from 2.3. |
| 4.2 | **Calendar booking of printer time** (reserve a printer for a slot; the queue respects reservations) | L | Needs a choice of calendar (built-in, or Outlook/Google sync). Decide the use case first: bookings for long prints, or for a whole printer for a class. |
| 4.3 | **Import by link** from Thingiverse, Printables or MakerWorld | M | Availability differs: Thingiverse has a public API; Printables and MakerWorld have no officially supported API for this, and their terms may restrict automated downloads, so check terms before building. A safe start is "paste a direct file link" or a browser-extension hand-off. |
| 4.4 | **API tokens and documented API** for scripts and other systems | M | Also lets other tools submit jobs. |
| 4.5 | **Webhooks** for external systems | S | Builds on 4.4. |

**Exit check:** a Slack channel shows the fleet's events, and a booked slot reliably holds a printer.

---

## Later (parked until the need appears)

| Item | Effort | Trigger to start |
|------|--------|------------------|
| **Timelapse for printers behind a gateway** (owner decision: back burner): the gateway records it already; the file would have to travel back through the gateway (an authenticated upload from the gateway, since the websocket only carries small messages) | M | Pick up when a gateway-connected printer needs timelapses. |
| **Camera-based bed detection** and **automatic bed-clearing hardware** as trusted bed-state sources (the gateway already has the plug-in point) | L | Start with camera as advice only, measured against real images, once cameras are available (roadmap 2.7). Needed most because printers run mostly unattended. |
| **Automated backups and restore test** (database and files, offsite copy) | M | Before the service holds data you cannot afford to lose, or when the user base grows. Until then `DEPLOY.md` has a manual command. |
| **Multi-colour and AMS** (colour per part, painting, slot mapping, prime tower) | XL | Users regularly ask for multi-colour prints. |
| **Open and export Bambu Studio `.3mf` projects with settings intact** | L | More people start bringing prepared projects. |
| **Postgres and separate slicer workers**, with a job queue such as Redis, and object storage for files | L | Slicing queues exceed a minute at busy times, or you need more than one server. |
| ~~**On-site agent** connecting out from the printers' network, instead of a VPN~~ | XL | **Done as the printer gateway (A.7).** |
| **Fully separate gateway component**: a top-level `gateway/` folder with its own package, requirements, image, version and tests that imports nothing from `backend/app`; the Bambu LAN connector and its tests move there; the message format becomes a written, versioned contract with shared contract tests; the server refuses a gateway version it does not support; the server drops direct printer connections, so a single-machine setup runs the gateway as a second compose service (breaking change for the live stack) | L | The gateway and the backend start being developed or released independently, or a second printer brand or transport is added. Until then the gateway shares `bambulan.py`, `adapter.py` and `catalog.py` with the backend, and `Dockerfile.gateway` copies those files. |
| **Observability**: structured logs, metrics, error tracking, uptime alerts | M | Before the user count passes about 100, or earlier if outages hurt. |
| **Accessibility and mobile review**, plus the display typeface if you hold the licence | M | Before a wide launch. |
| **Installable mobile app (PWA) with push notifications** | M | Not selected for now; revisit after notifications (2.3) are in use. |

---

## Suggested order

1. **Milestone 0**, starting this week with 0.1 and 0.2 (they only need your printer and Brevo account). Add **0.11** early so the next failed print explains itself.
2. **Milestone A, steps A.1 to A.4**, starting as soon as 0.1 and 0.2 are done. The job model, allocator and printer boundary are what the queue, approval, quotas and batching all sit on, so they come before most of Milestones 1 to 3. In parallel, start **1.1 (settings from profiles)** and **1.2 (auto-orient, move, arrange)**; Milestone A's batching (A.6) needs 1.2.
3. **Finish the viewport items in Milestone 1 (1.2, 1.4, 1.9), then Milestone U (the UI rework).** Milestone 2 builds many new screens, so they should be built on the new layout.
4. **2.14 (notification service, replacing 2.3, built) and 2.15 (central permission check) first in Milestone 2**, then **2.9 (the job page)**, then **2.1, 2.5, 2.6** (live progress and the rest land on the job page), then the camera and timelapse (2.7, 2.8), **2.18 (home overview for members)** and **2.17 (My Parts)**.
5. **2.2 (queue with dispatch)** and **2.4 (cancel/pause)**, then the rest of Milestone 1 (1.4, 1.6, 1.7 thin walls).
6. **2.7 and 2.8 (camera, timelapse)** once you have checked what your own printer models expose.
7. **Milestone 3, starting with 3.0 (the users, groups and permissions rework)**, as numbers grow past a few dozen active users; **Milestone 4** after notifications exist.
8. Revisit "Later" every few weeks against the triggers.

## Open questions still to settle

- **UI rework (U.1), notification service (2.14) and permissions rework (3.0):** each has a list of design questions in its row. We agreed to go through them with you when each one is reached, not before.
- **Hosting:** a machine on the printers' network avoids the VPN question; a cloud host needs a VPN or the on-site agent. Which will it be?
- **Printer models and count:** a list of the models you will add (and which have cameras or AMS) sizes 2.7 to 2.12.
- **Approval rules:** who approves, and for which users or groups? Does a failed print count against a quota?
- **Calendar booking use case:** reserve a printer for hours, or for a class? That decides the design of 4.2.
- **Link import:** which sources do your users actually use?
- **Data retention:** how long should uploaded files, G-code and timelapses be kept (affects 0.7 and storage)?

## Risks to keep in view

- **Printer firmware.** Bambu has already tightened control access (Developer Mode is now needed). A further change could break printing from third-party software. Record the firmware versions you test with and avoid auto-updating production printers.
- **Settings schema text.** `settings_schema.json` holds Bambu Studio's page and group names, option labels, units, ranges and choices, generated from its source (AGPL-3.0); the long explanatory tooltips were deliberately left out. Decided: tooltips stay out. Revisit the labels and group names before shipping to others.
- **Slicer engine licence and versions.** Bambu Studio and OrcaSlicer are AGPL-3.0. Running them as a network service has obligations if you change them, and profile names change between versions. Pin versions and keep the profile-matching tests.
- **Third-party site terms.** Import by link (4.3) can breach a site's terms if done by scraping. Prefer official APIs or user-supplied direct links.
- **A service that can start physical machines.** Keep admin actions audited, printer access codes encrypted, and the print endpoint treated like any other action with real-world effect. "Bed cleared" confirmation (2.2) is a safety item, not a convenience.
