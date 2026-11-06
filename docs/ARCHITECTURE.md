# slicyRS architecture: job pipeline

Status: **design agreed in discussion, not yet built.** Written so the next steps can be planned and reviewed.
Open decisions are marked **OPEN** and collected at the end.

## 1. Decisions so far

| Topic | Decision |
|---|---|
| Deployment | **Three deployables**, not five microservices: **Core**, **Slicer worker(s)**, **Printer gateway**. |
| "User storage" | Accounts, uploaded files, job history, and statistics tracking all live in Core. |
| Job model | A job is a **printer-agnostic specification**. It is allocated to a printer later, then sliced for that printer, then dispatched. |
| Preview | The user sees a preview and an estimate at submit time, from a **non-binding slice for a generic machine (Bambu Lab P2S)**. |
| Batching | **Opt-in** per job, allowed across users. The optional pickup time informs how long a job may wait for partners. |
| Filament colour | The job records a colour (or **"any"**), but **colour matching is deferred**: for now the allocator matches on material only. |
| Pickup time | **Optional**, with "whenever" as the default. A **target with at-risk alerts, not a guarantee**. If a time is given and the forecast says it is unlikely, the job is **refused** until the user picks a feasible time (the earliest likely time is offered). |
| Queue order | **Submission order.** The at-risk boost (section 6.1) is **deferred to the back burner**, as are wider fairness rules; the policy stays a replaceable rule so they can be added later. |
| Environment | **High trust.** No tracking of parts after they leave the bed, no dispute process, no timeouts that stop an operator acting. |
| Bed state | A **property of the printer gateway**, with pluggable sources: manual confirmation now, camera detection and automatic bed-clearing hardware later. |
| Who confirms a bed is clear (now) | The **owner of the finished plate** or a designated **operator** (admins too). Operators can do it **at any time**; there is no waiting period. |
| Attendance | Printers run **mostly unattended**, so waiting for pickup is the normal case and must be designed for, not treated as an exception. |
| After the bed is cleared | The part is **assumed collected**. Nothing tracks it further (no shelf, no uncollected state). |
| Batched plates | **Any one owner** can clear the whole plate; the other owners are told who did. |
| Operator notification | After a finished plate has waited a **configurable** time (per printer or group), operators are notified that the printer is blocked. |
| Operators | **Assigned by an admin** (an operator role on a user account). |
| Cooling margin | **Admin-defined** setting. |
| Maximum hold for batching | **24 hours** for a job waiting for batch partners. |
| Camera detection | **Deferred.** The bed-state plug-in point stays in the design. |
| Statistics | **Log as much as possible** now (section 10.1), so reports can be built later without having lost the data. |

## 2. The three deployables

```
 Browser ──► CORE ◄──── work queue ────► SLICER WORKER(S)
              │  (frontend, accounts,      (runs Bambu Studio CLI,
              │   storage, jobs,            stateless, scales out)
              │   allocator, stats)
              │
              └──── stream (outbound from gateway) ────► PRINTER GATEWAY ──► printers (MQTT + FTPS)
                                                          (runs next to the printers)
```

| Deployable | Owns | Why it is separate |
|---|---|---|
| **Core** | Web app and API, accounts and groups, file storage, job store and **job state machine**, **allocator** (a scheduler loop), statistics, notifications | Ordinary web and database work. The single owner of job state. |
| **Slicer worker** | Running the slicer for preview slices and production slices | CPU-heavy, stateless, needs the slicer binary and profiles, can run several at once. |
| **Printer gateway** | Printer connections (status, upload, start, cancel), printer secrets at runtime | Needs LAN access, holds long-lived connections, can be placed on the printers' own network. This is also the "on-site agent" from the roadmap. |

Modules **inside Core** (kept behind interfaces so any can be split out later): `accounts`, `storage`, `jobs`, `allocator`, `stats`, `notify`.
Rule: the allocator and other modules change job state **only** through the `jobs` module, never by writing to its tables.

## 3. Entities

