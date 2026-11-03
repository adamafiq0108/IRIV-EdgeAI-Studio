# UI/UX map and wireframes

Living document - updated as the product changes.

This is the Design-phase deliverable: what the operator sees, why it is arranged
that way, and which decisions are load-bearing enough that changing them back
would reintroduce a defect. It is not a style guide - the visual rules live in
the code and are enforced by tests. It is the reasoning behind the layout.

The product is one React SPA. The same bundle is served by the hub and by every
system's master, and which shell it mounts is decided by the bootstrap payload,
not by the URL. That single fact explains most of what follows.

## The three shells

`frontend/src/components/AppShell.jsx` fetches `/api/bootstrap` exactly once,
applies the deployment's branding, and then picks one of three shells:

| Bootstrap says | Shell | Serves |
| --- | --- | --- |
| `mode === "hub"` | `HubShell.jsx` | the hub, port 5100 - the admin surface over every system on the box |
| `deployRole === "standalone"` | `StandaloneShell.jsx` | one board running one system |
| otherwise | `MasterShell.jsx` | one system inside a fleet, ports 511x |

One request per app load is deliberate. The bootstrap carries brand, user,
system, capability-filtered nav, sidebar tree and the hub back-link together,
because a shell assembled from four calls is a shell that can render half of
itself and contradict the other half.

### Why the hub does not route

`HubShell` switches sections in local state (`useState("systems")`), not with
react-router. `MasterShell` and `StandaloneShell` both route, with `NavLink`
and `Outlet`.

The reason is not preference. The hub has no system, so every hub section talks
to `/api/hub/*` - endpoints that exist only on the hub process. A master-only
path typed into the hub, or a hub path pushed into history and later reloaded
against a master, is a 404 against an API that was never there. Client-side
sections cannot be deep-linked into the wrong process, which is exactly the
property wanted: the hub's panels either link *out* to a system's master, where
the real backend lives, or they do not leave the hub at all.

A master is the opposite case. It owns pages, those pages are what an operator
bookmarks and shares, and a page id maps to a URL through `PAGE_PATHS` in
`frontend/src/state/app.jsx`. Routing is what makes those links work.
`navRedirect()` bounces a typed URL for a page the server's nav omitted, so a
stale bookmark lands on a visible page rather than an empty one.

```
                       /api/bootstrap
                             |
                   +---------+---------+
                   |         |         |
              mode=hub   standalone   master
                   |         |         |
              HubShell   Standalone  MasterShell
             (sections)   Shell        (routes)
                             (routes)
                   |                     |
              /api/hub/*            this system's own API
```

## The hub's navigation

The hub's nav is a hardcoded `NAV` array in `HubShell.jsx` - hardcoded on
purpose, because unlike a system's nav it does not vary with hardware. What it
varies with is who is signed in and which bundle was built.

| Group | Item | Id | Visible to |
| --- | --- | --- | --- |
| Run | Systems | `systems` | everyone |
| Run | System Control | `control` | everyone |
| Run | Performance | `health` | everyone |
| Run | Live Preview | `wall` | everyone |
| Run | Template Store | `store` | developer, owner build only |
| Administration | Account Management | `accounts` | admin |
| Administration | System Templates | `templates` | developer, owner build only |
| Administration | Licenses | `licenses` | developer, owner build only |
| Administration | Data Retention | `retention` | admin |
| Administration | Platform Update | `update` | admin |

Three filters stack, and they are not interchangeable:

- **`adminOnly` on the group.** The whole Administration group disappears for a
  non-admin. Platform administration - accounts, retention, updates - is done
  once, at the hub, for every system on the box.
- **`devOnly` on the item.** Hidden from an account without the developer flag.
- **`__DEV_TOOLS__`, a build-time literal** from `frontend/vite.config.js`.
  In a customer build it is false, the lazy imports collapse to `null`, and
  Rollup drops the chunks entirely - the code is not in the bundle to hide.
  Their endpoints are equally absent from a customer install (`core/deploy.py`).

The third one matters because the first two hide a *button*, not a URL. Every
store route enforces the developer tier server-side (`core/routes/store.py`,
pinned by `tests/smoke_storegate.py`); the nav filtering is convenience on top
of a gate that already holds.

