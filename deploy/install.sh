#!/usr/bin/env bash
# Sets up and starts slicyRS on a Linux host (Ubuntu or Debian, x86-64). Safe to run again.
#   ./deploy/install.sh              first run: creates .env with a new secret key and stops so you can edit it
#   ./deploy/install.sh              second run: checks .env, builds and starts everything
#   ./deploy/install.sh --internal   first run only: use a LAN name or IP with Caddy's own certificate authority
set -euo pipefail
cd "$(dirname "$0")/.."

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1. $2"; exit 1; }; }
need docker "Install Docker Engine: https://docs.docker.com/engine/install/ (then: sudo systemctl enable --now docker)"
docker compose version >/dev/null 2>&1 || { echo "The Docker Compose v2 plugin is missing (package docker-compose-plugin)."; exit 1; }
[ "$(uname -m)" = "x86_64" ] || { echo "The slicer in this image is x86-64 only; this machine is $(uname -m)."; exit 1; }
mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
[ "$mem_kb" -ge 3500000 ] || echo "WARNING: under 4 GB of RAM ($((mem_kb / 1024)) MB). Slicing large parts may run out of memory."

if [ ! -f .env ]; then
  cp deploy/.env.example .env
  chmod 600 .env
  key=$(head -c 64 /dev/urandom | base64 | tr -d '/+=\n' | head -c 64)
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${key}|" .env
  if [ "${1:-}" = "--internal" ]; then sed -i 's|^CADDYFILE=.*|CADDYFILE=Caddyfile.internal|' .env; fi
  echo "Created .env (readable only by you) with a new SECRET_KEY."
  echo "Now edit it: SITE_ADDRESS, PUBLIC_URL, ADMIN_EMAILS and the SMTP_* settings. Then run this script again."
  exit 0
fi

val() { grep -E "^$1=" .env | head -1 | cut -d= -f2- | sed 's/^"//; s/"$//'; }
problems=0
caddy=0; case ",$(val COMPOSE_PROFILES)," in *,caddy,*) caddy=1 ;; esac   # empty: a Cloudflare Tunnel does HTTPS instead
for k in PUBLIC_URL SECRET_KEY ADMIN_EMAILS SMTP_HOST SMTP_USER SMTP_PASSWORD SMTP_FROM $([ "$caddy" = 1 ] && echo SITE_ADDRESS); do
  [ -n "$(val "$k")" ] || { echo "  .env is missing a value for $k"; problems=1; }
done
case "$(val SITE_ADDRESS) $(val ADMIN_EMAILS) $(val SMTP_USER) $(val SMTP_PASSWORD) $(val SMTP_FROM)" in
  *example*|*your-*|*you@*) echo "  .env still has placeholder values (containing 'example', 'your-' or 'you@')"; problems=1 ;;
esac
case "$(val PUBLIC_URL)" in https://*) ;; *) echo "  PUBLIC_URL must start with https://"; problems=1 ;; esac
[ "$caddy" = 0 ] || [ "$(val PUBLIC_URL)" = "https://$(val SITE_ADDRESS)" ] || echo "  note: PUBLIC_URL is not exactly https://SITE_ADDRESS. That is fine for a custom port, otherwise sign-in will fail."
[ "$(val DEV_AUTH)" != "1" ] || { echo "  DEV_AUTH=1 is not allowed on a server"; problems=1; }
first=$(echo "$(val SITE_ADDRESS)" | cut -d, -f1 | tr -d ' ' | sed 's/:.*//')
if [ "$caddy" = 1 ] && echo "$first" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' && [ "$(val CADDYFILE)" != "Caddyfile.internal" ]; then
  echo "  SITE_ADDRESS is an IP address, which needs CADDYFILE=Caddyfile.internal (an IP cannot get a public certificate)"; problems=1
fi
[ "$problems" = 0 ] || { echo "Fix the items above in .env and run this script again."; exit 1; }

docker compose config -q
docker compose up -d --build

echo -n "Waiting for the app to become healthy"
for _ in $(seq 1 60); do
  state=$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q app)" 2>/dev/null || echo starting)
  [ "$state" = "healthy" ] && break
  echo -n "."; sleep 3
done
echo
[ "$state" = "healthy" ] || { echo "The app did not become healthy. Look at: docker compose logs app"; exit 1; }

bind=$(val APP_BIND); port=$(val APP_PORT)
[ "$caddy" = 1 ] || echo "No Caddy (COMPOSE_PROFILES is empty): point the Cloudflare Tunnel at http://${bind:-127.0.0.1}:${port:-8000}"
echo "Running. Open $(val PUBLIC_URL) and sign in with an emailed code sent to: $(val ADMIN_EMAILS)"
if [ "$caddy" = 1 ] && [ "$(val CADDYFILE)" = "Caddyfile.internal" ]; then
  docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./slicyrs-root.crt 2>/dev/null \
    && echo "Internal certificate authority saved as ./slicyrs-root.crt: install it on each user's device (deploy/DEPLOY.md)."
fi
echo "Useful: docker compose logs -f app    docker compose ps    docker compose up -d --build   (after an update)"
