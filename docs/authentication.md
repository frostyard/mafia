# GitHub web authentication

GitHub OAuth authentication is optional and disabled by default for loopback-only
development. When enabled, MAFIA requires every user to match an immutable GitHub
user ID allowlist or active membership in one configured organization. There is
no unrestricted "any GitHub account" mode because users operate the deployment's
GitHub and Copilot identities.

## Create an OAuth App

Create a separate GitHub OAuth App for each deployment. Set:

- **Homepage URL:** the public HTTPS origin, such as `https://mafia.example.com`
- **Authorization callback URL:** the same origin followed by
  `/auth/callback`, such as `https://mafia.example.com/auth/callback`

The callback URL must exactly match `MAFIA_GITHUB_OAUTH_CALLBACK_URL`.

## Configure authorization

Generate independent secrets:

```bash
openssl rand -hex 32
openssl rand -hex 32
```

Configure the API and web process with the same environment:

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
# Immutable numeric IDs; find your own with: gh api user --jq .id
MAFIA_GITHUB_ALLOWED_USER_IDS=[37492]

# Optional alternative or additional policy:
MAFIA_GITHUB_ALLOWED_ORG=frostyard
```

When both are present, a user is allowed if their immutable ID is listed **or**
their organization membership is active. Organization mode requests
`read:org`; user-ID-only mode requests no GitHub OAuth scopes. MAFIA refuses to
start GitHub auth mode without either policy.

OAuth uses authorization-code flow with PKCE and a signed, short-lived state
cookie. After fetching `/user` and, when needed, organization membership,
MAFIA discards the GitHub access token. The browser receives only a signed,
HttpOnly, Secure, SameSite=Lax session cookie containing the immutable user ID,
login, avatar URL, and expiration.

## Caddy

Keep both application listeners bound to loopback and expose only Caddy.
`contrib/Caddyfile` terminates TLS, performs `forward_auth`, sends authenticated
REST and readiness traffic to FastAPI, and sends pages to Next.js:

```caddyfile
{$MAFIA_DOMAIN:mafia.example.com} {
	@auth path /auth/*
	@api path /api/* /readyz

	handle @auth {
		reverse_proxy 127.0.0.1:3000
	}

	handle {
		forward_auth 127.0.0.1:3000 {
			uri /auth/forward
			copy_headers X-Mafia-GitHub-User-ID X-Mafia-GitHub-Login
		}

		route {
			reverse_proxy @api 127.0.0.1:8000
			reverse_proxy 127.0.0.1:3000
		}
	}
}
```

Set `MAFIA_DOMAIN` in Caddy's service environment, validate the configuration,
and reload:

```bash
sudo MAFIA_DOMAIN=mafia.example.com caddy validate \
  --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy forwards the signed session cookie to FastAPI, which validates it again.
`MAFIA_INTERNAL_SECRET` authenticates only server-to-server traffic from
Next.js to FastAPI; it must not be exposed to the browser, added to Caddy, or
committed to source control.