| Entity | Purpose | Key fields |
|---|---|---|
| **Job** | One user's request. Printer-agnostic. | owner, group, model file(s) and transforms, slicer settings (agnostic subset), material, colour (`any` or a colour), `pickup_by` (optional; empty means "whenever"), `allow_batching`, priority class, constraints (minimum nozzle, needs enclosure, and so on), state |
| **Plate** | What is actually sliced and printed: one or more jobs on one build plate. A job alone is a plate of one. | jobs, printer (once assigned), arrangement, material and colour, settings fingerprint, estimated time and filament |
| **SliceArtifact** | Output of a slicer run. | kind (`preview` or `production`), plate or job, printer profile used, G-code and bundle files, time, filament, layers, slicer and profile version |
| **Dispatch** | One attempt to print a plate on a printer. A plate can have several (after failures). | plate, printer, started, result reported by the printer (state, error codes), finished |
| **Printer** | Capability model plus live status (as today, extended). | bed size, nozzle size, enclosure, hardened nozzle, build plate, AMS and loaded filament (type and colour), status, maintenance flag, firmware, **bed state** (see section 9) |
| **Event** | Append-only log of everything that happens to jobs, plates and printers. | time, actor, kind, details. Statistics are derived from this. |

**Printer-agnostic settings.** Most settings (infill, walls, supports, layer height) are valid on any printer; some are not (a 0.2 mm layer needs a suitable nozzle). Each job gets a **compatibility check** that returns the printers that can honour its settings. A job with no compatible printer is refused at submit time with a clear reason.

## 4. Job state machine

```
 draft ─► previewing ─► submitted ─► [awaiting_approval] ─► queued ─► batched? ─► allocated
                                                                                   │
   cancelled ◄── (from most states)                                                ▼
   failed ◄──────────────────────────────────────── dispatching ◄── sliced ◄── slicing
                                                          │
                                                          ▼
                                       printing ─► finished ─► awaiting_pickup ─► collected
```

- **draft → previewing → submitted:** the web app creates the job, Core asks a worker for a preview slice, the user reviews the estimate and confirms.
- **awaiting_approval:** only when a group rule requires it (milestone 3 of the roadmap).
- **queued:** in the shared pool, waiting for the allocator. **Batched** means grouped with other jobs into a plate.
- **allocated → slicing → sliced → dispatching → printing:** the plate is assigned to a printer, sliced for it, then uploaded and started by the gateway.
- **finished → awaiting_pickup → collected:** see section 9. A printer stays blocked until its bed state becomes clear, and `collected` is set at the moment the bed is confirmed clear.
- Every transition is recorded as an Event, is safe to repeat (idempotent), and has a defined failure path: a failed slice or dispatch returns the plate to the pool with a reason and a retry count; the owner is notified after the retries run out.

## 5. Preview slice

- Slice the job for the **reference machine: Bambu Lab P2S, 0.4 mm nozzle**, using the job's material and settings, at submit time.
- Show it clearly as an **estimate**: "about 2 h 10 min, 18 g, on a typical printer."
- **Fit rule:** the P2S has a 256 mm build height and a 256 × 256 mm bed. If a part is larger, use the **largest printer in the fleet** instead, so the preview exists. If no printer fits, refuse at submit time.
- **Cache** preview results by a fingerprint of (model, transforms, settings, material, reference profile, slicer version).
- **Measure the error.** When a print finishes, store estimated versus actual time per printer model. The statistics then show how good "assume similar times" really is, and the estimate can be corrected per model.
- The preview is **never** used as the G-code that gets printed. The production slice always uses the assigned printer's profile and build plate.
- Profile availability: the slicer installed during development ships P2S machine and process profiles (0.2 to 0.8 mm nozzles), so no extra profile work is needed. Profile names change between slicer versions, so the slicer version is stored with every artifact.

## 6. Allocation

**Pull model.** Jobs wait in one shared pool. The allocator runs on a timer and on events (a printer becomes free or goes offline, a job is submitted, a pickup time approaches). It does not put a job into a specific printer's queue at submit time, so a printer failing never strands work.

For each free (or soon free) printer the allocator:

1. **Filters** jobs and plates by hard constraints: fits the build volume, material and enclosure rules, hardened nozzle for carbon fibre, nozzle size, build plate compatibility, printer enabled and not in maintenance, filament colour (see below), group rules.
2. **Orders** the remaining candidates by a **priority policy** (OPEN; see below).
3. **Scores** the best few by practical cost: filament change needed or not, wait already accrued, how well the plate fills the bed.
4. Takes the winner to the batching step (section 7), then marks the plate `allocated`.

**Filament colour.** `any` matches any loaded colour of the right material. A specific colour matches loaded spools of that colour (exact match at first). Without filament inventory (roadmap 2.10), the allocator relies on what the printer reports as loaded, and a job needing a colour that is not loaded waits or is allocated with a "load this filament" instruction to the operator. **OPEN:** how close counts as the same colour.

