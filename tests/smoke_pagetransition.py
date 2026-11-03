# The handover from skeleton to page.
#
# A skeleton and the page that replaces it are both `.page.active`, so the
# entrance animation played TWICE for one navigation: the placeholder rose and
# faded in, then a moment later the real content rose and faded in again. Two
# rises for one page reads as a flash, which is the opposite of what a
# skeleton is for -- it exists so that nothing jumps.
#
# So the placeholder holds still and the content owns the entrance, arriving a
# card at a time. Cheap, because the skeleton already reserved the space:
# nothing moves, it only appears.
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


css = read("frontend", "src", "theme.css")
loading = read("frontend", "src", "components", "Loading.jsx")

# The placeholder is marked as one, which is the whole mechanism: without a
# class of its own there is no way to say "this one does not animate".
# The MARKUP, not the prose: the comment explaining this fix quotes the very
# element it is about, and a check that cannot tell code from a comment
# punishes documenting the thing.
loading_code = "\n".join(
    line for line in loading.split("\n")
    if not line.strip().startswith(("*", "/*", "//")))
check("every skeleton page says it is a skeleton",
      loading_code.count('className="page active page-skel"') == 2
      and '<section className="page active">' not in loading_code,
      "the placeholder must carry its own class to opt out of the entrance")
check("...and a skeleton does not play the page entrance",
      ".page.page-skel { animation: none; }" in css)
check("...nor stagger its cards",
      ".page.page-skel > .panel-card,\n.page.page-skel > .grid > .panel-card { animation: none; }" in css)

# The content's entrance: longer than the old 0.2s linear-ish fade, and eased
# so it decelerates into place rather than stopping dead.
# ONE duration for the whole app, named once: "make the transition 280ms" has
# to be a single edit, not a hunt through every rule that animates a page.
check("the entrance is 280ms, named in one place",
      ":root { --page-in: 280ms;" in css)
check("the page eases in rather than snapping",
      "animation: fade var(--page-in) var(--page-ease) both; }" in css)
check("...and the cards run at the SAME duration, not longer",
      css.count("animation: fade var(--page-in) var(--page-ease) both;") == 1
      and css.count("animation: fade var(--page-in) var(--page-ease) backwards;") == 1,
      "a card that outlasts its page reads as two movements, not one")

# The reason it looked instant however long the duration said, and the fix.
#
# A page renders <section className="page active"> and its skeleton renders a
# <section> too, in the same position -- so React reconciles them as the same
# element, swaps the children, and never replaces the DOM node. A CSS
# entrance runs on MOUNT, and nothing mounted. A key the real page does not
# share is what makes the placeholder unmount and the page mount.
check("the skeleton is keyed, so the page it hands over to actually mounts",
      loading.count('<section key="skel" className="page active page-skel">') == 2,
      "without this the entrance never plays: React reuses the same <section>")
check("...from slightly below, so it settles rather than appearing",
      "from { opacity: 0; transform: translateY(8px); }" in css)

# ...and lands on `transform: none`, which is a correctness requirement rather
# than a tidiness one. The fill is `both`, so the last frame stays applied
# forever, and ANY transform other than `none` -- `translateY(0)` moves
# nothing and still counts -- makes the element the containing block for every
# `position: fixed` descendant inside it. That is what sized the footage
# lightbox to its page instead of the window: the scrim covered the top few
# hundred pixels and the picture was clipped at the page's right edge.
check("the entrance ENDS at transform: none, or every overlay inside breaks",
      "@keyframes fade { from { opacity: 0; transform: translateY(8px); }"
      " to { opacity: 1; transform: none; } }" in css,
      "a filled non-none transform traps position:fixed children in the page")

# The cards arrive in order. Bounded on purpose: past the first few a delay is
# a wait, and a long page would animate below the fold for nobody.
for i, delay in ((1, ".02s"), (2, ".06s"), (3, ".10s")):
    check(f"card {i} arrives at {delay}",
          f".page.active > .panel-card:nth-child({i}),\n"
          f".page.active > .grid > .panel-card:nth-child({i}) {{ animation-delay: {delay}; }}"
          in css)
check("...and everything after them shares one delay, never a growing queue",
      ".page.active > .panel-card:nth-child(n + 4)" in css
      and "{ animation-delay: .13s; }" in css)

# A backwards fill matters: without one a card is briefly visible at full
# opacity before its delay elapses, which is a flicker -- the exact artefact
# this change exists to remove. `backwards`, not `both`, since 2026-10-14
# (37ef8d2d): a forwards fill kept a transform on the card, which made it a
# stacking context and put date pickers under sticky table headers.
entrance = css.split(".page.active > .panel-card,")[1].split("}")[0]
check("a delayed card is hidden until its turn, not flickering first",
      "backwards" in entrance, entrance)

# A dashboard on a factory wall is exactly where somebody may have turned
# motion off.
check("reduced motion switches the whole entrance off",
      ".page.active > .grid > .panel-card { animation: none; }" in
      css.split("@media (prefers-reduced-motion: reduce) {")[-3:][0]
      or "prefers-reduced-motion" in css)
reduced = [b for b in css.split("@media (prefers-reduced-motion: reduce) {")
           if ".page.active" in b.split("}\n}")[0]]
check("...including the stagger, not just the page fade", bool(reduced),
      "no reduced-motion block covers the page entrance")

# ...and every page gets it. The Hub is built from Tailwind utilities rather
# than the .page shell, which is why it was the one screen that appeared with
# no entrance at all.
hub = read("frontend", "src", "pages", "Hub.jsx")
check("the Hub page joins the same entrance",
      'className="page active space-y-4"' in hub)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
