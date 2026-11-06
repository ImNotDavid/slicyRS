# Email sign-in codes with Brevo

Sign-in codes are sent through any SMTP service. These steps are for Brevo. Dashboard labels change from time to time, so
follow the names loosely and check the provider's own help if a screen looks different.

## 1. In Brevo

1. **Domain and sender.** Add a *sender* and authenticate its *domain* (Senders, Domains & Dedicated IPs). Use an address on a
   domain you control. Brevo shows DNS records to add (DKIM, and usually a verification record); add them at whoever hosts
   your DNS and wait for Brevo to show the domain as authenticated.
   Do not send from a free-mail address (gmail, outlook): it cannot be authenticated, so codes often land in spam.
2. **SMTP key.** SMTP & API, SMTP tab: note the **SMTP login** shown there, and generate an **SMTP key**.
   The login is not your Brevo password, and the SMTP key is not the API key (API keys start with `xkeysib-`).
3. **IP restriction.** If Brevo's security settings restrict which IP addresses may use your keys, allow the server's address
   or switch the restriction off while testing. A symptom of this is a `525` reply.
4. Check your plan's daily sending limit (small plans have one), which is plenty for sign-in codes at this scale.

## 2. In the project

Create a file named **`.env`** in the project folder (next to `run.ps1`). It is ignored by git and excluded from the Docker
build, so the key stays on your machine. Never paste the key into chat, tickets or commits; if it ever is exposed,
generate a new one in Brevo and delete the old one.

```
SMTP_HOST=smtp-relay.brevo.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USER=<the SMTP login from Brevo>
SMTP_PASSWORD=<your SMTP key>
SMTP_FROM=slicyRS <the verified sender address>
ADMIN_EMAILS=<your real email address>
```

`ADMIN_EMAILS` matters: the first admin signs in with a code sent to that address, so it must be one you can read.
(`.\run.ps1` loads `.env` automatically; Docker Compose uses it too. Values already set in the environment win.)

## 3. Check it, from the project folder

```
backend\.venv\Scripts\python backend\scripts\check_email.py
```
Checks the settings, the sender domain's SPF, DKIM and DMARC records, connects to Brevo and logs in. It sends nothing and never
prints the key. Then send one real message:

```
backend\.venv\Scripts\python backend\scripts\check_email.py --send-to you@your-address
```
If Brevo gave you a DKIM selector name, add `--dkim-selector <name>` to confirm that exact record exists.
"Accepted by the relay" is not the same as delivered: look in your inbox and spam, and in Brevo's logs.

## 4. Check it end to end

1. Start with `.\run.ps1`. It should say "Loaded settings from .env" and should **not** say codes will be printed in the window.
2. Open the site, enter your `ADMIN_EMAILS` address, and use the emailed code to sign in.
3. In **Admin, Users**, press **Send myself a test email**.
4. In the received message choose *Show original* (Gmail) or view the headers, and confirm **SPF, DKIM and DMARC say PASS**.
5. Send a code to an Imperial address (both `@imperial.ac.uk` and `@ic.ac.uk` if you use both) and check it is not in junk.

## Troubleshooting

| What you see | Likely cause |
|---|---|
| `535` authentication failed | SMTP login or key wrong; you used the account password or an API key |
| `525` or "IP" in the message | Brevo restricts the IPs that may send; allow this server's IP |
| `550`/`553` sender not valid | The sender or domain is not verified, or `SMTP_FROM` does not match it exactly |
| Timed out or cannot connect | Firewall or ISP blocks port 587; try 2525, or 465 with `SMTP_SECURITY=ssl` |
| Codes arrive but go to spam | Domain not authenticated (DKIM), no DMARC record, or a free-mail sender |
| Codes never arrive and the form says "sent" | The app hides send failures from users on purpose; see *Admin, Audit log* for `login_code_send_failed`, and use the test email button |
