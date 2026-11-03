# "What now?" -- asked on the one screen the guided setup never spoke on.
#
# The tour was a Next/Skip bar in the top-right of the three pages it walks
# through, which meant it was only visible once you had already found the page
# it was going to send you to. A system created a minute ago lands on its
# Dashboard, where the tour said nothing at all.
#
# So it also travels with you, bottom-right, on every page. Minimise and Skip
# are separate controls with separate words, because "not right now" and "I
# know how this works" are different sentences and only one of them should end
# the tour for the next person to open the board.
#
# And the first camera: a new system arrives carrying ONE camera entry pointed
# at nothing, and adding a camera used to APPEND beside it -- leaving a
# permanent empty row above the real camera that nothing would ever fill.
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


# -- the steps are the three decisions, in the order they have to happen ---
check("the tour is models, then camera, then workflow",
      dc.TUTORIAL_STEPS == ("models", "detection", "workflow"),
      str(dc.TUTORIAL_STEPS))

wiz = read("frontend", "src", "components", "SetupWizard.jsx")
# BOTH shells. An appliance runs the standalone one, so mounting the guide only
# in MasterShell put it everywhere except on the boxes that ship to customers --
# which is exactly where a first-run guide is for.
for shell_file in ("MasterShell.jsx", "StandaloneShell.jsx"):
    shell = read("frontend", "src", "components", shell_file)
    check(f"the guide is mounted in {shell_file}", "<SetupWizard />" in shell)
check("...and reads the same per-system record the page bars use",
      "/api/detector/tutorial" in wiz)
check("...so it disappears the moment the tour is done",
      "if (!state || state.done) return null;" in wiz)

# Minimise is a fact about this browser tab; done-ness is a fact about the
# board. One operator collapsing the panel must not collapse it on the screen
# in the next room, and must not end the tour for anybody.
check("minimising is remembered per browser", 'localStorage.getItem(MINI_KEY)' in wiz)
check("...and does not end the tour",
      "setMini(true)" in wiz and "advance(true)" in wiz
      and 'onClick={() => setMini(true)}' in wiz)
check("minimised, it is still one click from coming back",
      "onClick={() => setMini(false)}" in wiz)
check("Skip ends it for good", 'title="Skip the guide' in wiz)

# Tok Aba's feedback: the panel opened bottom-right from the very first step,
# too small a box for a 24" monitor to bother sizing itself against. It should
# open centred on step 0 (nothing else on the page has been seen yet) and only
# move to the corner once Continue has advanced past that step, and it should
# read larger on a wide monitor than on a laptop.
check("step 0 renders centred, not in the corner",
      "idx === 0 ?" in wiz and "translate(-50%, -50%)" in wiz)
check("...every later step renders in the corner",
      "} : cornerBox;" in wiz)
check("...and the minimised pill always stays in the corner, even on step 0",
      "cornerBox" in wiz
      and 'style={{ ...cornerBox }} className="btn btn-accent btn-sm"' in wiz)
check("the panel widens on a wide monitor instead of staying one fixed size",
      "window.innerWidth" in wiz
      and "vw >= 1920" in wiz and "vw >= 1600" in wiz)
check("...tiered, not a straight per-pixel scale (avoids reflow on every resize)",
      'vw >= 1920 ? 420 : vw >= 1600 ? 360 : 320' in wiz)
check("...and never overflows a narrow screen once centred",
      'maxWidth: "calc(100vw - 32px)"' in wiz)
# On the step's own page the useful button is "done, what next"; anywhere else
# it is "take me there". Offering both asked the operator which they meant.
# Through atPath, not startsWith: "/workflow" is a prefix of "/workflows", and
# a plain startsWith would have called that standing on the step.
check("the button matches where you are standing",
      "const here = atPath(location.pathname, step.to);" in wiz
      and "Go to {step.label}" in wiz)
# Finishing a step from the page's own bar has to move this panel too.
check("it re-reads on navigation", "[load, location.pathname]" in wiz)

# -- the first camera fills the placeholder, it does not sit beside it ------
det = read("frontend", "src", "pages", "Detection.jsx")
check("a system's empty camera entry is recognised",
      "const placeholderCam" in det)
check("...and the first camera added fills it",
      "if (addingCam && placeholderCam)" in det)
check("...rather than spending a budget slot on a placeholder",
      "} else if (addingCam) {" in det)
# The page's whole purpose is blocked until a camera is chosen, so it opens
# the picker itself rather than leaving somebody to find the one useful
# control on the screen.
check("a brand-new system opens the camera picker by itself",
      "autoPicked.current = true;" in det and "beginAddCam();" in det)
check("...once per visit, and never for somebody who could not act on it",
      "if (autoPicked.current || !status || !editable) return;" in det)
# The notice belongs next to the blank picture it is about, not at the top of
# the page with a title and a toolbar in between. The red fault itself is the
# CamFaultDialog popup now; what stays inline is the amber "reconnecting"
# card (camX.fix) and the no-camera card. The fragment's close is looked
# for after it opens: the popup's own footer is an earlier "</>}".
check("both feed notices are handed to the panel, not stacked above it",
      det.index("notice={<>") < det.index("camX.fix")
      < det.index("No camera connected")
      < det.index("</>}", det.index("notice={<>")))
zones = read("frontend", "src", "pages", "Zones.jsx")
# Under the row that names the camera and says whether it is live: that row is
# the subject, and the notice is what happened to it. Above the panel they read
# as page-level banners about something further down.
check("...and the panel renders them under the camera's own status row",
      "notice = null" in zones
      and zones.index("{toolbar}") < zones.index("{notice}")
      < zones.index("gridTemplateColumns: \"minmax(0,2fr)"))

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
