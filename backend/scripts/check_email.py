"""Check the outgoing-email settings used for sign-in codes (works for Brevo or any SMTP service).

    python scripts/check_email.py                      # config + DNS + connect and log in (sends nothing)
    python scripts/check_email.py --send-to you@x.ac.uk   # also sends one real test message

Reads SMTP_* from the environment or from the project's .env. It never prints the password.
A message accepted by the relay is not the same as one delivered: look at the result in the provider's logs and at the
received message's headers (SPF / DKIM / DMARC should say "pass").
"""
import argparse
import os
import re
import smtplib
import socket
import ssl
import sys
from email.utils import parseaddr
from pathlib import Path
from typing import Callable

FREE_MAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "icloud.com", "proton.me",
             "protonmail.com", "aol.com"}
# Brevo's DKIM selector names are not documented anywhere we could verify, so these are only probes: the authoritative
# values are the ones shown in the provider's dashboard, passed with --dkim-selector.
DKIM_PROBES = ["brevo1", "brevo2", "mail"]
Finding = tuple[str, str]


def load_dotenv(path: Path) -> None:
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


# ----------------------------------------------------------------- configuration

def check_config(env: dict[str, str]) -> list[Finding]:
    out: list[Finding] = []
    host, port, sec = env.get("SMTP_HOST", ""), env.get("SMTP_PORT", "587"), env.get("SMTP_SECURITY", "starttls").lower()
    user, pw, sender = env.get("SMTP_USER", ""), env.get("SMTP_PASSWORD", ""), env.get("SMTP_FROM", "")
    if not host:
        return [("fail", "SMTP_HOST is not set (for Brevo: smtp-relay.brevo.com)")]
    out.append(("ok", f"Server {host}:{port}, security {sec}"))
    if not port.isdigit():
        out.append(("fail", f"SMTP_PORT '{port}' is not a number"))
    elif (int(port), sec) in {(587, "ssl"), (465, "starttls"), (465, "none"), (587, "none")} and "localhost" not in host and not host.startswith("127."):
        out.append(("fail", f"Port {port} does not match SMTP_SECURITY={sec}: use 587 with starttls, or 465 with ssl"))
    if sec not in ("starttls", "ssl", "none"):
        out.append(("fail", "SMTP_SECURITY must be starttls, ssl or none"))
    if sec == "none" and not (host.startswith("127.") or host == "localhost"):
        out.append(("warn", "SMTP_SECURITY=none sends the login and messages unencrypted; use starttls"))
    if not user:
        out.append(("warn", "SMTP_USER is empty: most providers, including Brevo, require a login"))
    if not pw and user:
        out.append(("fail", "SMTP_PASSWORD is empty (for Brevo this is an SMTP key from SMTP & API, not your account password or API key)"))
    if pw and re.match(r"^xkeysib-", pw):
        out.append(("fail", "SMTP_PASSWORD looks like a Brevo API key (starts with xkeysib-). SMTP needs an SMTP key, generated on the SMTP tab"))
    name, addr = parseaddr(sender)
    if not addr or "@" not in addr:
        out.append(("fail", "SMTP_FROM must contain an address, e.g.  slicyRS <slicyrs@your-domain>"))
    else:
        domain = addr.rsplit("@", 1)[1].lower()
        out.append(("ok", f"Sender address {addr} (domain {domain})"))
        if domain in FREE_MAIL:
            out.append(("warn", f"{domain} cannot be authenticated by your provider, so messages from it often fail DMARC and land in spam. Use an address on a domain you control"))
        if not name:
            out.append(("warn", "SMTP_FROM has no display name; 'slicyRS <address>' looks more trustworthy to recipients"))
    return out


# ------------------------------------------------------------------------- DNS

def _real_resolver() -> Callable[[str, str], list[str]] | None:
    try:
        import dns.resolver
    except ImportError:
        return None

    def resolve(name: str, rtype: str) -> list[str]:
        try:
            return [r.to_text().strip('"') for r in dns.resolver.resolve(name, rtype, lifetime=6)]
        except Exception:
            return []
    return resolve


