# 05 — Security and reliability assessment

Severity is for a launch with paying customers, not for the current single-founder state.

## Findings

| ID | Sev | Finding | Evidence |
|---|---|---|---|
| S1 | High | **No login throttling or lockout.** 30 wrong passwords in a row, all answered `401`, none delayed | probe R8 [RUN]; `app/web/auth.py:89` |
| S2 | High | **No password reset, no email verification, no password change.** A customer who forgets a password needs the founder; anyone can sign up as any address | `app/web/auth.py` (signup, login, logout only) |
| S3 | High | **Stored XSS via invoice upload.** The image endpoint replays the uploader's `Content-Type`; a `.jpg` sent as `text/html` is served as HTML from the app origin, no `nosniff` | probe R7 [RUN]; `app/web/receiving.py:154-164` |
| S4 | Medium | **No CSRF tokens.** Only `SameSite=Lax` on the session cookie (`app/main.py:60`) protects state-changing POSTs. Adequate in current browsers, insufficient once staff accounts and older devices appear. `GET /logout` changes state | probe R8; `app/main.py:60` |
| S5 | Medium | **Sessions cannot be revoked.** Signed client-side cookie (Starlette default 14 days), no server-side session id, not rotated at login; logout clears only the browser's copy; deactivating a user is the only kill switch | `app/security.py:30` |
| S6 | Medium | **Over-scoped Square OAuth.** Every merchant is asked for `ORDERS_WRITE`, `PAYMENTS_READ`, `PAYMENTS_WRITE` — needed only by the private snack-box module. Expect pushback in Square app review and from cautious owners | `app/providers/square/oauth.py:12-22` |
| S7 | Medium | **Merchant → tenant routing is ambiguous.** One Square merchant can be connected to several tenants; webhooks go to whichever row `.first()` returns | `app/models.py:144`, `app/web/webhooks.py:38-46` |
| S8 | Medium | **Tenant isolation is by convention only.** All 53 product routes were read: every object fetched by id is checked against the session tenant. Nothing enforces it for future routes; 6 cross-tenant tests cover documents, dating, scanning, codes, markdowns, reorder list; counts, receiving apply, item create and settings have none; no Postgres RLS | `tests/test_web.py:70,177`, `tests/test_scanning.py:72,260`, `tests/test_promotions_flow.py:207`, `tests/test_health_flow.py:264` |
| S9 | Medium | **AI cost abuse.** Unverified signup → 30-day trial with full write access → unlimited invoice and date-photo reads against the Anthropic key. `/app/scan/expiry` is reachable even read-only (`require_tenant`) | `app/web/scanning.py:309` |
| S10 | Medium | **No security headers**: no CSP, HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy | `grep` → none |
| S11 | Low | Entire `Settings` object (incl. Stripe, Square, S3 secrets) is a Jinja global; harmless unless a template ever renders user-controlled template source | `app/web/templating.py:52` |
| S12 | Low | Upload validation is by filename suffix only; content is not sniffed (Pillow is available) | `app/orchestration/receiving.py:119` |
| S13 | Info | `.env` holds real-format keys (Stripe, Square sandbox, S3, SMTP) and `.env.bak` exists; both git-ignored. Fine locally; rotate anything that was ever pasted elsewhere | `.gitignore` |

## What is done well [CODE, RUN]

- bcrypt password hashing; 10-character minimum.
- Square webhook HMAC over URL+body with constant-time compare; endpoint refuses all events
  until a key is configured (`app/providers/square/webhooks.py`). Tested.
- Stripe webhooks verified with `construct_event`; out-of-order events dropped by `stripe_event_at`.
- Square tokens Fernet-encrypted at rest; OAuth `state` checked.
- Prices chosen server-side; a browser cannot name its own Stripe price.
- No user input reaches raw SQL; the only `text()` calls are static advisory-lock statements.
- Jinja autoescaping on; one `|safe` on a constant.
- Sentry `before_send` scrubs cookies, auth headers, bodies and query strings; tested.
- Startup refuses SQLite, placeholder secrets, or plain-HTTP production.
- Secure cookies whenever `APP_BASE_URL` is https.

## Reliability and data integrity

| ID | Finding | Evidence |
|---|---|---|
| C4 | Square write failures on receipt or count are logged and dropped; the next sync overwrites local stock with Square's figure, erasing the delivery or count | probe R3 [RUN] |
| C5 | Invoice double-apply race | probe R4 [RUN] |
| C8 | Blocking calls in `async def` routes stall the worker (Anthropic default timeout 600 s, Square retries sleep up to 7 s) | [CODE] |
| O2 | No backups or restore procedure exist for any environment | `compose.yaml` |
| O3 | No uptime monitoring; Sentry optional and unset | — |
| O4 | Plain-text logs, no request ids | `app/main.py:37` |
| O5 | Unpinned dependency ranges; image builds are not reproducible | `pyproject.toml`, `Dockerfile` |
| — | **Dependency vulnerability scan not performed** — requires installing `pip-audit` from PyPI; recommended as a CI step | — |

## Required before first paying customer

S1, S2, S3, S5 (rotate on login + server-side revocation can follow), S6, S7, S9 (verify email
before AI features, per-tenant daily cap), S10, C4, C5, C8, O1, O2.