The store is grouped under Run rather than Administration because it is the
product's distribution channel, not a deployment's tool. A catalogue of other
people's templates sitting in a customer's hub is an invitation to install
something nobody chose for that deployment.

"New AI Vision system" (`vision`) is a section with a title but no nav entry.
It is reached from the Systems grid's own "New system" card, because creating a
system is something you do *from* the grid you are looking at, and the folder
you opened it from is carried through so the system lands there.

Two entries were deliberately removed and are worth recording, because the
routes still resolve and someone will wonder why nothing links to them. **User
Logs** was everyone's audit entries mixed together; it answered "what happened
on this box" but never "what has this account been doing" without reading past
everyone else's, so it moved to the Activity tab on each account. The hub's own
**process log** ring was removed because every system already has System Logs,
and the hub's ring is a different thing - this process's errors and requests,
which the hub cannot honestly relabel as a system's.

## A system's navigation

A system's nav is built on the server, per request, from the capabilities the
devices actually report: `core/services/nav.py`. Hardcoding a nav per system is
how one dashboard becomes N dashboards - the moment two systems differ, a
hardcoded nav needs a branch on the system slug, which `docs/conventions.md` §1
forbids outright.

Standard pages (`PAGES`), then admin-only pages (`ADMIN_PAGES`) appended for the
admin account, then any `extra_pages` a system package ships.

| Page id | Label | Category | Notes |
| --- | --- | --- | --- |
| `main` | Dashboard | Overview | |
| `analytics` | Analytics | Overview | |
| `builder` | (no nav entry) | Overview | reached from the Dashboard's "Edit dashboard" |
| `footage` | Footage | Overview | |
| `models` | AI Models | AI Configuration | admin |
| `detection` | Camera Setup | AI Configuration | |
| `workflow` | Workflow | AI Configuration | |
| `zones` | (no nav entry) | AI Configuration | a tab of Camera Setup; `/zones` lands there |
| `database` | Database | Data | |
| `dataset` | Dataset | AI Train | |
| `dataset_library` | Dataset Library | AI Train | |
| `blynk` | Blynk IoT | Integration | admin |
| `events` | Events Logs | Diagnostics | |
| `logs` | System Logs | Diagnostics | admin |

The order is the setup order, not alphabetical and not by importance: pick a
model, aim a camera, draw the zones, wire the workflow - then train on what you
captured, then read the results. Diagnostics is last because those are the pages
you open when something else went wrong.

Two pages have no nav entry by design. **Zones** is a tab of Camera Setup, drawn
over the same live feed the camera is aimed with; a second nav entry would be a
second door to the same room. The **Dashboard Builder** is reached from the
Dashboard's own Edit button, the same way the Analytics Builder is reached from
Analytics - an editor belongs next to the thing it edits, and a second entry
only invites the question of which one to use. Both routes still resolve, so
existing links and bookmarks work.

**System Logs sits with the system, not with the hub's platform administration.**
The log ring is per *process* (`core/services/logs.py`): this master's detection
errors, device timeouts and 5xx are in this master's memory and nowhere else.
The hub refuses to proxy, so a single "all logs" page would quietly show a
fraction of the answer. One page per process, on the process.

### The six categories, and per-account grants

```
SECTION_PAGES = {
  overview:    main, analytics, builder, footage
  ai_config:   models, detection, workflow, zones
  data:        database
  ai_train:    dataset, dataset_library
  integration: blynk
  diagnostics: events, logs
}
```

The same six exist twice: `SECTION_PAGES` in `core/services/nav.py` and
`SECTIONS` in `frontend/src/components/nav-sections.js`. They are separate
copies because one draws the rail and the other decides what a request may see,
and `tests/smoke_sections.py` pins them equal - a category that exists on one
side only is either a grant nobody can see or a rail group nobody can hide.

The keys are storage, not labels. An account's per-system visibility grant is
stored under them in `users.local.toml` as `[users.<name>.sections]`, so a key
must survive a label being reworded.

