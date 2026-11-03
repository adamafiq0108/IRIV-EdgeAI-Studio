# Download the ticked footage as one zip.
#
# The page could already EMAIL a selection, and it exported the LIST as a CSV,
# which left the obvious thing missing: the files themselves, on the machine
# the operator is sitting at. A CSV of filenames is not evidence, and asking
# somebody to right-click two hundred thumbnails is not an export. (That CSV
# has since gone -- see the end of this file.)
#
# The box builds the archive because the box has the files; a browser fetching
# each image to zip them itself would be two hundred round trips to rebuild
# what the disk already holds.
import io
import os
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import footage_mail as fm                    # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# -- 1. a download has its own budget -----------------------------------------
# The mail limits exist because a message crosses a mail server. A file saved
# to the machine in front of you crosses nothing, so refusing a 40MB download
# because Gmail would refuse it is a limit borrowed from a different problem.
rows = [{"kind": "snapshot", "name": "a.jpg", "path": "", "bytes": 40 * 1024 * 1024,
         "mtime": 1}]
ok, problem, total = fm.check(rows)                # the EMAIL budget
check("email still refuses 40MB, as it must", not ok and "email" in problem,
      problem)
ok, problem, total = fm.check_download(rows)       # the DOWNLOAD budget
check("...and a download of the same 40MB is allowed", ok, problem)
check("...and reports what it weighs", total == 40 * 1024 * 1024)

check("an empty selection is refused with a sentence",
      fm.check_download([])[1].startswith("nothing to download"))
big = [{"kind": "snapshot", "name": f"{i}.jpg", "path": "", "bytes": 1,
        "mtime": i} for i in range(fm.MAX_DOWNLOAD_FILES + 1)]
ok, problem, _ = fm.check_download(big)
check("...and so is a selection of more files than one zip should carry",
      not ok and str(fm.MAX_DOWNLOAD_FILES) in problem, problem)
huge = [{"kind": "clip", "name": "c.mp4", "path": "",
         "bytes": fm.MAX_DOWNLOAD_BYTES + 1, "mtime": 1}]
check("...and one that is simply too big, in words a person can act on",
      "narrow the date range" in fm.check_download(huge)[1])

# -- 2. the zip itself ---------------------------------------------------------
src = tempfile.mkdtemp()
files = []
for i, (kind, name, body) in enumerate((
        ("snapshot", "cap_1.jpg", b"\xff\xd8jpeg-ish"),
        ("snapshot", "cap_2.jpg", b"\xff\xd8second"),
        ("clip", "clip_1.mp4", b"\x00\x00\x00 ftypmp42"))):
    path = os.path.join(src, name)
    with open(path, "wb") as fh:
        fh.write(body)
    files.append({"kind": kind, "name": name, "path": path,
                  "bytes": len(body), "mtime": 1000 + i})

out = os.path.join(src, "bundle.zip")
ok, problem = fm.write_zip(files, out)
check("the bundle is written", ok, problem)
with zipfile.ZipFile(out) as z:
    names = sorted(z.namelist())
    check("...foldered by kind, so a mixed bundle opens as two folders",
          names == ["clips/clip_1.mp4", "snapshots/cap_1.jpg",
                    "snapshots/cap_2.jpg"], names)
    check("...with the bytes intact",
          z.read("snapshots/cap_2.jpg") == b"\xff\xd8second")
    check("...stored, not deflated: the CPU belongs to the detection loop",
          all(i.compress_type == zipfile.ZIP_STORED for i in z.infolist()))

# A missing file (deleted between listing and downloading) must not take the
# whole download with it -- it reports, it does not raise.
ok, problem = fm.write_zip([{"kind": "snapshot", "name": "gone.jpg",
                             "path": os.path.join(src, "gone.jpg"),
                             "bytes": 1, "mtime": 1}],
                           os.path.join(src, "b2.zip"))
check("a file that vanished is reported, not raised",
      not ok and "could not build" in problem, problem)

check("the saved file says which system and when",
      fm.zip_name("PCB Sensata").startswith("PCB-Sensata-footage-")
      and fm.zip_name("x").endswith(".zip"), fm.zip_name("PCB Sensata"))

# -- 3. the route ---------------------------------------------------------------
routes = read("core", "routes", "models.py")
route = routes.split('@router.post("/api/detector/footage/download"')[1] \
              .split("@router.post")[0]
check("downloading footage is the export permission, as emailing it is",
      'perm.require_any(request, ("footage.download", "footage.export"))' in route)
