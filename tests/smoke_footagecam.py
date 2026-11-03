# Footage is browsed by CAMERA, and a delete finishes wherever you go.
#
# The filter used to be "Zone alerts / Workflow captures" -- footage sorted by
# the MECHANISM that saved it, a distinction nobody looking for an incident has
# ever cared about. On a four-camera system the question is always "which
# camera", and the answer was already in every filename and thrown away.
#
# And the delete: rows hidden locally, the request sent when the undo window
# closes. Both halves lived in the page's own state, so leaving mid-window
# repainted every image that was about to go (and stranded the Undo button on a
# dead component), while the commit stopped hiding the ids the instant the
# DELETE returned -- one render with the pre-delete list and nothing filtering
# it, which is the sub-second flash of everything coming back.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core.services import detector_control as dc              # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# -- which camera saved this file ------------------------------------------
# capture.py sanitises the camera id into the name (non-alphanumerics become
# "_", 24 chars), which is LOSSY -- so a file is matched back against the
# system's own cameras rather than parsed out of the string.
cams = [("gate_2", "gate-2", "Back gate"), ("gate", "gate", "Front gate")]
cams.sort(key=lambda t: len(t[0]), reverse=True)

check("a capture names its camera",
      dc._tagged_camera("gate_1a2b3c", cams) == ("gate", "Front gate"))
# Longest first, or "gate" would claim every file belonging to "gate_2".
check("...and a longer camera id is not eaten by a shorter one",
      dc._tagged_camera("gate_2_1a2b3c", cams) == ("gate-2", "Back gate"))
check("a file from before tagging is attributed to nobody",
      dc._tagged_camera("1a2b3c", cams) == ("", ""))
check("...as is one whose camera has been removed",
      dc._tagged_camera("oldcam_1a2b3c", cams) == ("", ""))
# Two cameras whose ids sanitise the same way cannot be told apart, so neither
# claims the file. Guessing would put one camera's footage under another's
# name, which is worse than an "Unattributed" bucket.
ambiguous = [("line_a", "line-a", "A"), ("line_a", "line.a", "B")]
check("an ambiguous tag is claimed by neither camera",
      dc._tagged_camera("line_a_99", ambiguous) == ("", ""))

svc = read("core", "services", "detector_control.py")
check("snapshots carry their camera", '"camera": cid' in svc)
check("clips carry theirs too", '"camera_name": cname' in svc)
# cameras() rescans the USB bus to freshen device names. Footage polls every
# 8s; a bus scan is not the price of "which camera saved this".
listing = svc[svc.index("def _capture_cameras("):svc.index("def _tagged_camera(")]
check("...without rescanning the camera bus on every poll",
      "_camera_list(_merged(pkg_dir))" in listing
      and "cameras(systems_dir" not in listing, listing[:200])

# -- the page ---------------------------------------------------------------
page = read("frontend", "src", "pages", "Footage.jsx")
check("the mechanism filter is gone",
      "const KINDS" not in page and 'setKind(' not in page)
check("...replaced by one button per camera", "cameras.map((c)" in page)
check("...offered only when there is a choice to make",
      "cameras.length > 1" in page)
check("a file with no camera is named, not hidden",
      "NO_CAMERA" in page and "Unattributed" in page)
# Photos and clips share the buttons, and a camera that saved photos may have
# recorded no clips -- landing on an empty grid with no visible reason.
check("switching media clears the camera filter",
      'setCam("all"); }, [media]' in page)

# -- a clip has a face, and the page has two ways to read ------------------
# Every clip was drawn as the same black rectangle with a play triangle, so a
# page of thirty said nothing about which one held the thing being looked for.
check("a clip can be asked for a poster frame", "def clip_thumb(" in svc)
poster = svc[svc.index("def clip_thumb("):svc.index("def clip_path(")]
# Not the first frame: a clip carrying run-up opens on the seconds before
# anything happened.
check("...taken past the run-up, not at frame zero",
      "frames // 4" in poster, poster[:200])
check("...cached beside the clip, like a snapshot thumbnail",
      '".thumbs"' in poster and "os.replace(tmp, dst)" in poster)
check("...and answering None rather than raising when it cannot",
      "return None" in poster and "except Exception" in poster)
routes = read("core", "routes", "models.py")
check("the clip route serves it behind ?thumb=1, same as a snapshot",
      "def detector_clip(name: str, request: Request, thumb: int = Query(0))"
      in routes and "clip_thumb(systems_dir, slug, name)" in routes)
check("deleting a clip takes its poster with it",
      '".thumbs",' in svc[svc.index("def delete_clips("):
                          svc.index("def clip_thumb(")])
check("the grid draws a clip's poster instead of a black box",
      "overlay={isClip}" in page and 'background: "#000"' in page)
check("...keeping the play mark ON it, so a clip is still a clip",
      "pointerEvents: \"none\"" in page)

check("Footage can be read as a table as well as a grid",
      'VIEW_KEY = "footage.view"' in page and 'view === "table"' in page)
check("...remembered per browser", "localStorage.setItem(VIEW_KEY, view)" in page)
check("...with the columns a grid cannot show",
      "Camera{arrow(" in page and "human(r.size)" in page)
# Built on the SAME table the Database page uses -- a second hand-rolled table
# style would make two pages doing the same job look like two products.
check("...on the shared grid, not a table style of its own",
      'className="db-grid ft-grid"' in page and "db-pick" in page)
check("...with a play control and a Delete of its own",
      'className="ft-play"' in page and 'className="ft-del"' in page)