def check_dns(domain: str, resolve: Callable[[str, str], list[str]] | None, dkim_selectors: list[str] | None = None) -> list[Finding]:
    """Look for SPF, DKIM and DMARC. `resolve(name, 'TXT'|'CNAME')` returns text records (injectable for tests)."""
    if resolve is None:
        return [("warn", "dnspython is not installed, so DNS was not checked (pip install dnspython)")]
    out: list[Finding] = []
    txt = lambda name: [t.replace('" "', "") for t in resolve(name, "TXT")]
    spf = [t for t in txt(domain) if t.lower().startswith("v=spf1")]
    if len(spf) > 1:
        out.append(("fail", f"{domain} has {len(spf)} SPF records; there must be exactly one"))
    elif spf:
        out.append(("ok", f"SPF record present: {spf[0][:90]}"))
    else:
        out.append(("warn", f"No SPF record on {domain}. With a shared sending IP, DKIM carries the authentication, but an SPF record is still good practice"))
    selectors = dkim_selectors or DKIM_PROBES
    found = []
    for sel in selectors:
        name = f"{sel}._domainkey.{domain}"
        if resolve(name, "CNAME") or any("v=dkim1" in t.lower() or "p=" in t.lower() for t in txt(name)):
            found.append(sel)
    if found:
        out.append(("ok", f"DKIM record found for selector(s): {', '.join(found)}"))
    elif dkim_selectors:
        out.append(("fail", f"No DKIM record at {', '.join(s + '._domainkey.' + domain for s in dkim_selectors)}. Add the record shown in your provider's domain-authentication page"))
    else:
        out.append(("warn", "No DKIM record found at the usual selector names. Authenticate the domain in your provider's dashboard, "
                            "then re-run with --dkim-selector <name from the dashboard>"))
    dmarc = [t for t in txt(f"_dmarc.{domain}") if t.lower().startswith("v=dmarc1")]
    if not dmarc:
        out.append(("warn", f"No DMARC record at _dmarc.{domain}. Recommended: v=DMARC1; p=none; rua=mailto:you@{domain} to start"))
    else:
        policy = re.search(r"p=(\w+)", dmarc[0], re.I)
        out.append(("ok", f"DMARC present (policy {policy.group(1) if policy else '?'})"))
    return out


# ------------------------------------------------------------------------ SMTP

def diagnose_smtp_error(code: int | None, text: str) -> str:
    t = (text or "").lower()
    if code == 535 or "authentication" in t and "fail" in t:
        return "The login was rejected. Use the SMTP login shown on the provider's SMTP page and an SMTP key (not the account password or API key)"
    if code == 525 or "ip" in t and ("restrict" in t or "authoris" in t or "authoriz" in t or "not allowed" in t):
        return "The provider is restricting which IP addresses may send. Allow this server's IP, or switch off the IP restriction in the provider's security settings"
    if code in (550, 553, 554) and ("sender" in t or "from" in t or "not valid" in t or "verified" in t or "domain" in t):
        return "The sender address is not accepted. Verify the sender or authenticate the domain in the provider's dashboard, and make SMTP_FROM match it exactly"
    if code == 554 and ("spam" in t or "blocked" in t or "suspend" in t):
        return "The account or message was blocked by the provider. Check the account status and sending limits in the dashboard"
    if code in (421, 450, 451, 452) or "too many" in t or "rate" in t or "limit" in t:
        return "The provider is rate limiting or temporarily refusing mail (daily plan limit?). Try again later or check the plan's limits"
    if "starttls" in t or "tls" in t or "ssl" in t:
        return "A TLS problem. Check port and security: 587 with starttls, or 465 with ssl"
    return "Unrecognised response; the provider's logs usually explain it"


