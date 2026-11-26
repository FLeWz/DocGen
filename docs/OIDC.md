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
4. Names of the **claims**: email (`email`), name (`name`), and the claim holding the roles (Keycloak: `resource_access.<client>.roles`).
5. Does the IdP use a **private CA**? Get the PEM of the CA chain.
6. Public vs confidential client, and that the `openid profile email` scopes are allowed.
7. For Keycloak: the **client roles** `owner`/`admin`/`editor`/`viewer` on the DocGen client, assigned to groups (see Keycloak setup).

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
| `OIDC_ROLES_CLIENT_ID` | `OIDC_CLIENT_ID` | client whose roles are read at `resource_access.<id>.roles` (exact key, dots allowed) |
| `OIDC_REALM_ROLES` | `0` | also read `realm_access.roles` |
| `OIDC_ROLE_PREFIX` | empty | e.g. `docgen-`: the role `docgen-editor` counts as `editor` (unprefixed names keep working) |
| `OIDC_GROUPS_CLAIM` | empty | extra mapping: claim holding groups (list or string; dotted path ok) |
| `OIDC_ROLE_MAP` | empty | for `OIDC_GROUPS_CLAIM`: `/docgen/admin=admin,docgen-editors=editor` or JSON; roles owner/admin/editor/viewer |
| `OIDC_ALLOWED_EMAIL_DOMAINS` | empty (all) | comma list; others are refused |
| `OIDC_VERIFY_TLS` | `true` | `false` disables certificate checks (tests only) |
| `OIDC_CA_BUNDLE` | empty | PEM file for a private CA, e.g. `/certs/company-ca.pem` (put it in `./certs/`, mounted at `/certs`) |
| `OIDC_END_SESSION` | `true` | log out at the IdP too when it advertises `end_session_endpoint` |
| `OIDC_AUTO_PROVISION` | removed | ignored (warning at startup if set to 0): accounts are always created |
| `OIDC_DEFAULT_ORG` | `DocGen` | org that SSO users join |
| `OIDC_DEFAULT_ROLE` | empty | optional fallback role for users with no role in the token; empty = such logins are refused |
| `OIDC_LINK_BY_EMAIL` | `0` | link an SSO login to an existing user with the same *verified* email |

## Provisioning policy

* A person is identified by `(issuer, sub)` (table `identities`), not by email.
* **A missing account is always created at the first successful login**: user, identity and membership in the org named
  `OIDC_DEFAULT_ORG` (created on first use; **the very first user becomes its owner**). There is no invite-only mode and no
  `OIDC_AUTO_PROVISION` switch any more: access is controlled by the roles in the token.
* **Role** = highest of: client roles `resource_access.<OIDC_ROLES_CLIENT_ID>.roles` (names owner/admin/editor/viewer, case-insensitive,
  optional `OIDC_ROLE_PREFIX`), realm roles if `OIDC_REALM_ROLES=1`, groups mapped by `OIDC_GROUPS_CLAIM` + `OIDC_ROLE_MAP`. Claims are
  merged from the verified id_token, the access token (JWT, signature/issuer/expiry verified with the same JWKS, `azp` must be the
  client; `aud` is not checked because Keycloak sets it to the resource servers; an invalid or opaque one is ignored and logged) and `userinfo`.
* **No recognised role = login refused** (`Signed in as <email>, but no DocGen role is assigned ...`); nothing is created and an existing
  member stays untouched. `OIDC_DEFAULT_ROLE` (non-empty) is used instead of refusing.
* The role is **refreshed at every login**, up and down. A demotion of the **last owner of the org is skipped** (logged as a warning), so the org is never orphaned.
  Removing someone from the Keycloak group refuses their next login; the existing DocGen session lasts until it expires (14 days) or they log out.
* **Invites still work**: pending invites for the (IdP-asserted) email are accepted at sign-in and the invited org opens, but they are not required.
  A user must still have a role in the token.
* An existing password user with the same email is **not** linked unless `OIDC_LINK_BY_EMAIL=1` and the IdP does not say `email_verified: false`.
  Bootstrap alternative: `python -m app.manage create-user --email .. --name .. --org ..` plus `OIDC_LINK_BY_EMAIL=1`.