**Priority policy.** A replaceable rule behind one interface. The agreed default is **submission order, with a boost only for jobs at risk of missing their pickup time** (section 6.1). Per-user limits on boosts, and any wider fairness rules (group allowances, aging), plug in at the same point and are **OPEN**.

### 6.1 Pickup time: forecast, refusal and at-risk boost

The pickup time is optional. A job with no pickup time ("whenever") skips everything below and is simply scheduled in submission order.

**Meaning.** "I will arrive to collect by this time." The print must therefore finish, with a margin for the plate to cool, before it: `target finish = pickup time − cooling margin`. The margin is configurable (a starting value of about 15 minutes is a suggestion).

**Forecast at submit time.** The sum of:
- the wait for the first suitable printer (remaining time on running prints plus plates queued ahead);
- the expected time a finished printer sits **blocked** before someone clears the bed (measured by the statistics once available; large when printers run unattended);
- upload and preparation time;
- the print time, from the P2S estimate corrected by the measured error for the printer model.

It produces a **likely** finish and a **late-case** finish.

**Submit-time rule: refuse if unlikely.** If the *likely* finish is after the target finish, the job is **refused** with a message that gives the **earliest likely pickup time** and a one-click "use this time". If the likely finish meets the target but the late case does not, the job is accepted and labelled **tight**. This also keeps early times from being used to jump the queue, because a pickup time earlier than feasible cannot be entered at all.

**A target, not a guarantee.** Printers fail and people are slow. The forecast is recomputed on events (a printer finishes, fails or goes offline; another job is submitted). When the likely finish moves past the target, the job becomes **at risk** and the owner is notified early with the new estimate. A job that still misses is marked **late** and the owner is told the actual finish time; there is no penalty.

**At-risk boost.** Jobs that are at risk move ahead of jobs that are not. Boosts are limited per user (the numbers are **OPEN**), so a user cannot turn every job into an urgent one.

**Order of fallbacks shown to the user** when a time is refused: set colour to "any", allow batching, choose a faster preset (the estimate updates), or pick the offered time.

## 7. Batching

Opt-in per job; allowed across users and groups. A job's pickup time (if it has one) decides how long it can wait for partners.

**Eligible together** only if all are true: both opted in, same material and compatible colour (or `any`), identical slicer-settings fingerprint (layer height, infill, supports, and so on, because those apply to the whole plate unless per-object overrides are added later), same eligible printer set.

**How long to wait.** A job with a pickup time has *slack* = target finish − (now + expected queue wait + print time). While slack exceeds a safety margin, the job may wait for partners; when slack runs low it is released to print alone. A "whenever" job may wait for partners up to a configurable maximum hold (**OPEN**, since there is no deadline to bound it).

**Plate limits.** A maximum plate print time and part count (configurable) so one plate does not tie up a printer for a day or hold someone's part hostage to a long neighbour. The plate's pickup time is the **earliest** pickup among its jobs that have one.

**Feasibility, in two steps.**
1. A fast check in Core: pack the parts' footprints (bounding boxes, with margin, free rotation about the vertical axis) onto the target printer's bed.
2. The slicer's own arrange step is the authority when the plate is sliced. If it cannot fit everything, the last job added is removed from the plate, returned to the pool, and the plate is re-sliced.

**Accounting and failure.** Time and filament are split per job by estimated volume and time. If a plate fails, every job on it returns to the pool, and each owner is told. The opt-in text should say that a failed neighbouring part can affect other parts on the plate.

## 8. Contracts between the deployables

**Core ↔ Slicer worker: a work queue.** Core writes slice tasks (`slice_preview`, `slice_plate`) with references to input files and the settings. Workers claim a task with a time-limited lease, run the slicer, store the artifact, and report the result. A task whose worker dies is picked up again when the lease expires. Results are keyed by the fingerprint, so repeating a task is harmless. At first the queue is a database table; use Redis or similar only when workers run on more than one machine.

**Core ↔ Printer gateway: an outbound stream.** The gateway connects *out* to Core (WebSocket or gRPC) and authenticates with a service token. Core sends commands: `start(plate artifact, printer)`, `cancel`, `pause`, `resume`, `test_connection`, `confirm_bed_clear(printer, by, checklist)`. The gateway sends events: status, progress, errors (HMS codes), start confirmation, the printer's final result, and `bed_state_changed(printer, state, source, confidence)`. The gateway can therefore sit behind the printers' firewall with no inbound ports, and Core never needs LAN access to printers. Printer secrets are stored encrypted in Core and sent to the gateway over the authenticated connection; the gateway keeps them in memory only.

