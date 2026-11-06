# Cytron Dashboard - code conventions

The rules the code is written under. Referenced, never copied - if a rule lives in five places
it will mean five different things within a month.

## Why these exist

The precedent for this dashboard was one real per-device app's `api_controller.py`:
**1048 lines** holding users, events, analytics, footage, settings, storage, camera scanning,
app lifecycle, email, and device control - while its `models/` package sat empty at 3 lines.

Nothing about that was a bad decision at the time. Each commit added twenty reasonable lines to
the file that was already open. That is how every god-file is born, and it's why the rules below
are mechanical rather than tasteful: a ceiling you can measure with `wc -l` doesn't erode, and a
judgement call does.

## 1. Product vs deployment

**Product** (this repo): master server, core pages, contract, agent framework, skills, tooling.
**Deployment** (customer repo): system packages, branding, secrets, systemd units.

The product must never know a customer exists.

```bash
# every customer name and system slug this repo has ever been deployed for
grep -rin "<customer>\|<system-slug>" --exclude-dir=.git --exclude-dir=node_modules .
```

Empty output, or it isn't a product. This is the one check that can't be argued with. Keep the
pattern list in your deployment's own notes - the product cannot name the customers it must not
name.

The one exception is `systems/demo/`, a generic reference package that ships with the product so
a fresh clone runs and there is always one complete example to copy.

Corollaries:
- No imports from any deployment repo.
- No customer hostname, IP, subnet, email address, or serial scheme in code **or** defaults.
- No branching on `system_slug`. Ever. Use capabilities (`docs/contract-v1.md` §6).
- Anything a second customer would need different is config, not code.

## 2. Layering

Three layers, one direction. Routes → services → repos. Nothing points back up.

| Layer | Does | Never |
|---|---|---|
| `core/routes/` | parse request, validate, call a service, return | SQL, business logic |
| `core/services/` | logic, orchestration, formatting | SQL, `Request`, framework imports |
| `core/repos/` | SQL only | framework imports, logic |

Enforcement:

```bash
grep -rn "SELECT\|INSERT\|UPDATE\|DELETE" core/routes/ core/services/          # must be empty
grep -rn "fastapi\|starlette\|import flask" core/repos/ core/services/         # must be empty
```

Services take plain values and return plain values. That rule is what made the Flask → FastAPI
port cheap: `core/services/` was not edited at all, because nothing in it had ever heard of the
web framework.

A route that needs a value from another route's service imports the **service**, not the route.

## 3. Ceilings

| Path | Max lines |
|---|---|
| `core/routes/*.py` | 150 |
| `core/services/*.py` | 300 |
| `core/repos/*.py` | 300 |
| `core/templates/*.html`, `<systems_dir>/*/templates/*.html` | 200 |
| `core/static/js/**/*.js` | 200 |
| `agent/**/*.py` | 300 |

Hitting a ceiling means **split by domain**. It never means "append anyway" and it never means
"reformat until it fits".

```bash
find core agent -name '*.py' | xargs wc -l | sort -rn | head
```

The numbers are round on purpose. Their value is that they're checkable, not that 150 is
objectively correct.

## 4. One page, one set of files

A page owns exactly:

```
core/routes/<page>.py
core/services/<page>.py
core/templates/<page>.html      extends _base.html
core/static/js/core/<page>.js
```

Work on one page never edits another page's files. Shared markup goes in a `_partial.html`;
shared JS in `static/js/core/common.js`; shared logic in a service both import.

If two pages keep needing each other's internals, they were one page.

### System-owned template overrides

A system package may ship `<systems_dir>/<slug>/templates/<page>.html`, which wins over
`core/templates/<page>.html` for that system's own master (`core/view.py` searches the system's
folder first, by filename, no directory prefix). What never moves: `_base.html`, `_nav.html`, and
`core.css` - every system's pages extend the same shell and load the same stylesheet, so switching
between systems never feels like switching products.

This is optional per page. A system overrides only the pages it needs; anything it doesn't
provide falls straight through to the generic core page, same as today. `settings.html` is generic
for every system as of this writing - override it the same way once a system needs a custom
settings page, nothing structural changes to add that later.

Routes never choose between a system's template and core's - they ask for `"<page>.html"` and the
Jinja search path (built once per master at startup) decides. A route file gains no branch for this.

## 5. Naming

| Thing | Form | Example |
|---|---|---|
| system slug | lowercase, underscore | `zone_monitor` |
| module | lowercase, underscore, singular | `slave_events.py` |
| router | `router`, one per page module | `core/routes/footage.py` |
| route name | `<page>.<action>` | `system.footage`, `hub.index` |
| repo function | verb first | `get_events_since`, `upsert_slave` |
| device identity | `host_serial` | never bare `serial` |
| camera identity | `camera_serial` | never bare `serial` |

`serial` alone is banned. The codebase already has two unrelated meanings for it - Pi CPU
serial and GigE camera serial - and a bare name invites mixing them.

## 6. Config, not code

Four surfaces in `config/`:

| File | Owns |
|---|---|
| `brand.toml` | product name, logo, accent, brand sub |
| `storage.toml` | DB connection, footage paths, retention |
| `auth.toml` | role names, auth mode, session policy |
| `systems.toml` | enabled systems, card order, covers |

Two-file layering (`<name>.toml` + gitignored `<name>.local.toml`):
committed file holds structure and safe defaults, gitignored local file holds secrets and
per-device values, local wins per key. Sub-blocks merge field-by-field so a partial override
doesn't wipe its siblings.

Secrets never go in the committed file. Never in the DB. Mask on read.

## 7. Security

- Bearer token on every agent endpoint except `/api/health` and `/api/agent/info`.
- `hmac.compare_digest` for token comparison - never `==`.
- Pairing accepted only when unpaired or inside an operator-opened unlock window.
- Render user-controlled values with escaping. **Never** build DOM by concatenating strings into
  `innerHTML`. The mockup does exactly this (`:1687-1749`) and it becomes XSS the moment those
  rows come from a real device instead of a form the same user just typed.
- Parameterised SQL only. No f-strings into queries.
- Secrets masked in every API response.

## 8. Errors

Return the shortest true thing. `{"ok": false, "error": "..."}` with a real status code.

Don't swallow exceptions into a bare `except: pass`. Don't return 200 with an error body - the
master's health logic keys off status codes.

## 9. Comments

Comment the constraint, not the code. "Devicetree strings are null-terminated" earns its line.
"Loop over slaves" does not.

Never write a comment addressed to a reviewer ("changed this to fix X", "as requested") - it's
noise the moment the PR merges.

**No em dashes** (the "—" character) anywhere - comments, docs, UI copy, commit messages. A
double hyphen used as prose punctuation ("word -- word") is the accepted replacement, already
used throughout this convention document and the rest of the codebase; it is not itself the
target of this rule.

## 10. DEVNOTES

A deployment repo keeps a `DEVNOTES.txt` per app, plus a root one for shared infra. Update the
one that matches the change. This product repo uses git history and `docs/` instead - a
DEVNOTES here would duplicate both.

## 11. Off-limits - the running app

A device's detection app is **read-only** to everything in this repo. It is live on hardware,
and an adapter that edits it turns a dashboard bug into a stopped production line. Reach it
through a read-only adapter or a config file it already reads.

```bash
git -C <deployment-repo> diff --stat <app-dir>/     # must be empty
```

Anything that seems to need editing the app has another way in. Example: duplicate emails were
solved by setting `"enabled": false` in the device's gitignored `email_config.json`, because the
app's existing scheduler already gated on that key - no code change at all.