theme = read("frontend", "src", "theme.css")
# db-grid sizes columns to content and lets a trailing filler eat the rest --
# right for a database of unknown columns, wrong for a fixed set: it parked
# eight columns against the left edge and left a third of the screen blank
# while the ID was still cut off at an ellipsis.
check("the table uses the width it is given",
      "db-filler" not in page and ".ft-grid { width: 100%;" in theme)
check("...with the one column that can use more room taking the slack",
      ".ft-grid .ft-id { width: 100%; max-width: 0; }" in theme
      and 'className="mono small ft-id"' in page)
check("...and the full name one hover away", "title={r.id}" in page)
check("...and those three live in the stylesheet, not inline",
      ".ft-grid th" in theme and ".ft-play" in theme and ".ft-del" in theme)
# Quiet at rest, loud under the pointer. Fifty solid discs down a table is a
# column of colour and nothing else; the one that matters is the one being
# pointed at, and the change of weight IS the affordance.
play = theme[theme.index(".ft-play {"):theme.index(".ft-del {")]
check("the play control is a tinted well at rest, not a solid slab",
      "color-mix(in srgb, var(--accent, #04bade) 14%, transparent)" in play, play)
check("...marked in the accent colour, so it reads as a control",
      "color: var(--accent, #04bade);" in play)
check("...and fills solid, with a white mark, under the pointer",
      "background: var(--accent, #04bade);" in play and "color: #fff;" in play)

# -- the table can be ordered ----------------------------------------------
check("every column that answers a question can sort by it",
      page.count("sortBy(\"") >= 5
      and 'sortBy("camera")' in page and 'sortBy("size")' in page)
check("...with an arrow saying which one decides the order", "arrow(" in page)
# "All the Arducam clips" split into eleven per-day runs is not sorted by
# camera -- it is sorted by day and then by camera, which answers a question
# nobody asked.
check("a non-date sort drops the day headings and goes flat",
      'if (view === "table" && sort.col !== "mtime") {' in page
      and 'return [{ label: "", rows: paged }];' in page)
check("...and no heading is drawn over rows that are not in day order",
      "g.label ? (" in page)
# "Camera 10" belongs after "Camera 9", not between 1 and 2.
check("names sort the way people read numbers",
      "{ numeric: true }" in page)
check("...and ties fall back to newest first, not to disk order",
      "|| b.mtime - a.mtime" in page)
# A row gets copied into a report and read on its own, where "Sep 4" has no
# year -- the day heading that disambiguates it in the grid is not there.
check("a table row spells its date out", "function whenLong(" in page
      and 'month: "long", year: "numeric"' in page)
check("select-all in the header ticks that DAY, not rows off screen",
      "for (const r of g.rows)" in page)

# -- what the page exports -------------------------------------------------
# TWO things, both of them the footage itself: mailed, or saved here as one
# zip. There used to be a third -- a CSV of the LIST, without the pictures --
# and it is gone on purpose. Two buttons a row apart where one quietly hands
# over no footage is how an incident report leaves with no evidence in it.
check("the mail button says what it produces",
      '"Export evidence"' in page and "Report" not in page)
check("...and the zip button beside it saves the files here",
      "async function downloadZip()" in page and "Download all" in page)
check("the list-only CSV is gone, function and all",
      "Export CSV" not in page and "exportCsv" not in page)

# The rows on screen are already the answer: camera filter, date range and the
# undo-pending suppression have all been applied.
check("...exporting what is on screen, in the order it is on screen",
      "selected.size ? shown.filter((r) => selected.has(r.id)) : shown" in page)

# -- and it pages ----------------------------------------------------------
# A month of a busy line is thousands of rows, and rendering them all meant
# thousands of <img> elements: one browser connection per thumbnail.
check("the list is paged", "const [pageSize, setPageSize]" in page
      and "PAGE_SIZES = [10, 25, 50, 100]" in page)
check("...with the size remembered per browser",
      "localStorage.setItem(SIZE_KEY" in page)
# Ordering across the WHOLE list, or "biggest first" means "biggest on this
# page", which is a different and much less useful sentence.
check("...sorted before the page is cut, not after",
      page.index("const ordered = useMemo") < page.index("const paged = useMemo"))
check("...saying where you are in rows, not in pages",
      '{" of "}{total.toLocaleString()}' in page)
# Forty numbered buttons is a wall, and a plain window drops the two that are
# always worth one click: back to the start, and how far this goes.
check("...with first and last always reachable",
      "function pageWindow(" in page and "new Set([0, pages - 1])" in page)
# A filter that shortens the list must not strand somebody on page 12 of 3.
check("...and a shortened list cannot strand you past the end",
      "Math.min(page, pages - 1)" in page)
check("Select all means the page, not 1,997 rows behind it",
      "paged.every((r) => selected.has(r.id))" in page)
# Paging is a rendering decision; exporting page 1 of 40 when the toolbar says
# 1,997 would be exporting the scrollbar position.
check("...but a download is still the whole filtered list",
      "const shown = ordered;" in page)
check("the footer is styled beside the rest", ".ft-foot {" in theme
      and ".ft-pager {" in theme)

# -- the delete -------------------------------------------------------------
store = read("frontend", "src", "pages", "footageDeletes.js")
check("the pending set lives outside the page", "const pending = new Set()" in store)
check("...and the page subscribes rather than owning it",
      "useSyncExternalStore(subscribe, getPending)" in page)
check("...with a stable snapshot, or the store loops for ever",
      "snapshot = new Set(pending)" in store)
check("the commit refetches BEFORE it stops hiding",
      store.index("await refetch()") < store.index("unhide(ids);\n}"),
      "unhide runs before the refetch: the deleted rows flash back")
check("a failed delete un-hides at once -- they still exist",
      "unhide(ids);\n    throw e;" in store)
check("the page no longer posts the delete itself",
      "/api/detector/snapshots/delete" not in page)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