`nav.build(..., sections=...)` takes the viewer's grant for *this* system.
`None` means everything - admin, developer, and any account with no stored
grant. A set of keys means only pages in those categories. A page that no
category claims stays visible: an unmapped page must appear, not vanish, or
adding a page would silently hide it from everyone with a grant.

Enforcement is server-side, in the master's `/api/bootstrap`. The rail simply
draws what it was given, and the SPA bounces a typed URL for an omitted page.

`StandaloneShell` groups its rail by these sections and remembers which groups
are collapsed in `localStorage["nav.groups"]` - stored as the *closed* set, so a
newly added group defaults to open. The active group is always open regardless.
`MasterShell` renders one flat "Pages" group instead, because a fleet operator
is navigating between systems as much as within one.

## Key screens

### Systems grid - `frontend/src/pages/Hub.jsx`

The hub's home. One card per registered system; a non-technical operator clicks
a card to enter it. Building new ones lives in the sidebar.

```
+--------------------------------------------------------------+
| Systems                       [ Systems 3/8 ] [ AI 1/2 ] [ ☾ ]|
+--------------------------------------------------------------+
| [search............]                  [Select] [Import] [+New]|
|                                                               |
|  Unfiled                                                      |
|  +-------------+ +-------------+ +-------------+ +----------+ |
|  | [thumbnail] | | [thumbnail] | | [thumbnail] | |    +     | |
|  | Line 3      | | Bay Door    | | Yard        | |   New    | |
|  | ● ok    ▶ ■ | | ● down  ▶ ■ | | ● none  ▶ ■ | |  system  | |
|  +-------------+ +-------------+ +-------------+ +----------+ |
+--------------------------------------------------------------+
```

**The decision worth recording:** the capacity meters sit in the header, not on
the form. `CapacityMeters.jsx` reads systems-used/max and AI-running/max from
`/api/hub/pipelines`, caps included. Both are real limits the server refuses
past (`provision.MAX_SYSTEMS`, `detector_control.MAX_RUNNING_APPS`), and meeting
one used to mean filling in the whole create form, or pressing Start, and
reading a 400. A meter turns a limit you discover into a limit you knew about
before the attempt. The caps come from the server for the same reason the meters
exist: a UI holding its own copy of a server limit is a UI that lies the day the
limit moves.

Removing systems is behind a "Select" mode that flips the grid into pick-cards
whose only action is Remove, so a delete is never one misplaced click away from
an open.

### System Control - `frontend/src/components/ControlPanel.jsx`

One table of every system on the box: status, pipeline type, camera source,
model, live FPS, autostart, and run/restart/stop.

```
+---------------------------------------------------------------------+
| System   State     Pipeline    Camera        Model      FPS  Auto    |
+---------------------------------------------------------------------+
| Line 3   ● running detection   /dev/cam-usb0 yolov8s   28.4  [on ]  ▶■↻|
| Bay Door ● online  detection   rtsp://…      yolov8s    --   [off]  ▶■↻|
| Yard     ● offline detection   (unset)       --         --   [off]  ▶ |
+---------------------------------------------------------------------+
```

**The decision worth recording:** every per-system fact on this table - the dot,
the word beside it, the FPS, the camera - comes from a single poll of
`/api/hub/pipelines`. `/api/hub` supplies only the list of systems. Mixing two
polls is what made this table flicker: the dot from one response and the label
from another disagreed for one refresh interval each time. The server decides
the state; the dot and the word read the same field, so they cannot disagree.

Start, stop, restart, autostart and the capture switches are open to whoever was
*granted* the system, not to admins only. An operator standing in front of a
stopped line could previously see that it was stopped and not start it. The
authority that decides whether the row is visible is the same authority that
decides whether it can be worked: if you can see it, you can run it. What stays
admin-only is everything that changes what the deployment *is* rather than what
it is doing - creating, renaming, importing, exporting, deleting a system's
data. Both halves are pinned by `tests/smoke_opscope.py`.

### Camera Setup - `frontend/src/pages/Detection.jsx`

Run the camera. That is the whole job of the page. It used to do four unrelated
things - pick a model, choose a camera, run the app, and configure GPIO/MQTT
outputs - and each of those has a better home now: models on AI Models, outputs
on the Workflow canvas.