* Logs (INFO): where roles were found and the chosen role (never tokens). WARNING when `resource_access` is in none of the tokens.

## Security notes

Authorization Code + PKCE (S256), `state` and `nonce` in an HMAC-signed HTTP-only cookie (10 min, `SameSite=Lax`, path `/api/auth/oidc`);
id_token signature via the JWKS (cached; one refetch per 10 s on an unknown `kid` = key rotation; RS/PS/ES/EdDSA only, never HS/none),
`iss`, `aud`, `exp` (60 s skew), `nonce`, `azp`; `return_to` must be a relative path (`//`, schemes and backslashes fall back to `/`);
session cookie as in local mode (HTTP-only, `Secure` when `PUBLIC_URL` is https). Only the browser carries the state, nothing is stored server-side.
Shortcut: Entra's multi-tenant `common` issuer (templated `iss`) is not supported; use the tenant id.

## Examples

* **Keycloak** (client roles, step by step; `<realm>` = your realm):
  1. *Clients > Create client*: type OpenID Connect, Client ID `docgen`. *Client authentication* on (confidential, copy the secret from *Credentials*) or off (public, PKCE is used anyway); *Standard flow* on.
  2. Valid redirect URIs: `<PUBLIC_URL>/api/auth/oidc/callback`; Valid post logout redirect URIs: `<PUBLIC_URL>/login`.
  3. *Clients > docgen > Roles*: create the **client roles** `owner`, `admin`, `editor`, `viewer` (same names as DocGen roles).
  4. *Groups*: create e.g. `docgen-admins`, `docgen-editors`; *Role mapping > Assign role > Filter by clients* and assign the docgen client roles to each group (or make a composite role). Add users to the groups.
  5. Make sure the roles reach the token: *Client scopes > roles > Mappers > client roles*: **Add to access token** and **Add to userinfo** on (keep *Add to ID token* optional). The `roles` scope must be assigned to the client (default). Either keep *Clients > docgen > Client scopes > docgen-dedicated > Scope > Full scope allowed* on, or turn it off and add the docgen client roles under *Scope* (scope mappings).
  6. Verify: *Clients > docgen > Client scopes > Evaluate*, pick a user, *Generated access token* must show `resource_access.docgen.roles: ["editor", ...]`.
  7. `.env`: `OIDC_ISSUER=https://kc.example.com/realms/<realm>` (older Keycloak: `/auth/realms/<realm>`), `OIDC_CLIENT_ID=docgen`, `OIDC_CLIENT_SECRET=...`. Another client id holding the roles: `OIDC_ROLES_CLIENT_ID`. Realm roles instead: `OIDC_REALM_ROLES=1`. Groups instead: *Group Membership* mapper (claim `groups`, full path on) + `OIDC_GROUPS_CLAIM=groups OIDC_ROLE_MAP=/docgen/admin=admin,...`.
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
| `Signed in as ..., but no DocGen role is assigned` | the token carries none of owner/admin/editor/viewer for the client: user is in no group with those client roles, or the roles are not in the access token (log: `no resource_access ...`): check the client roles mapper (access token + userinfo), Full scope allowed, `OIDC_ROLES_CLIENT_ID`, `OIDC_ROLE_PREFIX`; use Evaluate to look at the token |
| log `access token ignored for role lookup` | token failed signature/issuer/expiry/`azp` check (or is opaque, not a JWT); roles then come from id_token/userinfo only |
| Admin cannot be demoted / log `only owner` | last owner is protected; make another owner first |
| warning `OIDC_AUTO_PROVISION is ignored` | variable still set to 0 in `.env`; remove it |
| `an account ... already exists but is not linked` | set `OIDC_LINK_BY_EMAIL=1` (IdP must assert verified emails) |
| `the identity provider rejected the login (invalid_client)` | wrong secret, or a confidential client configured as public (or vice versa) |

Logs: `docker compose logs api | grep OIDC` (never contains secrets or tokens).
