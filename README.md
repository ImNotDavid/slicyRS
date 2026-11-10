# slicyRS

Browser-based slicer for Bambu Lab printers. The server picks the printer for you: it looks at the part's
size, the filament and which printers are free, then slices with OrcaSlicer's CLI for that machine.

- `backend/` FastAPI: uploads, printer fleet, auto-assignment (`app/assign.py`), OrcaSlicer wrapper
  (`app/slicer.py`, `app/profiles.py`), Bambu LAN connector (`app/bambu.py`).
- `frontend/` React + TypeScript + three.js: layout from the reference screenshot, settings panel,
  3D bed, G-code layer preview.

## Run it (Windows)

```powershell
.\run.ps1            # builds what is missing, picks Bambu Studio or OrcaSlicer if installed, serves http://localhost:8000
.\run.ps1 -Mock      # no slicer needed: perimeter-only placeholder G-code (preview shows outer walls only)
```

The top bar shows which engine is active. With a real engine the preview shows walls, infill, top/bottom
surfaces and bridges. Bambu Studio's CLI is used when installed (its CLI works headless here); OrcaSlicer's
CLI crashed on the test machine (access violation at start-up) so it is only a fallback.
Override with `ORCA_BIN` and `ORCA_PROFILES_DIR` (set both). Demo printers are seeded on first run (`SEED_DEMO=0` to disable).

Manual start: `cd backend; .venv\Scripts\python -m uvicorn app.main:app --port 8000`; for UI development run
`npm run dev` in `frontend/` (port 5173, proxies `/api`).

## Docker image and the slicer engine

The image (`Dockerfile`) installs **Bambu Studio v02.09.01.70** (the Ubuntu 22.04 AppImage) and runs it headless
through `/usr/local/bin/slicer`. The download is pinned by URL **and SHA-256**; the build fails if the file changes.

```bash
docker build -t slicyrs:dev .
# real-engine tests inside the image (every printer's start G-code, real slices, settings reaching the slicer):
docker run --rm -v "$PWD/backend/tests:/srv/slicyrs/backend/tests:ro" -e SLICY_DATA_DIR=/tmp/data slicyrs:dev \
  python3 -m pytest -q -p no:cacheprovider backend/tests/test_profile_fidelity.py backend/tests/test_real_engine.py
```

**Upgrading the slicer** (do this deliberately, never automatically): pick a release from
https://github.com/bambulab/BambuStudio/releases, set `BAMBU_URL` and `BAMBU_SHA256` in the Dockerfile (build once without
the hash to read the one it prints), rebuild, and run the command above. The profile-fidelity tests exist because a
profile layout change once made every slice start with a generic G-code; they must pass before the new version is used.
Also re-check one real print on each printer model after an upgrade, because a profile update can change print behaviour.
The same tests run in CI (`.github/workflows/ci.yml`).

The image needs about 2 GB of memory to slice comfortably; the first build downloads the slicer (a few hundred MB).

## Tests

```bash
cd backend && .venv/Scripts/python -m pytest        # 100 tests, ~30 s
.venv/Scripts/python tests/make_fixtures.py          # regenerate tests/fixtures (STEP needs requirements-dev.txt)
```

`tests/fixtures/valid` holds STL (ASCII/binary), 3MF (single, multi-body, components, inch units), OBJ (tris, quads)
and STEP/STP files plus size limit cases; `tests/fixtures/invalid` holds empty, corrupt and wrong-type files.
`tests/test_api.py` also slices models from your Desktop (skipped if absent).
STEP and 3MF units are converted to millimetres on load.

## How the printer is chosen (`backend/app/assign.py`)

Hard filters: the part fits the build volume (after your rotation/scale), the material is allowed
(enclosure for ABS/ASA/PC/PA, hardened nozzle for CF), the nozzle can reach the temperature, and the
printer is online. Remaining printers are scored: idle beats busy, filament already in the AMS beats a
filament change, shorter queue wins, and the smallest bed that fits wins so large printers stay free.
You can still override the choice from the printer card.

## Job pool (Milestone A)

Besides slicing for a printer you pick (**SLICE**), a part can be **submitted to the pool** (**SUBMIT TO POOL**). The server then:

