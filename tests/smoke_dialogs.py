# The product asks its own questions.
#
# window.confirm() renders the BROWSER's box: "192.168.1.234:5101 says" above
# the sentence, the host and port announced to an operator who is looking at a
# product, no styling, no destructive colour, no theme. It is also modal to the
# whole tab -- rendering and polling stop behind it -- and several browsers
# suppress it after a few, which turns a delete button into a button that does
# nothing at all.
#
# ConfirmDialog and FormDialog already existed; what was missing was a way to
# use them without a piece of state per question, which is why call sites kept
# reaching for the browser. useConfirm/useAsk (components/ui/ask.jsx) are those
# two dialogs behind a promise, mounted once in AppShell.
#
# This pins the rule rather than the count: no native dialog anywhere in the
# app, and the replacement actually mounted -- a hook whose provider is missing
# answers "no" to every confirm, which would look like a dead button.
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "frontend", "src")

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(*parts), encoding="utf-8").read()


# -- nothing reaches for the browser's own dialogs --------------------------
NATIVE = re.compile(r"\bwindow\.(confirm|prompt|alert)\s*\(")
offenders = []
for folder, _dirs, files in os.walk(SRC):
    for name in files:
        if not name.endswith((".js", ".jsx")):
            continue
        path = os.path.join(folder, name)
        for i, line in enumerate(read(path).splitlines(), 1):
            # A COMMENT may name them -- several explain why they were left
            # behind, and that history is worth keeping.
            stripped = line.strip()
            if stripped.startswith(("//", "*", "/*")):
                continue
            if NATIVE.search(line):
                offenders.append(f"{os.path.relpath(path, ROOT)}:{i}")
check("no page opens a browser confirm, prompt or alert",
      not offenders, "\n      ".join(offenders))

# -- and the replacement is actually mounted --------------------------------
ask = read(SRC, "components", "ui", "ask.jsx")
check("useConfirm and useAsk are the two the app has",
      "export function useConfirm" in ask and "export function useAsk" in ask)
check("...built on the dialogs that already existed, not a third one",
      "ConfirmDialog" in ask and "FormDialog" in ask
      and 'from "./dialogs.jsx"' in ask)
# Escape and the backdrop are answers too. A promise left pending there hangs
# the caller for the life of the page -- the button would simply never respond.
check("dismissing settles the promise rather than dropping it",
      "if (!o) settle(false)" in ask and "if (!o) settle(null)" in ask)
check("a destructive question can say so", "destructive" in ask)
shell = read(SRC, "components", "AppShell.jsx")
check("the provider wraps both shells",
      shell.count("<AskProvider>") == 2 and "ui/ask.jsx" in shell)
check("...inside the toaster, so a page has both",
      shell.index("<ToastProvider>") < shell.index("<AskProvider>"))

# Every caller has to hold the hook: `confirm` resolving to the global
# window.confirm because the hook was forgotten is the exact bug this replaces.
users = []
for folder, _dirs, files in os.walk(SRC):
    for name in files:
        if not name.endswith((".js", ".jsx")):
            continue
        path = os.path.join(folder, name)
        src = read(path)
        if "await confirm({" in src or "await ask({" in src:
            users.append((os.path.relpath(path, ROOT), src))
check("something actually asks", len(users) >= 10, str(len(users)))
for rel, src in users:
    if "await confirm({" in src:
        check(f"{rel} holds the confirm hook", "useConfirm()" in src)
    if "await ask({" in src:
        check(f"{rel} holds the ask hook", "useAsk()" in src)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
