# Contributing

This repo is a **product template**, not one customer's dashboard. The rules below exist so it
stays one: [`docs/conventions.md`](docs/conventions.md) is the full set, this file is the
workflow around it.

## Before you start

- Read [`docs/conventions.md`](docs/conventions.md) §1–§4. Most rejected changes fail there.
- Contract questions are settled by `tools/contract_check.py`, not by the docs. If the harness
  and [`docs/contract-v1.md`](docs/contract-v1.md) disagree, the doc is the bug.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cd frontend && npm ci && npm run build && cd ..

export IRIV_HW_CHECK=off          # dev machine: not an IRIV Edge AI board with an AI HAT
python tools/mock_slave.py --port 5101 --token mock-token &
python master_server.py --system demo --port 5110 &
python hub_server.py --port 5100
```

Frontend work runs `npm run dev` on :5173 instead, which proxies the API to the master. The
FastAPI port serves the **built** bundle - a JSX edit is invisible there until `npm run build`.

## The rules that get changes rejected

**No customer in the product.** No customer name, hostname, IP, subnet, email address, serial
scheme or branding, in code *or* defaults *or* comments. Those live in a deployment's own repo
and its gitignored `*.local.toml`.

**No branching on `system_slug`.** Ever. Gate on capabilities the device reports
(`docs/contract-v1.md` §6). A feature that needs a slug check is a feature the contract is
missing.

**Layering, one direction.** `core/routes/` → `core/services/` → `core/repos/`. Routes hold no
SQL and no logic; services import no framework; repos hold nothing but SQL.

**Line ceilings.** routes 150, services 300, repos 300, agent 300, JS 200, templates 200. Over
the ceiling means split by domain - never append anyway, never reformat until it fits.

**One page, one set of files.** A change to one page never edits another page's files.

**Security.** Bearer token on every agent endpoint except `/api/health` and `/api/agent/info`;
`hmac.compare_digest` for token comparison, never `==`; parameterised SQL only; secrets masked in
every response and never in a committed file or the database.

## Run the audit before opening a PR

```bash
# layering - both must be empty
grep -rn "SELECT\|INSERT\|UPDATE\|DELETE" core/routes/ core/services/
grep -rn "fastapi\|starlette" core/repos/

# slug branching - must be empty
grep -rn "system_slug ==\|slug ==" core/ frontend/src/

# ceilings
find core agent -name '*.py' | xargs wc -l | sort -rn | head

# the device contract still passes, unedited
python tools/mock_slave.py --port 5101 --token mock-token &
python tools/contract_check.py http://127.0.0.1:5101 --token mock-token
```

## Commits and PRs

- One concern per PR. A refactor and a fix in one diff is two PRs.
- Present-tense subject, under ~72 chars, saying what changed and why it had to.
- Never commit `config/*.local.toml`, tokens, `.env`, or anything under `core/static/app/`.
- Migrations are forward-only: add `000N_<what>.sql`, never edit an applied file.
- Comment the constraint, not the code - and never address a reviewer in a comment.

## Reporting

Bugs and features go through the templates in `.github/ISSUE_TEMPLATE/`. Security issues do
**not** go in a public issue - see [`SECURITY.md`](SECURITY.md).

## License

Proprietary ([`LICENSE`](LICENSE)). By contributing you assign your work to Cytron Technologies
Sdn Bhd, to be released under that licence.