1. saves a printer-independent job and slices a **preview** (estimated time, filament, layers; X1C profile until a P2S model is added);
2. forecasts when it would be ready; an optional **pickup time** that the forecast calls unlikely is refused, with the earliest likely time offered;
3. keeps it in one shared pool; a printer that is idle, enabled, not holding another job and whose **build plate is confirmed clear** takes the best job it can
   print (jobs at risk of missing their pickup time first, then submission order), slices it for that printer, and optionally starts it (**auto start**);
4. emails the owner when a job may miss its pickup time, when it is late, and when a print has finished; operators are emailed if a finished plate stays
   unconfirmed past an admin-set delay.

Every phase change is an event (`GET /api/jobs/{id}/events`, **History** in the Jobs list). **Bed state** (`occupied`, `clear`, `unknown`) is kept per
printer: a running or finished print makes it `occupied`, a server restart makes it `unknown`, and the owner of the last print, an operator or an admin confirms it
clear with a three-item checklist (Fleet page). Operators are chosen by an admin (Admin > Users). Admin > Pipeline holds the margins and delays and shows statistics
(usage per user and printer, estimate accuracy, queue wait, pickup times met). Printing from the Print button is also blocked while a plate is `occupied`.

Slicing for the pool runs through a database task queue. By default the web process runs the workers itself (`SLICE_WORKERS`, default 2). For a separate worker,
set `EMBED_WORKERS=0` on the web process and run `python -m app.worker` against the same data directory; a worker that dies mid-slice loses nothing (its task
is handed on, and fails the job after three attempts). `ALLOCATOR_INTERVAL_S` (default 5) sets how often the pool is checked. Plate sharing between users, the printer gateway and the storage interfaces are built; a Postgres or
object-storage backend is not (see `ROADMAP.md`).

**Printer gateway.** If the server cannot reach the printers (for example it runs in the cloud), run a gateway on their network: create it under Admin > Gateways, then `CORE_URL=https://your.server GATEWAY_TOKEN=gw_… docker compose -f deploy/docker-compose.gateway.yml up -d --build` on any machine there, and set each printer's "Connected through" to it. The gateway connects out; nothing needs to be opened on either side.

## Cloud service: accounts, SSO, admin

**Sign-in.** Two methods, usable together; both are invite-only (an admin adds a person's Imperial email in
*Admin > Users*, and only then can they sign in):

