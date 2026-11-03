## What changed, and why it had to

<!-- One concern per PR. A refactor plus a fix is two PRs. -->

## Checks

- [ ] No customer name, host, IP, subnet, serial scheme or branding anywhere - including comments and defaults
- [ ] No branching on `system_slug`; gated on a reported capability instead
- [ ] Layering intact: routes hold no SQL or logic, services import no framework, repos hold only SQL
- [ ] Under the line ceilings (routes 150, services/repos/agent 300, JS 200)
- [ ] Secrets masked on read; nothing secret committed (`config/*.local.toml`, tokens, `.env`)
- [ ] `python tools/contract_check.py <device> --token <token>` still passes **unedited** (if the agent, the contract or an adapter changed)
- [ ] Migration is a new forward-only file; no applied file was edited (if the schema changed)

## How it was verified

<!-- Commands run and what they printed. "Ran the app" is not verification. -->
