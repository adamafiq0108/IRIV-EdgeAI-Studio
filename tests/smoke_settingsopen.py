# Clicking Settings in the rail always opens Accounts first. The remembered tab
# (hub.settings.tab) is still how a notice's "Go to Email" lands on Email, so
# only the rail click resets it.
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def src(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


shell = src("frontend", "src", "components", "HubShell.jsx")
hub = src("frontend", "src", "components", "SettingsHub.jsx")
check("the Settings rail click resets the remembered tab to accounts and remounts",
      'if (it.id === "settings")' in shell
      and 'localStorage.setItem("hub.settings.tab", "accounts")' in shell
      and "setGoto((n) => n + 1);" in shell.split('if (it.id === "settings")')[1][:400])
check("a notice's link still lands on its own tab (go() sets the tab it is given)",
      'function go(page, tab)' in shell and 'localStorage.setItem("hub.settings.tab", tab)' in shell)
check("Accounts is the first tab and the fallback",
      '{ id: "accounts", label: "Accounts"' in hub and ': "accounts";' in hub)
print()
if bad:
    print(f"{bad} FAILED")
    sys.exit(1)
print("all good")
