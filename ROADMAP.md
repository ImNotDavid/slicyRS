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
| A.6 | **Plates and batching**: opt-in, across users, footprint packing check, slicer arrange as authority, wait-for-partners using slack | XL | Two users' compatible parts print on one plate, and a failed plate returns both jobs to the pool. |
| A.7 | **Printer gateway as its own process** that connects out to Core | L | Core runs anywhere with no LAN access to printers; the gateway runs on-site. |
| A.8 | **DONE (API and an Admin > Pipeline view; no charts or export yet).** **Statistics from the event log**, including estimate versus actual print time | M | Admins can see usage by user, group and printer, and how accurate the preview estimate is. |
| A.9 | **Storage interfaces** (database and files) so Postgres and object storage are a contained change later | M | Switching backends needs no changes outside the storage layer. |

**Not started:** A.6 (plates and batching), A.7 (printer gateway as its own process), A.9 (storage interfaces). The exit check below is met except for plate sharing.

**Exit check:** a user submits a part with a pickup time, sees an estimate, and the part is allocated, sliced for the chosen printer, and printed with no manual choice of printer; two opted-in jobs share a plate; and killing a slicer worker mid-slice does not lose the job.

Items 2.2 (queue with dispatch) and 3.x (quotas, approval) below build on this milestone rather than standing alone.

---

## Milestone 1: Slicing power for raw CAD files

Focus: people arrive with an STL or STEP and want a good print without learning a slicer.

| # | Item | Effort | Notes |
|---|------|--------|-------|
| 1.1 | **Full settings coverage**, generated from the engine's own profiles instead of the hand-written list | L | Biggest item. Correct defaults, units and descriptions for every setting; stays correct when profiles update. |
| 1.2 | **Auto-orient and lay flat**, then manual **move and auto-arrange** | M | Auto-orient picks the face down that minimises overhangs and supports. Needs position sent to the slicer, not just rotation and scale. |
| 1.3 | **Sensible presets by goal**: "Fast draft", "Standard", "Strong", "Fine detail", with material-aware defaults | M | Gives beginners one choice instead of 500. Advanced users keep full control. |
| 1.4 | **Supports**: preview where supports go, on/off per region (block/enforce), tree vs normal | L | Today only the on/off and type settings exist. |
| 1.5 | **Saved user presets** and admin-published shared presets | M | |
| 1.6 | **Multiple parts on one plate**, per-object settings | L | Builds on 1.2. |
| 1.7 | **Print-readiness checks on upload**: thin walls, steep overhangs, non-manifold geometry, parts too big, with plain-language advice | M | Warnings before slicing, using the same checks that already run during assignment plus mesh analysis. Also reduces failed prints. |
| 1.8 | **Mesh repair** on upload | M | Common cause of bad slices from CAD exports. |
| 1.9 | **Better G-code viewer**: colour by speed/flow/temperature, layer scrubber, travel moves, animation | M | |
| 1.10 | **Nozzle sizes and more materials** (0.2, 0.6, 0.8 mm; extra filament profiles) | M | Printer record gets a nozzle size; assignment checks it. |
| 1.11 | **Plate thumbnail and name on the printer's screen** | S | Bambu Studio's bundle carries images; verify they show on the printer. |
| 1.12 | **Cut and measure tools** | M | Lower priority than the rest of this milestone. |

**Exit check:** a typical CAD part is sliced in slicyRS and in Bambu Studio, and time and filament agree within a few percent; auto-orient picks an orientation a reviewer agrees with on a set of ten sample parts.

---

## Milestone 2: Running a fleet of 5 to 20 printers

| # | Item | Effort | Notes |
|---|------|--------|-------|
| 2.1 | **Live print progress**: percent, time left, temperatures, layer, on job and fleet pages | M | MQTT status already carries most of it. |
| 2.2 | **Real per-printer queue with automatic dispatch** when a printer becomes free | L | Needs a "bed cleared" confirmation so the next job never starts on a full bed. Jobs wait unassigned when no printer is eligible and dispatch when one is. |
| 2.3 | **Notifications**: email on finished or failed, optional Slack or Teams; per-user opt-out | M | Brevo is already available for email. |
| 2.4 | **Cancel, pause and resume** from the web | M | Owner or admin only. |
| 2.5 | **Estimated wait time** per printer, shown when choosing | S | Sum of queued job times. |
| 2.6 | **Maintenance mode and a "needs attention" state** (HMS errors, failed last print, door open) | S | Excludes a printer from assignment without deleting it. |
| 2.7 | **Camera view** | M | See the note below on how cameras differ by model. |
| 2.8 | **Timelapse per job**: fetch the finished timelapse from the printer and attach it to the job | M | Your A1 Mini already has a `timelapse` folder on its SD card, reachable over the same FTPS connection used for uploads. Needs timelapse switched on per print. |
| 2.9 | **Job history, reprint, "same settings again"** | S | |
| 2.10 | **Filament inventory**: spools, remaining weight, low-stock warnings, tie AMS slots to spools | L | Feeds smarter printer choice and tells admins what to buy. |
| 2.11 | **Fleet dashboard for admins**: utilisation, queue length, failures by printer, filament use | M | |
| 2.12 | **H2D and other model support** | M | Needs process presets that the installed Bambu Studio lacks for some H2D layer heights. |
| 2.13 | **Failure learning**: record why prints fail and show patterns (plate, filament, printer) | M | Useful once there is enough history. |

