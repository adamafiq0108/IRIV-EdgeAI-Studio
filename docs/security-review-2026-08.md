# Security & robustness review - August 2026

Full read of `core/`, `systems/demo/app/`, `agent/` and `frontend/src/`, plus
live probing of the deployment on 192.168.1.63. Findings are split into what
was FIXED in this pass and what is KNOWN AND OPEN, with the reasoning for
leaving each open. Everything here was verified against the code, and the
critical findings were reproduced against the running box before being fixed.

Branch: `cytron_iriv`. Commits `bd8914a` and `e167581`.

---

## What was strong before the review

Worth stating plainly, because it shaped how much of the rest could be trusted:

- **The expression evaluator** (`systems/demo/app/functions.py`) is a
  default-deny AST whitelist. No `Attribute`, no `Subscript`, no string
  constants, calls restricted to ten numeric functions with no keywords.
  `pow`/`sqrt` are pre-wrapped against CPU-burn. The core-side validator
  duplicates it deliberately, and both fail closed. No escape was found.
- **Path traversal**: every file-serving route (snapshots, clips, dataset,
  model files, system images, store staging) resolves through a
  `os.path.basename` / allow-list check. Ten routes examined, all safe.
- **No dangerous primitives**: no `eval`/`exec` on input, no `pickle`, no
  `yaml.load`, no `shell=True`, no `os.system` anywhere in the tree. Every
  `subprocess` call is argv-list form.
- **Template store trust chain**: ed25519 signature verified BEFORE anything
  is written or executed, every file sha256-checked against the signed
  manifest, install refuses outright when `cryptography` is missing rather
  than degrading, and the system is created last so a failed install leaves
  nothing behind.
- **The GitHub PAT** is the best-handled secret in the product: AES-GCM
  sealed inside the license key, keyed by a KDF over the device serial and
  AAD-bound to it, so a license lifted off one box yields nothing on another.
- **Atomic writes** (tmp + `os.replace`) are the house convention for config
  and layout files, and daemon threads are used consistently.

---

## Fixed in this pass

### 1. The session wall could be walked around entirely - CRITICAL

`core/routes/auth.py` only returned 401 for paths starting with `/api/`.
Everything else was handed to the router so the SPA shell could render - but
the master mounts **real endpoints outside `/api/`**: the device video relay
and the footage list / download / export / **delete** proxy
(`core/routes/proxy.py`).

Reproduced on the live box before the fix:

```
GET /api/dashboard        -> 401   (the wall)
GET /footage/fake-serial  -> 404   (the HANDLER answered - no session at all)
GET /video_feed/fake-serial -> 404 (same)
```

The 404 came from `_slave_or_404` *inside* the handler, i.e. the request had
already executed. With a real paired device on that master, anyone routable
to the board could watch its cameras and delete its recordings, unauthenticated.

**Fixed** by naming those prefixes to the wall (`PROTECTED_PREFIXES`) *and*
re-checking the session inside every proxy handler - two independent checks,
because this router is the one that pays for a mistake in the other. Export
and delete additionally require admin. Verified after the fix: all three
paths now return 401.

The same matching bug was latent elsewhere: the open-prefix test ended in a
bare `startswith(p)`, so a future route merely *beginning* with an open
prefix (`/api/publicity`, `/loginsessions`) would have been silently open.
Matching is now boundary-anchored.

### 2. No brute-force protection on login - CRITICAL

`POST /api/auth/login` had no throttle, lockout or backoff of any kind, on an
8-character-minimum password. New `core/services/ratelimit.py`: a soft window
(8 attempts / 60s) plus a lockout after repeated failures, keyed by
**IP + username** so a shared NAT cannot lock out a site and an attacker
cannot lock out a named operator from elsewhere. Password reset and the reset
token check are throttled too - the reset endpoint sends real mail, so an
unthrottled loop was also a way to get the deployment's SMTP account
blacklisted.

Verified live: `401 ×8` then `429`.

### 3. Passwords stored weakly - CRITICAL / HIGH

- The **admin master password was stored in cleartext** in
  `auth.local.toml` - the same file as the session signing secret, readable
  by every master process on the box.
