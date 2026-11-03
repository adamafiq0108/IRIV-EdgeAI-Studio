# Defect log

Living document - updated as the product changes.

One row per defect worth remembering: what was seen, what it actually was, and
what now stops it returning. The last column is the point of the file - a fix
without a test is a fix that comes back, and this is where that is visible.

Severity: **S1** the box does something wrong and says nothing · **S2** a
feature does not work · **S3** a feature works but misleads or annoys.

Older defects (before this log was started on 2026-08-26) are recorded here
only where they still shape the code; the full history is in git.

## Open

| Found | Severity | Symptom | Status |
| --- | --- | --- | --- |
| 2026-08-24 | S3 | A system streaming healthily at ~29 fps still carried a stale `error: camera read failed` in its `state.json`, so its card could report a fault that had already cleared. | Not investigated - reported, then troubleshooting was ended for the session. |

## Closed

| Found | Severity | Symptom (as reported) | Root cause | Fix | Pinned by |
| --- | --- | --- | --- | --- | --- |
| 2026-08-26 | S2 | Publishing a store template would raise `NameError: _opener`. | Extracting the GitHub redirect handler into `core/services/ghnet.py` left `_gh_api` referring to the opener it no longer had. | `ghnet.opener()`, used by `_gh_api`. | `tests/check_undefined_names.py` (which caught it before it ever ran) |
| 2026-08-24 | S1 | "Why does it track all the objects? I already selected which to track." | Motion trails had no branch in *either* whitelist - the graph compiler nor `validate_function` - so every setting on the node, class filter included, was dropped and the engine ran on defaults. | Compile and validation branches for `trails`. | `tests/smoke_trails.py` §12 |
| 2026-08-24 | S1 | The live view drifted to a different camera's picture on its own. | When a named camera went away, `_pipe_for` fell back to the primary pipe, so the stream kept serving - with someone else's frames. | A named-and-missing camera now resolves to nothing; the stream ends after ten seconds and the reconnect gets an honest 404. | `tests/smoke_nocamera.py` |
| 2026-08-24 | S1 | Processes dying at random all day; the hub restarting sixteen times. | A hand-started `hub_server.py` was squatting port 5100, crash-looping systemd's copy. | Killed; rule recorded: never start the hub by hand, use `systemctl restart irivai-hub`. | - (operational) |
| 2026-08-24 | S2 | "Function type must be one of …" when saving a node the palette offered. | `core/services/functions.py` keeps its own copy of TYPES; the new type was added only app-side. | Added, and the two lists are now asserted equal. | `tests/smoke_trails.py` §10 |
| 2026-08-24 | S2 | Deleting a system left its detection app holding the port, so the next system on that port could not start (`Errno 98`). | `remove_system` stopped the master but not the app. | `remove_system` stops the app first. | `tests/smoke_masterreap.py` |
| 2026-08-24 | S2 | A newly created system could not add a camera: "camera source is required". | Whole-list re-validation refused the scaffold's own unset placeholder row. | `allow_unset` for stored rows; a new row still needs a real source. | `tests/smoke_nocamera.py` §6 |
| 2026-08-24 | S2 | "No camera None" - the last camera could not be removed. | The page fell back to the legacy single-camera shape on an explicitly empty list, inventing a row with no id. | The fallback fires only when the field is absent, and carries an id. | `tests/smoke_nocamera.py` §7b |
| 2026-08-24 | S2 | A system created from an edited template used the old template. | The format was not re-snapshotted from its workbench at create time. | Create-time re-snapshot. | `tests/smoke_templaterefresh.py` |
| 2026-08-24 | S2 | Changing to a second model: "no detection model named …". | Two folders held the same model name; the lookup treated ambiguity as absence. | Name lookup prefers the folder named like the model; id lookup stays exact. | `tests/smoke_modeldrop.py` |
| 2026-08-24 | S3 | Pages took a long time to reload. | Sticky MJPEG fallback held one socket per camera, exhausting the browser's six-per-host budget. | WebRTC rejoin loop with bounded retries. | `tests/smoke_webrtc.py` |
| 2026-08-24 | S3 | Edit Dashboard could not be clicked from the admin page. | The nav redirect treated `/builder` as a controlled page, but it is never emitted in nav. | Removed from the controlled set. | `tests/smoke_sections.py` |
| 2026-08-24 | S3 | A `file:` camera stopped opening after any settings edit. | The hot-reload spec swap dropped the runtime-only `media_dir`, so clips resolved to bare filenames; the disconnect message then nested itself three deep. | The swap re-stamps `media_dir`; the retry unwraps its own prefix. | `tests/smoke_nocamera.py` §9, §10 |

## How to add a row

At the moment the cause is understood, not at the moment the fix is written -
the symptom in the user's own words is the part that gets lost. Then write the
test, then the fix, and put the test's name in the last column.
