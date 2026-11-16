# Deploying slicyRS on a Linux host

For a server on the **same network as the printers**, running Ubuntu or Debian on x86-64. One machine runs everything:
the app with the slicer inside it, and Caddy in front for HTTPS. The server connects out to the printers
(MQTT port 8883, file transfer port 990), so no VPN is needed.

## What you need

| | |
|---|---|
| Machine | x86-64 (the slicer build is), at least 4 GB RAM (8 GB is more comfortable), 20 GB free disk, always on |
| Software | Docker Engine and the Docker Compose plugin (`sudo systemctl enable --now docker`) |
| Network | A fixed address (or reserved DHCP lease) on the printers' network; the machine can reach each printer on ports 8883 and 990 |
| Sign-in | Email codes through an SMTP service such as Brevo (see `EMAIL_SETUP.md`), and/or Imperial SSO (`ENTRA_SETUP.md`) |
| Printers | LAN mode with **Developer Mode** on, plus each printer's IP, serial number and access code |

## 1. Choose how users reach it

You haven't chosen a name yet, so both options are supported; you can switch later by editing `.env`.

**A) A DNS name** (for example `slicer.example.ac.uk`) that points at this machine. Caddy gets and renews a real certificate
by itself. Needs ports 80 and 443 reachable from the internet for the certificate check, or a DNS-challenge setup (not
included). Ask whoever runs your DNS, or ICT for a campus name.

**B) Internal only.** Users type a LAN name or the machine's IP (for example `https://192.168.1.50`). Caddy makes the certificate
from its own authority, so each user's device must trust that authority once (section 4). Nothing is exposed to the internet.
This is the right choice until you have a DNS name. Use `CADDYFILE=Caddyfile.internal`, put no port in `SITE_ADDRESS`, and
list several addresses with commas if people will use more than one (`SITE_ADDRESS=slicyrs.lan, 192.168.1.50`; then also set
`DEFAULT_SNI` to the IP, because browsers send no server name when you type an IP).

Options A and B use Caddy: set `COMPOSE_PROFILES=caddy` in `.env`.

**T) A Cloudflare Tunnel.** Cloudflare does HTTPS and no ports are opened to the internet; Caddy is not used.
- simplest, the stack runs cloudflared: `COMPOSE_PROFILES=tunnel` and `TUNNEL_TOKEN=<the tunnel's token>` in `.env`, and the
  public hostname's Service URL `http://app:8000` (`app` is the app's name inside the stack; `slicyrs_default` is the network's
  name and does not work as an address);
- cloudflared in its own container elsewhere on the server: attach it to this stack's network (`slicyrs_default`) and use
  `http://app:8000`;

Otherwise the app listens on plain HTTP at `APP_BIND:APP_PORT` (default `127.0.0.1:8000`; leave `COMPOSE_PROFILES` empty):
- cloudflared installed directly on the server: keep the default and set the Service URL to `http://localhost:8000`;
- cloudflared on another machine: set `APP_BIND` to this machine's LAN address (for example `192.168.1.111`), point the tunnel at
  `http://192.168.1.111:8000`, and let only the cloudflared machine reach that port (firewall), since it is plain HTTP.

Set `PUBLIC_URL` to the tunnel's hostname (`https://slicer.example.org`) and `MAX_UPLOAD_MB=95`: Cloudflare refuses requests over
100 MB on the Free and Pro plans. Printer gateways use the same `PUBLIC_URL` as `CORE_URL` (Cloudflare carries their
WebSocket). If you add Cloudflare Access in front, add a bypass for paths starting with `/gateway`, or the gateways cannot connect.

## 2. Install

On the server, in the project folder:

```bash
./deploy/install.sh              # option A.   Add --internal for option B.
```
The first run creates `.env` (readable only by you) with a freshly generated `SECRET_KEY`, then stops. Edit `.env`:

| Setting | Value |
|---|---|
| `SITE_ADDRESS` | the DNS name (A) or the IP / LAN name (B) |
| `CADDYFILE` | `Caddyfile` (A) or `Caddyfile.internal` (B) |
| `PUBLIC_URL` | `https://` plus `SITE_ADDRESS`, exactly what users type |
| `ADMIN_EMAILS` | your real address; the first admin signs in with a code sent here |
| `SMTP_*` | from `EMAIL_SETUP.md` (Brevo) |

Run `./deploy/install.sh` again. It checks the settings, builds the image, starts everything and waits until it is healthy.
The app refuses to start on an https address with development shortcuts or without a sign-in method, so a wrong `.env`
fails loudly instead of running unsafely.

**Back up `SECRET_KEY` somewhere safe.** It encrypts the printers' access codes: if it is lost or changed, the codes must be re-entered.

## 3. Firewall

Only Caddy is published. Allow just what users need and nothing else:

```bash
sudo ufw allow 22/tcp          # SSH, if you use it
sudo ufw allow 443/tcp
sudo ufw allow 80/tcp          # needed for option A (certificate check and redirect)
sudo ufw enable
```
Docker publishes ports by editing iptables directly, so check the result from another machine instead of trusting `ufw status`:
only 80 and 443 should answer. The app container itself publishes nothing.

## 4. Trusting the internal certificate (option B only)