- **User passwords** were one unstretched round of `sha256(salt + password)`,
  which a commodity GPU counts in billions per second.

Both are now **PBKDF2-HMAC-SHA256, 240k rounds** (standard library - no new
dependency on the Pi image; ~0.15s per login on a Pi). Migration is
transparent: an existing cleartext or sha256 credential still logs in, and is
re-stored under the new scheme on that first successful login, so nobody is
locked out of their own board by the upgrade.

### 4. Emailed reset links leaked and were poisonable - HIGH

The reset token embedded `sha256(cleartext master password)[:12]` - 48 bits
of an unsalted hash of the live admin credential, emailed in plaintext. The
nonce is now derived through the signing secret instead: it still dies when
the password changes, and reveals nothing about it.

The link was also built from `request.base_url`, i.e. the caller-supplied
`Host` header, so a stranger could have the reset link for your admin built
to point at their own domain. It is now built from a configured URL, or a
host proven to be this board, or the socket the process is actually answering
on.

### 5. No CSRF defence beyond SameSite - HIGH

There was no CSRF token, no custom-header requirement and no origin check
anywhere; the only barrier was the browser's `SameSite=Lax`, which still
permits cross-site top-level GETs. State-changing requests (POST/PUT/PATCH/
DELETE) now refuse a cross-origin `Origin`/`Referer` with 403. A request with
neither header is allowed - that is a CLI/curl caller, which carries no
ambient cookie and is therefore not a CSRF vector.

Ports deliberately do not have to match: the hub (5100) and each master
(512x) are one deployment to the operator.

Verified live: cross-origin POST → 403.

### 6. Cookie flags - HIGH