```
+---------------------------------------------------------------------+
| Cameras            | [ Live View | ● Live ]      [Zones] [Feed]      |
|  ● Cam 0  (usb0)   | +---------------------------------------------+ |
|  ● Cam 1  (rtsp)   | |                                             | |
|  ○ Cam 2  inactive | |            live feed (WebRTC,               | |
|  [+ add camera]    | |            MJPEG fallback)                  | |
|  3 of 4 cameras    | |                                             | |
|                    | +---------------------------------------------+ |
|                    |  Active [on]   [ ■ Stop ]   [snapshot][record] |
+---------------------------------------------------------------------+
```

**The decision worth recording - the Active switch beside Start/Stop.** Cameras
gained a real active/inactive switch, and the status dot in the rail was demoted
to reporting only: a status light that is also a button read as neither, and was
clicked by accident mid-select and missed on purpose. The switch then moved out
of the rail rows and into the feed toolbar, immediately before Stop/Start. The
reason is what Start actually does - it runs *every active camera* - so the
switch is the one control that decides whether the camera on screen is among
them, and it belongs beside the control it gates. One switch per selection, not
one per row.

**The decision worth recording - the Live View pill reports the camera.** The
pill (rendered by the Zones panel over the same feed) is a segmented shape: what
this box *is* on the left, what it is *doing* on the right, with the colour on
the state half so it reads from across the room. It originally knew only the
system's run state, so it said "Live" over a camera that was switched off or had
a fault of its own. It now checks the selected camera first, in the same
priority the camera rail uses: **Inactive** overrides everything, then **Error**,
then the system's running / stalled / stopped. Both of the first two override
"running" because neither is fixed by pressing Start, and an operator watching
"Live" over a dead camera is precisely the failure this pill exists to prevent.
The state half carries the camera's fault text as its tooltip.

### Zones - `frontend/src/pages/Zones.jsx`

A panel, not a page. It renders inside Camera Setup's Zones tab, over the same
live feed the cameras are aimed with; the `/zones` route lands there.

```
+---------------------------------------------------------------------+
| [ Live View | ● Live ]                        [Camera] [Zones] [Feed]|
| +-----------------------------------------+ +---------------------+ |
| |                 ,-----.                 | | Zones      ↶ ↷      | |
| |   +---------+  (  circ )                | | ● Bay      area     | |
| |   |  Bay    |   `-----'                 | | ● Entry    line     | |
| |   +---------+          \  entry line    | | ○ Yard     poly     | |
| |                                         | | [+ rect][+ circ]…   | |
| +-----------------------------------------+ | colour / count-when | |
|                                             +---------------------+ |
+---------------------------------------------------------------------+
```

**The decision worth recording:** this panel edits zone *definitions* only -
geometry, name, colour, count-when, enabled. Everything a zone *does* (counting,
alerts, timers) is wired on the Workflow page. The per-zone action editors this
panel used to carry duplicated that whole page in miniature, which meant two
places to configure one behaviour and two places for it to be wrong. It is
multi-camera aware in one specific way: only the shown camera's zones are drawn,
but a save writes the whole list, so hidden zones survive an edit.

### Workflow editor - `frontend/src/pages/Workflow.jsx`

The visual pipeline, on `@xyflow/react`.

```
+---------------------------------------------------------------------+
| Palette      |                                            [minimap]  |
|  Input       |  [Detection]->[Class ]->[ Zone ]->[ Zone  ]->[Alert]  |
|  Filters     |  [ Stream  ]  [Filter]  [ Bay  ]  [Counter]     |     |
|  Zones       |                                       |         v     |
|  Analytics   |                                       +----->[Tile]   |
|  Logic       |                                                       |
|  Dashboard   |                    [ ✓ compiled · 4 functions ]       |
|  Output      |                                                       |
+---------------------------------------------------------------------+
```

**The decision worth recording:** the graph is an *authoring format*, not the
runtime. On save the server compiles it into the artifacts the platform already
runs - `[[functions]]`, `[[outputs]]`, Builder widgets - in
`core/services/workflow.py`. The page owns editing UX and nothing else. That
split is why a zone action ticked on the Zones panel can appear here as a real
drawn chain (`tests/smoke_zonesync.py`, `smoke_zonetrigger.py`) and compile back
to the *same* function ids: the canvas is a view of the same compiled truth, not
a second source of it.

The palette's colours group by stage - blue takes data in, greens measure it,
oranges support the graph without being a stage in it, and the last three are
where a pipeline ends: kept, shown, acted on. The shade within a hue tells
neighbouring categories apart. This is checked, not commented, by
`tests/check_palette_colors.mjs`, because adding a category with a colour picked
at random looks fine on its own and quietly costs the palette the one property
that makes it scannable.

The canvas follows the theme through `useDarkClass()` rather than `useTheme()`.
`useTheme` *owns* the class; a component that merely needs to follow it cannot
use it, and reading the class once per render is how the canvas lagged a theme
change until its next poll.

### Dashboard and Builder - `frontend/src/pages/Main.jsx`, `Builder.jsx`

```
  Dashboard (read)                    Builder (edit)
