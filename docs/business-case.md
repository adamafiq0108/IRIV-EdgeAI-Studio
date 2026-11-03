# Business case

Living document - updated as the product changes.

## What this is

IRIV Edge AI is a no-code computer-vision platform that runs entirely on one
box: a Raspberry Pi CM5 carrier with a Hailo-8 accelerator, from Cytron
Technologies. An operator points it at cameras, draws zones on the picture,
wires up what should happen, and it detects, counts, alerts and records -
without a cloud account, a subscription, or anyone writing code.

## The problem it addresses

Vision AI on a factory floor, a car park or a shop today generally means one of
three things: a cloud service that needs the footage to leave the building, an
integrator's bespoke project that costs more than the hardware it runs on, or a
Python script somebody's engineer wrote and nobody else can change.

Each fails the same customer for a different reason. The cloud service fails on
privacy, bandwidth and running cost. The bespoke project fails on price and on
what happens when the integrator moves on. The script fails the moment the
person who wrote it is unavailable and the camera angle changes.

## What is different here

- **The box is the product.** Inference, storage, dashboards and the web
  interface are all local. No internet is required to run - only to fetch new
  templates or updates.
- **No code, and it means it.** Zones are drawn on the live picture; logic is
  wired in a visual workflow; dashboards are built by dragging widgets. What a
  developer would express in Python, an operator expresses in the browser.
- **Multiple independent systems on one box.** Each system has its own
  cameras, model, zones, workflow, dashboard and access grants, and runs in its
  own processes - so one line's configuration cannot disturb another's.
- **Ready-made systems.** The template store distributes whole working
  systems - model, zones, workflow and dashboard together - so a common job
  (people counting, intrusion, defect detection) is an install, not a project.

## Who it is for

- **Manufacturing and warehousing** - counting, presence, safety zones,
  simple defect checks on a line.
- **Retail and buildings** - footfall, queue length, occupancy.
- **Integrators and resellers** - a platform to deliver on, rather than a
  bespoke build per customer.
- **Cytron's own hardware customers** - the software that makes an IRIV board
  a finished product rather than a component.

## How it is sold

Three tiers, enforced in the product (see `config/builtin.toml` and
`core/services/store.py`):

| Tier | What it is |
| --- | --- |
| Built-in | Ships with the appliance. Works out of the box, no key, no internet. |
| Free | Downloaded from the public template store. Signed, verified, no key. |
| Pro | Downloaded from the private store, unlocked by a license key issued at purchase. The key carries an absolute expiry and a read-only access token, and is checked against server time so winding the clock back cannot revive it. |

The signing chain is the commercial mechanism as well as the security one: a
box installs only what the owner signed, so the catalogue is a channel rather
than a folder anyone can add to.

## Why over-the-air updates matter commercially

Until this week, updating a customer's box meant somebody with a shell. That
is the difference between selling an appliance and selling a project: a fleet
you cannot fix is a fleet you cannot support, and a fix you cannot ship is a
fix that does not exist for the customer who needs it.

With OTA in place: a defect found on one board can reach every board; a
customer sees "an update is available" and one button; and a release that does
not come up puts itself back rather than becoming a site visit. It also makes
the paid tiers defensible over time - a subscription or support contract has
something to deliver.

## Constraints accepted

- **One box, one site.** No fleet-wide management console yet; each appliance
  is administered on its own. OTA is the first fleet-shaped capability.
- **Hailo-bound.** The accelerator is assumed; models are `.hef`.
- **A paying customer can read their own license key's token.** Inherent to
  serverless licensing. The token is read-only and scoped to the store
  repository alone, so the exposure is the paid catalogue, never the source.

## Related

- `docs/commercialization-roadmap.md` - what is built and what is not yet
- `docs/features.md` - the current feature list
- `docs/srs.md` - the requirements these goals become
