---
title: GitHub authentication
description: Restrict a deployment to immutable GitHub user IDs or one organization.
group: Operations
order: 22
---

GitHub OAuth is optional and disabled by default for loopback-only development.

When enabled, every user must match an immutable GitHub user ID allowlist or active membership in one configured organization. There is no unrestricted GitHub-account mode because users operate the deployment's GitHub and Copilot identities.

## Create an OAuth App

Create a separate GitHub OAuth App for each deployment:

- **Homepage URL:** the public HTTPS origin, such as `https://mafia.example.com`
- **Authorization callback URL:** the same origin followed by `/auth/callback`

The callback URL must exactly match `MAFIA_GITHUB_OAUTH_CALLBACK_URL`.

## Configure credentials

Generate independent secrets:

```bash
openssl rand -hex 32
openssl rand -hex 32
```

```dotenv
MAFIA_AUTH_MODE=github
MAFIA_GITHUB_OAUTH_CLIENT_ID=...
MAFIA_GITHUB_OAUTH_CLIENT_SECRET=...
MAFIA_GITHUB_OAUTH_CALLBACK_URL=https://mafia.example.com/auth/callback
MAFIA_GITHUB_SESSION_SECRET=<first-generated-secret>
MAFIA_INTERNAL_SECRET=<second-generated-secret>
```

Then configure at least one authorization policy:

```dotenv
MAFIA_GITHUB_ALLOWED_USER_IDS=[37492]
MAFIA_GITHUB_ALLOWED_ORG=frostyard
```

Find your immutable numeric user ID with:

```bash
gh api user --jq .id
```

When both policies are present, a user is allowed if their ID is listed **or** their organization membership is active. Organization mode requests `read:org`; user-ID-only mode requests no GitHub OAuth scopes.

## Session boundary

OAuth uses authorization code flow with PKCE and a signed, short-lived state cookie. After fetching `/user` and any required organization membership, mafia discards the access token.

The browser receives only a signed, HttpOnly, Secure, SameSite=Lax session cookie containing the immutable user ID, login, avatar URL, and expiration.

Next.js protects pages, REST proxies, readiness, and CopilotKit. Keep FastAPI bound to loopback. `MAFIA_INTERNAL_SECRET` authenticates only server-to-server traffic from Next.js to FastAPI.

See the repository's [`docs/authentication.md`](https://github.com/frostyard/mafia/blob/main/docs/authentication.md) for the complete Caddy `forward_auth` example.
