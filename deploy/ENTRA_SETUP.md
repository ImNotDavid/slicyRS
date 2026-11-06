# Imperial single sign-on setup (Microsoft Entra ID)

slicyRS signs people in with OpenID Connect, authorization-code flow with PKCE, as a *confidential web app*.
Imperial's Microsoft 365 sign-in is Microsoft Entra ID, so the app needs an **app registration** in Imperial's tenant.
Ask Imperial ICT for it (your department IT or the ICT service desk). Registering apps is normally restricted to administrators.
This is a checklist you can hand over.

## What to ask ICT for

1. **App registration** named e.g. "slicyRS", *single tenant* (accounts in this organizational directory only).
2. **Redirect URI**, platform *Web*: `https://<your-domain>/auth/callback`
   (for local tests also `http://localhost:8000/auth/callback`; Entra allows http only for localhost).
3. A **client secret** (give it the longest lifetime ICT allows and diarise the expiry; a new one must be set in `.env` before it lapses).
4. **Supported account types:** this tenant only. **Implicit grant / hybrid flows:** off.
5. **Token configuration, optional claims, ID token:** add `email` and `upn`.
   The app matches invites on `email`, `preferred_username` or `upn`, and Imperial people have two identities
   (`first.last@imperial.ac.uk` and a short `abc123@ic.ac.uk`), so sending both claims makes invites easy.
   Accept the prompt to add the Graph `email` permission if shown.
6. **API permissions:** only `openid`, `profile`, `email` (delegated). No admin-consent-heavy Graph permissions are needed.
7. Optional but good: set *Assignment required* on the enterprise application and assign the people or group who may use it,
   so Entra itself refuses everyone else (slicyRS's invite list is a second check).

ICT gives back: **Directory (tenant) ID**, **Application (client) ID**, **client secret value**.

## Configure slicyRS

In `.env` (see `deploy/.env.example`):

```
OIDC_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0
OIDC_TENANT_ID=<tenant-id>
OIDC_CLIENT_ID=<application-id>
OIDC_CLIENT_SECRET=<secret value>
PUBLIC_URL=https://<your-domain>
ADMIN_EMAILS=<your own imperial email>
```

Use the tenant GUID in the issuer, never `common` or `organizations`: those discovery documents contain a
placeholder issuer, so the id_token issuer cannot be validated.

Check it before trying a browser (this fetches the discovery document and, with `--probe-secret`, proves the secret works):

```bash
cd backend
python scripts/check_sso.py --probe-secret
```

## First sign-in

1. Start the app and press **Sign in with Imperial College London**.
2. You land back signed in as an admin if your Imperial address is in `ADMIN_EMAILS`.
3. In *Admin → Audit log* open the `login` entry: it records the claims Imperial actually sent
   (`email=…, preferred_username=…`). If a later person is refused with *not invited* or *no email*,
   the matching `login_rejected` entry shows which address Imperial sent, so invite exactly that one.
4. Invite colleagues in *Admin → Users*.

## Testing without Imperial

* **Automated (already in the repo):** `pytest tests/test_sso_e2e.py` runs the full redirect flow against a mock
  Entra-style provider that signs real RS256 tokens and checks PKCE, state, nonce, signature, audience, expiry and tenant.
* **Your own Entra tenant:** register the same app in a free tenant (an Azure free account gives you Entra ID; add a test user),
  put that tenant's ids in `.env` with `PUBLIC_URL=http://localhost:8000`, run `.\run.ps1`
  and sign in as the test user. This proves the live Microsoft endpoints work before Imperial approves anything.
  Set `ALLOWED_EMAIL_DOMAINS` to your test user's domain for this.
* `DEV_AUTH` (password-less sign-in) can stay on locally while testing, but it is refused on any https `PUBLIC_URL`.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `AADSTS50011` redirect URI mismatch | The registered URI must equal `PUBLIC_URL` + `/auth/callback` exactly (scheme, host, port) |
| `AADSTS7000215` invalid client secret | Wrong or expired secret; use the secret *value*, not its id |
| `AADSTS700016` application not found | Wrong client id, or the app is in a different tenant than the issuer |
| `AADSTS50105` / "user not assigned" | *Assignment required* is on and the user is not assigned |
| Back at the login page with "Sign-in did not complete" | State/nonce/signature/audience failure; check `PUBLIC_URL` matches the address in the browser and cookies are allowed |
| "Not from the Imperial tenant" | `OIDC_TENANT_ID` differs from the `tid` in the token |
| "Not invited" for a real colleague | They authenticated as an address that is not in the invite list; check the `login_rejected` audit entry |
| "No email" | Optional claims `email` / `upn` are not configured on the app registration |