`install.sh` saves Caddy's authority certificate as `slicyrs-root.crt`. Install it as a trusted root on each device that will use the site:
- **Windows:** double-click, *Install Certificate*, *Local Machine*, place in *Trusted Root Certification Authorities*.
- **macOS:** open in Keychain Access, add to *System*, set *Always Trust*.
- **Linux (Chrome and others):** copy to `/usr/local/share/ca-certificates/` and run `sudo update-ca-certificates`. Firefox has its own store (*Settings, Privacy, View Certificates, Authorities, Import*).
- **iOS / Android:** install the profile or certificate, then enable full trust in the settings.

Keep the file's private half private: it stays inside the Caddy volume; only the public certificate is exported.
If you later get a DNS name, switch to option A and nobody needs the certificate any more.

## 5. First sign-in and printers

1. Open `PUBLIC_URL`, enter your `ADMIN_EMAILS` address, use the emailed code.
2. *Admin, Users, Send myself a test email* confirms the mail settings.
3. *Admin, Printers, Add printer*: name, model, IP, serial, access code, **build plate** (the plate physically fitted). Press **Test connection**.
4. Invite users in *Admin, Users*.

## 6. Everyday operation

```bash
docker compose ps                  # state and health
docker compose logs -f app         # live logs (rotated automatically, 5 x 10 MB each)
docker compose restart app
docker compose up -d --build       # after updating the project files
docker compose down                # stop (data is kept)
```
Containers restart by themselves after a reboot or crash as long as Docker starts at boot.
Data (database, uploads, G-code) lives in the Docker volume `slicyrs_slicyrs-data`; certificates in `slicyrs_caddy-data`.
`docker compose down -v` **deletes the data**: do not use `-v` on the real server.

**Updating:** copy the new project files over the old ones, then `docker compose up -d --build`. Check *Admin, Audit log* afterwards.
**Changing the slicer version:** see "Docker image and the slicer engine" in `README.md`; never change it casually.

## 6a. If the browser cannot connect (`ERR_SSL_PROTOCOL_ERROR` and friends)

Run `./deploy/diagnose.sh` on the server. It shows the settings (no secrets), container state, what owns ports 80 and 443, certificate
errors from Caddy and what the handshake looks like, then prints a verdict. The usual causes:

| Symptom | Cause and fix |
|---|---|
| `ERR_SSL_PROTOCOL_ERROR` when you type an **IP address** | The certificate was made for a name, or no server name is sent for an IP. Use `CADDYFILE=Caddyfile.internal`, put the IP in `SITE_ADDRESS` (and `DEFAULT_SNI`), then `docker compose up -d --force-recreate caddy` |
| `ERR_SSL_PROTOCOL_ERROR` with a **DNS name** and `Caddyfile` | Caddy could not get a public certificate (name does not point here, or ports 80/443 not reachable from the internet). Fix DNS and the firewall, or switch to the internal option |
| `ERR_SSL_PROTOCOL_ERROR` right after `https://` to a **port that is plain HTTP**, or another web server on 443 | Something other than Caddy answers: stop Apache/nginx on the host, and use `https://` only on 443 |
| `ERR_CONNECTION_REFUSED` or timeout | Caddy is not running, or the firewall blocks 443 (`sudo ufw status`) |
| Browser warns *Your connection is not private* (internal option) | Expected until the device trusts `slicyrs-root.crt` (section 4) |
| `NET::ERR_CERT_COMMON_NAME_INVALID` | You typed an address that is not in `SITE_ADDRESS`; add it, separated by a comma |

After editing `.env`, apply it with `docker compose up -d --force-recreate caddy` (Caddy reads its settings at start).

## 7. Rehearse before the real thing

You can run the identical stack on any machine with Docker, with a throwaway certificate and a mail catcher instead of Brevo:

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.rehearsal.yml --env-file deploy/rehearsal.env up -d --build
```
Open `https://localhost:8443` (the browser warns, because the certificate authority is untrusted; that is expected), request a sign-in
code for `admin@imperial.ac.uk` and read it at `http://localhost:8025`. Remove it with the same command ending `down -v`.
`deploy/rehearsal.env` contains a public secret and must never be used on a real server.

This rehearsal has been run end to end (HTTPS, headers, sign-in by emailed code, secure cookie, cross-origin protection, upload, real
slicing, restart with all data intact); see `ROADMAP.md`.

## Not covered yet

- **Backups.** There are none: if the volume is lost, so are users, jobs and printer settings. This is deliberately postponed (roadmap back burner).
  A manual stop-gap while there is no automation:
  `docker run --rm -v slicyrs_slicyrs-data:/data -v "$PWD":/backup busybox tar czf /backup/slicyrs-data-$(date +%F).tgz -C /data .`
  Copy the file off the machine, and keep a copy of `.env`.
- **Disk clean-up is automatic** (every 6 hours): job files are deleted after 30 days, failed jobs after 7, unused uploaded models after 30; history stays. Change the days or run it by hand in *Admin, Storage*, which also shows disk use and warns when it is low. Uploads are refused when under 1 GB is free.
- **Untested on real hardware:** the install script has been syntax-checked but not run on a fresh Ubuntu machine, and the printers have not yet been reached from the container.
