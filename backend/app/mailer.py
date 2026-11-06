"""Outgoing email for login codes: SMTP in production, console/outbox for local testing."""
import logging
import smtplib
import ssl
import threading
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from . import config

log = logging.getLogger("mailer")
outbox: list[dict[str, str]] = []  # filled in console mode so tests (and `EMAIL_CONSOLE=1` users) can read codes


def build_message(to: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = config.SMTP_FROM or f"{config.APP_NAME} <noreply@localhost>"
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=(config.SMTP_FROM.rsplit("@", 1)[-1].strip(" >") or "localhost"))
    msg.set_content(body)
    return msg


def send(to: str, subject: str, body: str) -> None:
    """Deliver one message. Raises on failure (callers decide how much of that to reveal)."""
    if config.EMAIL_CONSOLE and not config.SMTP_HOST:
        outbox.append({"to": to, "subject": subject, "body": body})
        del outbox[:-50]
        log.warning("EMAIL (console mode) to %s: %s\n%s", to, subject, body)
        print(f"\n[email to {to}] {subject}\n{body}\n", flush=True)
        return
    msg = build_message(to, subject, body)
    ctx = ssl.create_default_context()
    if config.SMTP_SECURITY == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=15, context=ctx)
    else:
        server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15)
    try:
        server.ehlo()
        if config.SMTP_SECURITY == "starttls":
            server.starttls(context=ctx)
            server.ehlo()
        if config.SMTP_USER:
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass


def send_in_background(to: str, subject: str, body: str, on_error=None) -> None:
    """Send without making the HTTP response timing depend on whether the address exists."""
    def run():
        try:
            send(to, subject, body)
        except Exception as e:  # logged and audited by the caller; the user always sees the same response
            log.exception("could not send email")
            if on_error:
                on_error(e)
    threading.Thread(target=run, daemon=True).start()
