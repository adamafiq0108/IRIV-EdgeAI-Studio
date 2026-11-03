# Security policy

## Reporting a vulnerability

Do **not** open a public issue. Report privately through GitHub's *Security →
Report a vulnerability* on this repository, or to the maintainers directly.

Include: what an attacker gets, the smallest reproduction you have, and the
commit or version you tested. Expect an acknowledgement within a few working
days.

## Scope

This repository is a template. A finding is in scope if it is a flaw in the
product: the master, the hub, the agent, the contract, or the reference system
package. A finding in someone's private deployment (their config, their
credentials, their network) is theirs to fix - though a deployment mistake that
the product *invites* is worth reporting as a design bug.

Especially interesting:

- anything that lets an unauthenticated caller read device footage, telemetry,
  or the event history
- anything that leaks a device bearer token, a database password, or an SMTP
  credential into a response, a log line, or a committed file
- authentication or session bypass on the master, or privilege escalation past
  the admin-only gate on destructive control actions
- pairing abuse: adopting a device, or being adopted, outside the operator's
  unlock window
- SQL injection, XSS in a rendered device value, or SSRF through the proxy or
  the discovery sweep

## What the product already assumes

These are documented trade-offs, not vulnerabilities, unless you can break the
boundary they assume:

- **The LAN between a master and its devices is trusted.** The agent serves
  video and event history to anything holding the device token, and
  `/api/health` and `/api/agent/info` are unauthenticated by contract. Restrict
  port 5101 to the device's own master (`docs/setup.md` §8).
- **Discovery accepts an address from whoever answers the sweep.** Serials are
  not secrets, so a rogue agent on the LAN claiming a known `host_serial` can
  attract that device's token. Set `[discovery].enabled = false` to keep
  addresses operator-only.
- **A database outage degrades, it does not fail closed.** Folders and audit go
  quiet; auth and the control channel are unaffected.

## For deployments

- Never commit `config/*.local.toml`, tokens, or `.env`.
- Rotate device tokens with the v1.3 rotation endpoints
  (`/api/agent/rotate` then `/api/agent/rotate/commit`) - keep-both-valid, so
  rotation costs no downtime.
- Do not expose a master or an agent directly to the internet. Put it behind a
  VPN or a reverse proxy that terminates TLS and authenticates.