+------------------------+       +----------------------------------+
| [feed]  [ 12 ]  [ ok ] |  Edit | Palette | 12-col grid  | Inspect |
|         in bay  status | ----> |  tile   | [f][f][ 12 ]| bind to: |
| [========= chart =====]|       |  gauge  | [==chart===]| zone_a…  |
+------------------------+       +----------------------------------+
                                        [ live preview ]
```

The Dashboard renders a *builder page* named `main`, not a fixed layout. The
feed, the violation indicator and the value tiles are all ordinary widgets;
the page is seeded from the system's zone actions on first open
(`core/services/builder.py`).

**The decisions worth recording.** One screen, one request: `/api/dashboard`
returns run state, widgets and setup progress together, because two polls for
one page is how a UI starts contradicting itself. The poll is 600 ms, chosen
rather than defaulted - what an operator reads as "the tile lags the video" is
this interval, made obvious by a live picture sitting beside a polled number.
In the Builder, the canvas is *always* the 12-column grid; it used to have an
off mode where widgets flowed as a plain list, which meant a layout could be
saved in a shape the dashboard had to guess how to render. And the preview is
rendered by the same endpoint and the same `<Widget>` the real page uses - a
builder whose preview is a mock teaches operators to distrust it.

### Account Management - `frontend/src/components/AccountsPanel.jsx`

Hub-only, admin-only. Accounts and roles in two tabs, master-detail inside.

```
+---------------------------------------------------------------------+
| [ Accounts ] [ Roles ]                                    [+ New]   |
+-------------------+-------------------------------------------------+
| ● admin           |  maria          operator          [Disable][Del]|
| ● maria           |  [ Details ] [ Systems ] [ Activity ]           |
| ○ dev             |                                                 |
| ● lee             |  Line 3   [x]Overview [x]AI Config [ ]Data …    |
|                   |  Bay Door [x]Overview [ ]AI Config [ ]Data …    |
+-------------------+-------------------------------------------------+
```

**The decision worth recording:** one place to manage every account. The
credential store and the session secret are already shared by the hub and every
master on the box, so a user created here logs into everything their systems
list allows - a per-system account page would have been the same file edited
from N places. A standalone system deliberately carries no account UI at all.
The layout is master-detail because the earlier page of stacked cards put every
account's full controls on screen at once, and twelve accounts was twelve chip
lists to scroll past. The per-system section checkboxes are generated from
`SECTIONS` in `nav-sections.js`, so the grant UI cannot drift from the rail it
controls.

The administrator is a *default* identity, not a fixed one - the name is stored
with `admin` as its default, every check follows a rename in both directions,
the name cannot collide with an operator account, and the administrator appears
in the list as a row. Listing zero accounts on a box somebody is signed into was
a lie (`tests/smoke_adminname.py`).

### Template Store - `frontend/src/components/StorePanel.jsx`

Developer-only, and absent from a customer bundle.

```
+---------------------------------------------------------------------+
| Template Store                                        [license key] |
| +-------------+ +-------------+ +-------------+                     |
| | People      | | PCB Defect  | | Forklift    |                     |
| | Counting    | | Pro  🔑     | | Safety      |                     |
| | free        | | needs key   | | free        |                     |
| | [Install]   | | [Install]   | | [Install]   |                     |
| +-------------+ +-------------+ +-------------+                     |
+---------------------------------------------------------------------+
```

**The decision worth recording:** a store template is a *whole system* somebody
built, exported and published - zones, workflow, dashboard, class filter, and
the `.hef` compiled for each supported Hailo board - not a settings preset. That
is why installing one produces a system that runs immediately rather than a
scaffold to fill in. Free templates come from the public repo and need no key;
Pro templates need a license key issued by IRIV, pasted once and checked against
this device's serial *and against server time*, never only the local clock -
a clock is the one input an appliance's operator can change.

### Platform Update - `frontend/src/components/UpdatePanel.jsx`

```
+---------------------------------------------------------------------+
| Platform Update                                                     |
|   Running   v2.1.3                                                  |
|   Available v2.2.0                                     [ Apply ]    |
|                                                                     |
|   [======================------------]  unpacking…                  |
|                                                                     |
|   Previous release v2.1.3 kept          [ Roll back ]               |
+---------------------------------------------------------------------+
```

**The decisions worth recording.** An appliance in the field has no shell and no
git, so this page is the whole update story - which is why it is admin, not
developer: a customer appliance has no developer account, and a customer unable
to take a fix is the problem this solves. The button is deliberately not
automatic. Applying an update restarts every system on the box, and the person
who decides when a line goes down for a minute is the admin standing next to it,
not a timer. And the progress bar has to survive the thing it is reporting on:
mid-update the hub it is polling goes away, so connection failures during the
expected phases are not rendered as errors - the last known phase stays on
screen until the new hub answers. The phase map (`PHASES`) exists so that a
half-second of silence looks like progress rather than a hang.

## Design system

**Primitives.** `frontend/src/components/ui/` holds the shadcn/ui set -
`button`, `card`, `dialog`, `dropdown-menu`, `input`, `popover`, `scroll-area`,
`select`, `separator`, `sheet`, `sidebar`, `skeleton`, `switch`, `table`,
`tabs`, `textarea`, `tooltip` and the rest - configured by
`frontend/components.json` (`style: base-nova`, `baseColor: neutral`,
`iconLibrary: lucide`, JSX not TSX). They are built on `@base-ui/react`, not
Radix; there is no `@radix-ui` dependency. Tailwind v4 (CSS-first, no
`tailwind.config`), `cn()` = `twMerge(clsx(...))` in `frontend/src/lib/utils.js`.
Alongside them sit app-local components in the same folder - `Toast.jsx`,
`DateRangePicker.jsx`, `HoldToDelete.jsx`, `UndoRedo.jsx`, `CollapsibleCard.jsx`
- which follow the same prop conventions.

**Theming.** `frontend/src/hooks/use-theme.js` owns dark mode: state seeded from
`localStorage["cy-theme"]`, falling back to `prefers-color-scheme`, applied by
toggling the `dark` class on `<html>`. `useDarkClass()` is the read-only
follower for components that must react to the class without owning it.

**Token layering** is the part that bites. `frontend/src/index.css` declares
`@layer theme, base, components, legacy, utilities` and imports the ported
legacy stylesheet as `layer(legacy)`. Without that, `theme.css` opens with an
unlayered universal reset, unlayered CSS beats layered CSS at any specificity,
and one reset silently defeats every Tailwind padding and margin utility in the
app. On top of that sits a token collision guard: `theme.css` defines `--muted`
and `--accent` with *different meanings* - a muted text colour and the brand
cyan - where shadcn means a muted surface and a subtle hover surface. So
`:root` resolves to the legacy meanings (legacy pages have no wrapper to scope
by), `.cy-app` re-declares the shadcn meanings **including the `--color-*`
mappings**, and `.cy-legacy` restores the legacy meanings for legacy content
rendered inside a `.cy-app` shell. This is why `HubShell` puts `cy-app` on the
sidebar provider and `MasterShell` puts `cy-legacy` on its `<main>`.

**Branding.** `config/brand.toml` holds `product_name`, `brand_sub`,
`logo`, `accent`, `accent_alt`. `core/config.py`'s
`brand()` merges it with the gitignored `brand.local.toml` and refuses to fall
back to defaults on a malformed file - a typo must not boot a deployment with
the wrong brand. It reaches the SPA in `/api/bootstrap`, and `applyBrand()` in
`frontend/src/state/app.jsx` sets `--accent`, `--brand-accent` and `--accent-alt`
on the root and sets `document.title`. `--brand-accent` exists because
`.cy-legacy` re-declares `--accent` and would shadow the inline value. Nothing
in the committed product may name a customer; a deployment ships its own local
file.

**Toasts.** `frontend/src/components/ui/Toast.jsx`, a context provider wrapping
the whole shell. Auto-dismiss is graded by kind - info 4 s, success 3.5 s,
warn 8 s, **error 0, meaning sticky**: an error the operator did not read is an
error that did not happen. `run(promise, {...})` narrates an async action;
`undoable(message, commit, {...})` holds the operator's regret window open and
performs the real work only when the toast closes. That is the deliberate
counterpart to the confirm dialog: *the dialog asks "are you sure" before, when
the operator has the least information; undo asks nothing and lets them look at
the result.* Used outside its provider the hook returns no-ops (and `undoable`
commits immediately), because a page rendered outside the provider must not
crash on its first error - that turns a small bug into a blank screen. The stack
is `role="status" aria-live="polite"`.

## Interaction and accessibility rules the code enforces

**Destructive actions go through `ConfirmDialog`, never `confirm()`.**
`frontend/src/components/ui/dialogs.jsx`. The browser's `prompt`/`confirm`/
`alert` cannot be styled, cannot be reviewed before committing, and cannot mark
an action destructive. `ConfirmDialog` takes `destructive`, `busy`,
`confirmLabel`, `cancelLabel`, and flips the confirm button to the destructive
variant.

**`requireText` arming.** For the actions that cannot be undone, the dialog
takes a string the operator must type exactly before Confirm arms:

```js
const armed = !requireText || typed.trim() === String(requireText).trim();
```

Used where the blast radius is a whole dataset or a whole system -
`RetentionPanel.jsx` (purge-all types the policy's own label) and `Control.jsx`
(a destructive system action types the system's name). The typed value is
cleared on every open: *never carry a previous answer into the next open - a
pre-armed destructive dialog is exactly the accident this component exists to
prevent.*

Both dialogs portal to `<body>`, which is outside the `.cy-app` subtree the
design tokens are scoped to, so the content element carries `cy-app` itself.

**Skeletons over spinners, wherever the shape of the answer is already known.**
`frontend/src/components/Loading.jsx` exports `SkelLine`, `SkelCard`,
`SkelPage`, `SkelTable`, `SkelEditor`, `SkelShell`. A spinner says "something is
happening"; a skeleton says "a table with rows is coming, here is where it will
be". `AppShell` renders `SkelShell` until bootstrap resolves, every lazy route
in `frontend/src/router.jsx` falls back to a `SkelPage` shaped like the page it
is loading, and the editors fall back to `SkelEditor`. The legacy
`primitives.jsx` skeleton carries `aria-busy`.

**Rendering primitives is tested, not assumed.** `check_jsx_refs.mjs` proves
every JSX component a file renders is imported or defined in it - a build that
passes is not proof a page renders, and the failure otherwise arrives as a blank
page weeks later on a dialog nobody opens often. `check_primitives_render.mjs`
mounts the shared primitives with the *real* lucide icons the pages pass, after
`EmptyState` shipped broken because a lucide icon is a `forwardRef` object, not
a function. `check_widget_render.mjs` mounts every widget kind in jsdom and lets
effects run, because recharts under `renderToString` emits an empty wrapper and
would report "rendered fine" for a chart that draws nothing.

**Standardised control shapes.** Where the same action appears on several
screens it takes the same form - the camera-setup source row is picker + icon
rescan + icon Ports on every mode, after an audit found three different shapes
for it (`tests/smoke_boardports.py`). A control that changes shape between two
pages is a control the operator has to re-learn on each.