check("...it reuses the same selection model as the email export",
      "footage_mail_service.collect" in route and 'body.get("items")' in route)
check("...refuses with the reason, not a 500",
      "raise HTTPException(status_code=400, detail=problem)" in route)
# A gigabyte of evidence in RAM on a box running inference is how a download
# takes the detection loop with it.
check("...stages the zip on disk rather than building it in memory",
      "tempfile.mkstemp" in route and "FileResponse(path" in route)
check("...and deletes it once the response has gone",
      "BackgroundTask(_cleanup)" in route)
check("...off the event loop, because zipping blocks",
      "asyncio.to_thread(\n        footage_mail_service.write_zip" in route)

# -- 4. the page ----------------------------------------------------------------
page = read("frontend", "src", "pages", "Footage.jsx")
check("the page offers a Download button", "async function downloadZip()" in page
      and "Download all" in page)
check("...following the same selection rule as Delete",
      "selected.size ? shown.filter((r) => selected.has(r.id)) : shown" in page)
check("...over the whole filtered list, not the visible page",
      "const shown = ordered;" in page)
check("...and it says it is working, so nobody presses twice",
      'zipping ? "Preparing…"' in page and "disabled={zipping" in page)

client = read("frontend", "src", "api", "client.js")
check("a file answer is read as a file, not as JSON",
      "async function download(method, path, body)" in client
      and "await res.blob()" in client)
check("...named by the server, which is the only side that knows",
      "content-disposition" in client)
check("...on the long timeout: the box builds the archive before it answers",
      "UPLOAD_TIMEOUT_MS" in client.split("async function download(")[1])
check("...and a refusal still reads as its sentence",
      "data.detail || data.error" in client)

# The list-only export is GONE. It answered a different question from the one
# the page is for -- what exists, without the pictures -- and having two
# buttons a row apart, one of which quietly gives you no footage, is how
# somebody exports an incident report with no evidence in it.
check("the page no longer offers a CSV of the list",
      "Export CSV" not in page and "exportCsv" not in page)
check("...and the function behind it went with it, not left as dead code",
      "function exportCsv" not in page)
check("...while Download and Export evidence remain",
      "downloadZip" in page and "Export evidence" in page)

# -- the wait for a picture ----------------------------------------------------
# Every feed said "Connecting to the video feed…" -- a sentence to read before
# being allowed to look at a camera. A camera in a turning ring says it
# faster, in every language, and fits a dashboard tile.
loader = read("frontend", "src", "components", "CameraLoader.jsx")
check("there is ONE camera loader, not one per feed",
      "export default function CameraLoader(" in loader)
check("...the ring turns and the camera does not",
      "cam-loader-still" in read("frontend", "src", "theme.css"))
check("...and the sentence survives where it was doing real work",
      'role="status"' in loader and "sr-only" in loader)
for who, path in (("the page player", ("frontend", "src", "components", "LiveFeed.jsx")),
                  ("every MJPEG feed", ("frontend", "src", "components", "StreamImg.jsx")),
                  ("the WebRTC wait", ("frontend", "src", "components", "WebRTCVideo.jsx"))):
    check(f"{who} shows it", "CameraLoader" in read(*path))
# The Zones editor had its OWN sentence, painted over the picture rather than
# drawn through StreamImg, so the shared default never reached it. It was the
# one feed still saying the wait in words after the rest had stopped.
zones = read("frontend", "src", "pages", "Zones.jsx")
check("the zone editor's own connecting sheet is the shared loader too",
      "<CameraLoader label=" in zones)
# The sentence survives as that loader's LABEL (this camera really is
# opening, unlike a switched-off one) -- what must not survive is a hand-
# painted sheet of text over the frame.
check("...and no feed paints a connecting sheet by hand any more",
      "placeItems: \"center\"" not in zones.split("!feedLive && !feedFailed && (")[1][:400])
# A tile, a wall panel and a zone editor all draw through StreamImg, so the
# default is what reaches them: none of them passed a placeholder, and each
# showed an empty box.
check("...by DEFAULT, since no caller ever passed one",
      "placeholder = <CameraLoader />," in read("frontend", "src", "components",
                                                "StreamImg.jsx"))
# Absolutely positioned inside the frame: without a positioned parent it hunts
# up the tree and lands in the middle of the page.
check("...inside the frame, not adrift on the page",
      'style={{ position: "relative",' in read("frontend", "src", "components",
                                               "StreamImg.jsx"))

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
