# The Integration category (Blynk, Modbus) is the developer's alone.
#
# Blynk and the Modbus server wire the box to somebody else's controller --
# product integration surface the owner is still shaping, not a control a
# site's master or admin should find in their rail. So the category is hidden
# from every tier but developer, and hidden by the NAV rather than by a grant:
# a grant is something master can widen back, and this is not master's call.
#
# The router bounces a typed URL for any page the nav omits (state/app.jsx
# navRedirect), so hiding the nav entry hides the page. The Blynk API says the
# same thing (developer only); the Modbus endpoint keeps its own grant because
# the RS485 node's inspector on the Workflow canvas reads it too, and that is
# the workflow's business, not the Integration page's.
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import nav                                 # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


class Sys:
    extra_pages = ()


check("the Integration category is the developer's",
      nav.DEVELOPER_SECTIONS == ("integration",)
      and set(nav.DEVELOPER_PAGES) == {"blynk", "modbus"}, repr(nav.DEVELOPER_PAGES))

caps = set()
for who, kw in (("an ordinary account", {}), ("an admin", {"is_admin": True}),
                ("master (every grant open)", {"is_admin": True, "sections": None,
                                               "pages": None})):
    pages = {i["page"] for i in nav.build(Sys(), caps, **kw)}
    check(f"{who} sees neither Blynk nor Modbus",
          not pages & {"blynk", "modbus"}, repr(sorted(pages)))
pages = {i["page"] for i in nav.build(Sys(), caps, is_admin=True, is_developer=True)}
check("the developer sees both", {"blynk", "modbus"} <= pages, repr(sorted(pages)))
pages = {i["page"] for i in nav.build(Sys(), caps, is_admin=True,
                                      sections={"integration"})}
check("a category grant naming Integration does not open it to a non-developer",
      not pages & {"blynk", "modbus"}, repr(sorted(pages)))

spa = io.open(os.path.join(ROOT, "core", "routes", "spa.py"), encoding="utf-8").read()
check("the SPA context tells the nav who is asking", "is_developer=is_developer" in spa)
blynk = io.open(os.path.join(ROOT, "core", "routes", "blynk.py"), encoding="utf-8").read()
check("every Blynk route is developer-only",
      blynk.count("_require_developer(request)") >= 3)
app = io.open(os.path.join(ROOT, "frontend", "src", "state", "app.jsx"),
              encoding="utf-8").read()
check("the router knows /modbus, so a typed URL bounces like /blynk does",
      'modbus: "/modbus"' in app)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
