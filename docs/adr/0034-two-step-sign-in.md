# ADR-0034: Two-step sign-in with an authenticator app

- **Status:** Proposed, 2026-11-11 (in the T1.11 pull request; accepted when the owner merges it, which also approves the code limit below)
- **Decision ID:** none (implements T1.11 under ID-23 and PD-20 → C-44; SAAS-A-07)
- **Tasks:** T1.11

## Context

Owners hold costs, billing and the POS connection, and a password is the only thing between an
attacker and all of it. SAAS-A-07 asks for TOTP for everyone; PD-20 (approved as recommended,
C-44) makes it optional at launch with a strong prompt for Owners. ID-23 approved a TOTP library;
the owner approved `segno` for the enrolment QR code on 2026-11-11 ("Approve segno").

## Decision

1. **Enrolling** (`/app/settings/mfa`): a random secret (`pyotp`), shown as a QR code (`segno`,
   inline SVG) and as a setup key. It is stored encrypted with the application key
   (`app/crypto.py`, Fernet, `ENCRYPTION_KEY`) and turns on only when a code from the app
   matches. Turning it on ends every other session, as a password change does.
2. **Recovery codes**: ten random codes (about 49 bits each, no look-alike characters), shown
   once, stored as SHA-256 hashes, each usable once. New ones replace the old.
3. **Signing in**: a correct password with two-step sign-in on ends whatever session the browser
   held and leaves only a five-minute "password passed" mark in the signed cookie; no session row
   exists until `/login/code` accepts an authenticator code or a recovery code. A code is good for
   its 30-second step and one step either side, and never for a step at or before the last one
   used (no replay).
4. **Limits** (security configuration; merging approves them): **5 wrong codes in 15 minutes** per
   person lock the code step for 1 minute, doubling to 1 hour (the sign-in rule's shape,
   ADR-0032). The same count covers the settings forms. A good code clears it and the account's
   sign-in count.
5. **Turning it off** needs the password and a code; a borrowed session alone cannot remove it.
   Support can turn it off for someone who lost both phone and codes
   (`python -m app.cli reset-mfa --email`), which also ends their sessions.
6. **The prompt**: Owners and Administrators without it see a banner on every page but its own
   (PD-20, USER_ROLES_AND_PERMISSIONS §7). It is not required.
7. **Invitations**: an account with two-step sign-in joins a business only while signed in; the
   invitation page never signs it in by password alone.

## Alternatives considered

- A session row marked "half signed in": every check of a session would have to know the flag; a
  short signed mark that names no session keeps the session table meaning "signed in".
- Hash recovery codes with bcrypt: ten slow checks per attempt; random 49-bit codes behind a
  5-guess limit do not need a slow hash.
- Setup key only, no QR: no new dependency, but typing 32 characters on a phone is where most
  people give up; the owner approved `segno`.
- Require it for Owners now: PD-20 decided optional with a strong prompt.

## Consequences

- `ENCRYPTION_KEY` now protects the authenticator secrets as well as Square tokens; changing it
  without re-encrypting locks enrolled people out until support resets them.
- Every sign-in path must go through the code step for an enrolled person; `login_user` is called
  directly only after a password for an account without it, or after `/login/code`.
- WebAuthn/passkeys and requiring it per role are not built.
- Built in T1.11 with migration `c5e1f0a94b2d`; dependencies `pyotp` 2.10.0 and `segno` 1.6.6.
