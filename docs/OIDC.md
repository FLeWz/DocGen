# OpenID Connect sign-in (`AUTH_MODE=oidc`)

With `AUTH_MODE=oidc` DocGen drops its own password handling: `POST /api/auth/login`, `POST /api/auth/signup` and password-based
invite acceptance answer **404** ("password login is disabled ..."), `reset-password` refuses, `create-user` makes a password-less
user, and the login screen shows only **Sign in with `<OIDC_PROVIDER_NAME>`**. Default is `AUTH_MODE=local` (unchanged).

Status: tested against the mock IdP in `backend/tests/mock_idp.py` (pytest + Playwright, `make test`). **Not tested against a real IdP**.

## What you need from the IdP administrator

1. **Issuer URL** (the one that serves `<issuer>/.well-known/openid-configuration`).
2. **Client ID**, and the **client secret** if it is a confidential client (empty = public client, PKCE only).
3. Register this **redirect URI** (exact match): `<PUBLIC_URL>/api/auth/oidc/callback`, e.g. `https://docgen.example.com/api/auth/oidc/callback`.
   Also the post-logout redirect `<PUBLIC_URL>/login` if the IdP checks it.
4. Names of the **claims**: email (`email`), name (`name`), and, for role mapping, the **groups claim** and the group names.
5. Does the IdP use a **private CA**? Get the PEM of the CA chain.
6. Public vs confidential client, and that the `openid profile email` scopes (+ groups scope if needed) are allowed.

## Setup

```sh
# .env
PUBLIC_URL=https://docgen.example.com        # https => session cookies get Secure
AUTH_MODE=oidc
OIDC_ISSUER=https://idp.example.com/realms/corp
OIDC_CLIENT_ID=docgen
OIDC_CLIENT_SECRET=...                        # never commit
OIDC_PROVIDER_NAME=Company SSO
SEED_DEMO=0                                   # demo users have passwords
```
`docker compose up -d`. The api logs `OIDC discovery ok` (or a clear error, retried in the background) at start; `GET /api/auth/config`
returns `{mode, provider_name, allow_signup}` for the login screen.

## Variables

| Variable | Default | Meaning |
|---|---|---|
| `AUTH_MODE` | `local` | `local` or `oidc` |
| `OIDC_ISSUER` | - (required) | issuer; must equal the `issuer` in the discovery document (trailing `/` ignored) |
| `OIDC_CLIENT_ID` | - (required) | |
| `OIDC_CLIENT_SECRET` | empty | empty = public client (PKCE only). Sent as `client_secret_basic`, or `_post` if only that is advertised |
| `OIDC_REDIRECT_URI` | `<PUBLIC_URL>/api/auth/oidc/callback` | change only if a proxy alters the external URL |
| `OIDC_SCOPES` | `openid profile email` | add the groups scope if your IdP needs one |
| `OIDC_PROVIDER_NAME` | `SSO` | button label |
| `OIDC_EMAIL_CLAIM` / `OIDC_NAME_CLAIM` | `email` / `name` | looked up in the id_token, then `userinfo` |
| `OIDC_USERNAME_CLAIM` | `preferred_username` | used as the email when there is no email claim **and** the value contains `@` (Entra UPN) |
| `OIDC_GROUPS_CLAIM` | empty | claim holding the groups (list or string; dotted path ok, e.g. `realm_access.roles`) |
| `OIDC_ROLE_MAP` | empty | `docgen-admins=admin,docgen-editors=editor` or JSON `{"docgen-admins":"admin"}`; roles owner/admin/editor/viewer |
| `OIDC_ALLOWED_EMAIL_DOMAINS` | empty (all) | comma list; others are refused |
| `OIDC_VERIFY_TLS` | `true` | `false` disables certificate checks (tests only) |
| `OIDC_CA_BUNDLE` | empty | PEM file for a private CA, e.g. `/certs/company-ca.pem` (put it in `./certs/`, mounted at `/certs`) |
| `OIDC_END_SESSION` | `true` | log out at the IdP too when it advertises `end_session_endpoint` |
| `OIDC_AUTO_PROVISION` | `1` | see policy |
| `OIDC_DEFAULT_ORG` / `OIDC_DEFAULT_ROLE` | `DocGen` / `viewer` | org that SSO users join, role when no group maps |
| `OIDC_LINK_BY_EMAIL` | `0` | link an SSO login to an existing user with the same *verified* email |

## Provisioning policy

* A person is identified by `(issuer, sub)` (table `identities`), not by email.
* **`OIDC_AUTO_PROVISION=1` (default)**: first sign-in creates the user and adds them to the org named `OIDC_DEFAULT_ORG`
  (created on first use; **the very first user becomes its owner**). Role = highest group in `OIDC_ROLE_MAP`, else `OIDC_DEFAULT_ROLE`.
  With a groups claim configured the role is **refreshed at every login**; owners are never demoted by groups. Anyone who can sign in at
  the IdP gets in, so restrict with `OIDC_ALLOWED_EMAIL_DOMAINS` or IdP-side assignment.
