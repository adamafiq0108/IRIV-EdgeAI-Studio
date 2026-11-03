# scripts/web_redirect.py -- the one root step behind "Use proxy (port 80)":
# TCP 80 -> the hub's port in its own nftables table, or no redirect.
#
# Root-owned, JSON on stdin, re-validates everything (the set-hostname
# pattern), and installed with the SAME sudoers line by scripts/install.sh
# and the shared root list (scripts/root_items.json: upgrade_steps.sh and
# every update). Pinned here without root:
#   a. the request: on + a port in 1024-65535 that is not reserved, or off;
#      anything else refused before nft is run
#   b. the ruleset: one table, prerouting from anything but lo plus output to
#      lo, replaced atomically (add + delete + define in one transaction)
#   c. on: refused while something listens on 80; nft failure leaves no state
#      file; success writes it 0644. off: deletes the table and the file
#   c2. tls_on / tls_off: 443 -> the HTTPS port in its own table and state
#      file (core/services/httpsrun.py), the port 80 rules unchanged
#   d. not root: refused. Both installers ship it and the same sudoers rule
#
#     python tests/smoke_webredirect.py
import importlib.util
import json
import os
import re
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

spec = importlib.util.spec_from_file_location(
    "web_redirect", os.path.join(ROOT, "scripts", "web_redirect.py"))
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def refused(raw):
    try:
        helper.parse(raw)
        return ""
    except ValueError as exc:
        return str(exc)


# -- a. the request -------------------------------------------------------------
check("on + port", helper.parse('{"action": "on", "port": 5100}') == ("on", 5100))
check("off", helper.parse('{"action": "off"}') == ("off", None))
for raw, why in (('nope', "not JSON"), ('[1]', "not a JSON object"),
                 ('{"action": "flush"}', "on or off"),
                 ('{"action": "on", "port": "5100"}', "whole number"),
                 ('{"action": "on", "port": true}', "whole number"),
                 ('{"action": "on", "port": 80}', "not a port"),
                 ('{"action": "on", "port": 70000}', "not a port"),
                 ('{"action": "on", "port": 5101}', "not a port"),
                 ('{"action": "on", "port": 5020}', "not a port")):
    check(f"refused: {raw}", why in refused(raw), refused(raw))

# -- b. the ruleset -----------------------------------------------------------------
rules = helper.ruleset(5100)
check("one table of our own, replaced in one transaction",
      rules.startswith("add table inet irivai_web\ndelete table inet irivai_web\n"
                       "table inet irivai_web {"), rules)
check("prerouting: anything but lo, addressed to the box only, TCP 80 -> the port",
      'iifname != "lo" fib daddr type local tcp dport 80 redirect to :5100' in rules
      and "hook prerouting priority dstnat" in rules)
check("output: the box's own browser and probe on lo",
      'oifname "lo" tcp dport 80 redirect to :5100' in rules
      and "hook output priority -100" in rules)
check("nothing else is redirected or dropped",
      rules.count("redirect") == 2 and "drop" not in rules and "accept;" in rules)

# -- c. on / off with a fake nft and a temp state file ---------------------------------
tmp = tempfile.mkdtemp()
helper.STATE = os.path.join(tmp, "irivai", "web-redirect.nft")
ran = []


def run(argv, **kw):
    ran.append(list(argv))
    return types.SimpleNamespace(returncode=run.rc, stderr=run.err, stdout="")


run.rc, run.err = 0, ""
helper.listening_on_80 = lambda: True
check("on is refused while another program listens on 80",
      helper.turn_on(5100, run=run) == 2 and ran == [] and not os.path.exists(helper.STATE))
helper.listening_on_80 = lambda: False
run.rc, run.err = 1, "Error: Could not process rule"
check("an nft failure leaves no state file behind",
      helper.turn_on(5100, run=run) == 2 and not os.path.exists(helper.STATE)
      and not os.path.exists(helper.STATE + ".irivai-tmp"))
run.rc, run.err = 0, ""
ran.clear()
check("on: nft -f the rules, then the state file (0644) says so",
      helper.turn_on(5200, run=run) == 0 and ran[-1][:2] == [helper.NFT, "-f"]
      and open(helper.STATE).read() == helper.ruleset(5200)
      and (os.name == "nt" or oct(os.stat(helper.STATE).st_mode & 0o777) == "0o644"))
ran.clear()
check("off: the table is deleted and the state file goes",
      helper.turn_off(run=run) == 0
      and ran == [[helper.NFT, "delete", "table", "inet", "irivai_web"]]
      and not os.path.exists(helper.STATE))
check("off twice is fine (no state file)", helper.turn_off(run=run) == 0)

# /proc/net/tcp parsing: LISTEN (0A) on :0050 only
proc = os.path.join(tmp, "tcp")
open(proc, "w").write(
    "  sl  local_address rem_address   st\n"
    "   0: 00000000:13EC 00000000:0000 0A\n"
    "   1: 0100007F:0050 0100007F:9C40 01\n")