**Printer interface (inside the gateway).** One small adapter interface (`status`, `upload`, `start`, `cancel`, `pause`, `capabilities`). The current Bambu LAN code becomes the first adapter; other transports or brands can be added later without touching Core.

**Authentication between deployables.** Service tokens at first (one per worker and per gateway, revocable by an admin); mutual TLS later if needed.

## 9. Bed state: a finished print is not a free printer

A printer is not free when a print ends. The part has to come off and the bed has to be clear. Most printing here is unattended, so a printer will often finish at night and wait. The design treats that wait as normal and makes it short, visible, and safe.

### 9.1 Bed state belongs to the printer gateway

The gateway owns a **bed state** for every printer and reports it to Core as an event. Core's allocator only reads it.

| Bed state | Meaning |
|---|---|
| `occupied` | A print just finished, or a print is running or was started by anything else (printer screen, Bambu Studio). Not clear. |
| `clear` | A trusted source has confirmed the bed is empty and ready. The next plate may start. |
| `unknown` | The gateway has just started, lost the printer, or the printer was power-cycled. Never treated as clear. |

Each state change carries its **source** and a **confidence**:

| Source | Status | Notes |
|---|---|---|
| `manual` | Built first | A person confirms through Core, which forwards `confirm_bed_clear` to the gateway. |
| `camera` | Later | An image check against an empty-bed reference. Starts as advice only ("looks empty", "looks occupied"), not trusted to start a print. |
| `auto_clear` | Later | Hardware that clears the bed itself reports it done. Trusted. |

The gateway has a small pluggable `BedMonitor` interface, so adding camera detection or auto-clear hardware changes the gateway and nothing else.

**Trust policy is per printer, set by an admin:** which sources may mark a bed `clear` for the allocator to act on.
- `manual` only (the starting setting for every printer).
- `camera_assisted`: a camera result can raise a "looks clear, please confirm" prompt but not start a print by itself.
- `camera_trusted`: a camera result above a confidence threshold counts as clear.
- `auto_clear`: the hardware's report counts as clear.

Safety rules the gateway enforces regardless of policy:
- After a print finishes, the bed state is `occupied`, always.
- After a gateway restart or lost connection, the state is `unknown`, never `clear`.
- If the printer begins a print that Core did not start, the state becomes `occupied`.

### 9.2 Manual confirmation, the first source

- **Who:** the **owner** of the finished plate (any one owner, for a batched plate), a designated **operator**, or an admin. Others are refused.
- **Checklist,** all required: the bed is empty; the **build plate fitted matches the printer's configured plate**; the loaded filament matches the next plate if one is already assigned. The wrong-plate check is there because plate mismatches are a known cause of failed prints.
- **Effect:** the gateway records who confirmed and when (an audit entry), sets the bed state to `clear`, and the allocator can start the next plate. Confirming and starting can be one action, so the printer sits idle for seconds, not hours.

### 9.3 Making unattended running work

- **Allocate and prepare early.** The allocator assigns the next plate while the current print is still running, and the slicer and gateway pre-slice and pre-upload it. When the bed is confirmed clear, the start command is immediate.
- **Remote start at any hour** is allowed once the bed state is `clear` from a trusted source. Printing is not restricted to opening hours, because that would defeat unattended use. **OPEN:** whether to add an optional per-printer "no remote start between these hours" rule for safety.
- **Idle-blocked time is measured** (finished but not yet cleared). It is a headline statistic, because it is the main source of wasted printer time.

### 9.4 Notifications and operators

This environment is **high trust**, so there are no timeouts that block people and no tracking of parts once they have left the bed.

1. **Finish:** the owner is notified (every owner, for a batched plate) with the printer's name and the pickup time, if there is one.
2. **At the pickup time** (if set): a reminder to the owner(s).
3. **Operator notification:** if the finished plate is still waiting after a **configurable delay** (set per printer or per group; an admin chooses the value, and a starting suggestion is 2 hours), the operators are told that the printer is blocked.
4. **No waiting period for operators.** An operator (or admin) can confirm the bed clear **at any time**, including straight away. The only effect is an audit entry recording who did it.
5. **After the bed is confirmed clear, the part is assumed collected.** The job becomes `collected`; nothing further is tracked. There is no shelf, no uncollected state and no discard step.

### 9.5 Batched plates with several owners