def check_smtp(env: dict[str, str], send_to: str | None = None, timeout: float = 15) -> list[Finding]:
    host, port = env["SMTP_HOST"], int(env.get("SMTP_PORT", "587"))
    sec, user, pw = env.get("SMTP_SECURITY", "starttls").lower(), env.get("SMTP_USER", ""), env.get("SMTP_PASSWORD", "")
    out: list[Finding] = []
    server = None
    try:
        ctx = ssl.create_default_context()
        server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx) if sec == "ssl" else smtplib.SMTP(host, port, timeout=timeout)
        out.append(("ok", f"Connected to {host}:{port}"))
        server.ehlo()
        if sec == "starttls":
            server.starttls(context=ctx)
            server.ehlo()
            out.append(("ok", "Encrypted with STARTTLS (certificate verified)"))
        if user:
            server.login(user, pw)
            out.append(("ok", "Logged in"))
        if send_to:
            from email.message import EmailMessage
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = env.get("SMTP_FROM") or user, send_to, "slicyRS email check"
            msg.set_content("This is a test from scripts/check_email.py. If you can read it, sign-in codes can reach you.\n"
                            "Open 'show original' and check that SPF, DKIM and DMARC all say PASS.\n")
            refused = server.send_message(msg)
            out.append(("ok" if not refused else "fail", f"Message accepted by the relay for {send_to}. Now check the inbox (and spam) and the provider's logs"
                        if not refused else f"Relay refused recipient: {refused}"))
    except smtplib.SMTPAuthenticationError as e:
        out.append(("fail", f"Login failed ({e.smtp_code}). " + diagnose_smtp_error(e.smtp_code, str(e.smtp_error))))
    except smtplib.SMTPNotSupportedError as e:
        out.append(("fail", f"The server does not offer a required feature ({e}). Check SMTP_SECURITY and port"))
    except smtplib.SMTPResponseException as e:
        out.append(("fail", f"Server replied {e.smtp_code}. " + diagnose_smtp_error(e.smtp_code, str(e.smtp_error))))
    except smtplib.SMTPException as e:
        out.append(("fail", f"SMTP error: {e}. " + diagnose_smtp_error(None, str(e))))
    except ssl.SSLError as e:
        out.append(("fail", f"TLS error: {e.reason or e}. " + diagnose_smtp_error(None, "tls")))
    except (socket.timeout, TimeoutError):
        out.append(("fail", f"Timed out connecting to {host}:{port}. A firewall or ISP may block this port: try 2525, or 465 with SMTP_SECURITY=ssl"))
    except OSError as e:
        out.append(("fail", f"Cannot connect to {host}:{port} ({e.strerror or e})"))
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                pass
    return out


def run(env: dict[str, str], send_to: str | None = None, dkim_selectors: list[str] | None = None,
        resolver: Callable[[str, str], list[str]] | None | str = "real") -> list[Finding]:
    findings = check_config(env)
    if any(level == "fail" for level, _ in findings) and not env.get("SMTP_HOST"):
        return findings
    name, addr = parseaddr(env.get("SMTP_FROM", ""))
    if addr and "@" in addr:
        domain = addr.rsplit("@", 1)[1].lower()
        findings += check_dns(domain, _real_resolver() if resolver == "real" else resolver, dkim_selectors)  # type: ignore[arg-type]
    findings += check_smtp(env, send_to)
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send-to", help="also send one real test message to this address")
    ap.add_argument("--dkim-selector", action="append", help="DKIM selector name from the provider's dashboard (repeatable)")
    ap.add_argument("--env-file", default=str(Path(__file__).resolve().parents[2] / ".env"))
    args = ap.parse_args()
    load_dotenv(Path(args.env_file))
    env = dict(os.environ)
    shown = {k: ("*" * 8 if "PASSWORD" in k and env.get(k) else env.get(k, "")) for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_SECURITY", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM")}
    print("Settings:", ", ".join(f"{k}={v or '(not set)'}" for k, v in shown.items()), "\n")
    findings = run(env, args.send_to, args.dkim_selector)
    mark = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}
    for level, msg in findings:
        print(f"[{mark[level]}] {msg}")
    bad = any(level == "fail" for level, _ in findings)
    print("\nResult:", "problems found" if bad else "no blocking problems (WARN items are advice)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