`Secure` was never set. It cannot be set unconditionally - this product ships
as plain HTTP on a LAN, and a `Secure` cookie there is one the browser never
sends back, i.e. nobody could log in. It is now set **when the request really
is TLS** (directly or via a proxy's `X-Forwarded-Proto`). Logout now deletes
the cookie with matching attributes, which some browsers require.

Update 2026-10-10: over TLS the cookie is now `__Secure-cytron_session` (Secure,
HttpOnly, SameSite=Lax) and over HTTP `cytron_session`, because the box can serve
both at once (its own HTTPS listener) and one Secure cookie under the plain name
would block every later plain-HTTP sign-in on the same host. Since Pipi's HTTPS
review (2026-10-10): over TLS only the `__Secure-` cookie is read (L1, no fixation
through a plain cookie), a TLS sign-out ends both cookies of the same account, and
a write over TLS must come from an `https` page (M3, CSRF is scheme-aware).

### 7. State-changing routes with no role check - HIGH

These were reachable by any logged-in account, including a `viewer`:

| Route | Now |
|---|---|
| `POST /api/settings/slaves` (pair a device; server-side fetch of a caller-supplied URL - also an SSRF primitive) | admin |
| `DELETE /api/settings/slaves/{serial}` (un-pair, revoke its token) | admin |
| `POST /api/settings/scan` (LAN sweep from the board) | admin |
| `POST /api/email/config` (repoint the deployment's SMTP) | admin |
| `POST /api/email/send` (send mail AS the deployment to any address) | admin |
| `POST /api/email/send/cancel` | admin |
| `POST /api/control/{serial}/{domain}` (write a device's camera/detector/network settings) | admin |
| `POST /api/detector/dataset/export` (bulk export of captured imagery) | admin |
| `POST /footage/{serial}/export`, `/delete` | admin |

### 8. The camera stream was open to the whole network - CRITICAL

`systems/demo/app/run.py` bound its MJPEG server to `0.0.0.0` with no
authentication of any kind, serving both the annotated feed and `/raw`
(unannotated). Every intended consumer is on the same box - the dashboard
relays it through the master, which *does* enforce a session, and the fleet
agent proxies it behind its bearer token - so the wildcard bind bought
nothing but exposure.

Now **binds loopback by default**. A deployment that genuinely shares a camera
between boards sets `[app] stream_bind = "0.0.0.0"`, which also prints a
warning naming the consequence. Concurrent viewers are capped
(`MAX_STREAM_CLIENTS = 12`): each one is a thread parked in a write loop, so
an unbounded count was a remote way to starve the detection loop.

> **Check this one on your deployment.** If any board consumes another
> board's `/raw` feed, that link stops working until you set `stream_bind`
> on the source board.

### 9. The Python kill switch did not kill anything - MED

`[python].enabled = false` correctly refused to *write* new browser-authored
Python, but the loader imported `wf_code/<handler>.py` **on filename alone**,
so:

- code written while the flag was on kept running after it was turned off,
  until someone happened to re-save that workflow; and
- any `.py` dropped into that folder by hand would be imported and executed
  inside the detection loop.

Now: the editor writes a manifest of sha256 digests, and the app refuses to
import anything that is not listed or whose bytes do not match (so an *edit*
to editor-written code is caught too). The hub sweeps the folder at boot when
the flag is off, so turning it off and restarting is genuinely enough.

While testing this I found a real portability bug in my own first attempt:
the writer used text mode (CRLF-translating on a Windows-hosted hub) while
hashing LF bytes, so the manifest would never have matched. It writes and
hashes the same bytes now.

### 10. Lost updates on config writes - MED

Every settings writer did read-whole-document → change one table → write it
back, with no lock. The write was atomic; the *sequence* was not, so two
requests landing together (a workflow recompile while zones are saved) could
each read the old document and the second would silently drop the first's
change. All seven setters plus `set_functions` now hold a reentrant lock
across the whole read-modify-write (`dc.locked`, `dc.edit_local`).

### 11. Smaller items

- `secrets.encrypt` warned **once per process** when `cryptography` was
  missing and then silently stored device tokens in plaintext; it now warns
  on every such store.
- `react-router-dom` 6.28 → 7.18.2 (open-redirect and `deserializeErrors`
  advisories). Build passes; every router API this app uses is unchanged
  across that major. **Worth a click-through of navigation and the unsaved-
  changes guard**, since it is a major version bump.

---

## Known and open

Listed with the reason, so these are decisions rather than oversights.

| Item | Severity | Why it is still open |
|---|---|---|
| **TLS is available but OFF by default; HTTP stays open** (was "No TLS anywhere") | HIGH -> MED where switched on | Since 2026-10-10 the box can serve HTTPS itself with its own name-constrained root certificate (Settings -> Network -> HTTPS; `docs/architecture.md` sec 6.1): TLS 1.2+, AEAD suites, the cookie goes `Secure` under its own `__Secure-` name. What is still OPEN, and the operator's job: (1) it is off until master switches it on; (2) plain HTTP stays reachable on the hub's port, on port 80 in proxy mode, and on every master's own port, so a client that types `http://` still sends its password in clear - "Send browsers to HTTPS" (opt-in) redirects page loads but cannot protect a first request or an API client, and there is deliberately no HSTS; (3) in port mode the systems themselves are plain HTTP on their ports (only proxy mode puts them under the HTTPS address); (4) each client must trust the root once, and must check the fingerprint shown on the card when it does - a browser warning clicked through is no protection against a man in the middle; (5) the root's private key sits in `config/https.local.toml` (0600) beside the other secrets, so anyone with the service account can mint certificates that every client which trusted the root accepts for ANY local name (`.local`, `localhost`, `home.arpa`, `.lan`, `.internal`) and ANY private, CGNAT (100.64/10, Tailscale) or link-local address - the router page, a NAS, a PLC's web page, other boxes on the customer's LAN, not only this box's names. That is the price of never re-trusting at a DHCP change. Not for the internet: the root is name-constrained (since Pipi's 2026-10-10 review M1, never the bare hostname, which would have permitted a whole top-level domain such as `.ai`) and is serverAuth only with e-mail names permitted only at `invalid` (M2), so it cannot vouch for code signing, mail or client certificates in a client that applies a CA's EKU (Windows, macOS, Chrome, Firefox). A real domain with a public CA, or a tunnel, remains the deployment's choice (`core/services/remote.py`). |
| **SMTP app password, MQTT credentials, Blynk token stored in plaintext** | HIGH | `core/services/secrets.py` already has the Fernet path used for device tokens; extending it to these three is mechanical but touches the email/MQTT/Blynk read paths and their config round-trips. Worth doing next; the files are gitignored and `chmod 600`. |
| **Fernet key lives beside the ciphertext it protects** | MED | Both are in `config/`. Anyone who can read one can read the other, so this is obfuscation, not encryption at rest. Fixing it properly needs a key outside the config dir (TPM, or a passphrase at boot) and changes the unattended-restart story. |
| **`store_signing.pem` unencrypted on the owner hub** | MED | Owner-side only, `chmod 600`, never on a customer box. Encrypting it means a passphrase at publish time. |
| **Sessions are stateless with no revocation list** | MED | Logout only clears the client cookie; a captured cookie stays valid for its window (default 60 min). Killing it early requires disabling the account or rotating the secret. A server-side session store is the real fix. |
| **`idle_timeout_minutes` is actually an absolute lifetime** | LOW | It is not refreshed on activity, so a working operator is logged out mid-task at 60 minutes. Misleading name; worth either renaming or implementing sliding expiry. |
| **GStreamer `source` string is unvalidated** | MED | An admin can inject pipeline elements via ` ! ` or the `gst:` escape hatch (`camera.py`). Admin-only, and admin can already select models - but it is the one gap in an otherwise consistent no-injection doctrine, and unlike the Python node it is not behind the developer tier. |
| **`.manifest_sha256` marker for store code is existence-only** | MED | `store_ext` loading checks that the marker file exists, not that its content matches. Requires filesystem write to the package dir (not remotely reachable), but the marker does not provide the property its docstring claims. The `wf_code` loader now does this properly and is the pattern to copy. |
| **Agent v1 pairing accepts a caller-supplied token** | MED | `agent/agent.py` v1 mode has no credential check; the only brake is an IP lockout. Agent-side change, on the device contract. |
| **`/api/health` is unauthenticated and leaks counts** | LOW | Deliberate - the hub probes masters with it, and "down" must stay distinguishable from "locked out". It does disclose system/device counts pre-auth. |
| **`issue_reauth` / `verify_reauth` are dead code** | LOW | The module docstring claims sensitive credential operations require step-up re-auth. Nothing calls them, so that control does not exist at runtime. Either wire them up or drop the claim. |
| **Developer owns every developer's templates** | LOW | `_require_template_owner` returns early for any developer, so the per-template ownership check below it is unreachable. Documented as intentional; noting the dead branch. |

---

## Engineering baseline (not security)

- **No automated tests in the repo.** ~31k lines of Python and 22k of JSX
  with a CI that checks conventions, a device contract and that the frontend
  builds - but no unit or integration tests. The 21 smoke tests written
  during this and previous sessions live in a scratch directory outside the
  repo, so they are lost the moment that directory is cleaned. **Moving them
  into `tests/` and running them in CI is the single highest-value
  engineering change available.**
- **CI does not run on this branch.** `.github/workflows/ci.yml` triggers on
  `push` to `main` and on pull requests. Work happens on `cytron_iriv`, so
  nothing has been checked mechanically for the whole of this work.
- **The CI line ceilings are unenforceable as written.** They cap routes at
  150 lines and services at 300; 6 route files and 16 service files exceed
  them, `workflow.py` by 11x. That job would fail on every push today, which
  means the ceiling is either aspirational or the numbers need to change -
  right now it is neither.
- **192 `except Exception` handlers.** Mostly deliberate ("a widget with a
  bug renders as an empty card, not a 500"), and none swallow silently with a
  bare `pass`. Worth a periodic audit that each one still has a reason.
- No unbounded caches were found without either a natural key ceiling or a
  prune path; all 18 spawned threads are daemons.

---

## Verification

- All 21 smoke tests pass, including two new ones written for this work:
  `smoke_security.py` (password hashing and migration, throttle, wall,
  CSRF, cookie flags) and `smoke_pygate.py` (manifest verification, edit
  detection, kill switch).
- Every Python module compiles; the frontend builds.
- The hub was boot-tested on a spare port with all changes: startup completes,
  `/api/health` answers, and the new boot-time policy sweep logs no errors.
- The three critical findings were reproduced against the live box before the
  fix and re-tested after it.
