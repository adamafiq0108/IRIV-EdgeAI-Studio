# The hotspot reaches the box, never the wired LAN behind it.
#
# WHAT WAS WRONG. The box's hotspot is NetworkManager `ipv4.method shared`,
# which turns on forwarding and masquerades wlan0's clients out of every
# other interface. With one hotspot key on every unit, anyone in radio range
# had a route through the box into the site's plant network (Pipi's CRITICAL
# chain, 2026-10-04, second link).
#
# Pinned: the fixed nftables table drops forwarded wlan0 -> wired traffic and
# touches nothing else (no input hook: the box's own dashboard, systems and
# WebRTC stay reachable); install.sh installs it root-owned with a oneshot
# unit ordered before NetworkManager; --allow-ap-forwarding is the one
# documented way out; nftables is in the package list; a box where the table
# did not load counts as a hardening problem.
#
#     python tests/smoke_apisolate.py
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8", newline="") as fh:
        return fh.read()


nft = read("scripts", "nftables", "ap-isolate.nft")
rules = "\n".join(ln for ln in nft.splitlines() if not ln.strip().startswith("#"))
check("the rule file is LF (nft reads a CR as part of the last token)", "\r" not in nft)
check("its own table, so it never touches the OS's or NetworkManager's rules",
      "table inet irivai_ap_isolate" in rules)
check("one chain, on the FORWARD hook only (the box's own services are input)",
      rules.count("hook ") == 1 and "hook forward" in rules
      and "hook input" not in rules and "hook prerouting" not in rules)
check("ahead of NetworkManager's shared-mode accept, policy accept for the rest",
      re.search(r"priority filter - \d+; policy accept;", rules) is not None)
check("hotspot -> ANY other interface is dropped (wired, LTE, VPN), not a list of names",
      'iifname "wlan0" oifname != "wlan0" drop' in rules
      and 'oifname "eth*"' not in rules)
check("...and nothing else is (one drop, no reject of the box's own ports)",
      len(re.findall(r"\bdrop\b", rules)) == 1 and "reject" not in rules
      and "dport" not in rules)

inst = read("scripts", "install.sh")
check("nftables is in the installer's package list",
      re.search(r"APT_PACKAGES=\([^)]*\bnftables\b", inst, re.S) is not None)
check("--allow-ap-forwarding is parsed and documented",
      "--allow-ap-forwarding) AP_FORWARD=1" in inst
      and inst.count("--allow-ap-forwarding") >= 3 and "AP_FORWARD=0" in inst)
step = inst.split("# THE HOTSPOT REACHES THE BOX, NOT THE LAN BEHIND IT.", 1)
check("install.sh has the isolation step", len(step) == 2)
if len(step) == 2:
    step = step[1].split("\n# ----", 1)[0]
    check("...installing the table root-owned outside every release tree",
          "install -m 644 -o root -g root" in step
          and "/usr/local/lib/irivai/ap-isolate.nft" in step)
    check("...loaded by a oneshot unit before NetworkManager brings the hotspot up",
          "irivai-ap-isolate.service" in step and "Type=oneshot" in step
          and "Before=network-pre.target NetworkManager.service" in step
          and "ExecStart=/usr/sbin/nft -f /usr/local/lib/irivai/ap-isolate.nft" in step)
    check("...re-runnable (the old table goes first) and removable (ExecStop)",
          "ExecStartPre=-/usr/sbin/nft delete table inet irivai_ap_isolate" in step
          and "ExecStop=/usr/sbin/nft delete table inet irivai_ap_isolate" in step)
    check("...proved loaded, not assumed", "nft list table inet irivai_ap_isolate" in step
          and "AP_ISOLATED=1" in step)
    check("--allow-ap-forwarding takes an earlier install's table away too",
          "systemctl disable --now irivai-ap-isolate.service" in step
          and "nft delete table inet irivai_ap_isolate" in step)
hard = inst.split('say "Hardening check"', 1)[-1]
check("a box where the table is not loaded is a hardening problem, unless forwarding was asked for",
      "$AP_FORWARD -eq 0 && $AP_ISOLATED -eq 0" in hard)
check(".gitattributes pins *.nft to LF",
      re.search(r"(?m)^\*\.nft\s+text\s+eol=lf", read(".gitattributes")) is not None)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