Camera and timelapse notes (to verify on your own printers before committing): Bambu printers expose their camera differently by model family. The A1 and P1 series use a proprietary image stream on a local port, while the X1 and H2D series use a standard video stream. Treat camera as one printer model first, then extend. Timelapse files are simpler: they are plain files on the printer's SD card.

**Exit check:** an admin can leave 10 printers running unattended for a week: jobs dispatch themselves, failures email the owner, a timelapse is attached to each finished job, and the dashboard shows what needs attention.

---

## Milestone 3: Governance for 20 to 200 users

| # | Item | Effort | Notes |
|---|------|--------|-------|
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
| **Camera-based bed detection** and **automatic bed-clearing hardware** as trusted bed-state sources (the gateway already has the plug-in point) | L | Start with camera as advice only, measured against real images, once cameras are available (roadmap 2.7). Needed most because printers run mostly unattended. |
| **Automated backups and restore test** (database and files, offsite copy) | M | Before the service holds data you cannot afford to lose, or when the user base grows. Until then `DEPLOY.md` has a manual command. |
| **Multi-colour and AMS** (colour per part, painting, slot mapping, prime tower) | XL | Users regularly ask for multi-colour prints. |
| **Open and export Bambu Studio `.3mf` projects with settings intact** | L | More people start bringing prepared projects. |
| **Postgres and separate slicer workers**, with a job queue such as Redis, and object storage for files | L | Slicing queues exceed a minute at busy times, or you need more than one server. |
| **On-site agent** connecting out from the printers' network, instead of a VPN | XL | The server must run somewhere that cannot reach the printers. |
| **Observability**: structured logs, metrics, error tracking, uptime alerts | M | Before the user count passes about 100, or earlier if outages hurt. |
| **Accessibility and mobile review**, plus the display typeface if you hold the licence | M | Before a wide launch. |
| **Installable mobile app (PWA) with push notifications** | M | Not selected for now; revisit after notifications (2.3) are in use. |

---

## Suggested order

1. **Milestone 0**, starting this week with 0.1 and 0.2 (they only need your printer and Brevo account). Add **0.11** early so the next failed print explains itself.
2. **Milestone A, steps A.1 to A.4**, starting as soon as 0.1 and 0.2 are done. The job model, allocator and printer boundary are what the queue, approval, quotas and batching all sit on, so they come before most of Milestones 1 to 3. In parallel, start **1.1 (settings from profiles)** and **1.2 (auto-orient, move, arrange)**; Milestone A's batching (A.6) needs 1.2.
3. **2.1, 2.3, 2.5, 2.6**: small, visible, and they make the service feel finished.
4. **2.2 (queue with dispatch)** and **2.4 (cancel/pause)**, then **1.3, 1.4, 1.5** (presets and supports).
5. **2.7 and 2.8 (camera, timelapse)** once you have checked what your own printer models expose.
6. **Milestone 3** as numbers grow past a few dozen active users, **Milestone 4** after notifications exist.
7. Revisit "Later" every few weeks against the triggers.

## Open questions still to settle

- **Hosting:** a machine on the printers' network avoids the VPN question; a cloud host needs a VPN or the on-site agent. Which will it be?
- **Printer models and count:** a list of the models you will add (and which have cameras or AMS) sizes 2.7 to 2.12.
- **Approval rules:** who approves, and for which users or groups? Does a failed print count against a quota?
- **Calendar booking use case:** reserve a printer for hours, or for a class? That decides the design of 4.2.
- **Link import:** which sources do your users actually use?
- **Data retention:** how long should uploaded files, G-code and timelapses be kept (affects 0.7 and storage)?

## Risks to keep in view

- **Printer firmware.** Bambu has already tightened control access (Developer Mode is now needed). A further change could break printing from third-party software. Record the firmware versions you test with and avoid auto-updating production printers.
- **Slicer engine licence and versions.** Bambu Studio and OrcaSlicer are AGPL-3.0. Running them as a network service has obligations if you change them, and profile names change between versions. Pin versions and keep the profile-matching tests.
- **Third-party site terms.** Import by link (4.3) can breach a site's terms if done by scraping. Prefer official APIs or user-supplied direct links.
- **A service that can start physical machines.** Keep admin actions audited, printer access codes encrypted, and the print endpoint treated like any other action with real-world effect. "Bed cleared" confirmation (2.2) is a safety item, not a convenience.