* **`OIDC_AUTO_PROVISION=0`**: only e-mail addresses with a pending invite (or an already known identity) may sign in; others see
  "DocGen is invite-only ...". Bootstrap the first admin with `python -m app.manage create-user --email .. --name .. --org ..`
  (no password) plus `OIDC_LINK_BY_EMAIL=1`: that user is linked at the first SSO login.
* **Invites keep working**: an admin invites an address as before; when that person signs in, pending invites for their (IdP-asserted)
  email are accepted automatically and the invited org opens. Org switching works as before.
* An existing password user with the same email is **not** linked unless `OIDC_LINK_BY_EMAIL=1` and the IdP says `email_verified` is not false.

## Security notes

Authorization Code + PKCE (S256), `state` and `nonce` in an HMAC-signed HTTP-only cookie (10 min, `SameSite=Lax`, path `/api/auth/oidc`);
id_token signature via the JWKS (cached; one refetch per 10 s on an unknown `kid` = key rotation; RS/PS/ES/EdDSA only, never HS/none),
`iss`, `aud`, `exp` (60 s skew), `nonce`, `azp`; `return_to` must be a relative path (`//`, schemes and backslashes fall back to `/`);
session cookie as in local mode (HTTP-only, `Secure` when `PUBLIC_URL` is https). Only the browser carries the state, nothing is stored server-side.
Shortcut: Entra's multi-tenant `common` issuer (templated `iss`) is not supported; use the tenant id.

## Examples

* **Keycloak**: client type OpenID Connect, *Client authentication* on (secret) or off (public), standard flow, valid redirect URI as above, post logout redirect `<PUBLIC_URL>/login`.
  `OIDC_ISSUER=https://kc.example.com/realms/<realm>`; groups: add a *Group Membership* mapper (claim `groups`, full path off) then `OIDC_GROUPS_CLAIM=groups`, or realm roles `OIDC_GROUPS_CLAIM=realm_access.roles`.
* **Microsoft Entra ID**: App registration, platform *Web*, redirect URI as above, certificates & secrets > client secret.
  `OIDC_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0`. Add optional claims `email` (and `groups`: Token configuration > groups claim; values are object IDs,
  so `OIDC_ROLE_MAP=<group-object-id>=admin`; users in more than ~200 groups get an overage link instead of the list, which DocGen does not follow). If `email` is missing DocGen falls back to `preferred_username` (UPN).
* **Okta**: Web app, Authorization Code, `OIDC_ISSUER=https://<org>.okta.com` (or `/oauth2/default`); add a groups claim to the ID token (filter e.g. `docgen-.*`), `OIDC_GROUPS_CLAIM=groups`.
* **Authentik**: provider OAuth2/OpenID, issuer `https://auth.example.com/application/o/<slug>/`; scopes `openid profile email`, groups arrive in `groups`.
* **AD FS** (2016+): application group with a *Server application*, `OIDC_ISSUER=https://adfs.example.com/adfs`, issue claims via a transform rule (email, `groups` as a claim); usually a private CA: `OIDC_CA_BUNDLE`.

## Troubleshooting

| Message / symptom | Cause |
|---|---|
| `cannot reach the identity provider ... (TLS: set OIDC_CA_BUNDLE ...)` | private CA not trusted: mount the PEM chain under `./certs/`, `OIDC_CA_BUNDLE=/certs/ca.pem`; or the api container cannot resolve/reach the IdP host (the browser and the api container both need it) |
| `issuer mismatch` | `OIDC_ISSUER` differs from the discovery `issuer` (check `/v2.0`, trailing slash, http vs https) |
| IdP shows "redirect_uri mismatch" | register exactly `<PUBLIC_URL>/api/auth/oidc/callback`; `PUBLIC_URL` must be the URL users type (behind a proxy too) |
| `token was rejected: The token is not yet valid (iat/exp)` / `expired` | clock skew > 60 s between server and IdP: fix NTP on the docker host |
| `Audience doesn't match` | `OIDC_CLIENT_ID` is not the client the token was issued for (Entra: Application ID) |
| `did not send an email address` | release the `email` claim/scope at the IdP, set `OIDC_EMAIL_CLAIM`, or rely on a UPN in `OIDC_USERNAME_CLAIM` |
| `your login attempt expired or the browser blocked the cookie` | took > 10 min, or cookies blocked, or the callback hit another host than the login started on |
| `DocGen is invite-only` | `OIDC_AUTO_PROVISION=0` and no pending invite for that email |
| `an account ... already exists but is not linked` | set `OIDC_LINK_BY_EMAIL=1` (IdP must assert verified emails) |
| `the identity provider rejected the login (invalid_client)` | wrong secret, or a confidential client configured as public (or vice versa) |

Logs: `docker compose logs api | grep OIDC` (never contains secrets or tokens).