spec2 = importlib.util.spec_from_file_location("wr2", os.path.join(ROOT, "scripts",
                                                                    "web_redirect.py"))
fresh = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(fresh)
check("/proc/net/tcp: only a LISTEN row on :0050 counts",
      fresh.listening_on_80((proc,)) is False)
open(proc, "a").write("   2: 00000000:0050 00000000:0000 0A\n")
check("...and one does", fresh.listening_on_80((proc,)) is True)

# -- c2. 443 -> the HTTPS port: its own table and state file ---------------------------
check("tls_on + port, tls_off", helper.parse('{"action": "tls_on", "port": 8443}')
      == ("tls_on", 8443) and helper.parse('{"action": "tls_off"}') == ("tls_off", None))
check("tls_on is held to the same port rules",
      "not a port" in refused('{"action": "tls_on", "port": 443}')
      and "not a port" in refused('{"action": "tls_on", "port": 5101}'))
tls_rules = helper.ruleset(8443, helper.TLS_TABLE, 443)
check("the 443 table: same shape, 443 -> the HTTPS port, nothing about 80",
      tls_rules.startswith("add table inet irivai_web_tls\n")
      and 'tcp dport 443 redirect to :8443' in tls_rules and "dport 80 " not in tls_rules
      and tls_rules.count("redirect") == 2, tls_rules)
check("...and the port 80 rules are exactly what they were",
      helper.ruleset(5100) == rules)
helper.TLS_STATE = os.path.join(tmp, "irivai", "web-redirect-tls.nft")
seen_port = []
helper.listening_on_80 = lambda paths=(), port=80: seen_port.append(port) or port == 443
check("tls_on is refused while another program listens on 443",
      helper.turn_on(8443, run=run, tls=True) == 2 and seen_port == [443]
      and not os.path.exists(helper.TLS_STATE))
helper.listening_on_80 = lambda paths=(), port=80: False
ran.clear()
check("tls_on: its own state file, the port 80 one untouched",
      helper.turn_on(8443, run=run, tls=True) == 0
      and open(helper.TLS_STATE).read() == tls_rules and not os.path.exists(helper.STATE))
ran.clear()
check("tls_off: only the 443 table goes",
      helper.turn_off(run=run, tls=True) == 0
      and ran == [[helper.NFT, "delete", "table", "inet", "irivai_web_tls"]]
      and not os.path.exists(helper.TLS_STATE))
open(proc, "a").write("   3: 00000000:01BB 00000000:0000 0A\n")
check("/proc/net/tcp: a LISTEN row on :01BB is 443", fresh.listening_on_80((proc,), port=443))

# -- d. root, and the installers ---------------------------------------------------------
if hasattr(os, "geteuid") and os.geteuid() != 0:
    check("not root: refused", fresh.main() == 2)
with open(os.path.join(ROOT, "scripts", "install.sh"), encoding="utf-8") as fh:
    inst = fh.read()
with open(os.path.join(ROOT, "scripts", "root_items.json"), encoding="utf-8") as fh:
    items = json.load(fh)
rule = re.search(r"cat > /etc/sudoers.d/irivai-web.tmp <<SUDOERS\n(.*?)\nSUDOERS", inst, re.S)
up_rule = next((s["rule"].replace("{user}", "${SERVICE_USER}") for s in items["sudoers"]
                if s["name"] == "irivai-web"), "")
want = "${SERVICE_USER} ALL=(root) NOPASSWD: /usr/bin/python3 -I /usr/local/lib/irivai/web-redirect.py"
check("install.sh writes irivai-web: one exact command, no arguments",
      rule and rule.group(1).strip() == want, rule and rule.group(1))
check("the root list (upgrade_steps.sh, every update) writes the identical rule",
      up_rule == want, up_rule)
check("install.sh installs the helper root-owned 755",
      'install -m 755 "${SRC_DIR}/scripts/web_redirect.py" /usr/local/lib/irivai/web-redirect.py'
      in inst and "chown root:root /usr/local/lib/irivai/web-redirect.py" in inst)
check("...and normalises its line endings with the others",
      "              /usr/local/lib/irivai/web-redirect.py \\\n" in inst)
check("...and installs the helper",
      {"src": "scripts/web_redirect.py", "dest": "web-redirect.py"} in items["helpers"])
check("the argv the hub runs is exactly the sudoers command",
      " ".join(("/usr/bin/python3", "-I", "/usr/local/lib/irivai/web-redirect.py"))
      in want)
with open(os.path.join(ROOT, "scripts", "web_redirect.py"), "rb") as fh:
    src = fh.read()
check("the helper is LF and starts with python3 -I", b"\r" not in src
      and src.startswith(b"#!/usr/bin/python3 -I\n"))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