* **Emailed one-time code** (works without any approval from Imperial). The person enters their email, receives a
  6-digit code valid for 10 minutes, and types it in. Codes are single use, stored only as an HMAC hash, burned after
  5 wrong guesses, and rate limited per address and per IP. The response is identical for invited and uninvited
  addresses, so the form cannot be used to discover who has an account, and nothing is sent to uninvited addresses.
  Set `SMTP_HOST` (plus `SMTP_PORT`, `SMTP_SECURITY=starttls|ssl|none`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`);
  *Admin > Users > Send myself a test email* checks the settings. Local testing: `EMAIL_CONSOLE=1` prints the code in
  the server window instead of sending it (`run.ps1` does this automatically; refused on https).
* **Imperial College SSO** (Microsoft Entra ID, OpenID Connect). Needs an app registration from Imperial ICT: see
  `deploy/ENTRA_SETUP.md`. Implemented and tested against a mock provider (`tests/test_sso_e2e.py`), not yet against the
  real tenant. `python backend/scripts/check_sso.py --probe-secret` validates the settings.

Imperial identities have two forms (`name@imperial.ac.uk` email and a short `abc123@ic.ac.uk` UPN); with SSO either
matches an invite. Addresses in `ADMIN_EMAILS` become admins on first sign-in (this is how the first admin is created),
with either method. With SSO, tokens from other tenants are rejected when `OIDC_TENANT_ID` is set.

**Build plate.** Each printer has a build plate setting (*Admin > Printers > Edit > Build plate*): Textured PEI Plate
(default), Cool Plate, Engineering Plate or High Temp Plate. It is applied to both the slice (`curr_bed_type`, so the
G-code and temperatures match the plate) and the start command sent to the printer (`bed_type`), so they cannot disagree.
Users do not choose it. Some filaments cannot print on some plates (for example ABS or PETG on a cool plate); the slicer
then refuses and the job shows the slicer's own message.

**Roles.** Users slice, see fleet status (state, filament, queue length) and only their *own* files and jobs.
Admins additionally manage users, **configure printers** and read the audit log, and can see all jobs.

**Printer (MQTT) configuration**, admin only, under *Admin → Printers*: name, model, IP/hostname, MQTT port (8883),
serial number, LAN access code, hardened nozzle, enabled. *Test connection* does a one-shot MQTT connect and reports
the printer's state and AMS slots. Access codes are encrypted at rest (key derived from `SECRET_KEY`) and never
returned by the API. Hosts that are loopback or link-local (including cloud metadata addresses) are refused.
The server connects out to the printers, so it must reach them on the LAN, via a VPN such as Tailscale or WireGuard.
Printers without a host stay "manual": an admin sets state and loaded filament by hand.

**Security notes.** Sessions are held on the server: the cookie is an opaque random token (HttpOnly, SameSite=Lax, Secure over https) whose hash is stored, with an 8-hour idle and 12-hour absolute limit; signing out, disabling or deleting a user, "sign out all devices" and an admin's "sign out" end sessions at once. Rate limits are stored in the database so restarts do not reset them. Dependencies are audited weekly in CI; state-changing
requests whose `Origin` is another host are rejected; users only reach their own models and jobs (others get 404);
every admin action and print is written to the audit log; a public (https) deployment refuses to start with
`DEV_AUTH=1`, `EMAIL_CONSOLE=1`, a missing `SECRET_KEY`, or no working sign-in method (SSO or SMTP with `SMTP_FROM`).

### Deploy

Full guide for a Linux host on the printers' network: **`deploy/DEPLOY.md`**.

```bash
./deploy/install.sh              # first run creates .env with a new SECRET_KEY; edit it; run again to build and start
./deploy/install.sh --internal   # (first run) a LAN name or IP with Caddy's own certificate, no DNS name needed
```
The stack is the app (slicer inside) plus Caddy for HTTPS; users reach it by a DNS name or, until you have one, an internal
address whose certificate authority each device trusts once. The whole stack can be rehearsed on any machine with Docker
(`deploy/docker-compose.rehearsal.yml`, with a mail catcher instead of Brevo). Backups are not automated yet (see the guide).

For SSO, register the app with Imperial ICT / Entra first (`deploy/ENTRA_SETUP.md`) and put the tenant id, client id and
secret in `.env`. For emailed codes you only need SMTP credentials (`deploy/EMAIL_SETUP.md`).

**Local development** (`.\run.ps1`) enables `DEV_AUTH` and signs you in as `admin@imperial.ac.uk` without a password.
That mode is blocked on any https `PUBLIC_URL`.

## Known gaps

- Brand: colours and Geist Mono come from the .fig (node 2712:65); the London Underground display face is not bundled.
- Real slicing is tested through Bambu Studio on Windows and inside the Linux Docker image. The Compose + Caddy HTTPS stack and the CI workflow have not been run yet.
- H2D uses X1C-tuned process presets for some layer heights because the installed Bambu Studio lacks H2D variants.
- Settings panel covers a representative subset of Orca's keys, not the full ~500. Extend `backend/app/catalog.py`.
- Move tool is not implemented (Orca auto-arranges); rotate and scale are.
- SQLite (JSON documents) and an in-process job pool: fine for one server and a small team, not for scaling out. No per-user quotas or rate limiting yet.
- Imperial SSO is implemented to the OIDC standard and tested against a mock provider that checks signatures, nonce, state, PKCE, audience, expiry and tenant; it has not been run against Imperial's real Entra tenant (needs an app registration).
- Emailed codes have been tested against a fake SMTP server, not a real mail provider; delivery to Imperial inboxes depends on your sender domain's SPF/DKIM. Rate-limit counters live in memory (reset on restart; one server process).
- Multi-colour/AMS colour painting, multiple plates and per-object settings are not supported.
- Licence: OrcaSlicer is AGPL-3.0; hosting it as a network service has obligations if you modify it.