**Any one owner** can clear the whole plate. The other owners get a short notice, "Cleared by *name* at *time*," and their pickup reminders stop. No dispute workflow is needed in a high-trust setting; the notice and the audit entry are enough to sort out any mistake informally.

## 10. Storage, statistics, retention

- **Database:** SQLite while one Core process runs; **Postgres** when several workers or gateways run on different hosts or write volume grows. The storage layer is written against a small interface so the change is contained.
- **Files:** a file-storage interface with a local-disk backend now and an S3-compatible backend later. Uploads, slice artifacts and timelapses use the same interface.
- **Statistics** are derived from the append-only Event log and Dispatch results, not stored separately: print time and filament per user, group and printer; success and failure rates and reasons; queue wait; estimate-versus-actual error; pickup delays.
- **Retention (OPEN):** how long to keep files, G-code and timelapses versus keeping only the history and statistics.

### 10.1 Logging for later analysis

Principle: **record everything cheaply now; build reports later.** Nothing here needs a dashboard to be useful, and data that was not logged cannot be recovered.

- **One event envelope** for everything: `id, time, type, actor, job_id, plate_id, printer_id, group_id, data{}` (JSON). Append-only; never edited.
- **Event types** (extend freely): job lifecycle (every state transition with the reason), preview and production slices (settings fingerprint, slicer and profile versions, estimated time and filament, wall-clock slicing time, cache hit or miss), forecasts at submit time (inputs, likely and late-case results, refusal and the earliest offered time), allocator decisions (candidates considered, why each was rejected, score of the winner), batching decisions, dispatches (file size, upload time, start command and printer reply), printer results (final state, error and HMS codes, layer reached, actual time, filament used), bed-state changes (source, who confirmed, how long the printer sat blocked), notifications sent, sign-ins and admin actions.
- **Printer telemetry samples** (state, progress, layer, nozzle and bed temperature, signal strength, errors) kept at a low rate while printing, plus every state change; raw printer reports can be kept for a short period.
- **Estimate versus actual** stored per finished job (the basis for calibrating the preview and the forecast).
- **Keep the inputs, not only the results:** store the settings fingerprint and the full settings used, so any later analysis can be re-run.
- **Privacy and size:** no file contents in events (file ids only); events are small, so a year at this scale is modest, but set a retention policy (section 12) before the table grows large.

## 11. How we get there from today's code

Each step is shippable and keeps the existing features working. The tests already use a fake printer and a fake identity provider; each step adds a fake for the next boundary.

| Step | Change | What it proves |
|---|---|---|
| **A** | Introduce the **Job** specification, the **state machine** and the **Event log** in the current app. Slicing stays in-process behind a `SlicerClient` interface. Submit creates a job and runs the **P2S preview slice**. | The job model works end to end; replaces today's immediate "slice for the chosen printer". |
| **B** | Add the **allocator loop** and the shared pool; add `pickup_by` and the awaiting-pickup steps; printing goes through a `PrinterGateway` interface (in-process, wrapping today's Bambu code). Priority default is submission order with the at-risk boost. | Queued, deadline-aware allocation with a bed-clear step. |
| **C** | Move slicing into a separate **worker process** using a database-backed task queue. | The slicer scales and fails independently. |
| **D** | Add **Plates** and **batching**: compatibility rule, footprint packing check, slicer arrange as authority, wait-for-partners using slack. | Multi-part, multi-user plates. |
| **E** | Extract the **printer gateway** as its own process that connects out to Core. | Core no longer needs LAN access; on-site deployment works. |
| **F** | Postgres and object storage, **only when** a trigger is hit (multiple hosts, or write load). | Scale-out without redesign. |

## 12. Open decisions

Resolved and recorded above: bed-clearing roles and timings, pickup-time meaning and refusal rule, queue order default, cooling margin (admin setting), operators (admin assigned), maximum hold (24 hours), and deferral of boosts, colour matching and camera detection.

1. **Wider fairness policy** (group allowances, aging), once the at-risk boost is revisited.
2. **Default cooling margin** (about 15 minutes suggested) and whether it varies by printer model or material.
3. **Likely and late-case definitions** for the forecast (for example, median and 80th percentile), calibrated from the logged data.
4. **Plate limits:** maximum plate print time and number of parts for batching.
5. **Operator notification delay:** the starting value (2 hours suggested).
6. **Reports wanted** on top of the raw logs.
7. **Retention** of files, G-code and timelapses (and of the raw telemetry in section 10.1).
8. **Remote-start safety:** whether any printer needs a "no remote start between these hours" rule.
