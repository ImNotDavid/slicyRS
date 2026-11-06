#!/usr/bin/env bash
# Finds out why the site cannot be reached over HTTPS (browser: ERR_SSL_PROTOCOL_ERROR, "connection refused", certificate warnings).
# Run on the server, in the project folder:   ./deploy/diagnose.sh
# It prints evidence and a plain verdict. It never prints secrets (only SITE_ADDRESS, CADDYFILE, PUBLIC_URL, DEFAULT_SNI).
cd "$(dirname "$0")/.."
ENV_FILE="${ENV_FILE:-.env}"   # override only for testing this script
val() { [ -f "$ENV_FILE" ] && grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- | sed 's/^"//; s/"$//'; }
say() { printf '\n== %s\n' "$*"; }
verdicts=()

say "Settings (.env)"
SITE=$(val SITE_ADDRESS); CADDYFILE=$(val CADDYFILE); PUBLIC=$(val PUBLIC_URL); SNI=$(val DEFAULT_SNI)
echo "SITE_ADDRESS=$SITE  CADDYFILE=${CADDYFILE:-Caddyfile}  PUBLIC_URL=$PUBLIC  DEFAULT_SNI=${SNI:-(same as SITE_ADDRESS)}"
FIRST=$(echo "$SITE" | cut -d, -f1 | tr -d ' ' | sed 's/:.*//')
is_ip() { echo "$1" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; }
[ -f "$ENV_FILE" ] || verdicts+=("There is no $ENV_FILE file. Run ./deploy/install.sh first.")
if is_ip "$FIRST" && [ "${CADDYFILE:-Caddyfile}" != "Caddyfile.internal" ]; then
  verdicts+=("SITE_ADDRESS is an IP address but CADDYFILE is not Caddyfile.internal. Set CADDYFILE=Caddyfile.internal in .env, then: docker compose up -d --force-recreate caddy")
fi
case "$FIRST" in
  *example*|"") verdicts+=("SITE_ADDRESS is empty or still a placeholder ($FIRST).") ;;
esac
case "$PUBLIC" in https://*) ;; *) verdicts+=("PUBLIC_URL must start with https://  (it is: ${PUBLIC:-empty})") ;; esac

say "Containers"
docker compose ps 2>&1 | head -8
state=$(docker inspect -f '{{.State.Status}}' "$(docker compose ps -q caddy 2>/dev/null)" 2>/dev/null)
[ "$state" = "running" ] || verdicts+=("The caddy container is not running (state: ${state:-missing}). Look at: docker compose logs caddy")

say "What is listening on ports 80 and 443"
(ss -ltnp 2>/dev/null || netstat -ltnp 2>/dev/null) | grep -E ':(80|443)\s' | head -5
other=$( (ss -ltnp 2>/dev/null) | grep -E ':(80|443)\s' | grep -vE 'docker-proxy|caddy' | head -1)
[ -z "$other" ] || verdicts+=("Another program is using port 80 or 443 (see the list above), so Caddy cannot be the one answering. Stop it (for example: sudo systemctl stop apache2 nginx) and run: docker compose up -d")

say "Certificate and Caddy errors in the log"
docker compose logs caddy --tail 200 2>&1 | grep -iE '"level":"(error|warn)"' | grep -iE "tls|cert|acme|obtain|challenge|bind|permission" | tail -6 | cut -c1-230
if docker compose logs caddy --tail 200 2>&1 | grep -qiE "could not get certificate|obtain.*error|acme.*(error|fail)|challenge failed"; then
  verdicts+=("Caddy could not get a public certificate (Let's Encrypt). With a DNS name this needs the name to point at this machine and ports 80 and 443 reachable from the internet. On a private network use the internal option: CADDYFILE=Caddyfile.internal, SITE_ADDRESS=<name or ip>, then docker compose up -d --force-recreate caddy")
fi
docker compose logs caddy --tail 200 2>&1 | grep -qiE "address already in use|bind: " && verdicts+=("Caddy could not open its port: something else is using it, or the firewall/SELinux blocks it.")

say "What the TLS handshake looks like from this machine"
for target in "127.0.0.1" "$FIRST"; do
  [ -n "$target" ] || continue
  printf '%s: ' "https://$target"
  out=$(curl -sk -m 8 -o /dev/null -w '%{http_code}' "https://$target/" 2>&1); rc=$?
  echo "HTTP $out (curl exit $rc)"
  if [ "$rc" = "35" ]; then
    detail=$(curl -sk -m 8 -v "https://$target/" 2>&1 | grep -iE "alert|wrong version|error:" | head -1)
    echo "   $detail"
    case "$detail" in
      *"internal error"*) verdicts+=("The TLS handshake to $target fails with 'internal error': Caddy has no certificate for that address. Typical causes: you are browsing by an IP address but SITE_ADDRESS is a name (put the IP in SITE_ADDRESS too: SITE_ADDRESS=\"name, ip\"), or a public certificate could not be obtained (see above).") ;;
      *"wrong version"*) verdicts+=("Something answered with plain HTTP where HTTPS was expected: you are using https:// on a plain-HTTP port, or another web server (not Caddy) holds the port.") ;;
    esac
  fi
done

say "Is the port reachable from outside this machine?"
echo "From another computer run:  curl -vk https://$FIRST/   (or open it in a browser)."
echo "On this server check the firewall:  sudo ufw status   (443/tcp must be allowed)"

say "Verdict"
if [ ${#verdicts[@]} -eq 0 ]; then
  echo "No obvious configuration problem found. If a browser still fails, copy the output above (it contains no secrets) when asking for help."
else
  for v in "${verdicts[@]}"; do echo " * $v"; done
fi
